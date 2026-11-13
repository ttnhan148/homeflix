"""Read the channel registry for viewers: only channels with valid streams; favorites and recently watched."""
import json
from urllib.parse import quote

from hometv import db, scoring

MAX_RECENT = 20
NAME_MAX = 120

# Watchable stream: still present, no DRM, source approved and enabled, channel enabled
_VALID = ("s.active = 1 AND s.drm_hint = 0 AND src.enabled = 1 "
          "AND src.rights_status = 'approved' AND ch.enabled = 1")
_HEALTH = " LEFT JOIN stream_health h ON h.stream_id = s.id"
_FROM = "streams s JOIN sources src ON src.id = s.source_id JOIN channels ch ON ch.id = s.channel_id"


def parse_headers(raw) -> dict:
    """Decode a stream's http_headers JSON column; anything invalid or not an object yields {}."""
    try:
        headers = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return headers if isinstance(headers, dict) else {}


def _is_valid_channel(c, channel_id: int) -> bool:
    row = c.execute(f"SELECT 1 FROM {_FROM} WHERE s.channel_id = ? AND {_VALID} LIMIT 1",
                    (channel_id,)).fetchone()
    return row is not None


def _status(ok_max, use_health: bool) -> str | None:
    if not use_health:
        return None
    return "unchecked" if ok_max is None else ("alive" if ok_max else "dead")


def list_channels(now: float, use_health: bool = False) -> list[dict]:
    """use_health: only channels with at least one valid stream that passed its last check (dead/unchecked hidden)."""
    alive = " AND h.ok = 1" if use_health else ""
    join = _HEALTH if use_health else ""
    # MAX(h.ok): 1 = some stream passed its last check, 0 = checked and all dead, NULL = not checked
    ok_max = (f"(SELECT MAX(h.ok) FROM {_FROM}{_HEALTH} WHERE s.channel_id = oc.id AND {_VALID}) AS ok_max"
              if use_health else "NULL AS ok_max")
    rows = db.read(f"""
        SELECT oc.id, oc.category, oc.logo_url, oc.display_name, oc.normalized_name,
               (SELECT s.display_name FROM {_FROM} WHERE s.channel_id = oc.id AND {_VALID}
                ORDER BY src.priority, s.id LIMIT 1) AS best_name,
               f.channel_id IS NOT NULL AS favorite, r.viewed_at,
               {ok_max}
        FROM channels oc
        LEFT JOIN favorites f ON f.channel_id = oc.id
        LEFT JOIN recents r ON r.channel_id = oc.id
        WHERE oc.enabled = 1 AND EXISTS (SELECT 1 FROM {_FROM}{join} WHERE s.channel_id = oc.id AND {_VALID}{alive})
        ORDER BY oc.normalized_name, oc.id""")
    return [{
        "id": r["id"],
        "name": (r["best_name"] or r["display_name"] or r["normalized_name"] or "")[:NAME_MAX],
        "category": r["category"] or "Unknown",
        "logo": "/proxy/image?url=" + quote(r["logo_url"], safe="") if r["logo_url"] else None,
        "favorite": bool(r["favorite"]),
        "recent_at": r["viewed_at"],
        "status": _status(r["ok_max"], use_health),
    } for r in rows]


def channel_streams(channel_id: int, use_health: bool = False) -> list[dict]:
    """Try order; use_health: health score descending first (unchecked counts as 50)."""
    join = _HEALTH if use_health else ""
    by_score = f"COALESCE(h.score, {scoring.UNKNOWN}) DESC, " if use_health else ""
    rows = db.read(f"""
        SELECT s.id, s.url, s.quality_hint FROM {_FROM}{join}
        WHERE s.channel_id = ? AND {_VALID}
        ORDER BY {by_score}src.priority, (s.url LIKE 'https:%') DESC, s.id""", (channel_id,))
    return [{"id": r["id"], "url": r["url"], "quality_hint": r["quality_hint"]} for r in rows]


def valid_stream(stream_id: int) -> dict | None:
    found = db.read(f"SELECT s.id, s.url, s.http_headers FROM {_FROM} WHERE s.id = ? AND {_VALID}", (stream_id,))
    if not found:
        return None
    r = found[0]
    return {"id": r["id"], "url": r["url"], "http_headers": parse_headers(r["http_headers"])}


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
