import asyncio
import gzip
import os
import tracemalloc
import zlib

import httpx
import pytest

from hometv import fetcher

MB = 1024 * 1024
URL = "http://example.com/a.m3u"


def _resp(status, content=None, headers=None, stream=None):
    # Dựng response dạng stream như transport thật (content= thì httpx đã đọc sẵn, aiter_raw lỗi)
    h = dict(headers or {})
    if content is not None:
        h.setdefault("content-length", str(len(content)))
        stream = httpx.ByteStream(content)
    return httpx.Response(status, headers=h, stream=stream)


def fetch(handler, url=URL, timeout=5.0, max_bytes=2 * MB, **kw):
    async def run():
        async with fetcher.make_client(httpx.MockTransport(handler)) as client:
            return await fetcher.fetch_playlist(
                client, url, timeout=timeout, max_bytes=max_bytes, **kw)
    return asyncio.run(run())


def fail(handler, **kw):
    with pytest.raises(fetcher.FetchError) as e:
        fetch(handler, **kw)
    return e.value


def test_ok_body(public_dns):
    assert fetch(lambda r: _resp(200, content=b"#EXTM3U\n")) == b"#EXTM3U\n"


def test_http_error_message(public_dns):
    e = fail(lambda r: _resp(404))
    assert (e.message, e.retryable) == ("HTTP 404", True)


def test_too_large(public_dns):
    e = fail(lambda r: _resp(200, content=b"x" * (3 * MB)))
    assert (e.message, e.retryable) == ("Quá 2 MB", True)


def test_too_large_without_content_length(public_dns):
    # Không có Content-Length: phải dừng khi đếm vượt giới hạn
    def handler(r):
        return _resp(200, stream=httpx.ByteStream(b"x" * (3 * MB)),
                              headers={"transfer-encoding": "chunked"})
    assert fail(handler).message == "Quá 2 MB"


