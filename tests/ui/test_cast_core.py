import json

URL = "http://homeflix.test/"
FAST = "window.__HF_CAST_CONFIG = {heartbeatMs: 100, reconnectBaseMs: 20, reconnectMaxMs: 80, silenceMs: 400, stateMs: 50};"


def _open(open_app, tv=False, fast=True):
    page = open_app(tv=tv)
    if fast:
        page.add_init_script(FAST)
    bodies = []
    page.on("request", lambda r: bodies.append(r.post_data) if "/api/devices/heartbeat" in r.url else None)
    page.goto(URL)
    page.wait_for_function("window.HFCast && HFCast.started === true")
    return page, bodies


def test_heartbeat_posts_kind_repeatedly(open_app):
    phone, phone_bodies = _open(open_app)
    phone.wait_for_timeout(450)
    assert len(phone_bodies) >= 2 and all(json.loads(b) == {"kind": "phone"} for b in phone_bodies)
    tv, tv_bodies = _open(open_app, tv=True)
    tv.wait_for_timeout(450)
    assert len(tv_bodies) >= 2 and all(json.loads(b) == {"kind": "tv"} for b in tv_bodies)


def test_tv_opens_events_immediately_iphone_does_not(open_app):
    tv, _ = _open(open_app, tv=True)
    assert tv.es_urls() == ["/api/cast/events"]
    phone, _ = _open(open_app)
    assert phone.es_urls() == []


def test_events_reconnect_with_backoff_and_reset_on_hello(open_app):
    page, _ = _open(open_app, tv=True)
    for expected_delay, expected_count in ((20, 2), (40, 3), (80, 4)):
        before = page.evaluate("performance.now()")
        page.es_error()
        page.wait_for_function(f"__es.length === {expected_count}")
        gap = page.evaluate("__es[__es.length - 1].t") - before
        assert expected_delay - 2 <= gap < expected_delay + 250, (expected_delay, gap)
    assert page.evaluate("HFCast.attempt") == 3
    page.es_emit("hello", {"device_id": "x", "controllers": []})
    assert page.evaluate("HFCast.attempt") == 0
    page.es_error()
    page.wait_for_function("__es.length === 5")
    assert page.es_open_count() == 1


def test_becoming_visible_reopens_events_and_fires_resume(open_app):
    page, _ = _open(open_app, tv=True)
    page.evaluate("window.__resumed = 0; HFCast.on('resume', () => window.__resumed++)")
    page.evaluate("""() => { Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'visible'});
        document.dispatchEvent(new Event('visibilitychange')); }""")
    assert page.es_open_count() == 1 and len(page.es_urls()) == 2
    assert page.evaluate("window.__resumed") == 1


def test_phone_without_events_does_not_reopen_when_visible(open_app):
    page, _ = _open(open_app)
    page.evaluate("""() => { Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'visible'});
        document.dispatchEvent(new Event('visibilitychange')); }""")
    assert page.es_urls() == []


def test_unknown_event_names_are_ignored_without_errors(open_app):
    page, _ = _open(open_app, tv=True)
    page.es_emit("weird", {"a": 1})
    page.es_emit("hello", {"device_id": "x", "controllers": []})
    assert page.console_errors == [] and page.es_open_count() == 1
