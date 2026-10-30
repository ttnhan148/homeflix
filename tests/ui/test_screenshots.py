import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SIZES = [(430, 932, False), (440, 956, False), (1920, 1080, True)]


def test_shell_precaches_new_static_files():
    sw = (ROOT / "static" / "sw.js").read_text()
    assert "homeflix-shell-v2" in sw
    assert "/static/ios26.css" in sw and "/static/home-utils.js" in sw


@pytest.mark.parametrize("w,h,tv", SIZES)
def test_screenshots_render_non_blank(open_app, tmp_path, w, h, tv):
    page = open_app(width=w, height=h, tv=tv, safe=(0 if tv else 59, 0, 0 if tv else 34, 0))
    page.goto("http://homeflix.test/")
    page.wait_for_selector("#homeGridContent .movie-card")
    page.wait_for_function("Object.keys(heroState.details).length >= 1")
    out = Path(os.environ.get("SCREENSHOT_DIR", tmp_path))
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"home-{w}x{h}.png"
    page.screenshot(path=str(path))
    assert path.stat().st_size > 20_000
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_iphone_first_screen_shows_filter_bar_above_tabbar(open_app):
    page = open_app(safe=(59, 0, 34, 0))
    page.goto("http://homeflix.test/")
    page.wait_for_selector("#homeGridContent .movie-card")
    bar_bottom = page.evaluate("document.getElementById('filterBar').getBoundingClientRect().bottom")
    tab_top = page.evaluate("document.querySelector('#floatTabBar .fab-capsule').getBoundingClientRect().top")
    assert bar_bottom <= tab_top + 0.5


def test_tv_controls_use_at_least_20px_text(open_app):
    page = open_app(tv=True)
    page.goto("http://homeflix.test/")
    page.wait_for_selector(".hero-btn-play")
    for sel in ("#filterBar .grid-tab-btn", "#filterBar .filter-btn", ".hero-btn-play", ".hero-badge", "#resultCount"):
        size = page.evaluate("s => parseFloat(getComputedStyle(document.querySelector(s)).fontSize)", sel)
        assert size >= 20, (sel, size)


def test_iphone_logo_stays_below_dynamic_island(open_app):
    page = open_app(safe=(59, 0, 34, 0))
    page.goto("http://homeflix.test/")
    page.wait_for_selector("#homeGridContent .movie-card")
    assert page.evaluate("document.querySelector('#appHeader h1').getBoundingClientRect().top") >= 59
