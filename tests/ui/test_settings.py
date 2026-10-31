import pytest

from tests.ui.conftest import tap

URL = "http://homeflix.test/"
DEVICE = '.set-group[data-group="device"]'


def _settings(open_app, **kw):
    """Mở app, chạm bánh răng, đợi nhóm "Thiết bị này"."""
    page = open_app(**kw)
    page.goto(URL)
    tap(page, "#settingsBtn")
    page.wait_for_selector(DEVICE)
    return page


def groups(page):
    return page.evaluate("[...document.querySelectorAll('#settingsBody .set-group')].map(g => g.dataset.group)")


def checked(page, key):
    return page.get_attribute(f'[data-key="{key}"] .set-switch', "aria-checked")


def wait_request(page, entry, count=1, timeout=3000):
    for _ in range(timeout // 50):
        if page.requests.count(entry) >= count:
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"expected {count}x {entry}, saw {page.requests}")


def reopen(page):
    page.reload()
    tap(page, "#settingsBtn")
    page.wait_for_selector(DEVICE)


@pytest.mark.parametrize("tv", [False, True])
def test_gear_opens_settings_on_phone_and_tv(open_app, tv):
    page = open_app(tv=tv)
    page.goto(URL)
    box = page.locator("#settingsBtn").bounding_box()
    assert box["width"] >= 44 and box["height"] >= 44
    assert page.get_attribute("#settingsBtn", "aria-label") == "Cài đặt"
    tap(page, "#settingsBtn")
    page.wait_for_selector(DEVICE)
    assert page.evaluate("document.getElementById('settings-tab').classList.contains('active')")
    assert page.locator("#settings-tab .settings-head h2").inner_text() == "Cài đặt"
    assert page.locator(DEVICE + " .set-group-title").inner_text() == "Thiết bị này"
    keys = page.evaluate(f"[...document.querySelectorAll('{DEVICE} .set-row')].map(r => r.dataset.key)")
    assert keys == ["device.name", "cast.allow_control", "player.autoNext", "player.autoIntro", "player.introSeconds"]
    # tab bar và nav header không tô nút nào khi đang ở Cài đặt
    assert page.evaluate("document.querySelectorAll('.fab-tab.active, .tab-btn.active').length") == 0
    # mọi dòng cao ít nhất var(--tap)
    heights = page.evaluate("[...document.querySelectorAll('#settingsBody .set-row')].map(r => r.getBoundingClientRect().height)")
    assert min(heights) >= 44
    assert page.console_errors == []


def test_old_cast_settings_popup_is_gone(open_app):
    page = open_app(tv=True)
    page.goto(URL)
    assert page.locator("#castSettings, #castSettingsBtn, #castAllowControl").count() == 0


def test_regular_device_sees_only_device_and_admin_groups(pw, open_app):
    page = _settings(open_app)
    assert groups(page) == ["device", "admin"]
    keys = page.evaluate("[...document.querySelectorAll('[data-group=\"admin\"] .set-row')].map(r => r.dataset.key)")
    assert keys == ["admin.login", "admin.request"]
    assert page.locator('[data-group="admin"] .set-group-title').inner_text() == "Quản trị"
    assert page.locator('[data-key="admin.login"]').inner_text().strip() == "Đăng nhập admin"
    assert page.locator('[data-key="admin.request"]').inner_text().strip() == "Xin duyệt từ máy admin"


def test_admin_group_hidden_when_admin_disabled(open_app, monkeypatch):
    monkeypatch.delenv("HOMEFLIX_ADMIN_PASSWORD", raising=False)
    assert groups(_settings(open_app)) == ["device"]


def test_device_name_edit_persists(open_app):
    page = _settings(open_app)
    assert page.locator('[data-key="device.name"] button').inner_text() == "iPhone"
    tap(page, '[data-key="device.name"] button')
    page.fill('[data-key="device.name"] input', "  Máy của Duy  ")
    page.press('[data-key="device.name"] input', "Enter")
    wait_request(page, "PATCH /api/device")
    page.wait_for_selector('[data-key="device.name"] button')
    assert page.locator('[data-key="device.name"] button').inner_text() == "Máy của Duy"
    reopen(page)
    assert page.locator('[data-key="device.name"] button').inner_text() == "Máy của Duy"


def test_device_name_rejects_empty_and_never_shows_raw_id(open_app):
    page = _settings(open_app)
    tap(page, '[data-key="device.name"] button')
    page.fill('[data-key="device.name"] input', "   ")
    page.press('[data-key="device.name"] input', "Enter")
    page.wait_for_selector('[data-key="device.name"] .set-error')
    assert page.locator('[data-key="device.name"] button').inner_text() == "iPhone"
    assert "PATCH /api/device" not in page.requests
    raw_id = page.evaluate("fetch('/api/device').then(r => r.json()).then(d => d.id)")
    assert raw_id not in page.content()


def test_remote_control_switch_persists(open_app):
    page = _settings(open_app)
    assert checked(page, "cast.allow_control") == "true"
    tap(page, '[data-key="cast.allow_control"] .set-switch')
    assert checked(page, "cast.allow_control") == "false"
    wait_request(page, "PATCH /api/cast/settings")
    reopen(page)
    assert checked(page, "cast.allow_control") == "false"


