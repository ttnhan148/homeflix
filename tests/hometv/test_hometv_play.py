import asyncio
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from hometv import fetcher, play

MEDIA = (b"#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:4\n#EXT-X-KEY:METHOD=AES-128,URI=\"k.key\"\n"
         b"#EXT-X-MAP:URI=\"init.mp4\"\n#EXTINF:4,\nseg1.ts\n#EXTINF:4,\n/abs/seg2.ts\n")
MASTER = (b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nlow/index.m3u8\n"
          b"#EXT-X-STREAM-INF:BANDWIDTH=2000\nhttp://other/hi.m3u8\n")


def target(line: str) -> str:
    return parse_qs(urlsplit(line).query)["u"][0]


def test_is_direct_url():
    assert play.is_direct_url("http://a/b/live.TS?x=1")
    assert play.is_direct_url("http://a/v.mp4")
    assert not play.is_direct_url("http://a/index.m3u8")
    assert not play.is_direct_url("http://a/stream")


def test_rewrite_media_playlist_resolves_against_base():
    out = play.rewrite_playlist(MEDIA, "http://cdn.example/live/ch/index.m3u8", 7)
    uris = [l for l in out.splitlines() if l.startswith("/api/hometv/play/7/seg?u=")]
    assert [target(u) for u in uris] == ["http://cdn.example/live/ch/seg1.ts", "http://cdn.example/abs/seg2.ts"]
    assert 'URI="/api/hometv/play/7/seg?u=' in out
    assert target(out.split('EXT-X-KEY:METHOD=AES-128,URI="')[1].split('"')[0]) == "http://cdn.example/live/ch/k.key"
    assert target(out.split('EXT-X-MAP:URI="')[1].split('"')[0]) == "http://cdn.example/live/ch/init.mp4"


def test_rewrite_master_playlist():
    out = play.rewrite_playlist(MASTER, "http://cdn.example/live/index.m3u8", 3)
    uris = [l for l in out.splitlines() if l.startswith("/api/hometv/play/3/seg?u=")]
    assert [target(u) for u in uris] == ["http://cdn.example/live/low/index.m3u8", "http://other/hi.m3u8"]


def test_rewrite_rejects_non_m3u():
    with pytest.raises(play.PlaylistError):
        play.rewrite_playlist(b"<html>nope</html>", "http://a/b", 1)
    assert "#EXTM3U" in play.rewrite_playlist(b"\xef\xbb\xbf  #EXTM3U\n#EXTINF:4,\nx.ts\n", "http://a/b/i.m3u8", 1)


def test_upstream_headers_maps_and_filters():
    h = play.upstream_headers({"user_agent": "UA", "referrer": "https://r/", "cookie": "x"}, "bytes=0-9")
    assert h == {"User-Agent": "UA", "Referer": "https://r/", "Range": "bytes=0-9"}
    assert play.upstream_headers({}, None) == {}


def run_open(handler, url="http://example.com/a", **kw):
    async def go():
        async with fetcher.make_client(httpx.MockTransport(handler)) as client:
            resp, final = await play.open_upstream(client, url, {"User-Agent": "UA"}, **kw)
            body = await resp.aread()
            await resp.aclose()
            return body, final
    return asyncio.run(go())


def test_open_upstream_follows_redirect_and_returns_final_url():
    def handler(request):
        assert request.headers["user-agent"] == "UA"
        if request.url.path == "/a":
            return httpx.Response(302, headers={"location": "/b/c"})
        return httpx.Response(200, content=b"ok")
    assert run_open(handler) == (b"ok", "http://example.com/b/c")


def test_open_upstream_too_many_redirects():
    def handler(request):
        return httpx.Response(302, headers={"location": "/loop"})
    with pytest.raises(fetcher.FetchError):
        run_open(handler)


def test_open_upstream_http_error_is_fetch_error():
    with pytest.raises(fetcher.FetchError):
        run_open(lambda r: httpx.Response(404))
