"""Trang Cài đặt, phần admin: Quản trị, Thiết bị trong nhà, Hệ thống, HomeTV."""
import json
import re

import pytest

from tests.ui.conftest import tap
from tests.ui.test_settings import DEVICE, URL, _settings, groups, wait_request

ADMIN_GROUPS = ["device", "admin", "devices", "system", "hometv"]
SYSTEM = '.set-group[data-group="system"]'
PLUS_GB = '[data-key="cache.max_gb"] .set-step[data-dir="1"]'
GB_VALUE = '[data-key="cache.max_gb"] .set-value'


def login(page, password):
    tap(page, '[data-key="admin.login"]')
    page.fill("#adminPassword", password)
    page.press("#adminPassword", "Enter")


def admin_page(open_app, clock=False, **kw):
    page = open_app(**kw)
    if clock:
        page.clock.install()   # đồng hồ vẫn chạy; fast_forward để nhảy qua chu kỳ tải lại
    page.goto(URL)
    tap(page, "#settingsBtn")
    page.wait_for_selector(DEVICE)
    login(page, "mk-test")
    page.wait_for_selector(SYSTEM)
    return page


def device_id(page):
    return next(c["value"] for c in page.context.cookies() if c["name"] == "hf_device")


def row_keys(page, group):
    return page.evaluate(f"[...document.querySelectorAll('[data-group=\"{group}\"] .set-row')].map(r => r.dataset.key)")


def server_json(page, path):
    return page.evaluate(f"fetch('{path}').then(r => r.json())")


def wait_groups(page, expected, timeout=8000):
    page.wait_for_function("(g) => JSON.stringify([...document.querySelectorAll('#settingsBody .set-group')]"
                           ".map(x => x.dataset.group)) === JSON.stringify(g)", arg=expected, timeout=timeout)


def reopen(page, via="#settingsBack"):
    """Rời trang Cài đặt rồi mở lại; đợi lượt dựng mới (DOM cũ vẫn nằm trong tab đã ẩn)."""
    page.evaluate("document.querySelectorAll('#settingsBody .set-group').forEach(g => g.dataset.stale = '1')")
    tap(page, via)
    tap(page, "#settingsBtn")
    page.wait_for_selector("#settingsBody .set-group:not([data-stale])")


def focused_key(page):
    return page.evaluate("(() => { const r = document.activeElement.closest('[data-key]'); return r && r.dataset.key; })()")


@pytest.mark.parametrize("tv", [False, True])
def test_login_reveals_admin_groups(pw, open_app, tv):
    page = _settings(open_app, tv=tv)
    login(page, "mk-test")
    page.wait_for_selector(SYSTEM)
    assert groups(page) == ADMIN_GROUPS
    assert [page.locator(f'[data-group="{g}"] .set-group-title').inner_text() for g in ADMIN_GROUPS] == [
        "Thiết bị này", "Quản trị", "Thiết bị trong nhà", "Hệ thống", "HomeTV"]
    assert row_keys(page, "admin") == ["admin.logout", "admin.revoke_others"]
    assert page.locator('[data-key="admin.logout"]').inner_text().strip() == "Đăng xuất admin"
    assert page.locator('[data-key="admin.revoke_others"]').inner_text().strip() == "Thu quyền mọi máy khác"
    assert row_keys(page, "system") == ["cache.usage", "cache.max_gb", "cache.max_age_hours",
                                        "prefetch.concurrency", "home.refresh_minutes", "cache.clear"]
    values = page.evaluate("[...document.querySelectorAll('[data-group=\"system\"] .set-value')].map(v => v.textContent)")
    assert values == ["10 GB", "6 giờ", "4", "10 phút"]
    assert page.locator('[data-key="cache.max_gb"] .set-label').inner_text() == "Dung lượng tối đa"
    assert "/ 10 GB" in page.locator('[data-key="cache.usage"]').inner_text()
    # màu đỏ chỉ cho thao tác nguy hiểm
    reds = page.evaluate("[...document.querySelectorAll('#settingsBody .set-danger')].map(b => b.closest('[data-key]').dataset.key)")
    assert sorted(reds) == ["admin.revoke_others", "cache.clear"]
    heights = page.evaluate("[...document.querySelectorAll('#settingsBody .set-row')].map(r => r.getBoundingClientRect().height)")
    assert min(heights) >= 44
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.console_errors == []