def test_content_length_rejected_early(public_dns):
    class Boom(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise AssertionError("body không được đọc")
            yield b""
    e = fail(lambda r: _resp(200, stream=Boom(), headers={"content-length": str(3 * MB)}))
    assert e.message == "Quá 2 MB"


def test_exactly_max_bytes_ok(public_dns):
    assert len(fetch(lambda r: _resp(200, content=b"x" * (2 * MB)))) == 2 * MB


def test_timeout(public_dns):
    def handler(r):
        raise httpx.ReadTimeout("slow", request=r)
    e = fail(handler)
    assert (e.message, e.retryable) == ("Hết thời gian chờ", True)


def test_total_timeout(public_dns):
    async def handler(r):
        await asyncio.sleep(5)
        return _resp(200, content=b"x")
    assert fail(handler, timeout=0.05).message == "Hết thời gian chờ"


def test_connect_error(public_dns):
    def handler(r):
        raise httpx.ConnectError("no", request=r)
    e = fail(handler)
    assert (e.message, e.retryable) == ("Không kết nối được", True)


def test_other_transport_error(public_dns):
    def handler(r):
        raise httpx.RemoteProtocolError("bad", request=r)
    assert fail(handler).message == "Không kết nối được"


def test_redirect_followed_and_checked(monkeypatch):
    import hometv.netguard
    monkeypatch.setattr(hometv.netguard, "resolve",
                        lambda h: ["10.0.0.9"] if h == "internal.example" else ["185.199.108.153"])
    calls = []

    def handler(r):
        calls.append(str(r.url))
        return _resp(302, headers={"location": "http://internal.example/x"})
    e = fail(handler)
    assert (e.message, e.retryable) == ("Địa chỉ bị chặn", False)
    assert calls == [URL]


def test_redirect_to_ip_literal_blocked(public_dns):
    e = fail(lambda r: _resp(307, headers={"location": "http://169.254.169.254/"}))
    assert (e.message, e.retryable) == ("Địa chỉ bị chặn", False)


def test_redirect_bad_url_not_retryable(public_dns):
    e = fail(lambda r: _resp(302, headers={"location": "ftp://example.com/x"}))
    assert (e.message, e.retryable) == ("URL không hợp lệ", False)


def test_relative_redirect(public_dns):
    def handler(r):
        if r.url.path == "/a.m3u":
            return _resp(302, headers={"location": "/b.m3u"})
        assert r.url.path == "/b.m3u" and r.url.host == "example.com"
        return _resp(200, content=b"ok")
    assert fetch(handler) == b"ok"


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_redirect_codes(public_dns, code):
    def handler(r):
        if r.url.path == "/a.m3u":
            return _resp(code, headers={"location": "/b.m3u"})
        return _resp(200, content=b"ok")
    assert fetch(handler) == b"ok"


def test_five_redirects_ok(public_dns):
    def handler(r):
        n = int(r.url.params.get("n", 0))
        if n < 5:
            return _resp(302, headers={"location": f"/a.m3u?n={n + 1}"})
        return _resp(200, content=b"ok")
    assert fetch(handler) == b"ok"


def test_too_many_redirects(public_dns):
    n = []

    def handler(r):
        n.append(1)
        return _resp(302, headers={"location": "/loop"})
    e = fail(handler)
    assert (e.message, e.retryable) == ("Quá nhiều lần chuyển hướng", True)
    assert len(n) == 6


@pytest.mark.parametrize("headers", [{}, {"location": ""}])
def test_redirect_without_location(public_dns, headers):
    assert fail(lambda r: _resp(302, headers=headers)).message == "HTTP 302"


def test_blocked_initial_url(monkeypatch):
    import hometv.netguard
    monkeypatch.setattr(hometv.netguard, "resolve", lambda h: ["127.0.0.1"])
    calls = []
    e = fail(lambda r: calls.append(r) or _resp(200, content=b"x"))
    assert (e.message, e.retryable) == ("Địa chỉ bị chặn", False)
    assert calls == []


def test_invalid_initial_url_no_request(public_dns):
    calls = []
    e = fail(lambda r: calls.append(r) or _resp(200), url="file:///etc/passwd")
    assert (e.message, e.retryable) == ("URL không hợp lệ", False)
    assert calls == []


def test_client_ignores_proxy_env(monkeypatch):
    # Không truyền transport: nếu trust_env=True thì httpx dựng mount proxy từ biến môi trường
    for k in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(k, "http://proxy.invalid:3128")
    client = fetcher.make_client()
    assert client._mounts == {}
    assert not client.follow_redirects
    assert client.headers["user-agent"] == "HomeTV/1.0"


def test_sends_identity_encoding(public_dns):
    seen = []

    def handler(r):
        seen.append(r.headers["accept-encoding"])
        return _resp(200, content=b"ok")
    fetch(handler)
    assert seen == ["identity"]


def _gz(data: bytes) -> bytes:
    return gzip.compress(data, 9)


def test_gzip_playlist_decoded(public_dns):
    body = b"#EXTM3U\n" * 100
    assert fetch(lambda r: _resp(200, content=_gz(body),
                                          headers={"content-encoding": "GZip"})) == body


def test_deflate_playlist_decoded(public_dns):
    body = b"#EXTM3U\n" * 100
    assert fetch(lambda r: _resp(200, content=zlib.compress(body),
                                          headers={"content-encoding": "deflate"})) == body
    raw = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    rawbody = raw.compress(body) + raw.flush()
    assert fetch(lambda r: _resp(200, content=rawbody,
                                          headers={"content-encoding": "deflate"})) == body


def test_gzip_bomb_memory_bounded(public_dns):
    # 50 MB giải nén từ ~50 KB: bộ nhớ đỉnh phải ~ max_bytes, không phải 50 MB
    bomb = _gz(b"\0" * (50 * MB))
    assert len(bomb) < 100_000
    tracemalloc.start()
    try:
        e = fail(lambda r: _resp(200, content=bomb, headers={"content-encoding": "gzip"}),
                 max_bytes=1 * MB)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert e.message == "Quá 1 MB" and e.retryable
    assert peak < 8 * MB, peak


def test_trailing_garbage_counts_raw_bytes(public_dns):
    # sau luồng gzip hợp lệ là rác: phải đếm byte thô để dừng
    body = _gz(b"#EXTM3U\n") + os.urandom(2 * MB)
    e = fail(lambda r: _resp(200, stream=httpx.ByteStream(body),
                             headers={"content-encoding": "gzip"}), max_bytes=1 * MB)
    assert e.message == "Quá 1 MB"


def test_truncated_gzip_is_broken(public_dns):
    gz = _gz(b"#EXTM3U\n" * 1000)[:-4]
    e = fail(lambda r: _resp(200, content=gz, headers={"content-encoding": "gzip"}))
    assert (e.message, e.retryable) == ("Playlist hỏng", True)


def test_dns_failure_is_retryable(monkeypatch):
    import socket
    import hometv.netguard

    def boom(host):
        raise socket.gaierror(-3, "Temporary failure")
    monkeypatch.setattr(hometv.netguard, "resolve", boom)
    e = fail(lambda r: _resp(200, content=b"x"))
    assert (e.message, e.retryable) == ("Không kết nối được", True)


def test_stacked_encoding_rejected(public_dns):
    bomb = _gz(_gz(b"\0" * (50 * MB)))
    e = fail(lambda r: _resp(200, content=bomb,
                                      headers={"content-encoding": "gzip, gzip"}))
    assert (e.message, e.retryable) == ("Playlist hỏng", True)


def test_unknown_encoding_rejected(public_dns):
    e = fail(lambda r: _resp(200, content=b"x", headers={"content-encoding": "br"}))
    assert e.message == "Playlist hỏng"


def test_identity_encoding_passthrough(public_dns):
    assert fetch(lambda r: _resp(200, content=b"ok",
                                          headers={"content-encoding": "identity"})) == b"ok"


def test_bad_gzip_body(public_dns):
    e = fail(lambda r: _resp(200, content=b"not gzip at all",
                                      headers={"content-encoding": "gzip"}))
    assert (e.message, e.retryable) == ("Playlist hỏng", True)


@pytest.mark.parametrize("loc", ["http:x", "http://xn--/"])
def test_malformed_location_is_fetch_error(public_dns, loc):
    e = fail(lambda r: _resp(302, headers={"location": loc}))
    assert (e.message, e.retryable) == ("URL không hợp lệ", False)


def test_per_call_timeout_passed_to_request(public_dns):
    seen = []

    def handler(r):
        seen.append(r.extensions["timeout"])
        return _resp(200, content=b"ok")
    fetch(handler, timeout=60)
    assert seen[0] == {"connect": 60, "read": 60, "write": 60, "pool": 60}


def test_caller_client_with_redirects_cannot_bypass(monkeypatch):
    import hometv.netguard
    monkeypatch.setattr(hometv.netguard, "resolve",
                        lambda h: ["10.0.0.1"] if h == "internal.example" else ["185.199.108.153"])
    urls = []

    def handler(r):
        urls.append(str(r.url))
        return _resp(302, headers={"location": "http://internal.example/x"})

    async def run():
        async with httpx.AsyncClient(follow_redirects=True,
                                     transport=httpx.MockTransport(handler)) as c:
            return await fetcher.fetch_playlist(c, URL, timeout=5, max_bytes=MB)
    with pytest.raises(fetcher.FetchError) as e:
        asyncio.run(run())
    assert e.value.message == "Địa chỉ bị chặn"
    assert urls == [URL]
