import json

from tests.ui.conftest import tap
from tests.ui.test_library import api

URL = "http://homeflix.test/"
HOST = "http://homeflix.test"
FAST = "window.__HF_CAST_CONFIG = {heartbeatMs: 100000, reconnectBaseMs: 20, reconnectMaxMs: 80, silenceMs: 600, stateMs: 50};"
EVIL = '<img src=x onerror="window.__pwn=1">'


class Tv:
    """TV cùng nhà với trang iPhone của Playwright (cầu nối dùng client ('testclient', 50000))."""

    def __init__(self, m, make_client, name=None):
        self.m = m
        self.client = make_client(ip="testclient")
        self.real_id = self.client.get("/api/device").json()["id"]
        self.id = m._cast_public_id(self.real_id)        # id công khai: cái iPhone nhìn thấy và dùng trong route
        self.client.post("/api/devices/heartbeat", json={"kind": "tv"})
        if name:
            self.client.patch("/api/device", json={"name": name})
        self.sub = m.cast_hub.subscribe(self.real_id)

    def events(self):
        return self.sub.drain()

    def answer(self, phone_id, allow=True):
        return self.client.post(f"/api/cast/requests/{self.m._cast_public_id(phone_id)}", json={"allow": allow})


def open_phone(open_app, init=None, **kw):
    page = open_app(safe=(59, 0, 34, 0), **kw)
    page.add_init_script(FAST)
    if init:
        page.add_init_script(init)
    page.seen = []
    page.on("request", lambda r: page.seen.append((r.method, r.url.replace(HOST, ""), r.post_data)))
    page.goto(URL)
    page.wait_for_function("window.HFCast && HFCast.started === true")
    page.wait_for_selector(".hero-slide")
    page.phone_id = page.evaluate("fetch('/api/device').then(r => r.json()).then(d => d.id)")
    return page


def approve(page, tv):
    """TV cho phép iPhone này (qua server) và dọn sự kiện."""
    api(page, "POST", f"/api/cast/{tv.id}/connect")
    tv.answer(page.phone_id, True)
    page.evaluate("HFCast.linked = null")
    tv.events()


def open_sheet(page):
    tap(page, "#castBtn")
    page.wait_for_selector("#castSheet:not([hidden])")
    page.wait_for_function("document.querySelectorAll('#castDeviceList .cast-device').length > 0 || !document.getElementById('castEmpty').hidden")


