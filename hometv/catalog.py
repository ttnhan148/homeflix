"""Đọc sổ kênh cho người xem: chỉ kênh có stream hợp lệ; yêu thích và kênh xem gần đây."""
import json
from urllib.parse import quote

from hometv import db, scoring

MAX_RECENT = 20
FAIL_HIDE = 3  # số lần kiểm chết liên tiếp thì ẩn kênh
NAME_MAX = 120

# Stream xem được: còn hiện diện, không DRM, nguồn đã duyệt và đang bật, kênh đang bật
_VALID = ("s.active = 1 AND s.drm_hint = 0 AND src.enabled = 1 "
          "AND src.rights_status = 'approved' AND ch.enabled = 1")
_HEALTH = " LEFT JOIN stream_health h ON h.stream_id = s.id"
_FROM = "streams s JOIN sources src ON src.id = s.source_id JOIN channels ch ON ch.id = s.channel_id"


def _is_valid_channel(c, channel_id: int) -> bool:
    row = c.execute(f"SELECT 1 FROM {_FROM} WHERE s.channel_id = ? AND {_VALID} LIMIT 1",
                    (channel_id,)).fetchone()
    return row is not None


def list_channels(now: float, use_health: bool = False) -> list[dict]:
    """use_health: ẩn kênh mà mọi stream hợp lệ đều chết liên tiếp từ FAIL_HIDE lần kiểm trở lên."""
    alive = f" AND (h.stream_id IS NULL OR h.fail_streak < {FAIL_HIDE})" if use_health else ""
    join = _HEALTH if use_health else ""
    rows = db.conn().execute(f"""
        SELECT oc.id, oc.category, oc.logo_url, oc.display_name, oc.normalized_name,
               (SELECT s.display_name FROM {_FROM} WHERE s.channel_id = oc.id AND {_VALID}
                ORDER BY src.priority, s.id LIMIT 1) AS best_name,
               f.channel_id IS NOT NULL AS favorite, r.viewed_at
        FROM channels oc
        LEFT JOIN favorites f ON f.channel_id = oc.id
        LEFT JOIN recents r ON r.channel_id = oc.id
        WHERE oc.enabled = 1 AND EXISTS (SELECT 1 FROM {_FROM}{join} WHERE s.channel_id = oc.id AND {_VALID}{alive})
        ORDER BY oc.normalized_name, oc.id""").fetchall()
    return [{
        "id": r["id"],
        "name": (r["best_name"] or r["display_name"] or r["normalized_name"] or "")[:NAME_MAX],
        "category": r["category"] or "Unknown",
        "logo": "/proxy/image?url=" + quote(r["logo_url"], safe="") if r["logo_url"] else None,
        "favorite": bool(r["favorite"]),
        "recent_at": r["viewed_at"],
    } for r in rows]


def channel_streams(channel_id: int, use_health: bool = False) -> list[dict]:
    """Thứ tự thử; use_health: điểm sức khỏe giảm dần trước (chưa kiểm tính 50)."""
    join = _HEALTH if use_health else ""
    by_score = f"COALESCE(h.score, {scoring.UNKNOWN}) DESC, " if use_health else ""
    rows = db.conn().execute(f"""
        SELECT s.id, s.url, s.quality_hint FROM {_FROM}{join}
        WHERE s.channel_id = ? AND {_VALID}
        ORDER BY {by_score}src.priority, (s.url LIKE 'https:%') DESC, s.id""", (channel_id,)).fetchall()
    return [{"id": r["id"], "url": r["url"], "quality_hint": r["quality_hint"]} for r in rows]


def valid_stream(stream_id: int) -> dict | None:
    r = db.conn().execute(f"SELECT s.id, s.url, s.http_headers FROM {_FROM} WHERE s.id = ? AND {_VALID}",
                          (stream_id,)).fetchone()
    if r is None:
        return None
    try:
        headers = json.loads(r["http_headers"]) if r["http_headers"] else {}
    except ValueError:
        headers = {}
    return {"id": r["id"], "url": r["url"], "http_headers": headers if isinstance(headers, dict) else {}}


def set_favorite(channel_id: int, on: bool, now: float) -> bool:
    with db.tx() as c:
        if not on:
            c.execute("DELETE FROM favorites WHERE channel_id = ?", (channel_id,))
            return True
        if not _is_valid_channel(c, channel_id):
            return False
        c.execute("INSERT OR IGNORE INTO favorites(channel_id, created_at) VALUES (?, ?)", (channel_id, now))
        return True


def mark_recent(channel_id: int, now: float) -> bool:
    with db.tx() as c:
        if not _is_valid_channel(c, channel_id):
            return False
        c.execute("INSERT INTO recents(channel_id, viewed_at) VALUES (?, ?) "
                  "ON CONFLICT(channel_id) DO UPDATE SET viewed_at = excluded.viewed_at", (channel_id, now))
        c.execute("DELETE FROM recents WHERE channel_id NOT IN "
                  "(SELECT channel_id FROM recents ORDER BY viewed_at DESC LIMIT ?)", (MAX_RECENT,))
        return True
