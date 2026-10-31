from tests.ui.conftest import tap
from tests.ui.test_library import progress, save
from tests.ui.test_three_taps import CARD, PLAYER_ACTIVE, URL

LEAD = "#homeContinue .home-cont.is-lead"
UNDO_MS = 5000


def swipe(page, selector, dx, dy=0, ms=240, steps=8):
    """Vuốt bằng ngón tay thật (CDP touch): sự kiện trusted như trên iPhone."""
    box = page.locator(selector).bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    cdp = page.context.new_cdp_session(page)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
    for i in range(1, steps + 1):
        cdp.send("Input.dispatchTouchEvent",
                 {"type": "touchMove", "touchPoints": [{"x": x + dx * i / steps, "y": y + dy * i / steps}]})
        page.wait_for_timeout(ms / steps)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    cdp.detach()


def _with_history(open_app, *slugs, clock=False):
    page = open_app()
    if clock:
        page.clock.install()  # đồng hồ vẫn chạy; fast_forward để nhảy qua cửa sổ hoàn tác
    page.goto(URL)
    page.wait_for_selector(CARD)
    for s in slugs:
        progress(page, s, ep=2, pos=1080, dur=2700, meta=True)
    if slugs:
        page.evaluate("fetchSavedMovies()")
        page.wait_for_selector(LEAD)
    return page


def home_slugs(page):
    return page.evaluate("[...document.querySelectorAll('#homeContinue .home-cont')].map(e => e.dataset.slug)")


def deletes(page):
    return [r for r in page.requests if r.startswith("DELETE ")]


def test_swipe_right_resumes_playback(open_app):
    page = _with_history(open_app, "phim-bo-1")
    swipe(page, LEAD, 160)
    page.wait_for_function(PLAYER_ACTIVE)
    assert page.evaluate("playbackEpisodes[currentPlaybackIndex].name") == "Tập 2"


def test_swipe_left_removes_after_undo_window(open_app):
    page = _with_history(open_app, "phim-le-1", "phim-le-2", clock=True)
    assert home_slugs(page) == ["phim-le-2", "phim-le-1"]
    swipe(page, LEAD, -160)
    page.wait_for_function("document.querySelectorAll('#homeContinue .home-cont').length === 1")
    assert home_slugs(page) == ["phim-le-1"]
    assert page.locator("#undoToast").is_visible()
    assert not page.evaluate(PLAYER_ACTIVE)
    assert deletes(page) == []
    page.clock.fast_forward(UNDO_MS + 100)
    page.wait_for_function("!document.getElementById('undoToast').classList.contains('is-open')")
    assert deletes(page) == ["DELETE /api/saved/phim-le-2/progress"]
    page.evaluate("fetchSavedMovies()")
    page.wait_for_timeout(200)
    assert home_slugs(page) == ["phim-le-1"]


def test_undo_brings_the_card_back_and_sends_nothing(open_app):
    page = _with_history(open_app, "phim-le-1", "phim-le-2", clock=True)
    swipe(page, LEAD, -160)
    page.wait_for_selector("#undoToast.is-open")
    tap(page, "#undoToast .undo-btn")
    page.wait_for_function("document.querySelectorAll('#homeContinue .home-cont').length === 2")
    assert home_slugs(page) == ["phim-le-2", "phim-le-1"]
    page.clock.fast_forward(UNDO_MS + 100)
    assert deletes(page) == []


def test_refresh_during_undo_window_keeps_card_hidden(open_app):
    page = _with_history(open_app, "phim-le-1", "phim-le-2", clock=True)
    swipe(page, LEAD, -160)
    page.wait_for_selector("#undoToast.is-open")
    page.evaluate("fetchSavedMovies()")
    page.wait_for_timeout(200)
    assert home_slugs(page) == ["phim-le-1"]


def test_second_removal_commits_the_first(open_app):
    page = _with_history(open_app, "phim-le-1", "phim-le-2", "phim-le-3", clock=True)
    swipe(page, LEAD, -160)
    page.wait_for_function("document.querySelectorAll('#homeContinue .home-cont').length === 2")
    swipe(page, LEAD, -160)
    page.wait_for_function("document.querySelectorAll('#homeContinue .home-cont').length === 1")
    page.wait_for_timeout(200)
    assert deletes(page) == ["DELETE /api/saved/phim-le-3/progress"]


def test_saved_movie_removed_from_continue_stays_in_library(open_app):
    page = _with_history(open_app, clock=True)
    save(page, "phim-le-1")
    progress(page, "phim-le-1", pos=60, dur=600)
    page.evaluate("fetchSavedMovies()")
    page.wait_for_selector(LEAD)
    swipe(page, LEAD, -160)
    page.wait_for_selector("#undoToast.is-open")
    page.clock.fast_forward(UNDO_MS + 100)
    page.wait_for_function("savedMoviesList.length === 1 && savedMoviesList[0].last_watched_url === ''")
    assert page.locator("#homeContinue").is_hidden()
    page.evaluate("switchTab('saved-tab')")
    page.wait_for_selector("#savedMoviesGrid .saved-movie-row")
    assert page.locator("#continueSection").is_hidden()


def test_short_slow_drag_snaps_back(open_app):
    page = _with_history(open_app, "phim-le-1")
    swipe(page, LEAD, 50, ms=600)
    page.wait_for_timeout(400)
    assert home_slugs(page) == ["phim-le-1"]
    assert not page.evaluate(PLAYER_ACTIVE)
    assert page.evaluate(f"getComputedStyle(document.querySelector('{LEAD}')).transform") in ("none", "matrix(1, 0, 0, 1, 0, 0)")


def test_vertical_scroll_is_not_a_swipe(open_app):
    page = _with_history(open_app, "phim-le-1")
    swipe(page, LEAD, -40, dy=-220)
    page.wait_for_timeout(400)
    assert home_slugs(page) == ["phim-le-1"]
    assert not page.locator("#undoToast.is-open").count()


def test_library_continue_row_swipes_too(open_app):
    page = _with_history(open_app, "phim-le-1", "phim-le-2", clock=True)
    page.evaluate("switchTab('saved-tab')")
    page.wait_for_selector("#continueRow .continue-card")
    swipe(page, "#continueRow .swipe-row:first-child .continue-card", -160)
    page.wait_for_function("document.querySelectorAll('#continueRow .continue-card').length === 1")
    page.clock.fast_forward(UNDO_MS + 100)
    page.wait_for_function("document.querySelectorAll('#homeContinue .home-cont').length === 1")
    assert deletes(page) == ["DELETE /api/saved/phim-le-2/progress"]
