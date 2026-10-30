import json

from tests.ui.conftest import tap
from tests.ui.test_cast_controller import EVIL, link_phone as _link_phone  # noqa: F401

STATE = {"slug": "phim-a", "title": "Phim A", "episode": "Tập 2", "position": 50, "duration": 200, "playing": True}


def link_phone(app_module, make_client, open_app):
    page, tv = _link_phone(app_module, make_client, open_app)
    tap(page, "#castSheetClose")        # tờ "Phát lên" vẫn mở sau khi nối: đóng để chạm được thanh nhỏ
    return page, tv


def push_state(page, tv, **over):
    page.es_emit("state", {**STATE, "target_id": tv.id, **over})


def wait_commands(page, tv, timeout=3000):
    for _ in range(timeout // 50):
        events = tv.events()
        if events:
            return events
        page.wait_for_timeout(50)
    raise AssertionError("TV received no command")


def open_remote(page):
    tap(page, "#castMiniBar")
    page.wait_for_selector("#castRemote:not([hidden])")


def test_mini_bar_shows_state_above_tabbar_and_opens_remote(app_module, make_client, open_app):
    page, tv = link_phone(app_module, make_client, open_app)
    assert page.locator("#castMiniBar").is_hidden()          # chưa có state
    push_state(page, tv)
    page.wait_for_selector("#castMiniBar:not([hidden])")
    assert page.locator("#castMiniTitle").inner_text() == "Phim A" and "Tập 2" in page.locator("#castMiniEp").inner_text()
    mini = page.locator("#castMiniBar").bounding_box()
    tab = page.locator("#floatTabBar .fab-capsule").bounding_box()
    assert mini["y"] + mini["height"] <= tab["y"] + 0.5
    open_remote(page)
    assert page.locator("#castRemoteTitle").inner_text() == "Phim A"
    assert page.locator("#castRemotePos").inner_text() == "0:50" and page.locator("#castRemoteDur").inner_text() == "3:20"


def test_state_for_other_target_is_ignored_and_idle_state_hides_mini(app_module, make_client, open_app):
    page, tv = link_phone(app_module, make_client, open_app)
    push_state(page, tv, target_id="someone-else")
    page.wait_for_timeout(100)
    assert page.locator("#castMiniBar").is_hidden()
    push_state(page, tv)
    page.wait_for_selector("#castMiniBar:not([hidden])")
    push_state(page, tv, slug=None, title=None, episode=None, position=None, duration=None, playing=False)
    page.wait_for_selector("#castMiniBar", state="hidden")


def test_remote_buttons_send_expected_commands(app_module, make_client, open_app):
    page, tv = link_phone(app_module, make_client, open_app)
    push_state(page, tv)
    open_remote(page)
    expected = [
        ("#castPlayPause", {"type": "pause"}),
        ("#castBack10", {"type": "seek", "delta": -10.0}),
        ("#castFwd10", {"type": "seek", "delta": 10.0}),
        ("#castPrevEp", {"type": "episode", "delta": -1}),
        ("#castNextEp", {"type": "episode", "delta": 1}),
        ("#castStop", {"type": "stop"}),
    ]
    for selector, cmd in expected:
        tap(page, selector)
        assert wait_commands(page, tv) == [("command", cmd)], selector
    push_state(page, tv, playing=False)
    open_remote(page) if page.locator("#castRemote").is_hidden() else None
    tap(page, "#castPlayPause")
    assert wait_commands(page, tv) == [("command", {"type": "resume"})]
    page.locator("#castAutoNext").check()
    assert wait_commands(page, tv) == [("command", {"type": "options", "autoNext": True})]
    page.locator("#castAutoIntro").check()
    assert wait_commands(page, tv) == [("command", {"type": "options", "autoIntro": True})]


def test_disconnect_unlinks_server_side_and_hides_ui(app_module, make_client, open_app):
    page, tv = link_phone(app_module, make_client, open_app)
    push_state(page, tv)
    open_remote(page)
    tap(page, "#castDisconnect")
    page.wait_for_function("HFCast.linked === null")
    assert page.locator("#castMiniBar").is_hidden() and page.locator("#castRemote").is_hidden()
    for _ in range(40):
        if app_module.cast_hub.linked_target(page.phone_id) is None:
            break
        page.wait_for_timeout(50)
    assert app_module.cast_hub.linked_target(page.phone_id) is None
    assert [e for e, _ in tv.events()] == ["controller_left"]
    assert page.locator("#castDot").is_hidden() and page.locator(".hero-btn-play").first.inner_text() == "Phát"


def test_silence_marks_tv_unresponsive_and_play_reverts(app_module, make_client, open_app):
    page, tv = link_phone(app_module, make_client, open_app)
    push_state(page, tv)
    page.wait_for_selector("#castMiniBar:not([hidden])")
    page.wait_for_selector("#castToast:not([hidden])", timeout=3000)       # silenceMs = 600
    assert "TV không phản hồi" in page.locator("#castToast").inner_text()
    assert page.evaluate("HFCast.linked") is None and page.locator("#castMiniBar").is_hidden()
    assert page.locator(".hero-btn-play").first.inner_text() == "Phát"


def test_fresh_state_keeps_link_alive(app_module, make_client, open_app):
    page, tv = link_phone(app_module, make_client, open_app)
    for _ in range(8):
        push_state(page, tv)
        page.wait_for_timeout(150)
    assert page.evaluate("HFCast.linked") is not None


def test_paused_tv_is_not_declared_dead_by_silence(app_module, make_client, open_app):
    page, tv = link_phone(app_module, make_client, open_app)
    push_state(page, tv, playing=False)
    page.wait_for_timeout(1000)
    assert page.evaluate("HFCast.linked") is not None


def test_command_403_after_tv_turns_off_control_reverts_to_local(app_module, make_client, open_app):
    page, tv = link_phone(app_module, make_client, open_app)
    push_state(page, tv)
    open_remote(page)
    tv.client.patch("/api/cast/settings", json={"allow_control": False})
    tv.events()
    tap(page, "#castPlayPause")
    page.wait_for_selector("#castToast:not([hidden])")
    assert page.evaluate("HFCast.linked") is None
    assert page.locator("#castRemote").is_hidden() and page.locator(".hero-btn-play").first.inner_text() == "Phát"


def test_resume_after_sleep_fetches_state_and_drops_dead_link(app_module, make_client, open_app):
    page, tv = link_phone(app_module, make_client, open_app)
    tv.client.post("/api/cast/state", json=STATE)
    page.evaluate("HFCast.emit('resume', {})")
    page.wait_for_selector("#castMiniBar:not([hidden])")
    assert page.locator("#castMiniTitle").inner_text() == "Phim A"
    app_module.cast_hub._state.pop(tv.real_id)                        # TV khởi động lại: không còn trạng thái
    page.evaluate("HFCast.emit('resume', {})")
    page.wait_for_selector("#castToast:not([hidden])")
    assert page.evaluate("HFCast.linked") is None


def test_remote_controls_meet_44pt_and_safe_area(app_module, make_client, open_app):
    page, tv = link_phone(app_module, make_client, open_app)
    push_state(page, tv)
    open_remote(page)
    for sel in ("#castPlayPause", "#castBack10", "#castFwd10", "#castPrevEp", "#castNextEp", "#castStop", "#castDisconnect", "#castRemoteClose"):
        box = page.locator(sel).bounding_box()
        assert box["width"] >= 44 - 0.5 and box["height"] >= 44 - 0.5, sel
    panel = page.locator("#castRemote").bounding_box()
    assert panel["y"] >= 59 and panel["y"] + panel["height"] <= 932 - (34 + 8 + 64) + 1


def test_state_with_html_title_renders_as_text(app_module, make_client, open_app):
    page, tv = link_phone(app_module, make_client, open_app)
    push_state(page, tv, title=EVIL, episode=EVIL)
    page.wait_for_selector("#castMiniBar:not([hidden])")
    assert EVIL in page.locator("#castMiniTitle").inner_text()
    assert page.evaluate("window.__pwn === undefined")


def test_remote_switches_follow_tv_prefs(app_module, make_client, open_app):
    page, tv = link_phone(app_module, make_client, open_app)
    push_state(page, tv, autoNext=True, autoIntro=False)
    open_remote(page)
    assert page.is_checked("#castAutoNext") and not page.is_checked("#castAutoIntro")
    push_state(page, tv, autoNext=False, autoIntro=True)
    page.wait_for_function("!document.getElementById('castAutoNext').checked && document.getElementById('castAutoIntro').checked")
