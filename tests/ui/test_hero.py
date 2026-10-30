import pytest

from tests.ui.conftest import tap
from tests.ui.phimapi_fake import FakePhimapi

URL = "http://homeflix.test/"
FIRST = "phim-chieu-rap-1"


def _visible(page, sel):
    return page.evaluate("s => { const e = document.querySelector(s); return !!e && getComputedStyle(e).display !== 'none' && e.getBoundingClientRect().height > 0; }", sel)


def _open(open_app, **kw):
    page = open_app(**kw)
    page.goto(URL)
    return page


def _details_ready(page, n=1):
    page.wait_for_function(f"Object.keys(heroState.details).length >= {n}")


def test_hero_shows_five_chieu_rap_cards_under_one_heading(open_app):
    page = _open(open_app)
    page.wait_for_selector(".hero-slide")
    assert page.locator(".hero-slide").count() == 5
    assert page.locator("#heroHeading").inner_text() == "Đang chiếu rạp"
    assert page.locator(".hero-badge").count() == 0
    assert "/proxy/image" in page.locator(".hero-slide img.hero-bg").first.get_attribute("src")
    assert page.locator(".hero-slide .hero-title").first.inner_text() == FIRST


def test_hero_text_stays_below_dynamic_island_and_above_tabbar(open_app):
    page = _open(open_app, safe=(59, 0, 34, 0))
    page.wait_for_selector(".hero-slide")
    _details_ready(page)
    rects = page.evaluate("""() => [...document.querySelectorAll('.hero-slide')[0].querySelectorAll('.hero-content, .hero-content *')]
        .map(e => e.getBoundingClientRect()).filter(r => r.height > 0).map(r => [r.top, r.bottom])""")
    assert rects
    for top, bottom in rects:
        assert top >= 59 - 1
        assert bottom <= 932 - (34 + 8 + 64 + 16) + 2


def test_hero_prefetches_current_and_next_detail_only(open_app):
    page = _open(open_app)
    page.wait_for_selector(".hero-slide")
    _details_ready(page, 2)
    movie = lambda n: f"GET /api/movie/phim-chieu-rap-{n}"
    assert movie(1) in page.requests and movie(2) in page.requests
    assert movie(5) not in page.requests
    page.evaluate("(() => { const t = document.getElementById('heroTrack'); const s = t.querySelectorAll('.hero-slide');"
                  " t.scrollTo({left: s[3].offsetLeft - s[0].offsetLeft, behavior: 'instant'}); })()")
    page.wait_for_function("heroState.index === 3")
    _details_ready(page, 4)
    assert movie(4) in page.requests and movie(5) in page.requests


def test_hero_meta_is_short_and_content_is_never_injected(open_app):
    fake = FakePhimapi(content={FIRST: '<p>Mô tả &amp; hay</p><img src=x onerror="window.__pwn=1">'})
    page = _open(open_app, fake=fake)
    _details_ready(page)
    page.wait_for_function("document.querySelector('.hero-meta').textContent.includes('Mỹ')")
    assert page.locator(".hero-meta").first.inner_text() == "2026 · Hành Động · Mỹ"
    assert page.locator(".hero-desc").count() == 0
    assert page.evaluate("typeof window.__pwn") == "undefined"


def test_hero_survives_detail_failure(open_app):
    page = _open(open_app, fake=FakePhimapi(fail=["/phim/"]))
    page.wait_for_selector(".hero-slide")
    page.wait_for_timeout(500)
    assert page.locator(".hero-title").first.inner_text() == FIRST
    assert page.locator(".hero-meta").first.inner_text() == "2026"
    assert page.console_errors == []


def test_hero_falls_back_to_phim_le_when_chieu_rap_fails(open_app):
    page = _open(open_app, fake=FakePhimapi(fail=["danh-sach/phim-chieu-rap"]))
    page.wait_for_selector(".hero-slide")
    assert page.locator(".hero-title").first.inner_text() == "phim-le-1"


def test_hero_hidden_when_no_movies(open_app):
    page = _open(open_app, fake=FakePhimapi(fail=["danh-sach/phim-chieu-rap", "danh-sach/phim-le"]))
    page.wait_for_function("heroState.loaded === true")
    assert not _visible(page, "#hero")
    assert page.console_errors == []


def test_single_slide_hides_dots(open_app):
    page = _open(open_app, fake=FakePhimapi(counts={"phim-chieu-rap": 1}))
    page.wait_for_selector(".hero-slide")
    assert page.locator(".hero-slide").count() == 1
    assert not _visible(page, "#heroDots")


def test_hero_title_with_quotes_and_backticks_is_intact_and_save_works(open_app):
    name = "Phim \"A\" & 'B' `C`"
    page = _open(open_app, fake=FakePhimapi(names={FIRST: name}))
    page.wait_for_selector(".hero-slide")
    _details_ready(page)
    assert page.locator(".hero-title").first.inner_text() == name
    tap(page, ".hero-slide:first-child .hero-btn-save")
    page.wait_for_function("document.querySelector('.hero-btn-save').textContent.includes('Đã lưu')")
    assert "POST /api/saved" in page.requests
    assert page.console_errors == []


def test_tv_hero_arrow_keys_change_slide_and_buttons_focusable(open_app):
    page = _open(open_app, tv=True)
    page.wait_for_selector(".hero-slide")
    page.focus(".hero-btn-play")
    assert page.evaluate("document.activeElement.classList.contains('hero-btn-play')")
    page.keyboard.press("ArrowRight")  # Phát -> Lưu
    page.keyboard.press("ArrowRight")  # Lưu -> slide kế
    page.wait_for_function("heroState.index === 1")
    page.focus(".hero-btn-save")
    assert page.evaluate("document.activeElement.classList.contains('hero-btn-save')")