def test_wrong_password_shows_error_and_lockout_time(pw, open_app):
    page = _settings(open_app)
    tap(page, '[data-key="admin.login"]')
    assert page.get_attribute("#adminPassword", "type") == "password"
    error = '[data-key="admin.login"] .set-error'
    for i in range(5):
        page.fill("#adminPassword", f"sai-{i}")
        page.press("#adminPassword", "Enter")   # gửi thì xoá lỗi cũ ngay, lỗi mới hiện khi có kết quả
        wait_request(page, "POST /api/admin/login", count=i + 1)
        page.wait_for_selector(error)
        assert page.locator(error).inner_text() == "Sai mật khẩu"
    page.fill("#adminPassword", "mk-test")   # đúng mật khẩu nhưng đang bị khóa
    page.press("#adminPassword", "Enter")
    page.wait_for_function("document.querySelector('[data-key=\"admin.login\"] .set-error')?.textContent.includes('phút')")
    assert page.locator(error).inner_text() == "Thử lại sau 15 phút"
    assert groups(page) == ["device", "admin"]
    assert "sai-" not in " ".join(page.requests)   # mật khẩu đi trong thân JSON, không nằm trên URL


def test_password_escape_closes_input(pw, open_app):
    page = _settings(open_app, tv=True)
    tap(page, '[data-key="admin.login"]')
    page.press("#adminPassword", "Escape")
    assert page.locator("#adminPassword").count() == 0
    assert focused_key(page) == "admin.login"
    assert "POST /api/admin/login" not in page.requests


def test_system_stepper_saves(pw, open_app):
    page = admin_page(open_app)
    tap(page, PLUS_GB)
    page.wait_for_function("document.querySelector('[data-key=\"cache.max_gb\"] .set-value').textContent === '11 GB'")
    page.wait_for_timeout(600)
    assert page.requests.count("PATCH /api/settings") == 1
    conf = server_json(page, "/api/settings")
    assert {i["key"]: i["value"] for i in conf["groups"][0]["items"]}["cache.max_gb"] == 11
    page.wait_for_function("document.querySelector('[data-key=\"cache.usage\"]').textContent.includes('/ 11 GB')")


def test_save_error_reverts_value(pw, open_app):
    page = admin_page(open_app)

    def reject(route):
        if route.request.method != "PATCH":
            return route.fallback()
        route.fulfill(status=422, content_type="application/json",
                      body=json.dumps({"errors": {"cache.max_gb": "phải từ 1 đến 500"}}))
    page.route("**/api/settings", reject)
    tap(page, PLUS_GB)
    assert page.locator(GB_VALUE).inner_text() == "11 GB"
    page.wait_for_selector('[data-key="cache.max_gb"] .set-error')
    assert page.locator(GB_VALUE).inner_text() == "10 GB"


def test_system_rows_come_from_settings_api(pw, open_app):
    page = admin_page(open_app)
    real = server_json(page, "/api/settings")
    real["groups"][0]["items"].append({"key": "test.flag", "label": "Cờ thử", "type": "bool", "value": True,
                                       "default": False, "min": None, "max": None, "step": 1, "unit": ""})
    real["groups"][0]["items"].append({"key": "test.count", "label": "Số thử", "type": "int", "value": 3,
                                       "default": 3, "min": 1, "max": 9, "step": 2, "unit": "lần"})
    page.route("**/api/settings", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(real))
               if r.request.method == "GET" else r.fallback())
    reopen(page)
    page.wait_for_selector('[data-key="test.count"]')
    assert page.get_attribute('[data-key="test.flag"] .set-switch', "aria-checked") == "true"
    assert page.locator('[data-key="test.count"] .set-value').inner_text() == "3 lần"
    assert row_keys(page, "system")[-3:] == ["test.flag", "test.count", "cache.clear"]


def test_403_reloads_settings(pw, open_app, app_module):
    page = admin_page(open_app)
    app_module._db_set_admin(device_id(page), False)   # máy khác vừa thu quyền
    tap(page, PLUS_GB)
    wait_groups(page, ["device", "admin"], timeout=3000)
    assert row_keys(page, "admin") == ["admin.login", "admin.request"]
    assert page.console_errors == []


