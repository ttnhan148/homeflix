"""Đo sức khỏe một stream: tải playlist và đoạn đầu bằng Python, ffprobe đọc file tạm.

Bản gọn: đường tải dùng play.open_upstream (chưa chặn SSRF, chưa giới hạn tài nguyên ngoài các mức dưới đây)."""
import asyncio
import json
import os
import tempfile
import time
from dataclasses import dataclass

import httpx
import m3u8

from hometv import play
from hometv.fetcher import FetchError

FFPROBE = "ffprobe"
PROBE_TIMEOUT = 15.0
DOWNLOAD_TIMEOUT = 10.0
SEGMENT_CAP = 2 * 1024 * 1024
DIRECT_CAP = 1024 * 1024
PLAYLIST_CAP = 1024 * 1024
DEAD_VIDEO = "Không đọc được video"
DEAD_SEGMENT = "Không tải được đoạn"
DEAD_PLAYLIST = "Không phải playlist"
DEAD_TIMEOUT = "Hết thời gian chờ"


@dataclass
class HealthResult:
    ok: bool
    error: str | None = None
    latency_ms: int | None = None
    throughput_kbps: int | None = None
    width: int | None = None
    height: int | None = None
    vcodec: str | None = None
    bitrate_kbps: int | None = None


class FfprobeMissing(Exception):
    pass


class ProbeError(Exception):
    pass


class _Dead(Exception):
    """Stream chết với lý do ngắn (đã là chuỗi hiển thị)."""


async def ffprobe(data: bytes) -> dict:
    """Chạy ffprobe trên byte đã tải (qua file tạm, xoá ngay); trả JSON đã parse."""
    fd, path = tempfile.mkstemp(prefix="hometv-probe-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        try:
            proc = await asyncio.create_subprocess_exec(
                FFPROBE, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path,
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        except FileNotFoundError:
            raise FfprobeMissing()
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), PROBE_TIMEOUT)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            proc.kill()
            await proc.wait()
            raise
        if proc.returncode != 0:
            raise ProbeError(DEAD_VIDEO)
        try:
            info = json.loads(out)
        except ValueError:
            raise ProbeError(DEAD_VIDEO)
        if not isinstance(info, dict):
            raise ProbeError(DEAD_VIDEO)
        return info
    except asyncio.TimeoutError:
        raise ProbeError(DEAD_VIDEO)
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


async def _open(client, url, headers):
    try:
        return await play.open_upstream(client, url, headers)
    except FetchError as e:
        raise _Dead(e.message)


async def _playlist(client, url, headers) -> tuple[str, str, int]:
    """Trả (nội dung, URL cuối, độ trễ ms tới khi mở được kết nối)."""
    t0 = time.monotonic()
    resp, final = await _open(client, url, headers)
    latency = int((time.monotonic() - t0) * 1000)
    try:
        body = b""
        async for chunk in resp.aiter_raw():
            body += chunk
            if len(body) > PLAYLIST_CAP:
                break
    finally:
        await resp.aclose()
    text = body.decode("utf-8", errors="replace").lstrip("﻿ \t\r\n")
    if not text.startswith("#EXTM3U"):
        raise _Dead(DEAD_PLAYLIST)
    return text, final, latency


async def _download(client, url, headers, cap) -> tuple[bytes, float, int]:
    """Tải tối đa cap byte. Trả (dữ liệu, giây tải, độ trễ ms)."""
    t0 = time.monotonic()
    resp, _ = await _open(client, url, headers)
    latency = int((time.monotonic() - t0) * 1000)
    data = bytearray()
    t1 = time.monotonic()
    try:
        async for chunk in resp.aiter_raw():
            data += chunk
            if len(data) >= cap:
                break
    finally:
        await resp.aclose()
    return bytes(data[:cap]), time.monotonic() - t1, latency


def _pick_variant(master: m3u8.M3U8) -> str | None:
    """Variant có độ phân giải khai báo và BANDWIDTH thấp nhất; không có thì variant đầu."""
    plays = [p for p in master.playlists if p.absolute_uri]
    if not plays:
        return None
    with_res = [p for p in plays if p.stream_info.resolution]
    pool = with_res or plays[:1]
    return min(pool, key=lambda p: p.stream_info.bandwidth or 0).absolute_uri


async def _measure(client, url, headers, probe) -> HealthResult:
    latency, seg_duration = None, None
    if play.is_direct_url(url):
        data, secs, latency = await _download(client, url, headers, DIRECT_CAP)
    else:
        text, final, latency = await _playlist(client, url, headers)
        pl = m3u8.loads(text, uri=final)
        if pl.is_variant:
            variant = _pick_variant(pl)
            if not variant:
                raise _Dead(DEAD_PLAYLIST)
            text, final, _ = await _playlist(client, variant, headers)
            pl = m3u8.loads(text, uri=final)
        if not pl.segments:
            raise _Dead(DEAD_SEGMENT)
        seg_duration = pl.segments[0].duration
        try:
            data, secs, _ = await _download(client, pl.segments[0].absolute_uri, headers, SEGMENT_CAP)
        except _Dead:
            raise _Dead(DEAD_SEGMENT)
    if not data:
        raise _Dead(DEAD_SEGMENT)
    try:
        info = await probe(data)
    except ProbeError:
        raise _Dead(DEAD_VIDEO)
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        raise _Dead(DEAD_VIDEO)
    bits = video.get("bit_rate") or (info.get("format") or {}).get("bit_rate")
    try:
        bitrate = int(int(bits) / 1000) if bits else None
    except (TypeError, ValueError):
        bitrate = None
    if bitrate is None and seg_duration:
        bitrate = int(len(data) * 8 / 1000 / seg_duration)
    return HealthResult(
        ok=True, latency_ms=latency, throughput_kbps=int(len(data) * 8 / 1000 / max(secs, 1e-3)),
        width=video.get("width"), height=video.get("height"), vcodec=video.get("codec_name"),
        bitrate_kbps=bitrate)


async def check_stream(client: httpx.AsyncClient, url: str, headers: dict[str, str], *,
                       probe=ffprobe) -> HealthResult:
    """Không bao giờ ném lỗi, trừ FfprobeMissing (cả bộ kiểm không chạy được)."""
    try:
        async with asyncio.timeout(DOWNLOAD_TIMEOUT * 3 + PROBE_TIMEOUT):
            return await _measure(client, url, headers, probe)
    except FfprobeMissing:
        raise
    except _Dead as e:
        return HealthResult(ok=False, error=str(e))
    except (asyncio.TimeoutError, httpx.TimeoutException):
        return HealthResult(ok=False, error=DEAD_TIMEOUT)
    except Exception:
        return HealthResult(ok=False, error=DEAD_VIDEO)
