from pathlib import Path

CSS = Path(__file__).resolve().parents[2] / "static" / "hf.css"


def test_glass_class_has_blur(open_app):
    page = open_app()
    page.goto("http://homeflix.test/")
    page.evaluate("document.body.insertAdjacentHTML('beforeend', '<div id=g class=glass>x</div>')")
    assert "blur" in page.evaluate("getComputedStyle(document.getElementById('g')).backdropFilter")


def test_safe_vars_follow_override(open_app):
    page = open_app(safe=(59, 0, 34, 0))
    page.goto("http://homeflix.test/")
    get = "n => getComputedStyle(document.documentElement).getPropertyValue(n).trim()"
    assert page.evaluate(get, "--safe-top") == "59px"
    assert page.evaluate(get, "--safe-bottom") == "34px"


def test_safe_vars_default_to_env():
    css = CSS.read_text()
    for side in ("top", "right", "bottom", "left"):
        assert f"--safe-{side}: env(safe-area-inset-{side}, 0px)" in css


def test_fallback_rule_for_missing_backdrop_filter_exists():
    css = CSS.read_text()
    assert "@supports not" in css and "backdrop-filter" in css.split("@supports not", 1)[1]


def test_tv_font_and_focus_ring(open_app):
    page = open_app(tv=True)
    page.goto("http://homeflix.test/")
    page.wait_for_selector(".movie-card")
    assert page.evaluate("document.body.classList.contains('device-tv')")
    assert page.evaluate("getComputedStyle(document.body).fontSize") == "20px"
    page.focus(".movie-card")
    assert page.evaluate("document.activeElement.matches(':focus-visible')")
    page.wait_for_timeout(400)  # thẻ có transition 0.2s trên mọi thuộc tính
    style = page.evaluate("(() => { const s = getComputedStyle(document.activeElement); return [s.outlineWidth, s.outlineColor]; })()")
    assert style == ["4px", "rgb(255, 255, 255)"]
