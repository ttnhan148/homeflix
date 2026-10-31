"""Lịch tải nguồn IPTV: khóa job, một lượt tải, chạy các nguồn đến hạn, dọn dẹp, vòng nền."""
import asyncio
import hashlib
import logging
import random
import time
from urllib.parse import urlsplit

from hometv import db, fetcher, m3u, registry, sources

logger = logging.getLogger("hometv.scheduler")

LOCK_STALE = sources.FETCH_STALE
MAX_PARALLEL = 2
TICK = 60
CLEANUP_EVERY = 86400
JOB_KEEP = 30 * 86400
INGEST_ERROR = "Lỗi nhập dữ liệu"


def acquire_job(source_id: str, now: float) -> int | None:
    """Tạo job running; None nếu nguồn đang có lượt chạy còn trẻ."""
    with db.tx() as c:
        for r in c.execute("SELECT id, started_at FROM jobs WHERE source_id=? AND status='running'",
                           (source_id,)).fetchall():
            if now - r["started_at"] < LOCK_STALE:
                return None
            c.execute("UPDATE jobs SET status='error', completed_at=?, error_message=? WHERE id=?",
                      (now, "Lượt trước bị gián đoạn", r["id"]))
        return c.execute("INSERT INTO jobs(job_type, source_id, status, started_at) "
                         "VALUES ('fetch', ?, 'running', ?)", (source_id, now)).lastrowid


def finish_job(job_id: int, *, status: str, now: float, records: int = 0, skipped: int = 0,
               error: str | None = None) -> None:
    with db.tx() as c:
        c.execute("UPDATE jobs SET status=?, completed_at=?, duration_ms=CAST((?-started_at)*1000 AS INTEGER), "
                  "records_processed=?, skipped_lines=?, error_message=? WHERE id=?",
                  (status, now, now, records, skipped, error, job_id))


def _process(source_id: str, body: bytes, now: float, rand: float):
    """Phân tích, kiểm tra bản tốt, nhập. Trả (kết quả, thông báo lỗi, số bản ghi, số dòng bỏ)."""
    src = sources.internal(source_id)
    if src is None:
        return "gone", None, 0, 0
    digest = hashlib.sha256(body).hexdigest()
    if digest == src["content_hash"]:
        sources.record_unchanged(source_id, now=now)
        return "unchanged", None, 0, 0
    playlist = m3u.parse(m3u.decode(body))
    forced = source_id in sources._force_next  # làm mới tay: bỏ qua kiểm tra mất kênh
    sources._force_next.discard(source_id)
    reason = sources.check_version(None if forced else sources.last_good_entry_count(source_id), playlist)
    if reason:  # giữ bản tốt, không nhập
        sources.record_failure(source_id, now=now, message=reason, retryable=True, rand=rand)
        return "error", reason, 0, playlist.skipped_lines
    stats = registry.ingest(source_id, playlist, now)
    sources.record_success(source_id, now=now, content_hash=digest, body=body,
                           stats=stats, entry_count=len(playlist.entries))
    return "ok", None, len(playlist.entries), playlist.skipped_lines


