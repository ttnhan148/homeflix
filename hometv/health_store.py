"""Lưu và truy vấn sức khỏe stream (bảng stream_health) cùng lịch kiểm của nguồn."""
import json

from hometv import catalog, db, scoring
from hometv.health import HealthResult

CHECK_EVERY = 6 * 3600
FAIL_HIDE = catalog.FAIL_HIDE

# Nguồn đang có lượt kiểm chạy (scheduler thêm/bớt; chỉ để hiển thị cờ "Đang kiểm tra…")
checking: set[str] = set()


def eligible(source_id: str) -> bool:
    """Nguồn còn được phép kiểm: tồn tại, đang bật, đã duyệt."""
    r = db.conn().execute("SELECT 1 FROM sources WHERE id=? AND enabled=1 AND rights_status='approved'",
                          (source_id,)).fetchone()
    return r is not None


def streams_to_check(source_id: str) -> list[dict]:
    rows = db.conn().execute(
        "SELECT id, url, http_headers FROM streams WHERE source_id=? AND active=1 AND drm_hint=0 ORDER BY id",
        (source_id,)).fetchall()
    out = []
    for r in rows:
        try:
            headers = json.loads(r["http_headers"]) if r["http_headers"] else {}
        except ValueError:
            headers = {}
        out.append({"id": r["id"], "url": r["url"], "http_headers": headers if isinstance(headers, dict) else {},
                    "https": r["url"].lower().startswith("https:")})
    return out


def record(stream_id: int, result: HealthResult, https: bool, now: float) -> None:
    with db.tx() as c:
        if c.execute("SELECT 1 FROM streams WHERE id=?", (stream_id,)).fetchone() is None:
            return  # stream bị xoá giữa lúc kiểm
        prev = c.execute("SELECT fail_streak FROM stream_health WHERE stream_id=?", (stream_id,)).fetchone()
        streak = 0 if result.ok else (prev["fail_streak"] if prev else 0) + 1
        c.execute(
            "INSERT INTO stream_health(stream_id, checked_at, ok, error, latency_ms, throughput_kbps, width, height, "
            "vcodec, bitrate_kbps, fail_streak, score) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(stream_id) DO UPDATE SET checked_at=excluded.checked_at, ok=excluded.ok, "
            "error=excluded.error, latency_ms=excluded.latency_ms, throughput_kbps=excluded.throughput_kbps, "
            "width=excluded.width, height=excluded.height, vcodec=excluded.vcodec, "
            "bitrate_kbps=excluded.bitrate_kbps, fail_streak=excluded.fail_streak, score=excluded.score",
            (stream_id, now, int(result.ok), result.error, result.latency_ms, result.throughput_kbps,
             result.width, result.height, result.vcodec, result.bitrate_kbps, streak,
             scoring.score(result, https)))


def due_sources(now: float) -> list[str]:
    rows = db.conn().execute(
        "SELECT id FROM sources WHERE enabled=1 AND rights_status='approved' "
        "AND (next_check_at IS NULL OR next_check_at <= ?) ORDER BY priority, name", (now,)).fetchall()
    return [r["id"] for r in rows]


def finish_source(source_id: str, now: float) -> None:
    with db.tx() as c:
        c.execute("UPDATE sources SET last_check_at=?, next_check_at=? WHERE id=?",
                  (now, now + CHECK_EVERY, source_id))


def request_check(source_id: str, now: float) -> bool:
    with db.tx() as c:
        return c.execute("UPDATE sources SET next_check_at=? WHERE id=? AND enabled=1 AND rights_status='approved'",
                         (now, source_id)).rowcount > 0


def source_health(now: float) -> dict[str, dict]:
    c = db.conn()
    out = {r["id"]: {"checked": 0, "alive": 0, "dead": 0, "unknown": 0, "checking": r["id"] in checking}
           for r in c.execute("SELECT id FROM sources")}
    for r in c.execute(
            "SELECT s.source_id, COUNT(h.stream_id) AS checked, COALESCE(SUM(h.ok = 1), 0) AS alive, "
            "COALESCE(SUM(h.ok = 0), 0) AS dead, COALESCE(SUM(h.stream_id IS NULL), 0) AS unknown "
            "FROM streams s LEFT JOIN stream_health h ON h.stream_id = s.id "
            "WHERE s.active = 1 AND s.drm_hint = 0 GROUP BY s.source_id"):
        if r["source_id"] in out:
            out[r["source_id"]].update(checked=r["checked"], alive=r["alive"], dead=r["dead"], unknown=r["unknown"])
    return out


def dead_streams(source_id: str, status: str, limit: int) -> list[dict]:
    where = "AND h.ok = 0" if status == "dead" else ""
    rows = db.conn().execute(
        "SELECT s.id AS stream_id, COALESCE(NULLIF(s.display_name, ''), ch.display_name, ch.normalized_name, '') "
        "AS channel_name, h.ok, h.error, h.fail_streak, h.width, h.height, h.score "
        "FROM stream_health h JOIN streams s ON s.id = h.stream_id JOIN channels ch ON ch.id = s.channel_id "
        f"WHERE s.source_id = ? AND s.active = 1 AND s.drm_hint = 0 {where} "
        "ORDER BY h.fail_streak DESC, s.id LIMIT ?", (source_id, limit)).fetchall()
    return [{**dict(r), "ok": bool(r["ok"])} for r in rows]