def test_rerender_keeps_focus_on_same_row_tv(pw, open_app, app_module):
    page = admin_page(open_app, clock=True, tv=True)
    page.focus('[data-key="player.autoNext"] .set-switch')
    app_module._db_set_admin(device_id(page), False)
    page.clock.fast_forward(5100)   # lượt tải lại yêu cầu chờ nhận 403 → dựng lại trang
    wait_groups(page, ["device", "admin"], timeout=3000)
    assert page.evaluate("document.activeElement.matches('[data-key=\"player.autoNext\"] .set-switch')"
                         " && document.activeElement.isConnected")


def test_rerender_moves_focus_to_gear_when_row_is_gone_tv(pw, open_app, app_module):
    page = admin_page(open_app, tv=True)
    page.focus(PLUS_GB)
    app_module._db_set_admin(device_id(page), False)
    page.keyboard.press("Enter")   # (+) bằng remote → PATCH 403
    wait_groups(page, ["device", "admin"], timeout=3000)
    assert page.evaluate("document.activeElement.id") == "settingsBtn"


def test_tv_requests_phone_approves(pw, open_app):
    tv = _settings(open_app, tv=True)
    tap(tv, '[data-key="admin.request"]')
    tv.wait_for_selector("#adminCode")
    code = tv.locator("#adminCode").inner_text()
    assert re.fullmatch(r"\d{6}", code)
    assert re.search(r"\b(9:5\d|10:00)\b", tv.locator('[data-key="admin.request"]').inner_text())
    phone = admin_page(open_app)
    phone.wait_for_selector(f'.set-request:has-text("{code}")')
    assert phone.locator(".set-request").count() == 1
    assert phone.locator(".set-request").inner_text().startswith("TV")
    tap(phone, ".set-request .set-approve")
    phone.wait_for_selector(".set-request", state="detached")
    tv.wait_for_selector('.set-group[data-group="system"]', timeout=8000)
    assert groups(tv) == ADMIN_GROUPS
    assert tv.console_errors == [] and phone.console_errors == []


def test_phone_sees_new_request_within_5s(pw, open_app):
    phone = admin_page(open_app, clock=True)
    assert phone.locator(".set-request").count() == 0
    tv = _settings(open_app, tv=True)
    tap(tv, '[data-key="admin.request"]')
    code = tv.locator("#adminCode").inner_text()
    phone.clock.fast_forward(5100)
    phone.wait_for_selector(f'.set-request:has-text("{code}")', timeout=3000)


def test_request_denied_then_expired_copy(pw, open_app):
    tv = _settings(open_app, tv=True)
    tap(tv, '[data-key="admin.request"]')
    code = tv.locator("#adminCode").inner_text()
    phone = admin_page(open_app)
    phone.wait_for_selector(f'.set-request:has-text("{code}")')
    tap(phone, ".set-request .set-deny")
    tv.wait_for_selector('[data-key="admin.request"] .set-error', timeout=5000)
    assert tv.locator('[data-key="admin.request"] .set-error').inner_text() == "Bị từ chối"
    assert tv.locator("#adminCode").count() == 0
    assert groups(tv) == ["device", "admin"]
    # xin lại, rồi server báo hết hạn
    tv.route("**/api/admin/requests/mine", lambda r: r.fulfill(status=200, content_type="application/json",
                                                               body='{"status": "expired"}'))
    tap(tv, '[data-key="admin.request"]')
    tv.wait_for_selector("#adminCode")
    tv.wait_for_function("document.querySelector('[data-key=\"admin.request\"] .set-error')"
                         " && document.querySelector('[data-key=\"admin.request\"] .set-error').textContent === 'Hết hạn, thử lại'",
                         timeout=5000)


def test_waiting_request_resumes_when_reopened(pw, open_app):
    tv = _settings(open_app, tv=True)
    tap(tv, '[data-key="admin.request"]')
    code = tv.locator("#adminCode").inner_text()
    reopen(tv)
    tv.wait_for_selector("#adminCode")
    assert tv.locator("#adminCode").inner_text() == code


