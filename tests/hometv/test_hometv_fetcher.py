import asyncio

import httpx
import pytest

from hometv import fetcher

MB = 1024 * 1024
URL = "http://example.com/a.m3u"


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
    assert fetch(lambda r: httpx.Response(200, content=b"#EXTM3U\n")) == b"#EXTM3U\n"


def test_http_error_message(public_dns):
    e = fail(lambda r: httpx.Response(404))
    assert (e.message, e.retryable) == ("HTTP 404", True)


def test_too_large(public_dns):
    e = fail(lambda r: httpx.Response(200, content=b"x" * (3 * MB)))
    assert (e.message, e.retryable) == ("Quá 2 MB", True)


def test_too_large_without_content_length(public_dns):
    # Không có Content-Length: phải dừng khi đếm vượt giới hạn
    def handler(r):
        return httpx.Response(200, stream=httpx.ByteStream(b"x" * (3 * MB)),
                              headers={"transfer-encoding": "chunked"})
    assert fail(handler).message == "Quá 2 MB"


def test_content_length_rejected_early(public_dns):
    class Boom(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise AssertionError("body không được đọc")
            yield b""
    e = fail(lambda r: httpx.Response(200, stream=Boom(), headers={"content-length": str(3 * MB)}))
    assert e.message == "Quá 2 MB"


def test_exactly_max_bytes_ok(public_dns):
    assert len(fetch(lambda r: httpx.Response(200, content=b"x" * (2 * MB)))) == 2 * MB


def test_timeout(public_dns):
    def handler(r):
        raise httpx.ReadTimeout("slow", request=r)
    e = fail(handler)
    assert (e.message, e.retryable) == ("Hết thời gian chờ", True)


def test_total_timeout(public_dns):
    async def handler(r):
        await asyncio.sleep(5)
        return httpx.Response(200, content=b"x")
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
        return httpx.Response(302, headers={"location": "http://internal.example/x"})
    e = fail(handler)
    assert (e.message, e.retryable) == ("Địa chỉ bị chặn", False)
    assert calls == [URL]


def test_redirect_to_ip_literal_blocked(public_dns):
    e = fail(lambda r: httpx.Response(307, headers={"location": "http://169.254.169.254/"}))
    assert (e.message, e.retryable) == ("Địa chỉ bị chặn", False)


def test_redirect_bad_url_not_retryable(public_dns):
    e = fail(lambda r: httpx.Response(302, headers={"location": "ftp://example.com/x"}))
    assert (e.message, e.retryable) == ("URL không hợp lệ", False)


def test_relative_redirect(public_dns):
    def handler(r):
        if r.url.path == "/a.m3u":
            return httpx.Response(302, headers={"location": "/b.m3u"})
        assert r.url.path == "/b.m3u" and r.url.host == "example.com"
        return httpx.Response(200, content=b"ok")
    assert fetch(handler) == b"ok"


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_redirect_codes(public_dns, code):
    def handler(r):
        if r.url.path == "/a.m3u":
            return httpx.Response(code, headers={"location": "/b.m3u"})
        return httpx.Response(200, content=b"ok")
    assert fetch(handler) == b"ok"


def test_five_redirects_ok(public_dns):
    def handler(r):
        n = int(r.url.params.get("n", 0))
        if n < 5:
            return httpx.Response(302, headers={"location": f"/a.m3u?n={n + 1}"})
        return httpx.Response(200, content=b"ok")
    assert fetch(handler) == b"ok"


def test_too_many_redirects(public_dns):
    n = []

    def handler(r):
        n.append(1)
        return httpx.Response(302, headers={"location": "/loop"})
    e = fail(handler)
    assert (e.message, e.retryable) == ("Quá nhiều lần chuyển hướng", True)
    assert len(n) == 6


@pytest.mark.parametrize("headers", [{}, {"location": ""}])
def test_redirect_without_location(public_dns, headers):
    assert fail(lambda r: httpx.Response(302, headers=headers)).message == "HTTP 302"


def test_blocked_initial_url(monkeypatch):
    import hometv.netguard
    monkeypatch.setattr(hometv.netguard, "resolve", lambda h: ["127.0.0.1"])
    calls = []
    e = fail(lambda r: calls.append(r) or httpx.Response(200, content=b"x"))
    assert (e.message, e.retryable) == ("Địa chỉ bị chặn", False)
    assert calls == []


def test_invalid_initial_url_no_request(public_dns):
    calls = []
    e = fail(lambda r: calls.append(r) or httpx.Response(200), url="file:///etc/passwd")
    assert (e.message, e.retryable) == ("URL không hợp lệ", False)
    assert calls == []


def test_client_ignores_proxy_env(monkeypatch, public_dns):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.invalid:3128")
    seen = []

    def handler(r):
        seen.append(str(r.url))
        return httpx.Response(200, content=b"ok")
    client = fetcher.make_client(httpx.MockTransport(handler))
    assert client._mounts == {}
    assert not client.follow_redirects
    assert client.headers["user-agent"] == "HomeTV/1.0"
    assert fetch(handler, url="https://example.com/a.m3u") == b"ok"
    assert seen == ["https://example.com/a.m3u"]
