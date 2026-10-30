from tests.ui.conftest import tap

URL = "http://homeflix.test/"
VIEW_H = 932


def _visible(page, sel):
    return page.evaluate("s => { const e = document.querySelector(s); return !!e && getComputedStyle(e).display !== 'none' && e.getBoundingClientRect().width > 0; }", sel)


def test_tabbar_sits_above_home_indicator(open_app):
    page = open_app(safe=(59, 0, 34, 0))
    page.goto(URL)
    bottom = page.evaluate("document.getElementById('floatTabBar').getBoundingClientRect().bottom")
    assert abs((VIEW_H - bottom) - 42) <= 1


def test_tabbar_buttons_meet_44pt_touch_target(open_app):
    page = open_app()
    page.goto(URL)
    for sel in ("#fabTabExplore", "#fabTabLibrary", "#fabTabSearch"):
        box = page.locator(sel).bounding_box()
        assert box["width"] >= 44 and box["height"] >= 44, sel


def test_library_button_switches_tab_and_marks_active(open_app):
    page = open_app()
    page.goto(URL)
    tap(page, "#fabTabLibrary")
    assert page.evaluate("document.getElementById('saved-tab').classList.contains('active')")
    assert page.evaluate("document.getElementById('fabTabLibrary').classList.contains('active')")
    assert not page.evaluate("document.getElementById('fabTabExplore').classList.contains('active')")
    tap(page, "#fabTabExplore")
    assert page.evaluate("document.getElementById('search-tab').classList.contains('active')")
    assert page.evaluate("document.getElementById('fabTabExplore').classList.contains('active')")


def test_search_button_opens_overlay_and_focuses_input(open_app):
    page = open_app()
    page.goto(URL)
    tap(page, "#fabTabSearch")
    assert page.evaluate("document.getElementById('searchOverlay').classList.contains('active')")
    assert page.evaluate("document.activeElement.id") == "searchOverlayInput"


def test_tabbar_hidden_on_tv_and_during_playback(open_app):
    tv = open_app(tv=True)
    tv.goto(URL)
    assert not _visible(tv, "#floatTabBar")
    assert _visible(tv, "#appTabsNav")
    phone = open_app()
    phone.goto(URL)
    assert _visible(phone, "#floatTabBar") and not _visible(phone, "#appTabsNav")
    phone.evaluate("document.body.classList.add('player-active')")
    assert not _visible(phone, "#floatTabBar")


def test_manual_url_still_reachable(open_app):
    page = open_app()
    page.goto(URL)
    tap(page, "#fabTabLibrary")
    tap(page, "#manualUrlLink")
    assert page.evaluate("document.getElementById('player-tab').classList.contains('active')")
