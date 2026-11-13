"""IPTV sources: validated CRUD, default sources, last-good-version retention, retry schedule."""
import gzip
import re
import time
import unicodedata

from hometv import db, netguard, registry
from hometv.m3u import Playlist

DEFAULT_REFRESH = 21600  # 6 hours
FETCH_STALE = 600  # a running job older than this many seconds is considered stuck
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
    return type(v) is int  # excludes bool, str, float


def _validate(changes: dict) -> tuple[dict, dict]:
    """Return (valid values, errors); collects errors for all fields."""
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
    """No diacritics, lowercase, joined with '-'. Duplicates not handled (see _unique_id)."""
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
    """Add the default sources (approved, no review needed) when the sources table is empty; does not call netguard (no DNS)."""
    with db.tx() as c:
        if c.execute("SELECT COUNT(*) FROM sources").fetchone()[0]:
            return 0
        for d in DEFAULT_SOURCES:
            c.execute("INSERT INTO sources(id, name, url, priority, enabled, rights_status, "
                      "refresh_interval, next_fetch_at, created_at, updated_at) "
                      "VALUES (?,?,?,?,1,'approved',?,?,?,?)",
                      (d["id"], d["name"], d["url"], d["priority"], DEFAULT_REFRESH, now, now, now))
        return len(DEFAULT_SOURCES)


def _out(r) -> dict:
    """Full sources row + refresh_hours (internal, for the scheduler)."""
    d = dict(r)
    d["refresh_hours"] = d["refresh_interval"] // 3600
    return d


def _row(source_id: str):
    """Raw sources row (content_hash, retry_count, timeout, max_size_mb, next_fetch_at...)."""
    return db.read_one("SELECT * FROM sources WHERE id=?", (source_id,))


def internal(source_id: str) -> dict | None:
    r = _row(source_id)
    return _out(r) if r else None


_SELECT_API = ("SELECT s.*, EXISTS(SELECT 1 FROM jobs j WHERE j.source_id=s.id AND j.status='running' "
               "AND j.started_at > ?) AS fetching FROM sources s ")


def _api(r) -> dict:
    """Single API shape for get/create/update/list_sources."""
    return {"id": r["id"], "name": r["name"], "url": r["url"], "enabled": bool(r["enabled"]),
            "priority": r["priority"], "rights_status": r["rights_status"],
            "refresh_hours": r["refresh_interval"] // 3600,
            "last_fetch_at": r["last_fetch_at"], "last_success_at": r["last_success_at"],
            "last_error": r["last_error"], "channel_count": r["channel_count"],
            "stream_count": r["stream_count"], "fetching": bool(r["fetching"])}


def get(source_id: str, now: float | None = None) -> dict | None:
    now = time.time() if now is None else now
    r = db.read_one(_SELECT_API + "WHERE s.id=?", (now - FETCH_STALE, source_id))
    return _api(r) if r else None


def list_sources(now: float) -> list[dict]:
    rows = db.read(_SELECT_API + "ORDER BY s.priority, s.name", (now - FETCH_STALE,))
    return [_api(r) for r in rows]


def create(name: str, url: str, now: float) -> dict:
    ok, errors = _validate({"name": name, "url": url})
    if errors:
        raise ValidationError(errors)
    with db.tx() as c:
        sid = _unique_id(c, ok["name"])
        c.execute("INSERT INTO sources(id, name, url, priority, enabled, rights_status, "
                  "refresh_interval, next_fetch_at, created_at, updated_at) "
                  "VALUES (?,?,?,50,1,'approved',?,?,?,?)",
                  (sid, ok["name"], ok["url"], DEFAULT_REFRESH, now, now, now))
    return get(sid, now)


def update(source_id: str, changes: dict, now: float) -> dict:
    cur_row = _row(source_id)
    if cur_row is None:
        raise KeyError(source_id)
    ok, errors = _validate(changes)
    if errors:
        raise ValidationError(errors)
    sets = dict(ok, updated_at=now)
    if ok.get("rights_status") == "approved" and cur_row["rights_status"] != "approved":
        sets["next_check_at"] = now  # just approved: score streams soon
    url_changed = "url" in ok and ok["url"] != cur_row["url"]
    if url_changed:  # URL changed: refetch from scratch, drop the old URL's good version
        sets.update(content_hash=None, next_fetch_at=now, retry_count=0, last_error=None)
    with db.tx() as c:
        cur = c.execute(f"UPDATE sources SET {', '.join(k + '=?' for k in sets)} WHERE id=?",
                        (*sets.values(), source_id))
        if cur.rowcount == 0:  # deleted in the meantime
            raise KeyError(source_id)
        if url_changed:  # new URL has no "good version" baseline to compare channel counts
            c.execute("DELETE FROM source_versions WHERE source_id=?", (source_id,))
    return get(source_id, now)


