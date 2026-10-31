import asyncio

import httpx
import pytest

from hometv import geo


@pytest.fixture(autouse=True)
def fresh_geo():
    geo.reset()
    yield
    geo.reset()


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def run(coro):
    return asyncio.run(coro)


def answers(ipinfo=None, country_is=None):
    """ipinfo/country_is: chuỗi (200) hoặc None (503)."""
    def handler(request):
        if request.url.host == "ipinfo.io":
            return httpx.Response(200, text=ipinfo) if ipinfo is not None else httpx.Response(503)
        return httpx.Response(200, json={"country": country_is}) if country_is is not None else httpx.Response(503)
    return handler


def detect(handler):
    async def go():
        async with client(handler) as c:
            return await geo.detect_country(c)
    return run(go())


def refresh(handler, mode, now):
    async def go():
        async with client(handler) as c:
            return await geo.refresh_mode(c, mode, now)
    return run(go())


def test_detect_reads_ipinfo_plain_text():
    assert detect(answers(ipinfo="vn\n")) == "VN"


def test_detect_falls_back_to_country_is():
    assert detect(answers(ipinfo=None, country_is="VN")) == "VN"


def test_detect_none_when_both_fail():
    assert detect(answers()) is None


def test_detect_rejects_garbage():
    assert detect(answers(ipinfo="<html>blocked</html>", country_is=None)) is None


def test_parse_mode():
    assert [geo.parse_mode(v) for v in ("auto", "ON", "off", "bậy", "", None)] == \
        ["auto", "on", "off", "auto", "auto", "auto"]


def test_auto_enabled_only_for_vn():
    assert refresh(answers(ipinfo="VN"), "auto", 100.0).enabled is True
    assert refresh(answers(ipinfo="MY"), "auto", 200.0).enabled is False
    geo.reset()
    m = refresh(answers(), "auto", 300.0)
    assert m.enabled is False and m.country is None


def test_on_off_override_detection():
    m = refresh(answers(ipinfo="MY"), "on", 100.0)
    assert m.enabled is True and m.country == "MY"
    m = refresh(answers(ipinfo="VN"), "off", 200.0)
    assert m.enabled is False and m.country == "VN"


def test_failed_detect_keeps_previous_until_stale():
    refresh(answers(ipinfo="VN"), "auto", 100.0)
    refresh(answers(), "auto", 5000.0)
    assert geo.current(5001.0).enabled is True
    assert geo.current(100.0 + geo.STALE + 1).enabled is False


def test_current_before_first_detect_is_disabled():
    m = geo.current(1000.0)
    assert (m.mode, m.enabled, m.country) == ("auto", False, None)
