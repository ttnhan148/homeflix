import json

from tests.ui.conftest import tap
from tests.ui.test_cast_controller import link_phone, wait_posts
from tests.ui.test_cast_receiver import open_tv, posts
from tests.ui.test_cast_remote import STATE, push_state

CONNECT_FOR = lambda tv: f"/api/cast/{tv.id}/connect"


def wait_seen(page, method, path, timeout=3000):
    for _ in range(timeout // 50):
        if any(m == method and u == path for m, u, _ in page.seen):
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"no {method} {path}: {[(m, u) for m, u, _ in page.seen][-8:]}")


def test_resume_does_not_drop_live_session_while_state_refresh_is_in_flight(app_module, make_client, open_app):
    page, tv = link_phone(app_module, make_client, open_app)
    tap(page, "#castSheetClose")
    push_state(page, tv)
    page.wait_for_selector("#castMiniBar:not([hidden])")
    tv.client.post("/api/cast/state", json=STATE)
    # mô phỏng iPhone ngủ 10 giây (đồng hồ nhảy), trong khi lần lấy trạng thái sau khi thức còn đang bay
    page.evaluate("""() => { const real = Date.now.bind(Date); window.__skew = 0; Date.now = () => real() + window.__skew;
        const api = HFCast.api; HFCast.api = (m, p, b) => p.startsWith('/api/cast/state/')
            ? new Promise(r => setTimeout(() => api(m, p, b).then(r), 800)) : api(m, p, b); }""")
    page.evaluate("window.__skew = 10000; HFCast.emit('resume', {})")
    page.wait_for_timeout(1200)
    assert page.evaluate("HFCast.linked") is not None
    assert page.locator("#castToast").is_hidden()


def test_hello_while_linked_restores_server_link(app_module, make_client, open_app):
    page, tv = link_phone(app_module, make_client, open_app)
    page.seen.clear()
    page.es_emit("hello", {"device_id": "x", "controllers": [], "pending": []})
    wait_seen(page, "POST", CONNECT_FOR(tv))
    assert page.evaluate("HFCast.linked") is not None


def test_resume_restores_server_link(app_module, make_client, open_app):
    page, tv = link_phone(app_module, make_client, open_app)
    page.seen.clear()
    page.evaluate("HFCast.emit('resume', {})")
    wait_seen(page, "POST", CONNECT_FOR(tv))


def test_failed_restore_drops_the_link(app_module, make_client, open_app):
    page, tv = link_phone(app_module, make_client, open_app)
    tv.client.patch("/api/cast/settings", json={"allow_control": False})
    page.es_emit("hello", {"device_id": "x", "controllers": [], "pending": []})
    page.wait_for_selector("#castToast:not([hidden])")
    assert page.evaluate("HFCast.linked") is None


def test_unresponsive_tv_sends_disconnect_so_tv_drops_its_label(app_module, make_client, open_app):
    page, tv = link_phone(app_module, make_client, open_app)
    tap(page, "#castSheetClose")
    app_module.cast_hub.unsubscribe(tv.sub)
    tap(page, ".hero-btn-play")
    page.wait_for_selector("#castToast:not([hidden])")
    wait_seen(page, "DELETE", "/api/cast/link")


def test_unlinked_event_from_tv_clears_phone_ui(app_module, make_client, open_app):
    page, tv = link_phone(app_module, make_client, open_app)
    tap(page, "#castSheetClose")
    push_state(page, tv)
    page.wait_for_selector("#castMiniBar:not([hidden])")
    page.es_emit("unlinked", {"target_id": "someone-else"})
    page.wait_for_timeout(100)
    assert page.evaluate("HFCast.linked") is not None                    # của TV khác: bỏ qua
    page.es_emit("unlinked", {"target_id": tv.id})
    page.wait_for_selector("#castToast:not([hidden])")
    assert "tắt điều khiển" in page.locator("#castToast").inner_text()
    assert page.evaluate("HFCast.linked") is None and page.locator("#castMiniBar").is_hidden()
    assert page.locator(".hero-btn-play").first.inner_text() == "Phát"


def test_hello_pending_requests_show_dialog_once(open_app):
    page = open_tv(open_app)
    page.es_emit("hello", {"device_id": "tv", "controllers": [], "pending": [{"controller_id": "p1", "controller_name": "iPhone"}]})
    page.wait_for_selector("#castConfirm:not([hidden])")
    page.es_emit("connect_request", {"controller_id": "p1", "controller_name": "iPhone"})   # trùng: không xếp hàng lần hai
    tap(page, "#castAllow")
    assert posts(page, "/api/cast/requests/p1") == [{"allow": True}]
    assert page.locator("#castConfirm").is_hidden()


def test_play_still_works_if_cast_script_did_not_load(open_app):
    page = open_app()
    page.goto("http://homeflix.test/")
    page.wait_for_function("document.querySelectorAll('.hero-slide').length > 0 && Object.keys(heroState.details).length > 0")
    page.evaluate("delete window.__castRoute")
    page.evaluate("document.querySelector('.hero-btn-play').click()")
    assert page.evaluate("document.body.classList.contains('player-active')")