def delete(source_id: str) -> bool:
    with db.tx() as c:
        n = c.execute("DELETE FROM sources WHERE id=?", (source_id,)).rowcount
        registry.prune_orphan_channels(c)
    return n > 0


# Sources where "Refresh now" was clicked: the next run skips the lost-over-half-channels check.
# Kept in memory (lost on restart: just click again then).
_force_next: set[str] = set()


def request_refresh(source_id: str, now: float) -> bool:
    with db.tx() as c:
        ok = c.execute("UPDATE sources SET next_fetch_at=?, retry_count=0 WHERE id=?",
                       (now, source_id)).rowcount > 0
    if ok:
        _force_next.add(source_id)
    return ok


def due(now: float) -> list[dict]:
    rows = db.read(
        "SELECT * FROM sources WHERE enabled=1 AND next_fetch_at IS NOT NULL AND next_fetch_at<=? "
        "ORDER BY priority, name", (now,))
    return [_out(r) for r in rows]


def check_version(prev_count: int | None, playlist: Playlist) -> str | None:
    """Reason to reject the new version (keep the good one), or None if usable."""
    if not playlist.has_header or not playlist.entries:
        return "Playlist hỏng"
    if prev_count and len(playlist.entries) < prev_count * MAX_DROP:
        return "Mất quá nửa số kênh"
    return None


def next_retry_delay(retry_count: int, rand: float) -> float | None:
    if retry_count >= len(RETRY_DELAYS):
        return None
    base = RETRY_DELAYS[retry_count]
    return base + base * JITTER * rand  # avoids the float error of base * (1 + ...)


def last_good_entry_count(source_id: str) -> int | None:
    r = db.read_one("SELECT entry_count FROM source_versions WHERE source_id=? "
                    "ORDER BY fetched_at DESC, id DESC LIMIT 1", (source_id,))
    return r[0] if r else None


def record_success(source_id: str, *, now: float, content_hash: str, body: bytes,
                   stats, entry_count: int) -> None:
    with db.tx() as c:
        r = c.execute("SELECT content_hash, refresh_interval FROM sources WHERE id=?",
                      (source_id,)).fetchone()
        if r is None:
            return
        changed = r["content_hash"] != content_hash
        if changed:  # store a version only when the hash changed
            c.execute("INSERT INTO source_versions(source_id, content_hash, fetched_at, entry_count, body) "
                      "VALUES (?,?,?,?,?)", (source_id, content_hash, now, entry_count, gzip.compress(body)))
            c.execute("DELETE FROM source_versions WHERE source_id=? AND id NOT IN (SELECT id FROM "
                      "source_versions WHERE source_id=? ORDER BY fetched_at DESC, id DESC LIMIT ?)",
                      (source_id, source_id, KEEP_VERSIONS))
        c.execute("UPDATE sources SET content_hash=?, last_fetch_at=?, last_success_at=?, channel_count=?, "
                  "stream_count=?, retry_count=0, last_error=NULL, next_fetch_at=? WHERE id=?",
                  (content_hash, now, now, stats.channels, stats.streams,
                   now + r["refresh_interval"], source_id))
        if changed:  # new playlist: new streams should be scored soon
            c.execute("UPDATE sources SET next_check_at=? WHERE id=?", (now, source_id))


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
        if not retryable:  # URL blocked: wait for a URL change or a manual refresh
            retry, nxt = 0, None
        else:
            delay = next_retry_delay(r["retry_count"], rand)
            if delay is None:  # out of retries: back to the normal interval
                retry, nxt = 0, now + r["refresh_interval"]
            else:
                retry, nxt = r["retry_count"] + 1, now + delay
        c.execute("UPDATE sources SET last_error=?, last_fetch_at=?, retry_count=?, next_fetch_at=? "
                  "WHERE id=?", (message, now, retry, nxt, source_id))
