from tests.ui.conftest import tap
from tests.ui.phimapi_fake import FakePhimapi
from tests.ui.test_library import api, save
from tests.ui.test_three_taps import _open

URL = "http://homeflix.test/"
EVIL = 'Phim "Hay" \\ <img src=x onerror="window.__pwn=1"> `x` ${y}'
PWN = "typeof window.__pwn"


def test_detail_page_renders_hostile_name_and_content_as_text_and_buttons_work(open_app):
    fake = FakePhimapi(names={"phim-le-1": EVIL}, content={"phim-le-1": '<img src=x onerror="window.__pwn=1"><b>hay</b>'})
    page = _open(open_app, fake=fake)
    page.evaluate("showMovieDetailBySlug('phim-le-1')")
    page.wait_for_selector(".detail-action-play")
    assert page.locator(".detail-hero-title").inner_text() == EVIL
    assert page.locator(".detail-overview img").count() == 0 and page.locator(".detail-overview b").count() == 0
    assert page.evaluate(PWN) == "undefined"
    tap(page, "#btnSaveDetail")
    page.wait_for_function("document.getElementById('saveBtnText').textContent === 'Đã lưu'")
    assert next(r for r in api(page, "GET", "/api/saved") if r["slug"] == "phim-le-1")["name"] == EVIL
    tap(page, ".detail-action-play")
    assert page.evaluate("document.body.classList.contains('player-active')")
    assert page.console_errors == []


def test_saved_rows_render_hostile_name_as_text(open_app):
    page = _open(open_app)
    api(page, "POST", "/api/saved", {"slug": "phim-le-1", "name": EVIL, "poster_url": '/p.jpg" onerror="window.__pwn=1', "year": "2026",
                                      "episodes": [{"name": "Tập 1", "link_m3u8": "http://media.test/1.m3u8"}]})
    tap(page, "#fabTabLibrary")
    page.wait_for_selector("#savedMoviesGrid .saved-movie-row")
    assert page.locator("#savedMoviesGrid .movie-title").inner_text() == EVIL
    page.wait_for_timeout(300)
    assert page.evaluate(PWN) == "undefined"


def test_search_results_render_hostile_name_as_text(open_app):
    page = _open(open_app, fake=FakePhimapi(names={"search-1": EVIL}))
    page.evaluate("document.getElementById('searchInput').value = 'x'; searchMovies()")
    page.wait_for_selector("#searchResults .movie-card")
    assert page.locator("#searchResults .movie-title").inner_text() == EVIL
    assert page.evaluate(PWN) == "undefined"


def test_filter_options_render_hostile_name_as_text_and_select(open_app):
    page = _open(open_app, fake=FakePhimapi(categories=[EVIL]))
    page.wait_for_function("filterData.categories && filterData.categories.length")
    tap(page, "#filterBtn-category")
    assert page.locator(".filter-menu.open .filter-option").nth(1).inner_text() == EVIL
    tap(page, ".filter-menu.open .filter-option >> nth=1")
    assert page.evaluate(PWN) == "undefined"
    assert page.evaluate("homeState.all.filters.category") == "cat-0"


def test_hero_save_label_follows_save_made_on_detail_page(open_app):
    page = _open(open_app)
    page.evaluate("showMovieDetailBySlug('phim-chieu-rap-1')")
    page.wait_for_selector("#btnSaveDetail")
    tap(page, "#btnSaveDetail")
    page.wait_for_function("document.getElementById('saveBtnText').textContent === 'Đã lưu'")
    page.evaluate("backToHomeFromDetail()")
    assert page.locator(".hero-btn-save").first.inner_text() == "Đã lưu"


def test_tv_dpad_reaches_hero_save_then_next_slide(open_app):
    page = _open(open_app, tv=True)
    page.focus(".hero-btn-play")
    page.keyboard.press("ArrowRight")
    assert page.evaluate("document.activeElement.classList.contains('hero-btn-save')")
    assert page.evaluate("heroState.index") == 0
    page.keyboard.press("ArrowRight")
    page.wait_for_function("heroState.index === 1")
    assert page.evaluate("document.activeElement.classList.contains('hero-btn-play')")
    page.keyboard.press("ArrowLeft")
    page.wait_for_function("heroState.index === 0")


def test_failed_first_load_is_retried_when_user_switches_tabs(open_app):
    fake = FakePhimapi(fail=["/v1/api/home"])
    page = open_app(fake=fake)
    page.goto(URL)
    page.wait_for_selector(".filter-empty")
    assert "Không tải được" in page.locator(".filter-empty").inner_text()
    fake.fail = ()
    tap(page, ".grid-tab-btn[data-tab=phim-le]")
    page.wait_for_selector("#homeGridContent .movie-card")
    tap(page, ".grid-tab-btn[data-tab=all]")
    page.wait_for_function("document.querySelectorAll('#homeGridContent .movie-card').length > 0 && !document.querySelector('.filter-empty')")


def test_search_overlay_content_below_dynamic_island(open_app):
    page = _open(open_app, safe=(59, 0, 34, 0))
    tap(page, "#fabTabSearch")
    for sel in (".search-overlay-input-wrap", ".search-overlay-close"):
        assert page.locator(sel).bounding_box()["y"] >= 59 - 0.5, sel