async def refresh_source(client, source_id: str, *, now: float, rand: float) -> str:
    """Một lượt tải: ok | unchanged | error | skipped | gone.

    Mọi chạm DB đi qua to_thread: db.lock có thể đang bị ingest của nguồn khác giữ,
    gọi thẳng trên event loop sẽ làm đứng cả HomeFlix."""
    job = await asyncio.to_thread(acquire_job, source_id, now)
    if job is None:
        return "skipped"
    t0 = time.monotonic()
    status, error, records, skipped = "error", "Bị hủy", 0, 0
    result = "error"
    try:
        src = await asyncio.to_thread(sources.internal, source_id)
        if src is None:
            result, status, error = "gone", "error", "Nguồn đã bị xoá"
            return result
        try:
            body = await fetcher.fetch_playlist(client, src["url"], timeout=src["timeout"],
                                                max_bytes=src["max_size_mb"] * 1024 * 1024)
        except fetcher.FetchError as e:
            await asyncio.to_thread(_fail, source_id, now, e.message, e.retryable, rand)
            result, status, error = "error", "error", e.message
            return result
        try:
            result, error, records, skipped = await asyncio.to_thread(_process, source_id, body, now, rand)
            status = "error" if result in ("error", "gone") else "ok"
            if result == "gone":
                error = "Nguồn đã bị xoá"
        except Exception:
            logger.exception("HomeTV: lỗi nhập nguồn %s", source_id)
            await asyncio.to_thread(_fail, source_id, now, INGEST_ERROR, True, rand)
            result, status, error = "error", "error", INGEST_ERROR
        return result
    except Exception:
        logger.exception("HomeTV: lỗi không mong đợi khi tải nguồn %s", source_id)
        try:
            await asyncio.to_thread(_fail, source_id, now, INGEST_ERROR, True, rand)
        except Exception:
            logger.exception("HomeTV: không ghi được lỗi nguồn %s", source_id)
        result, status, error = "error", "error", INGEST_ERROR
        return result
    finally:
        # đóng job dù bị hủy; thời điểm kết thúc = now + thời gian thực đã trôi
        done = now + (time.monotonic() - t0)
        fin = asyncio.ensure_future(asyncio.to_thread(
            lambda: finish_job(job, status=status, now=done, records=records, skipped=skipped, error=error)))
        try:
            await asyncio.shield(fin)
        except asyncio.CancelledError:
            await fin  # job đã đóng xong rồi mới cho hủy lan tiếp
            raise


def _fail(source_id: str, now: float, message: str, retryable: bool, rand: float) -> None:
    sources.record_failure(source_id, now=now, message=message, retryable=retryable, rand=rand)


async def run_due(client, now: float) -> list[str]:
    """Chạy các nguồn đến hạn: tối đa MAX_PARALLEL lượt, mỗi host một lượt."""
    due = await asyncio.to_thread(sources.due, now)
    sem = asyncio.Semaphore(MAX_PARALLEL)
    host_locks: dict[str, asyncio.Lock] = {}

    async def one(src: dict) -> str:
        host = urlsplit(src["url"]).hostname or ""
        async with host_locks.setdefault(host, asyncio.Lock()):  # khóa host trước, khỏi chiếm chỗ vô ích
            async with sem:
                try:
                    return await refresh_source(client, src["id"], now=now, rand=random.random())
                except Exception:
                    logger.exception("HomeTV: lượt tải %s hỏng", src["id"])
                    return "error"
    # return_exceptions: đợi mọi lượt đóng job xong rồi mới trả (kể cả khi bị hủy)
    return list(await asyncio.gather(*(one(s) for s in due), return_exceptions=True))


def cleanup(now: float) -> None:
    """Xoá job cũ hơn 30 ngày, mỗi nguồn giữ tối đa KEEP_VERSIONS bản."""
    with db.tx() as c:
        c.execute("DELETE FROM jobs WHERE started_at < ? AND status != 'running'", (now - JOB_KEEP,))
        c.execute("DELETE FROM source_versions WHERE id NOT IN (SELECT id FROM (SELECT id, ROW_NUMBER() "
                  "OVER (PARTITION BY source_id ORDER BY fetched_at DESC, id DESC) AS rn "
                  "FROM source_versions) WHERE rn <= ?)", (sources.KEEP_VERSIONS,))


async def hometv_refresher(tick: float = TICK) -> None:
    """Vòng nền: lỗi chỉ log, không dừng; đóng client khi bị hủy."""
    client = fetcher.make_client()
    last_cleanup = 0.0
    try:
        while True:
            try:
                now = time.time()
                await run_due(client, now)
                if now - last_cleanup >= CLEANUP_EVERY:
                    await asyncio.to_thread(cleanup, now)
                    last_cleanup = now
            except Exception:
                logger.exception("HomeTV: vòng làm mới lỗi")
            await asyncio.sleep(tick)
    finally:
        await client.aclose()
