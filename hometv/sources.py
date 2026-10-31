"""Nguồn IPTV: CRUD có kiểm tra đầu vào, nguồn mặc định, giữ bản tốt, lịch thử lại."""
import gzip
import re
import unicodedata

from hometv import db, netguard, registry
from hometv.m3u import Playlist

DEFAULT_REFRESH = 21600  # 6 giờ
FETCH_STALE = 600  # job running quá ngần này giây thì coi như treo
RETRY_DELAYS = (60, 300, 900)
JITTER = 0.2
MAX_DROP = 0.5
KEEP_VERSIONS = 5

DEFAULT_SOURCES = (
    {"id": "doubo-vn", "name": "Doubo Vietnam", "priority": 10,
     "url": "https://cdn.jsdelivr.net/gh/streamline-iptv/iptv-playlists@dist/countries/vn.m3u"},
    {"id": "iptv-org-vn", "name": "IPTV Org Vietnam", "priority": 20,
     "url": "https://iptv-org.github.io/iptv/countries/vn.m3u"},
    {"id": "dangdvd-iptv", "name": "dangdvd IPTV", "priority": 30,
     "url": "https://raw.githubusercontent.com/dangdvd/dangdvd.github.io/main/IPTV.m3u"},
    {"id": "khanh71-iptv", "name": "khanh71 IPTV", "priority": 40,
     "url": "https://raw.githubusercontent.com/khanh71/All-In-One-IPTV/main/iptv.m3u8"},
    {"id": "khanh71-http", "name": "khanh71 HTTP", "priority": 50,
     "url": "https://raw.githubusercontent.com/khanh71/All-In-One-IPTV/main/http-iptv.m3u"},
)

RIGHTS = ("pending_review", "approved", "rejected")
_EDITABLE = ("name", "url", "enabled", "priority", "refresh_hours", "rights_status")


class ValidationError(ValueError):
    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


def _is_int(v) -> bool:
    return type(v) is int  # loại bool, str, float


def _validate(changes: dict) -> tuple[dict, dict]:
    """Trả (giá trị hợp lệ, lỗi); gom lỗi của mọi trường."""
    ok: dict = {}
    errors: dict[str, str] = {}
    for k, v in changes.items():
        if k == "name":
            if isinstance(v, str) and 1 <= len(v.strip()) <= 60:
                ok[k] = v.strip()
            else:
                errors[k] = "Tên phải từ 1 đến 60 ký tự"
        elif k == "url":
            try:
                netguard.check(v)
                ok[k] = v
            except netguard.BlockedURL as e:
                errors[k] = e.reason
        elif k == "priority":
            if _is_int(v) and 1 <= v <= 100:
                ok[k] = v
            else:
                errors[k] = "Ưu tiên phải từ 1 đến 100"
        elif k == "refresh_hours":
            if _is_int(v) and 1 <= v <= 168:
                ok["refresh_interval"] = v * 3600
            else:
                errors[k] = "Chu kỳ phải từ 1 đến 168 giờ"
        elif k == "rights_status":
            if isinstance(v, str) and v in RIGHTS:
                ok[k] = v
            else:
                errors[k] = "Trạng thái không hợp lệ"
        elif k == "enabled":
            if isinstance(v, bool):
                ok[k] = int(v)
            else:
                errors[k] = "Phải là true hoặc false"
        else:
            errors[k] = "không hỗ trợ"
    return ok, errors


def slugify(name: str) -> str:
    """Không dấu, chữ thường, nối bằng '-'. Chưa xử lý trùng (xem _unique_id)."""
    s = unicodedata.normalize("NFD", name.replace("đ", "d").replace("Đ", "D"))
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "nguon"


def _unique_id(c, name: str) -> str:
    base = slugify(name)
    sid, n = base, 1
    while c.execute("SELECT 1 FROM sources WHERE id=?", (sid,)).fetchone():
        n += 1
        sid = f"{base}-{n}"
    return sid


def seed_defaults(now: float) -> int:
    """Thêm 5 nguồn mặc định khi bảng sources trống; không gọi netguard (không DNS)."""
    with db.tx() as c:
        if c.execute("SELECT COUNT(*) FROM sources").fetchone()[0]:
            return 0
        for d in DEFAULT_SOURCES:
            c.execute("INSERT INTO sources(id, name, url, priority, enabled, rights_status, "
                      "refresh_interval, next_fetch_at, created_at, updated_at) "
                      "VALUES (?,?,?,?,1,'pending_review',?,?,?,?)",
                      (d["id"], d["name"], d["url"], d["priority"], DEFAULT_REFRESH, now, now, now))
        return len(DEFAULT_SOURCES)


def _out(r) -> dict:
    d = dict(r)
    d["refresh_hours"] = d["refresh_interval"] // 3600
    return d


def get(source_id: str) -> dict | None:
    with db.lock:
        r = db.conn().execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
    return _out(r) if r else None


def list_sources(now: float) -> list[dict]:
    with db.lock:
        rows = db.conn().execute(
            "SELECT s.*, EXISTS(SELECT 1 FROM jobs j WHERE j.source_id=s.id AND j.status='running' "
            "AND j.started_at > ?) AS fetching FROM sources s ORDER BY s.priority, s.name",
            (now - FETCH_STALE,)).fetchall()
    return [{"id": r["id"], "name": r["name"], "url": r["url"], "enabled": bool(r["enabled"]),
             "priority": r["priority"], "rights_status": r["rights_status"],
             "refresh_hours": r["refresh_interval"] // 3600,
             "last_fetch_at": r["last_fetch_at"], "last_success_at": r["last_success_at"],
             "last_error": r["last_error"], "channel_count": r["channel_count"],
             "stream_count": r["stream_count"], "fetching": bool(r["fetching"])} for r in rows]


