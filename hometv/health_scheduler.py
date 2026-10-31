"""Vòng nền kiểm tra sức khỏe stream: chỉ chạy khi máy chủ ở Việt Nam (xem geo.py)."""
import asyncio
import logging
import os
import time
from urllib.parse import urlsplit

from hometv import fetcher, geo, health, health_store, play

logger = logging.getLogger("hometv.health")

MAX_PROBES = 4
TICK = 60
DEAD_UNEXPECTED = "Không đọc được video"


async def _one(client, source_id: str, stream: dict, now: float, probe, sem, host_locks) -> bool:
    """Kiểm một stream rồi ghi kết quả; False nếu nguồn không còn đủ điều kiện (bỏ, không ghi)."""
    host = urlsplit(stream["url"]).hostname or ""
    async with host_locks.setdefault(host, asyncio.Lock()):
        async with sem:
            if not await asyncio.to_thread(health_store.eligible, source_id):
                return False
            headers = play.upstream_headers(stream["http_headers"], None)
            try:
                result = await health.check_stream(client, stream["url"], headers, probe=probe)
            except health.FfprobeMissing:
                raise
            except Exception:
                logger.exception("HomeTV: lỗi bất ngờ khi kiểm stream %s", stream["id"])
                result = health.HealthResult(ok=False, error=DEAD_UNEXPECTED)
            if not await asyncio.to_thread(health_store.eligible, source_id):
                return False   # bị gỡ duyệt giữa lúc kiểm
            await asyncio.to_thread(health_store.record, stream["id"], result, stream["https"], now)
            return True


async def check_source(client, source_id: str, now: float, *, probe=health.ffprobe,
                       sem: asyncio.Semaphore | None = None, host_locks: dict | None = None) -> int:
    """Kiểm mọi stream của một nguồn; trả số stream đã ghi."""
    sem = sem or asyncio.Semaphore(MAX_PROBES)
    host_locks = {} if host_locks is None else host_locks
    health_store.checking.add(source_id)
    tasks: list[asyncio.Task] = []
    try:
        streams = await asyncio.to_thread(health_store.streams_to_check, source_id)
        tasks = [asyncio.ensure_future(_one(client, source_id, s, now, probe, sem, host_locks)) for s in streams]
        try:
            done = await asyncio.gather(*tasks)
        except BaseException:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        await asyncio.to_thread(health_store.finish_source, source_id, now)
        return sum(1 for d in done if d)
    finally:
        health_store.checking.discard(source_id)


async def run_due_checks(client, now: float, *, probe=health.ffprobe) -> list[str]:
    """Kiểm các nguồn đến hạn; tối đa MAX_PROBES stream song song, mỗi host một lượt."""
    due = [s for s in await asyncio.to_thread(health_store.due_sources, now) if s not in health_store.checking]
    sem = asyncio.Semaphore(MAX_PROBES)
    host_locks: dict = {}
    results = await asyncio.gather(
        *(check_source(client, s, now, probe=probe, sem=sem, host_locks=host_locks) for s in due),
        return_exceptions=True)
    for r in results:
        if isinstance(r, health.FfprobeMissing):
            raise r
        if isinstance(r, Exception):
            logger.error("HomeTV: lượt kiểm hỏng: %r", r)
    return due


async def hometv_health_checker(tick: float = TICK) -> None:
    """Vòng nền: lỗi chỉ log; thiếu ffprobe thì dừng hẳn; đóng client khi bị huỷ."""
    client = fetcher.make_client()
    last_geo = 0.0
    try:
        while True:
            try:
                now = time.time()
                mode = geo.parse_mode(os.environ.get("HOMETV_HEALTH"))
                if last_geo == 0.0 or now - last_geo >= geo.REFRESH_EVERY or geo.current(now).mode != mode:
                    await geo.refresh_mode(client, mode, now)
                    last_geo = now
                if geo.current(now).enabled:
                    await run_due_checks(client, now)
            except health.FfprobeMissing:
                logger.error("HomeTV: không tìm thấy ffprobe, tắt bộ kiểm stream")
                return
            except Exception:
                logger.exception("HomeTV: vòng kiểm stream lỗi")
            await asyncio.sleep(tick)
    finally:
        await client.aclose()