def test_close_stops_admin_polling(pw, open_app):
    page = admin_page(open_app, clock=True)
    entry = "GET /api/admin/requests"
    n = page.requests.count(entry)
    page.clock.fast_forward(5100)
    wait_request(page, entry, count=n + 1)
    tap(page, "#fabTabExplore")   # rời trang Cài đặt
    page.wait_for_timeout(200)
    n = page.requests.count(entry)
    page.clock.fast_forward(16000)
    page.wait_for_timeout(300)
    assert page.requests.count(entry) == n
    page.evaluate("document.querySelectorAll('#settingsBody .set-group').forEach(g => g.dataset.stale = '1')")
    tap(page, "#settingsBtn")   # mở lại: tải lại như cũ
    page.wait_for_selector(f"{SYSTEM}:not([data-stale])")
    n = page.requests.count(entry)
    page.clock.fast_forward(5100)
    wait_request(page, entry, count=n + 1)


def test_close_stops_waiting_poll(pw, open_app):
    tv = open_app(tv=True)
    tv.clock.install()
    tv.goto(URL)
    tap(tv, "#settingsBtn")
    tv.wait_for_selector(DEVICE)
    tap(tv, '[data-key="admin.request"]')
    tv.wait_for_selector("#adminCode")
    entry = "GET /api/admin/requests/mine"
    n = tv.requests.count(entry)
    tv.clock.fast_forward(3100)
    wait_request(tv, entry, count=n + 1)
    tv.evaluate("switchTab('saved-tab')")
    tv.wait_for_timeout(200)
    n = tv.requests.count(entry)
    tv.clock.fast_forward(10000)
    tv.wait_for_timeout(300)
    assert tv.requests.count(entry) == n


def test_devices_grouped_and_rename(pw, open_app, app_module):
    page = _settings(open_app)               # máy mình đăng ký trước
    tv = open_app(tv=True)
    tv.goto(URL)                             # TV mới hơn: server xếp nó trước máy mình
    far = open_app(width=1280, height=800)
    far.goto(URL)
    app_module.db_execute("UPDATE devices SET household = ? WHERE id = ?", ("f" * 16, device_id(far)))
    login(page, "mk-test")
    page.wait_for_selector(SYSTEM)
    lists = page.locator('[data-group="devices"] .set-list:not(.set-requests)')
    assert lists.count() == 2
    assert page.locator('[data-group="devices"] .set-sub').all_inner_texts() == ["Nhà này", "Nhà khác"]
    first = lists.nth(0).locator(".set-device")
    assert first.count() == 2
    assert "máy này" in first.nth(0).inner_text()
    assert first.nth(1).locator(".set-device-name").inner_text() == "TV"
    assert "admin" in first.nth(0).locator(".set-tag").inner_text()
    content = page.content()
    for p in (page, tv, far):
        assert device_id(p) not in content   # id thật là cookie đăng nhập
    # đổi tên TV
    tv_row = first.nth(1)
    tv_id = tv_row.get_attribute("data-id")
    tap(page, f'.set-device[data-id="{tv_id}"] .set-action')
    tap(page, f'.set-device[data-id="{tv_id}"] [data-act="rename"]')
    page.fill(f'.set-device[data-id="{tv_id}"] input', "  Phòng khách  ")
    page.press(f'.set-device[data-id="{tv_id}"] input', "Enter")
    wait_request(page, f"PATCH /api/admin/devices/{tv_id}")
    page.wait_for_function("(id) => document.querySelector(`.set-device[data-id=\"${id}\"] .set-device-name`)"
                           ".textContent === 'Phòng khách'", arg=tv_id)
    names = {d["id"]: d["name"] for d in server_json(page, "/api/admin/devices")}
    assert names[tv_id] == "Phòng khách"
    # cấp quyền admin cho TV
    tap(page, f'.set-device[data-id="{tv_id}"] [data-act="admin"]')
    page.wait_for_selector(f'.set-device[data-id="{tv_id}"] .set-tag')
    assert {d["id"]: d["is_admin"] for d in server_json(page, "/api/admin/devices")}[tv_id] is True
    assert page.locator(f'.set-device[data-id="{tv_id}"] [data-act="admin"]').inner_text() == "Thu quyền admin"
    assert page.eval_on_selector(f'.set-device[data-id="{tv_id}"] [data-act="admin"]',
                                 "b => getComputedStyle(b).color") == "rgb(229, 9, 20)"   # thu quyền: đỏ
    assert page.console_errors == []


