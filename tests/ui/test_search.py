from tests.ui.conftest import tap
from tests.ui.phimapi_fake import FakePhimapi
from tests.ui.test_three_taps import _open


def test_search_results_render_with_short_meta_and_no_type_badge(open_app):
    page = _open(open_app)
    tap(page, "#fabTabSearch")
    page.fill("#searchOverlayInput", "abc")
    page.keyboard.press("Enter")
    page.wait_for_selector("#searchResults .movie-card")
    assert page.locator("#searchResults .movie-card .movie-meta").first.inner_text() == "Full · 2026"
    assert page.locator("#searchResults .badge-type").count() == 0
    assert page.console_errors == []


def test_search_failure_shows_short_message(open_app):
    page = _open(open_app, fake=FakePhimapi(fail=["/v1/api/tim-kiem"]))
    tap(page, "#fabTabSearch")
    page.fill("#searchOverlayInput", "abc")
    page.keyboard.press("Enter")
    page.wait_for_selector("#searchResults .filter-empty:not([role=status])")
    assert page.locator("#searchResults .filter-empty").inner_text() in ("Không có phim phù hợp.", "Không tìm được. Thử lại sau.")