def create(name: str, url: str, now: float) -> dict:
    ok, errors = _validate({"name": name, "url": url})
    if errors:
        raise ValidationError(errors)
    with db.tx() as c:
        sid = _unique_id(c, ok["name"])
        c.execute("INSERT INTO sources(id, name, url, priority, enabled, rights_status, "
                  "refresh_interval, next_fetch_at, created_at, updated_at) "
                  "VALUES (?,?,?,50,1,'pending_review',?,?,?,?)",
                  (sid, ok["name"], ok["url"], DEFAULT_REFRESH, now, now, now))
    return get(sid)


def update(source_id: str, changes: dict, now: float) -> dict:
    if get(source_id) is None:
        raise KeyError(source_id)
    ok, errors = _validate(changes)
    if errors:
        raise ValidationError(errors)
    sets = dict(ok, updated_at=now)
    if "url" in ok:  # đổi URL: tải lại từ đầu
        sets.update(content_hash=None, next_fetch_at=now, retry_count=0, last_error=None)
    with db.tx() as c:
        cur = c.execute(f"UPDATE sources SET {', '.join(k + '=?' for k in sets)} WHERE id=?",
                        (*sets.values(), source_id))
        if cur.rowcount == 0:  # bị xoá giữa chừng
            raise KeyError(source_id)
    return get(source_id)


def delete(source_id: str) -> bool:
    with db.tx() as c:
        n = c.execute("DELETE FROM sources WHERE id=?", (source_id,)).rowcount
        registry.prune_orphan_channels(c)
    return n > 0


def request_refresh(source_id: str, now: float) -> bool:
    with db.tx() as c:
        return c.execute("UPDATE sources SET next_fetch_at=?, retry_count=0 WHERE id=?",
                         (now, source_id)).rowcount > 0


def due(now: float) -> list[dict]:
    with db.lock:
        rows = db.conn().execute(
            "SELECT * FROM sources WHERE enabled=1 AND next_fetch_at IS NOT NULL AND next_fetch_at<=? "
            "ORDER BY priority, name", (now,)).fetchall()
    return [_out(r) for r in rows]


def check_version(prev_count: int | None, playlist: Playlist) -> str | None:
    """Lý do bỏ bản mới (giữ bản tốt), hoặc None nếu dùng được."""
    if not playlist.has_header or not playlist.entries:
        return "Playlist hỏng"
    if prev_count and len(playlist.entries) < prev_count * MAX_DROP:
        return "Mất quá nửa số kênh"
    return None


def next_retry_delay(retry_count: int, rand: float) -> float | None:
    if retry_count >= len(RETRY_DELAYS):
        return None
    base = RETRY_DELAYS[retry_count]
    return base + base * JITTER * rand  # tránh sai số float của base * (1 + ...)


def last_good_entry_count(source_id: str) -> int | None:
    with db.lock:
        r = db.conn().execute("SELECT entry_count FROM source_versions WHERE source_id=? "
                              "ORDER BY fetched_at DESC, id DESC LIMIT 1", (source_id,)).fetchone()
    return r[0] if r else None


def record_success(source_id: str, *, now: float, content_hash: str, body: bytes,
                   stats, entry_count: int) -> None:
    with db.tx() as c:
        r = c.execute("SELECT content_hash, refresh_interval FROM sources WHERE id=?",
                      (source_id,)).fetchone()
        if r is None:
            return
        if r["content_hash"] != content_hash:  # hash đổi mới lưu bản
            c.execute("INSERT INTO source_versions(source_id, content_hash, fetched_at, entry_count, body) "
                      "VALUES (?,?,?,?,?)", (source_id, content_hash, now, entry_count, gzip.compress(body)))
            c.execute("DELETE FROM source_versions WHERE source_id=? AND id NOT IN (SELECT id FROM "
                      "source_versions WHERE source_id=? ORDER BY fetched_at DESC, id DESC LIMIT ?)",
                      (source_id, source_id, KEEP_VERSIONS))
        c.execute("UPDATE sources SET content_hash=?, last_fetch_at=?, last_success_at=?, channel_count=?, "
                  "stream_count=?, retry_count=0, last_error=NULL, next_fetch_at=? WHERE id=?",
                  (content_hash, now, now, stats.channels, stats.streams,
                   now + r["refresh_interval"], source_id))


def record_unchanged(source_id: str, *, now: float) -> None:
    with db.tx() as c:
        c.execute("UPDATE sources SET last_fetch_at=?, last_success_at=?, retry_count=0, last_error=NULL, "
                  "next_fetch_at=?+refresh_interval WHERE id=?", (now, now, now, source_id))


def record_failure(source_id: str, *, now: float, message: str, retryable: bool, rand: float) -> None:
    with db.tx() as c:
        r = c.execute("SELECT retry_count, refresh_interval FROM sources WHERE id=?",
                      (source_id,)).fetchone()
        if r is None:
            return
        if not retryable:  # URL bị chặn: chờ đổi URL hoặc bấm làm mới
            retry, nxt = 0, None
        else:
            delay = next_retry_delay(r["retry_count"], rand)
            if delay is None:  # hết lượt thử lại: về chu kỳ thường
                retry, nxt = 0, now + r["refresh_interval"]
            else:
                retry, nxt = r["retry_count"] + 1, now + delay
        c.execute("UPDATE sources SET last_error=?, last_fetch_at=?, retry_count=?, next_fetch_at=? "
                  "WHERE id=?", (message, now, retry, nxt, source_id))