def test_device_rename_rejects_empty(pw, open_app):
    tv = open_app(tv=True)
    tv.goto(URL)
    page = admin_page(open_app)
    row = '.set-device:not(:has-text("máy này"))'
    tap(page, f"{row} .set-action")
    tap(page, f'{row} [data-act="rename"]')
    page.fill(f"{row} input", "   ")
    page.press(f"{row} input", "Enter")
    page.wait_for_selector(f"{row} .set-error")
    assert page.locator(f"{row} .set-error").inner_text() == "Tên cần 1–40 ký tự"
    assert not any(r.startswith("PATCH /api/admin/devices") for r in page.requests)


def test_self_device_has_no_delete_or_admin_toggle(pw, open_app):
    page = admin_page(open_app)
    me = '.set-device:has-text("máy này")'
    tap(page, f"{me} .set-action")
    page.wait_for_selector(f'{me} [data-act="rename"]')
    assert page.locator(f'{me} [data-act="delete"], {me} [data-act="admin"]').count() == 0


def test_device_without_last_seen_shows_no_time(pw, open_app):
    page = admin_page(open_app)
    fake = [{"id": "aaa", "name": "Máy tôi", "kind": "phone", "household": "h1", "last_seen": 1e9,
             "is_admin": True, "is_self": True},
            {"id": "bbb", "name": "Máy cũ", "kind": "desktop", "household": "h1", "last_seen": None,
             "is_admin": False, "is_self": False}]
    page.route("**/api/admin/devices", lambda r: r.fulfill(status=200, content_type="application/json",
                                                         body=json.dumps(fake)))
    reopen(page)
    page.wait_for_selector('.set-device[data-id="bbb"]')
    assert page.locator('.set-device[data-id="bbb"] .set-device-meta').inner_text() == "Máy tính"


def test_delete_device_asks_confirmation(pw, open_app):
    tv = open_app(tv=True)
    tv.goto(URL)
    page = admin_page(open_app)
    tv_id = page.locator('.set-device:not(:has-text("máy này"))').get_attribute("data-id")
    row = f'.set-device[data-id="{tv_id}"]'
    messages = []

    def dismiss(d):
        messages.append(d.message)
        d.dismiss()
    page.once("dialog", dismiss)
    tap(page, f"{row} .set-action")
    red = page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--accent')")
    assert red.strip() == "#e50914"
    assert page.eval_on_selector(f'{row} [data-act="delete"]', "b => getComputedStyle(b).color") == "rgb(229, 9, 20)"
    tap(page, f'{row} [data-act="delete"]')
    page.wait_for_timeout(300)
    assert messages == ["Xoá TV? Tủ phim và tiến độ xem của máy này sẽ mất."]
    assert not any(r.startswith("DELETE ") for r in page.requests)
    assert page.locator(row).count() == 1
    page.once("dialog", lambda d: d.accept())
    tap(page, f'{row} [data-act="delete"]')
    wait_request(page, f"DELETE /api/admin/devices/{tv_id}")
    page.wait_for_selector(row, state="detached")
    assert tv_id not in [d["id"] for d in server_json(page, "/api/admin/devices")]


def test_clear_cache_asks_confirmation(pw, open_app):
    page = admin_page(open_app)
    messages = []

    def dismiss(d):
        messages.append(d.message)
        d.dismiss()
    page.once("dialog", dismiss)
    tap(page, '[data-key="cache.clear"]')
    page.wait_for_timeout(300)
    assert messages == ["Xoá toàn bộ cache?"]
    assert "POST /api/admin/cache/clear" not in page.requests
    page.once("dialog", lambda d: d.accept())
    tap(page, '[data-key="cache.clear"]')
    wait_request(page, "POST /api/admin/cache/clear")
    assert page.console_errors == []


def test_hometv_placeholder(pw, open_app):
    page = admin_page(open_app)
    rows = page.locator('[data-group="hometv"] .set-row')
    assert rows.count() == 1
    assert rows.inner_text().strip() == "Nguồn IPTV · có ở bản sau"


def test_logout_hides_admin_groups(pw, open_app):
    page = admin_page(open_app)
    tap(page, '[data-key="admin.logout"]')
    wait_request(page, "POST /api/admin/logout")
    wait_groups(page, ["device", "admin"], timeout=3000)
    assert row_keys(page, "admin") == ["admin.login", "admin.request"]
    assert server_json(page, "/api/settings")["is_admin"] is False


