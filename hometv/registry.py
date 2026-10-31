"""Sổ kênh: nhập một playlist đã phân tích vào channels + streams (một transaction mỗi lượt)."""
import hashlib
import json
from dataclasses import dataclass
from urllib.parse import urlsplit

from hometv import db, normalize
from hometv.m3u import Playlist

MAX_HEADER_VALUE = 1024


@dataclass
class IngestStats:
    channels: int  # số kênh có stream active của nguồn sau lượt nhập
    streams: int  # số stream active của nguồn
    new_streams: int
    deactivated: int


def _host(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def _protocol(url: str) -> str:
    try:
        return urlsplit(url).scheme.lower()
    except ValueError:
        return ""


def _find_channel(c, tvg, norm, bc, disp, tvg_map, name_map):
    """Thứ tự: tvg-id (không gộp nếu hai đài khác rỗng và khác nhau); tên chuẩn hóa + đài;
    tên gốc do chính nguồn này tạo (khi không rõ đài hoặc tên chuẩn hóa rỗng).
    tvg_map/name_map: tra cứu trong lượt nhập (tvg -> [(kênh, đài)], tên gốc của nguồn -> kênh)."""
    if tvg:
        cands = list(tvg_map.get(tvg, ()))
        cands += [(r[0], r[1]) for r in c.execute(
            "SELECT id, broadcaster FROM channels WHERE tvg_id=? ORDER BY id", (tvg,))]
        for cid, cbc in cands:
            if not (bc and cbc and bc != cbc):
                return cid
    if norm and bc:
        r = c.execute("SELECT id FROM channels WHERE normalized_name=? AND broadcaster=? ORDER BY id LIMIT 1",
                      (norm, bc)).fetchone()
        if r:
            return r[0]
    if not bc or not norm:
        return name_map.get(disp)
    return None


def _still_matches(ch, old_disp, tvg, norm, bc, disp) -> bool:
    """Quy tắc không chuyển kênh: stream đã có chỉ đổi kênh khi khóa gộp của chính nó đổi.
    Khóa vẫn còn: cùng tên gốc, hoặc cùng tvg-id, hoặc cùng tên chuẩn hóa + đài.
    Kênh mang tvg-id khác (tvg_id chỉ điền khi trống) KHÔNG phải là khóa đổi."""
    if old_disp == disp:
        return True
    if tvg and ch["tvg_id"] == tvg:
        return True
    return bool(norm and bc and ch["normalized_name"] == norm and ch["broadcaster"] == bc)


def _refresh_channel(c, cid, now):
    """display_name theo stream active của nguồn ưu tiên cao nhất (priority nhỏ = cao)."""
    r = c.execute(
        "SELECT st.display_name FROM streams st JOIN sources so ON so.id=st.source_id "
        "WHERE st.channel_id=? AND st.active=1 ORDER BY so.priority, so.id, st.id LIMIT 1", (cid,)).fetchone()
    if r and r[0]:
        c.execute("UPDATE channels SET display_name=?, updated_at=? WHERE id=? AND display_name IS NOT ?",
                  (r[0], now, cid, r[0]))


def ingest(source_id: str, playlist: Playlist, now: float) -> IngestStats:
    with db.tx() as c:
        if not c.execute("SELECT 1 FROM sources WHERE id=?", (source_id,)).fetchone():
            return IngestStats(0, 0, 0, 0)
        touched: set[int] = set()
        cats: dict[int, str] = {}
        seen: set[str] = set()
        tvg_map: dict[str, list] = {}
        name_map: dict[str, int] = {
            r[0]: r[1] for r in c.execute(
                "SELECT display_name, MIN(channel_id) FROM streams WHERE source_id=? GROUP BY display_name",
                (source_id,))}
        new_streams = 0
        for e in playlist.entries:
            url = e.url.strip()
            h = hashlib.sha256(url.encode()).hexdigest()
            if not url or h in seen:
                continue
            seen.add(h)
            disp = e.name.strip() or e.tvg_name.strip() or _host(url)
            norm = normalize.normalize_name(disp)
            tvg = normalize.clean_tvg_id(e.tvg_id)
            bc = normalize.broadcaster(norm, e.group_title)
            cat = normalize.category(e.group_title, bc)
            headers = {k: v for k, v in e.http_headers.items() if len(v) <= MAX_HEADER_VALUE}

            old = c.execute("SELECT id, channel_id, display_name FROM streams "
                            "WHERE source_id=? AND url_hash=?", (source_id, h)).fetchone()
            cid = None
            if old:
                ch = c.execute("SELECT * FROM channels WHERE id=?", (old["channel_id"],)).fetchone()
                if ch and _still_matches(ch, old["display_name"], tvg, norm, bc, disp):
                    cid = ch["id"]
            if cid is None:
                cid = _find_channel(c, tvg, norm, bc, disp, tvg_map, name_map)
            if cid is None:
                cid = c.execute(
                    "INSERT INTO channels(tvg_id, normalized_name, broadcaster, display_name, category, "
                    "logo_url, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    (tvg or None, norm, bc, disp, cat, e.tvg_logo or None, now, now)).lastrowid
            else:
                # điền chỗ trống của kênh; không đè giá trị đã có
                c.execute("UPDATE channels SET tvg_id=COALESCE(NULLIF(tvg_id,''), ?), "
                          "normalized_name=COALESCE(NULLIF(normalized_name,''), ?), "
                          "broadcaster=COALESCE(NULLIF(broadcaster,''), ?), "
                          "logo_url=COALESCE(NULLIF(logo_url,''), ?), updated_at=? WHERE id=?",
                          (tvg or None, norm, bc, e.tvg_logo or None, now, cid))
            if old and old["channel_id"] != cid:
                touched.add(old["channel_id"])
            touched.add(cid)
            cats[cid] = cat
            name_map.setdefault(disp, cid)
            if tvg:
                tvg_map.setdefault(tvg, []).append((cid, bc))
            c.execute(
                "INSERT INTO streams(channel_id, source_id, url, url_hash, protocol, http_headers, display_name, "
                "quality_hint, drm_hint, first_seen_at, last_seen_at, active) VALUES (?,?,?,?,?,?,?,?,?,?,?,1) "
                "ON CONFLICT(source_id, url_hash) DO UPDATE SET channel_id=excluded.channel_id, "
                "url=excluded.url, protocol=excluded.protocol, http_headers=excluded.http_headers, "
                "display_name=excluded.display_name, quality_hint=excluded.quality_hint, "
                "drm_hint=excluded.drm_hint, last_seen_at=excluded.last_seen_at, active=1",
                (cid, source_id, url, h, _protocol(url), json.dumps(headers) if headers else None, disp,
                 normalize.quality_hint(disp), 1 if e.drm_hint else 0, now, now))
            if not old:
                new_streams += 1

        # stream nguồn không còn liệt kê: active=0 (không xoá)
        deactivated = 0
        for r in c.execute("SELECT id, channel_id, url_hash FROM streams WHERE source_id=? AND active=1",
                           (source_id,)).fetchall():
            if r["url_hash"] not in seen:
                c.execute("UPDATE streams SET active=0 WHERE id=?", (r["id"],))
                touched.add(r["channel_id"])
                deactivated += 1

        for cid in touched:
            _refresh_channel(c, cid, now)
            # category tự động: theo mục của nguồn đang thắng ở kênh này
            if cid in cats:
                win = c.execute(
                    "SELECT st.source_id FROM streams st JOIN sources so ON so.id=st.source_id "
                    "WHERE st.channel_id=? AND st.active=1 ORDER BY so.priority, so.id, st.id LIMIT 1",
                    (cid,)).fetchone()
                if win and win[0] == source_id:
                    c.execute("UPDATE channels SET category=? WHERE id=? AND category_source='auto'",
                              (cats[cid], cid))
        # kênh vừa mất hết stream do chuyển kênh thì xoá
        if touched:
            c.execute("DELETE FROM channels WHERE id IN (%s) AND NOT EXISTS "
                      "(SELECT 1 FROM streams WHERE channel_id=channels.id)" % ",".join("?" * len(touched)),
                      list(touched))

        r = c.execute("SELECT COUNT(*), COUNT(DISTINCT channel_id) FROM streams WHERE source_id=? AND active=1",
                      (source_id,)).fetchone()
        return IngestStats(r[1], r[0], new_streams, deactivated)


def prune_orphan_channels(conn) -> int:
    """Xoá kênh không còn stream nào; gọi khi đang giữ db.lock. Trả số kênh đã xoá."""
    return conn.execute("DELETE FROM channels WHERE NOT EXISTS "
                        "(SELECT 1 FROM streams WHERE channel_id=channels.id)").rowcount
