import os
from urllib.parse import quote

import httpx

import hometv
from tests.hometv.test_hometv_catalog import seed_channel, seed_stream
from tests.hometv.test_hometv_play import MEDIA

CHAN = "http://cdn.example/live/ch/index.m3u8"


def seed(url=CHAN, headers=None):
    c = hometv.db.conn()
    c.execute("UPDATE sources SET rights_status='approved' WHERE id='doubo-vn'")
    seed_channel(c, 1, "VTV1")
    seed_stream(c, 1, 1, "doubo-vn", url, headers=headers)
    seed_stream(c, 2, 1, "iptv-org-vn", "http://pending/x.m3u8")


def upstream(app_module, handler):
    app_module.hometv_play_client = hometv.fetcher.make_client(httpx.MockTransport(handler))


def streamed(status, content, headers=None):
    # Như transport thật: body chưa đọc, aiter_raw dùng được
    return httpx.Response(status, headers=headers, stream=httpx.ByteStream(content))


def seg(stream_id, url):
    return f"/api/hometv/play/{stream_id}/seg?u={quote(url, safe='')}"


def test_index_rewrites_and_sends_stream_headers(app_module, make_client):
    seed(headers={"user_agent": "UA1", "referrer": "https://ref/"})
    seen = {}

    def handler(request):
        seen.update(ua=request.headers["user-agent"], ref=request.headers.get("referer"))
        return httpx.Response(200, content=MEDIA)
    upstream(app_module, handler)
    r = make_client().get("/api/hometv/play/1/index.m3u8")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.apple.mpegurl")
    assert seg(1, "http://cdn.example/live/ch/seg1.ts") in r.text
    assert seen == {"ua": "UA1", "ref": "https://ref/"}


def test_index_resolves_relative_uris_against_final_url(app_module, make_client):
    seed()

    def handler(request):
        if request.url.host == "cdn.example":
            return httpx.Response(302, headers={"location": "http://edge.example/other/p.m3u8"})
        return httpx.Response(200, content=MEDIA)
    upstream(app_module, handler)
    r = make_client().get("/api/hometv/play/1/index.m3u8")
    assert seg(1, "http://edge.example/other/seg1.ts") in r.text


def test_unknown_or_unapproved_stream_404(app_module, make_client):
    seed()
    c = make_client()
    assert c.get("/api/hometv/play/99/index.m3u8").status_code == 404
    assert c.get("/api/hometv/play/2/index.m3u8").status_code == 404
    assert c.get(seg(2, "http://pending/x.ts")).status_code == 404


def test_index_502_on_non_m3u_and_on_upstream_error(app_module, make_client):
    seed()
    c = make_client()
    upstream(app_module, lambda r: httpx.Response(200, content=b"<html></html>"))
    assert c.get("/api/hometv/play/1/index.m3u8").status_code == 502
    upstream(app_module, lambda r: httpx.Response(404))
    assert c.get("/api/hometv/play/1/index.m3u8").status_code == 502


def test_seg_streams_bytes_and_writes_nothing_to_cache(app_module, make_client):
    seed()
    upstream(app_module, lambda r: streamed(200, b"\x47" * 5000, {"content-type": "video/MP2T"}))
    r = make_client().get(seg(1, "http://cdn.example/live/ch/seg1.ts"))
    assert r.status_code == 200 and r.content == b"\x47" * 5000
    assert os.listdir(app_module.CACHE_DIR) == []


def test_seg_playlist_url_is_rewritten_too(app_module, make_client):
    seed()
    upstream(app_module, lambda r: httpx.Response(200, content=MEDIA))
    r = make_client().get(seg(1, "http://cdn.example/live/low/index.m3u8"))
    assert seg(1, "http://cdn.example/live/low/seg1.ts") in r.text


def test_seg_upstream_error_502(app_module, make_client):
    seed()
    upstream(app_module, lambda r: httpx.Response(500))
    assert make_client().get(seg(1, "http://cdn.example/a.ts")).status_code == 502


def test_direct_forwards_range_and_206(app_module, make_client):
    seed("http://cdn.example/live.mp4")
    seen = {}

    def handler(request):
        seen["range"] = request.headers.get("range")
        return streamed(206, b"abc", {
            "content-range": "bytes 0-2/10", "accept-ranges": "bytes", "content-type": "video/mp4"})
    upstream(app_module, handler)
    r = make_client().get(seg(1, "http://cdn.example/live.mp4"), headers={"range": "bytes=0-2"})
    assert r.status_code == 206 and r.content == b"abc"
    assert r.headers["content-range"] == "bytes 0-2/10" and seen["range"] == "bytes=0-2"
