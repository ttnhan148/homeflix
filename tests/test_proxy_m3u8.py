import httpx

GEO_PAGE = "<h1>404</h1><p>Vui lòng sử dụng mạng trong nước để truy cập trang này.</p>"
SRC = "https://cdn.test/20261030/abc/index.m3u8"


class _Upstream:
    def __init__(self, status=None, text="", exc=None):
        self.status, self.text, self.exc = status, text, exc

    async def get(self, url, **kw):
        if self.exc:
            raise self.exc
        return httpx.Response(self.status, text=self.text, request=httpx.Request("GET", url))


def _get(make_client, app_module, upstream):
    app_module.client = upstream
    return make_client().get("/proxy/m3u8", params={"url": SRC})


def test_geo_blocked_upstream_is_502_with_reason(make_client, app_module):
    r = _get(make_client, app_module, _Upstream(404, GEO_PAGE))
    assert r.status_code == 502
    assert r.headers["x-upstream-status"] == "404"
    assert r.headers["x-upstream-block"] == "geo"


def test_plain_upstream_404_is_502_without_geo_flag(make_client, app_module):
    r = _get(make_client, app_module, _Upstream(404, "Not Found"))
    assert r.status_code == 502
    assert r.headers["x-upstream-status"] == "404"
    assert "x-upstream-block" not in r.headers


def test_unreachable_upstream_is_502(make_client, app_module):
    r = _get(make_client, app_module, _Upstream(exc=httpx.ConnectError("boom")))
    assert r.status_code == 502
    assert "x-upstream-status" not in r.headers