def test_switch_save_error_reverts(open_app):
    page = open_app()
    page.route("**/api/cast/settings", lambda r: r.fulfill(status=500, body="x") if r.request.method == "PATCH" else r.fallback())
    page.goto(URL)
    tap(page, "#settingsBtn")
    page.wait_for_selector(DEVICE)
    tap(page, '[data-key="cast.allow_control"] .set-switch')
    page.wait_for_selector('[data-key="cast.allow_control"] .set-error')
    assert checked(page, "cast.allow_control") == "true"


def test_player_prefs_shared_with_player_panel(open_app):
    page = _settings(open_app)
    assert checked(page, "player.autoNext") == "true" and checked(page, "player.autoIntro") == "false"
    tap(page, '[data-key="player.autoNext"] .set-switch')
    assert page.evaluate("HFPlayerAuto.getPrefs().autoNext") is False
    assert page.evaluate("document.getElementById('prefAutoNext').checked") is False   # panel trong trình phát
    page.evaluate("HFPlayerAuto.setPrefs({introSeconds: 120})")
    assert page.locator('[data-key="player.introSeconds"] .set-value').inner_text() == "120 giây"
    page.evaluate("HFPlayerAuto.setPrefs({autoIntro: true})")
    assert checked(page, "player.autoIntro") == "true"


def test_stepper_saves_once_after_400ms(open_app):
    page = _settings(open_app)
    value = '[data-key="player.introSeconds"] .set-value'
    assert page.locator(value).inner_text() == "85 giây"
    page.evaluate("window.__prefsEvents = 0; document.addEventListener('hf-prefs', () => window.__prefsEvents++)")
    for _ in range(3):
        tap(page, '[data-key="player.introSeconds"] .set-step[data-dir="1"]')
    assert page.locator(value).inner_text() == "100 giây"
    page.wait_for_timeout(700)
    assert page.evaluate("window.__prefsEvents") == 1
    assert page.evaluate("HFPlayerAuto.getPrefs().introSeconds") == 100
    assert page.locator(value).inner_text() == "100 giây"
    assert not any("/api/" in r and r.startswith("PATCH") for r in page.requests)   # chỉ lưu trên máy


def test_stepper_clamps_at_min(open_app):
    page = open_app()
    page.add_init_script("localStorage.setItem('hf_player_prefs', JSON.stringify({introSeconds: 15}))")
    page.goto(URL)
    tap(page, "#settingsBtn")
    page.wait_for_selector(DEVICE)
    minus = '[data-key="player.introSeconds"] .set-step[data-dir="-1"]'
    assert page.get_attribute(minus, "aria-disabled") == "false"
    tap(page, minus)
    assert page.locator('[data-key="player.introSeconds"] .set-value').inner_text() == "10 giây"
    assert page.get_attribute(minus, "aria-disabled") == "true"
    page.dispatch_event(minus, "click")   # vẫn bấm được trên remote, nhưng kẹp ở min
    assert page.locator('[data-key="player.introSeconds"] .set-value').inner_text() == "10 giây"
    page.wait_for_timeout(600)
    assert page.evaluate("HFPlayerAuto.getPrefs().introSeconds") == 10


def test_back_returns_to_previous_tab(open_app):
    page = open_app()
    page.goto(URL)
    tap(page, "#fabTabLibrary")
    tap(page, "#settingsBtn")
    page.wait_for_selector(DEVICE)
    assert not page.evaluate("document.getElementById('saved-tab').classList.contains('active')")
    tap(page, "#settingsBack")
    assert page.evaluate("document.getElementById('saved-tab').classList.contains('active')")
    assert page.evaluate("document.getElementById('fabTabLibrary').classList.contains('active')")


def test_tabbar_leaves_settings(open_app):
    page = _settings(open_app)
    tap(page, "#fabTabExplore")
    assert page.evaluate("document.getElementById('search-tab').classList.contains('active')")
    assert not page.evaluate("document.getElementById('settings-tab').classList.contains('active')")


def test_device_group_works_when_settings_api_fails(open_app):
    page = open_app()
    page.route("**/api/settings", lambda r: r.fulfill(status=500, body="x"))
    page.goto(URL)
    tap(page, "#settingsBtn")
    page.wait_for_selector(DEVICE)
    assert groups(page) == ["device"]
    assert page.console_errors == []


@pytest.mark.parametrize("tv", [False, True])
def test_no_horizontal_scroll(pw, open_app, tv):
    page = open_app(tv=tv)
    page.goto(URL)
    page.evaluate("fetch('/api/device', {method: 'PATCH', headers: {'Content-Type': 'application/json'},"
                  " body: JSON.stringify({name: 'W'.repeat(40)})})")   # tên dài nhất, không có khoảng trắng
    tap(page, "#settingsBtn")
    page.wait_for_selector(DEVICE)
    assert page.locator('[data-key="device.name"] button').inner_text() == "W" * 40
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    width = page.evaluate("document.querySelector('#settings-tab .settings-page').getBoundingClientRect().width")
    assert width <= 640
