"""Enter that confirms an IME composition (Vietnamese Telex/VNI on macOS/iOS, CJK) must not act as
"save" / "next field". Real composition cannot be driven headless, so dispatch the keydown the browser
sends: isComposing=true (Chrome/Firefox) or keyCode 229 (Safari's out-of-order confirming Enter)."""
import pytest

from tests.ui.conftest import tap
from tests.ui.test_settings import _settings
from tests.ui.test_settings_hometv import hometv_page

COMPOSING_ENTER = {
    "isComposing": "new KeyboardEvent('keydown', {key: 'Enter', isComposing: true, bubbles: true, cancelable: true})",
    "keyCode229": "Object.defineProperty(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true, cancelable: true}),"
                  " 'keyCode', {value: 229})",
}


@pytest.mark.parametrize("kind", COMPOSING_ENTER)
def test_composing_enter_does_not_save_device_name(open_app, kind):
    page = _settings(open_app)
    tap(page, '[data-key="device.name"] button')
    sel = '[data-key="device.name"] input'
    page.fill(sel, "Máy của Duy")
    page.eval_on_selector(sel, f"el => el.dispatchEvent({COMPOSING_ENTER[kind]})")
    page.wait_for_timeout(200)
    assert page.locator(sel).count() == 1          # still editing
    assert "PATCH /api/device" not in page.requests
    page.press(sel, "Enter")                       # a plain Enter still saves
    page.wait_for_selector('[data-key="device.name"] button')


@pytest.mark.parametrize("kind", COMPOSING_ENTER)
def test_composing_enter_in_source_name_stays_in_field(pw, open_app, public_dns, kind):
    page = hometv_page(open_app)
    tap(page, '[data-key="hometv.add"] .set-action')
    page.wait_for_selector("#hometvName")
    page.fill("#hometvName", "Nguồn thử")
    page.eval_on_selector("#hometvName", f"el => el.dispatchEvent({COMPOSING_ENTER[kind]})")
    assert page.evaluate("document.activeElement.id") == "hometvName"
    page.press("#hometvName", "Enter")             # a plain Enter still moves to the URL field
    assert page.evaluate("document.activeElement.id") == "hometvUrl"