def wait_posts(page, path_suffix, timeout=3000):
    for _ in range(timeout // 50):
        hits = [(m, u, b) for m, u, b in page.seen if m == "POST" and u.endswith(path_suffix)]
        if hits:
            return hits
        page.wait_for_timeout(50)
    raise AssertionError(f"no POST to *{path_suffix}: {page.seen}")


def test_cast_button_visible_on_iphone_not_on_tv(open_app):
    page = open_phone(open_app)
    box = page.locator("#castBtn").bounding_box()
    assert box["y"] >= 59 and box["width"] >= 44 and box["height"] >= 44
    tv = open_app(tv=True)
    tv.goto(URL)
    assert tv.locator("#castBtn").is_hidden()


def test_sheet_lists_nearby_tv_and_hides_other_households(app_module, make_client, open_app):
    mine = Tv(app_module, make_client, name="Phòng khách")
    stranger = make_client(ip="198.51.100.9")
    stranger.get("/api/device"); stranger.post("/api/devices/heartbeat", json={"kind": "tv"}); stranger.patch("/api/device", json={"name": "TV nhà khác"})
    page = open_phone(open_app)
    open_sheet(page)
    rows = page.locator("#castDeviceList .cast-device")
    assert rows.count() == 1 and "Phòng khách" in rows.first.inner_text() and "Sẵn sàng" in rows.first.inner_text()
    assert "TV nhà khác" not in page.locator("#castSheet").inner_text()
    assert page.locator("#castEmpty").is_hidden()


def test_sheet_empty_state_message(open_app):
    page = open_phone(open_app)
    open_sheet(page)
    text = page.locator("#castEmpty").inner_text()
    assert "Không thấy TV nào cùng mạng" in text and "HomeFlix" in text


def test_first_connect_shows_waiting_then_connected_after_tv_allows(app_module, make_client, open_app):
    tv = Tv(app_module, make_client)
    page = open_phone(open_app)
    open_sheet(page)
    tap(page, "#castDeviceList .cast-device")
    page.wait_for_function("document.querySelector('#castDeviceList .cast-device').textContent.includes('Chờ TV cho phép')")
    assert [e for e, _ in tv.events()] == ["connect_request"]
    tv.answer(page.phone_id, True)
    page.es_emit("connect_result", {"target_id": tv.id, "allowed": True})
    page.wait_for_function("HFCast.linked && HFCast.linked.id === " + json.dumps(tv.id))
    assert page.locator("#castDot").is_visible()
    assert "Đã kết nối" in page.locator("#castDeviceList .cast-device").first.inner_text()


def test_denied_result_shows_message_and_stays_unlinked(app_module, make_client, open_app):
    tv = Tv(app_module, make_client)
    page = open_phone(open_app)
    open_sheet(page)
    tap(page, "#castDeviceList .cast-device")
    page.wait_for_function("document.querySelector('#castDeviceList .cast-device').textContent.includes('Chờ TV')")
    page.es_emit("connect_result", {"target_id": tv.id, "allowed": False})
    page.wait_for_selector("#castToast:not([hidden])")
    assert "từ chối" in page.locator("#castToast").inner_text().lower()
    assert page.evaluate("HFCast.linked") is None
    assert page.locator("#castDot").is_hidden()


def link_phone(app_module, make_client, open_app, **kw):
    tv = Tv(app_module, make_client, **kw)
    page = open_phone(open_app)
    approve(page, tv)
    open_sheet(page)
    tap(page, "#castDeviceList .cast-device")
    page.wait_for_function("HFCast.linked !== null")
    tv.events()
    return page, tv


def test_play_buttons_say_phat_len_tv_when_linked_and_send_play_command(app_module, make_client, open_app):
    page, tv = link_phone(app_module, make_client, open_app)
    tap(page, "#castSheetClose")
    assert page.locator(".hero-btn-play").first.inner_text() == "Phát lên TV"
    tap(page, ".hero-btn-play")
    events = []
    for _ in range(40):
        events = tv.events()
        if events:
            break
        page.wait_for_timeout(50)
    assert events == [("command", {"type": "play", "slug": "phim-chieu-rap-1", "episode_url": None, "position": None})]
    assert not page.evaluate("document.body.classList.contains('player-active')")
    # trang chi tiết và chạm một tập
    page.evaluate("showMovieDetailBySlug('phim-le-1')")
    page.wait_for_selector("#detailEpisodeList .ep-item")
    assert "Phát lên TV" in page.locator(".detail-action-play").inner_text()
    tap(page, "#detailEpisodeList .ep-item >> nth=1")
    for _ in range(40):
        events = tv.events()
        if events:
            break
        page.wait_for_timeout(50)
    assert events[0][1]["episode_url"] == "http://media.test/phim-le-1/2.m3u8" and events[0][1]["slug"] == "phim-le-1"
    tap(page, ".detail-action-play")
    for _ in range(40):
        events = tv.events()
        if events:
            break
        page.wait_for_timeout(50)
    assert events[0][1] == {"type": "play", "slug": "phim-le-1", "episode_url": None, "position": None}
    assert not page.evaluate("document.body.classList.contains('player-active')")


def test_unreachable_tv_reverts_play_to_local_with_message(app_module, make_client, open_app):
    page, tv = link_phone(app_module, make_client, open_app)
    tap(page, "#castSheetClose")
    app_module.cast_hub.unsubscribe(tv.sub)                    # TV mất kết nối SSE
    tap(page, ".hero-btn-play")
    page.wait_for_selector("#castToast:not([hidden])")
    assert "TV không phản hồi" in page.locator("#castToast").inner_text()
    assert page.evaluate("HFCast.linked") is None
    assert page.locator(".hero-btn-play").first.inner_text() == "Phát"
    assert page.locator("#castDot").is_hidden()
    assert not page.evaluate("document.body.classList.contains('player-active')")
    tap(page, ".hero-btn-play")                                # chạm lại thì phát trên iPhone
    assert page.evaluate("document.body.classList.contains('player-active')")


def test_last_tv_is_remembered_and_reconnected_silently(app_module, make_client, open_app):
    page, tv = link_phone(app_module, make_client, open_app)
    stored = page.evaluate("localStorage.getItem('hf_cast_last_tv')")
    assert json.loads(stored)["id"] == tv.id
    page.reload()
    page.wait_for_function("window.HFCast && HFCast.started === true")
    page.wait_for_function("HFCast.linked && HFCast.linked.id === " + json.dumps(tv.id))
    assert page.locator("#castSheet").is_hidden()


def test_last_tv_not_allowed_anymore_does_not_leave_pending_state(app_module, make_client, open_app):
    tv = Tv(app_module, make_client)
    page = open_phone(open_app, init=f"localStorage.setItem('hf_cast_last_tv', JSON.stringify({{id: '{tv.id}', name: 'TV'}}))")
    page.wait_for_timeout(500)
    assert page.evaluate("HFCast.linked") is None and page.evaluate("HFCast.pending") is None


def test_rapid_double_tap_on_device_sends_single_connect(app_module, make_client, open_app):
    tv = Tv(app_module, make_client)
    page = open_phone(open_app)
    open_sheet(page)
    page.evaluate("(() => { const b = document.querySelector('#castDeviceList .cast-device'); b.click(); b.click(); b.click(); })()")
    page.wait_for_function("document.querySelector('#castDeviceList .cast-device').textContent.includes('Chờ TV')")
    assert len([1 for m, u, _ in page.seen if m == "POST" and u.endswith("/connect")]) == 1


def test_sheet_and_button_respect_safe_area_and_touch_targets(app_module, make_client, open_app):
    Tv(app_module, make_client)
    page = open_phone(open_app)
    open_sheet(page)
    row = page.locator("#castDeviceList .cast-device").first.bounding_box()
    assert row["height"] >= 44
    sheet = page.locator("#castSheet").bounding_box()
    assert sheet["y"] >= 59 and sheet["y"] + sheet["height"] <= page.evaluate("document.getElementById('floatTabBar').getBoundingClientRect().top") + 0.5
    assert page.locator("#castSheetClose").bounding_box()["height"] >= 44


def test_device_name_with_html_renders_as_text(app_module, make_client, open_app):
    tv = Tv(app_module, make_client)
    tv.client.patch("/api/device", json={"name": EVIL})
    page = open_phone(open_app)
    open_sheet(page)
    assert EVIL in page.locator("#castDeviceList .cast-device").first.inner_text()
    assert page.evaluate("window.__pwn === undefined")
