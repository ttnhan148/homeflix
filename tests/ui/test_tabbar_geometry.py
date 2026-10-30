VIEW_H = 932


def box(page, sel):
    return page.evaluate("s => { const r = document.querySelector(s).getBoundingClientRect(); return {t: r.top, b: r.bottom, h: r.height}; }", sel)


def test_tabbar_glass_reaches_screen_bottom_and_buttons_sit_above_home_indicator(open_app):
    page = open_app(safe=(59, 0, 34, 0))
    page.goto("http://homeflix.test/")
    assert abs(box(page, "#floatTabBar")["b"] - VIEW_H) <= 1          # không lộ nội dung dưới thanh Home
    assert box(page, "#floatTabBar .fab-capsule")["b"] <= VIEW_H - 34 + 0.5
    assert "blur" in page.evaluate("getComputedStyle(document.getElementById('floatTabBar')).backdropFilter")


def test_status_bar_shield_covers_dynamic_island_band_on_iphone_only(open_app):
    page = open_app(safe=(59, 0, 34, 0))
    page.goto("http://homeflix.test/")
    shield = box(page, "#statusBarShield")
    assert shield["t"] == 0 and abs(shield["h"] - 59) <= 0.5
    tv = open_app(tv=True)
    tv.goto("http://homeflix.test/")
    assert tv.evaluate("getComputedStyle(document.getElementById('statusBarShield')).display") == "none"


def test_sticky_filter_bar_stops_under_dynamic_island(open_app):
    page = open_app(safe=(59, 0, 34, 0))
    page.goto("http://homeflix.test/")
    page.wait_for_selector("#homeGridContent .movie-card")
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(100)
    assert abs(box(page, "#filterBar")["t"] - 59) <= 1