def test_revoke_others_asks_and_revokes(pw, open_app):
    other = admin_page(open_app, tv=True)
    page = admin_page(open_app)
    assert page.locator(".set-device .set-tag").count() == 2
    page.once("dialog", lambda d: d.accept())
    tap(page, '[data-key="admin.revoke_others"]')
    wait_request(page, "POST /api/admin/revoke-others")
    page.wait_for_selector('[data-key="admin.revoke_others"] .set-note')
    assert page.locator('[data-key="admin.revoke_others"] .set-note').inner_text() == "Đã thu quyền 1 máy"
    page.wait_for_function("document.querySelectorAll('.set-device .set-tag').length === 1")
    assert server_json(other, "/api/settings")["is_admin"] is False


def active_desc(page):
    return page.evaluate("(() => { const a = document.activeElement; const r = a.closest('.set-row');"
                         " return [r && (r.dataset.key || r.dataset.id), a.dataset.act || a.dataset.dir || a.tagName]; })()")


def test_tv_every_row_reachable_by_arrows(pw, open_app):
    page = admin_page(open_app, tv=True)
    page.focus('[data-key="device.name"] button')
    seen = ["device.name"]
    for _ in range(40):
        page.keyboard.press("ArrowDown")
        seen.append(active_desc(page)[0])
    expected = page.evaluate("[...document.querySelectorAll('#settingsBody .set-row')].map(r => r.dataset.key || r.dataset.id)")
    assert set(expected) <= set(seen), set(expected) - set(seen)
    assert page.evaluate("document.activeElement.closest('.set-row').dataset.key") == "hometv.iptv"
    # và ngược lên
    for _ in range(40):
        page.keyboard.press("ArrowUp")
    assert page.evaluate("document.activeElement.closest('.set-row').dataset.key") == "device.name"
    # dòng chỉ-đọc có vòng focus nhìn thấy
    page.focus('[data-key="cache.usage"]')
    assert page.evaluate("getComputedStyle(document.activeElement).outlineStyle") != "none"


def test_tv_open_device_actions_walk_down_one_by_one(pw, open_app):
    page = open_app(tv=True)
    page.goto(URL)
    other = open_app(width=1280, height=800)
    other.goto(URL)
    page.reload()
    tap(page, "#settingsBtn")
    page.wait_for_selector(DEVICE)
    login(page, "mk-test")
    page.wait_for_selector(SYSTEM)
    other_row = page.locator(".set-device").filter(has_not=page.locator(".set-device-meta", has_text="máy này")).first
    other_row.locator(".set-action").focus()
    page.keyboard.press("Enter")
    page.wait_for_selector('.set-device [data-act="delete"]')
    # mỗi thao tác xếp dọc là một dòng riêng: Xuống đi qua từng cái
    for act in ("rename", "admin", "delete"):
        page.keyboard.press("ArrowDown")
        assert page.evaluate("document.activeElement.dataset.act") == act
    page.keyboard.press("ArrowUp")
    assert page.evaluate("document.activeElement.dataset.act") == "admin"


def test_admin_empty_password_sends_nothing(pw, open_app):
    page = open_app(tv=True)
    page.goto(URL)
    tap(page, "#settingsBtn")
    page.wait_for_selector(DEVICE)
    tap(page, '[data-key="admin.login"]')
    page.wait_for_selector("#adminPassword")
    page.evaluate("document.querySelector('.set-form').requestSubmit()")
    page.press("#adminPassword", "Enter")
    page.wait_for_timeout(400)
    assert not [r for r in page.requests if "admin/login" in r], page.requests
    assert page.locator(".set-error").count() == 0
    page.fill("#adminPassword", "mk-test")
    page.press("#adminPassword", "Enter")
    page.wait_for_selector(SYSTEM)


def test_tv_arrows_inside_password_keep_caret(pw, open_app):
    page = open_app(tv=True)
    page.goto(URL)
    tap(page, "#settingsBtn")
    page.wait_for_selector(DEVICE)
    tap(page, '[data-key="admin.login"]')
    page.fill("#adminPassword", "abc")
    page.keyboard.press("ArrowLeft")
    assert page.evaluate("document.activeElement.id") == "adminPassword"
    assert page.evaluate("document.activeElement.selectionStart") == 2
