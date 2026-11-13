"""Settings > Quản trị > "Đổi mật khẩu admin": stored (generated) password can be changed from the UI;
hidden when HOMEFLIX_ADMIN_PASSWORD manages it."""
import pytest

from tests.ui.conftest import tap
from tests.ui.test_settings import DEVICE, URL, wait_request
from tests.ui.test_settings_admin import SYSTEM, admin_page, login

ROW = '[data-key="admin.password"]'
ERR = lambda field: f'{ROW} .hometv-field:has(#{field}) .set-error'


@pytest.fixture
def stored_pw(app_module, monkeypatch):
    monkeypatch.delenv("HOMEFLIX_ADMIN_PASSWORD", raising=False)
    return app_module.ensure_admin_password()


def stored_admin_page(open_app, password):
    page = open_app()
    page.goto(URL)
    tap(page, "#settingsBtn")
    page.wait_for_selector(DEVICE)
    login(page, password)
    page.wait_for_selector(SYSTEM)
    return page


def open_form(page):
    tap(page, f"{ROW} .set-action")
    page.wait_for_selector("#adminPwCurrent")
    assert page.evaluate("document.activeElement.id") == "adminPwCurrent"


def fill(page, current, new, again):
    page.fill("#adminPwCurrent", current)
    page.fill("#adminPwNew", new)
    page.fill("#adminPwAgain", again)
    tap(page, f'{ROW} button[type="submit"]')


def test_change_password_then_login_with_new(open_app, app_module, stored_pw):
    page = stored_admin_page(open_app, stored_pw)
    open_form(page)
    fill(page, stored_pw, "mật khẩu mới 2026", "mật khẩu mới 2026")
    wait_request(page, "POST /api/admin/password")
    page.wait_for_selector(f"{ROW} .set-action")          # form closed back into the button
    assert "Đã đổi mật khẩu" in page.locator(ROW).inner_text()
    assert app_module.admin.verify_password("mật khẩu mới 2026", app_module._db_admin_hash())
    # field types: browsers/password managers fill the right ones
    open_form(page)
    assert page.get_attribute("#adminPwCurrent", "autocomplete") == "current-password"
    assert page.get_attribute("#adminPwNew", "autocomplete") == "new-password"
    assert page.get_attribute("#adminPwNew", "type") == "password"


def test_mismatch_is_caught_before_sending(open_app, stored_pw):
    page = stored_admin_page(open_app, stored_pw)
    open_form(page)
    fill(page, stored_pw, "abcdefgh12", "abcdefgh13")
    page.wait_for_selector(ERR("adminPwAgain"))
    assert page.locator(ERR("adminPwAgain")).inner_text() == "Mật khẩu nhập lại không khớp"
    assert "POST /api/admin/password" not in page.requests


def test_wrong_current_shows_under_current(open_app, stored_pw):
    page = stored_admin_page(open_app, stored_pw)
    open_form(page)
    fill(page, "not-the-password", "abcdefgh12", "abcdefgh12")
    page.wait_for_selector(ERR("adminPwCurrent"))
    assert page.locator(ERR("adminPwCurrent")).inner_text() == "Sai mật khẩu hiện tại"
    assert page.evaluate("document.activeElement.id") == "adminPwCurrent"


def test_too_short_shows_under_new(open_app, stored_pw):
    page = stored_admin_page(open_app, stored_pw)
    open_form(page)
    fill(page, stored_pw, "short", "short")
    page.wait_for_selector(ERR("adminPwNew"))
    assert "8" in page.locator(ERR("adminPwNew")).inner_text()


def test_escape_closes_form(open_app, stored_pw):
    page = stored_admin_page(open_app, stored_pw)
    open_form(page)
    page.press("#adminPwCurrent", "Escape")
    page.wait_for_selector(f"{ROW} .set-action")
    assert page.locator("#adminPwCurrent").count() == 0


def test_hidden_when_env_manages_password(pw, open_app):
    page = admin_page(open_app)
    assert page.locator('[data-key="admin.logout"]').count() == 1
    assert page.locator(ROW).count() == 0
