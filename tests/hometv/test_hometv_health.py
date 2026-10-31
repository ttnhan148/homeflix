import asyncio
import os
import shutil
import stat
import subprocess
import time

import httpx
import pytest

from hometv import fetcher, health
from hometv.health import FfprobeMissing, ProbeError

MEDIA = b"#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\nseg1.ts\n"
MASTER = (b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720\nhi.m3u8\n"
          b"#EXT-X-STREAM-INF:BANDWIDTH=500000,RESOLUTION=640x360\nlo.m3u8\n")
VIDEO = {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720}],
         "format": {"bit_rate": "1500000"}}
URL = "http://cdn.example/live/index.m3u8"


def R(status, content=b"", headers=None):
    return httpx.Response(status, headers=headers, stream=httpx.ByteStream(content))


async def good_probe(data):
    return VIDEO


def check(handler, url=URL, probe=good_probe, headers=None):
    async def go():
        async with fetcher.make_client(httpx.MockTransport(handler)) as client:
            return await health.check_stream(client, url, headers or {}, probe=probe)
    return asyncio.run(go())


def routes(table, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(request.url.path)
        body = table.get(request.url.path)
        return R(200, body) if body is not None else R(404)
    return handler


def test_media_playlist_ok_fills_fields():
    seen = []
    r = check(routes({"/live/index.m3u8": MEDIA, "/live/seg1.ts": b"\x47" * 4000}, seen))
    assert r.ok and r.error is None
    assert (r.width, r.height, r.vcodec, r.bitrate_kbps) == (1280, 720, "h264", 1500)
    assert r.latency_ms is not None and r.throughput_kbps > 0
    assert seen == ["/live/index.m3u8", "/live/seg1.ts"]


def test_master_picks_lowest_bandwidth_variant():
    seen = []
    r = check(routes({"/live/index.m3u8": MASTER, "/live/lo.m3u8": MEDIA, "/live/seg1.ts": b"x" * 100}, seen))
    assert r.ok and seen == ["/live/index.m3u8", "/live/lo.m3u8", "/live/seg1.ts"]


def test_sends_stream_headers():
    ua = []

    def handler(request):
        ua.append(request.headers["user-agent"])
        return R(200, MEDIA) if request.url.path.endswith("m3u8") else R(200, b"x" * 100)
    check(handler, headers={"User-Agent": "UA9"})
    assert ua == ["UA9", "UA9"]


def test_not_a_playlist_is_dead():
    r = check(routes({"/live/index.m3u8": b"<html>no</html>"}))
    assert (r.ok, r.error) == (False, "Không phải playlist")


def test_http_error_is_dead_with_reason():
    r = check(lambda req: R(403))
    assert (r.ok, r.error) == (False, "HTTP 403")


def test_variant_404_is_dead_not_exception():
    r = check(routes({"/live/index.m3u8": MASTER}))
    assert (r.ok, r.error) == (False, "HTTP 404")


def test_empty_segment_is_dead():
    r = check(routes({"/live/index.m3u8": MEDIA, "/live/seg1.ts": b""}))
    assert (r.ok, r.error) == (False, "Không tải được đoạn")


def test_missing_segment_is_dead():
    r = check(routes({"/live/index.m3u8": MEDIA}))
    assert (r.ok, r.error) == (False, "Không tải được đoạn")


def test_no_video_stream_is_dead():
    async def audio_only(data):
        return {"streams": [{"codec_type": "audio"}], "format": {}}
    r = check(routes({"/live/index.m3u8": MEDIA, "/live/seg1.ts": b"x" * 100}), probe=audio_only)
    assert (r.ok, r.error) == (False, "Không đọc được video")


def test_probe_error_is_dead():
    async def bad(data):
        raise ProbeError("Không đọc được video")
    r = check(routes({"/live/index.m3u8": MEDIA, "/live/seg1.ts": b"x" * 100}), probe=bad)
    assert (r.ok, r.error) == (False, "Không đọc được video")


def test_direct_stream_skips_playlist_and_caps_download():
    got = {}

    async def probe(data):
        got["n"] = len(data)
        return VIDEO
    seen = []
    r = check(routes({"/live.ts": b"\x47" * (health.DIRECT_CAP * 2)}, seen), url="http://cdn.example/live.ts",
              probe=probe)
    assert r.ok and seen == ["/live.ts"] and got["n"] == health.DIRECT_CAP


def test_timeout_is_dead():
    def handler(request):
        raise httpx.ReadTimeout("slow")
    r = check(handler)
    assert (r.ok, r.error) == (False, "Hết thời gian chờ")


def test_ffprobe_missing_propagates():
    async def missing(data):
        raise FfprobeMissing()
    with pytest.raises(FfprobeMissing):
        check(routes({"/live/index.m3u8": MEDIA, "/live/seg1.ts": b"x" * 100}), probe=missing)


def script(tmp_path, body):
    p = tmp_path / "fake-ffprobe"
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def test_ffprobe_timeout_kills_process(tmp_path, monkeypatch):
    pidfile = tmp_path / "pid"
    monkeypatch.setattr(health, "FFPROBE", script(tmp_path, f"echo $$ > {pidfile}\nexec sleep 30\n"))
    monkeypatch.setattr(health, "PROBE_TIMEOUT", 0.5)
    with pytest.raises(ProbeError):
        asyncio.run(health.ffprobe(b"data"))
    pid = int(pidfile.read_text())
    for _ in range(20):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_ffprobe_garbage_output_is_probe_error_and_temp_file_removed(tmp_path, monkeypatch):
    seen = tmp_path / "arg"
    monkeypatch.setattr(health, "FFPROBE", script(tmp_path, f'for a; do last="$a"; done\necho "$last" > {seen}\necho garbage\n'))
    with pytest.raises(ProbeError):
        asyncio.run(health.ffprobe(b"data"))
    assert not os.path.exists(seen.read_text().strip())


def test_ffprobe_missing_binary(monkeypatch):
    monkeypatch.setattr(health, "FFPROBE", "/nonexistent/ffprobe")
    with pytest.raises(FfprobeMissing):
        asyncio.run(health.ffprobe(b"data"))


@pytest.mark.skipif(not (shutil.which("ffprobe") and shutil.which("ffmpeg")), reason="cần ffmpeg và ffprobe")
def test_ffprobe_real_on_generated_clip(tmp_path):
    clip = tmp_path / "c.ts"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10", "-t", "1",
                    "-c:v", "mpeg2video", "-f", "mpegts", str(clip)], check=True)
    info = asyncio.run(health.ffprobe(clip.read_bytes()))
    video = [s for s in info["streams"] if s["codec_type"] == "video"][0]
    assert video["width"] == 320
