import json

from tests.ui.conftest import tap

URL = "http://homeflix.test/"


def eps(slug):
    return [{"name": f"Tập {i}", "link_m3u8": f"http://media.test/{slug}/{i}.m3u8"} for i in (1, 2, 3)]


def api(page, method, path, body=None):
    return page.evaluate(
        "async ([m, p, b]) => (await fetch(p, {method: m, headers: {'Content-Type': 'application/json'}, body: b ? JSON.stringify(b) : undefined})).json()",
        [method, path, body])


def save(page, slug):
    api(page, "POST", "/api/saved", {"slug": slug, "name": slug, "poster_url": "/p.jpg", "year": "2026", "episodes": eps(slug)})


def progress(page, slug, ep=1, pos=None, dur=None, meta=False):
    body = {"slug": slug, "last_watched_episode": f"Tập {ep}", "last_watched_url": eps(slug)[ep - 1]["link_m3u8"]}
    if pos is not None:
        body.update(position=pos, duration=dur)
    if meta:
        body.update(name=slug, poster_url="/p.jpg", year="2026", episodes=eps(slug))
    return api(page, "POST", "/api/saved/progress", body)


def _open(open_app, **kw):
    page = open_app(**kw)
    page.goto(URL)
    page.wait_for_selector("#homeGridContent .movie-card")
    return page


def slugs(page, sel):
    return page.evaluate(f"[...document.querySelectorAll('{sel}')].map(e => e.dataset.slug)")


def test_playing_unsaved_movie_creates_history_but_not_saved(open_app):
    page = _open(open_app)
    page.evaluate("showMovieDetailBySlug('phim-le-1')")
    page.wait_for_selector(".detail-action-play")
    tap(page, ".detail-action-play")
    page.wait_for_function("fetch('/api/saved').then(r => r.json()).then(rows => rows.some(r => r.slug === 'phim-le-1'))")
    row = next(r for r in api(page, "GET", "/api/saved") if r["slug"] == "phim-le-1")
    assert row["saved"] is False and row["name"] == "phim-le-1" and len(row["episodes"]) == 3
    assert page.evaluate("checkIsSaved('phim-le-1')") is False
    assert page.locator("#saveBtnText").inner_text() == "Lưu phim"


def test_library_splits_continue_and_saved(open_app):
    page = _open(open_app)
    save(page, "phim-le-2")                                   # chỉ lưu
    save(page, "phim-le-1"); progress(page, "phim-le-1", pos=50, dur=200)   # lưu và đã xem
    progress(page, "phim-bo-1", pos=10, dur=200, meta=True)   # chỉ lịch sử
    tap(page, "#fabTabLibrary")
    page.wait_for_selector(".continue-card")
    assert slugs(page, ".continue-card") == ["phim-bo-1", "phim-le-1"]
    assert sorted(slugs(page, "#savedMoviesGrid .saved-movie-row")) == ["phim-le-1", "phim-le-2"]


def test_continue_card_shows_progress_bar_and_resumes_in_two_taps(open_app):
    page = _open(open_app)
    progress(page, "phim-bo-1", pos=50, dur=200, meta=True)
    tap(page, "#fabTabLibrary")
    page.wait_for_selector(".continue-card .continue-progress-fill")
    ratio = page.evaluate("""() => { const t = document.querySelector('.continue-card .continue-progress').getBoundingClientRect().width;
        return document.querySelector('.continue-card .continue-progress-fill').getBoundingClientRect().width / t; }""")
    assert abs(ratio - 0.25) < 0.02
    tap(page, ".continue-card")
    assert page.evaluate("document.body.classList.contains('player-active')")
    assert page.taps == 2


def test_save_after_watching_keeps_single_entry(open_app):
    page = _open(open_app)
    progress(page, "phim-bo-1", pos=50, dur=200, meta=True)
    page.evaluate("fetchSavedMovies()")
    page.evaluate("showMovieDetailBySlug('phim-bo-1')")
    page.wait_for_selector("#btnSaveDetail")
    assert page.locator("#saveBtnText").inner_text() == "Lưu phim"
    tap(page, "#btnSaveDetail")
    page.wait_for_function("document.getElementById('saveBtnText').textContent === 'Đã lưu'")
    assert page.evaluate("savedMoviesList.filter(m => m.slug === 'phim-bo-1').length") == 1
    assert next(r for r in api(page, "GET", "/api/saved") if r["slug"] == "phim-bo-1")["position"] == 50


def test_delete_saved_movie_removes_it_from_both_lists(open_app):
    page = _open(open_app)
    save(page, "phim-le-1"); progress(page, "phim-le-1", pos=50, dur=200)
    tap(page, "#fabTabLibrary")
    page.wait_for_selector("#savedMoviesGrid .saved-movie-row")
    page.locator("#savedMoviesGrid .saved-movie-row .btn-sec").first.tap()
    page.wait_for_function("document.querySelectorAll('.continue-card, .saved-movie-row').length === 0")
    assert page.is_visible("#libraryEmpty")


def test_empty_library_message(open_app):
    page = _open(open_app)
    tap(page, "#fabTabLibrary")
    page.wait_for_selector("#libraryEmpty:not([hidden])")
    assert "Tủ phim trống" in page.locator("#libraryEmpty").inner_text()
    assert not page.is_visible("#continueSection") and not page.is_visible("#savedSection")
