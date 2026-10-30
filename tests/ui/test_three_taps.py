from tests.ui.conftest import tap
from tests.ui.phimapi_fake import LONG_SERIES, FakePhimapi
from tests.ui.test_library import api, eps, progress, save

URL = "http://homeflix.test/"
CARD = "#homeGridContent .movie-card"
PLAYER_ACTIVE = "document.body.classList.contains('player-active')"
SPY = """(() => { const real = window.play; window.play = function (...a) { window.__playSync = true; return real.apply(this, a); }; })()"""


def _open(open_app, **kw):
    page = open_app(**kw)
    page.goto(URL)
    page.wait_for_selector(CARD)
    page.wait_for_function("Object.keys(heroState.details).length >= 2")
    return page


def test_hero_play_is_one_tap_to_playback_and_play_is_called_synchronously(open_app):
    page = _open(open_app)
    page.evaluate(SPY)
    result = page.evaluate("""() => { document.querySelector('.hero-btn-play').click();
        return [document.body.classList.contains('player-active'), window.__playSync === true, window.__hlsAttached === true]; }""")
    assert result == [True, True, True]


def test_hero_play_one_real_tap(open_app):
    page = _open(open_app)
    tap(page, ".hero-btn-play")
    assert page.evaluate(PLAYER_ACTIVE) and page.taps == 1


def test_hero_play_resumes_saved_episode_and_position(open_app):
    page = _open(open_app)
    slug = "phim-chieu-rap-1"
    save(page, slug)
    progress(page, slug, ep=1, pos=100, dur=2700)
    progress(page, slug, ep=2, pos=120, dur=2700)
    page.evaluate("fetchSavedMovies()")
    page.wait_for_function(f"savedMoviesList.some(m => m.slug === '{slug}' && m.position === 120)")
    page.evaluate("document.querySelector('.hero-btn-play').click()")
    assert page.evaluate("playbackEpisodes[currentPlaybackIndex].name") == "Tập 2"
    page.evaluate("Object.defineProperty(video, 'currentTime', {value: 0, writable: true}); video.dispatchEvent(new Event('loadedmetadata'))")
    assert page.evaluate("video.currentTime") == 120


def test_card_then_play_is_two_taps(open_app):
    page = _open(open_app)
    tap(page, CARD)
    page.wait_for_selector(".detail-action-play")
    tap(page, ".detail-action-play")
    assert page.evaluate(PLAYER_ACTIVE) and page.taps == 2


def test_series_card_then_episode_is_two_taps(open_app):
    page = _open(open_app)
    tap(page, CARD)
    page.wait_for_selector("#detailEpisodeList .ep-item")
    tap(page, "#detailEpisodeList .ep-item >> nth=1")
    assert page.evaluate(PLAYER_ACTIVE) and page.taps == 2
    assert page.evaluate("playbackEpisodes[currentPlaybackIndex].name") == "Tập 2"


def _detail_play_box(open_app, slug):
    page = _open(open_app, safe=(59, 0, 34, 0))
    page.evaluate(f"showMovieDetailBySlug('{slug}')")
    page.wait_for_selector(".detail-action-play")
    page.wait_for_selector("#detailEpisodeList .ep-item")
    return page.locator(".detail-action-play").bounding_box()


def test_play_button_position_same_for_3_and_80_episodes(open_app):
    short = _detail_play_box(open_app, "phim-le-1")
    long = _detail_play_box(open_app, LONG_SERIES)
    assert short["y"] == long["y"]
    assert short["y"] + short["height"] <= 932 - 34 - 12 + 1
    assert short["height"] >= 44


def test_detail_back_button_below_dynamic_island(open_app):
    page = _open(open_app, safe=(59, 0, 34, 0))
    page.evaluate("showMovieDetailBySlug('phim-le-1')")
    page.wait_for_selector(".detail-back-btn")
    assert page.locator(".detail-back-btn").bounding_box()["y"] >= 59 + 8


def test_play_before_detail_ready_shows_loading_then_works(open_app):
    page = open_app(delay={"/api/movie/": 0.8})
    page.goto(URL)
    page.wait_for_selector(".hero-btn-play")
    tap(page, ".hero-btn-play")
    assert page.evaluate("document.querySelector('.hero-btn-play').classList.contains('is-loading')")
    assert not page.evaluate(PLAYER_ACTIVE)
    page.wait_for_function("!document.querySelector('.hero-btn-play').classList.contains('is-loading')")
    tap(page, ".hero-btn-play")
    assert page.evaluate(PLAYER_ACTIVE)
    assert page.console_errors == []


def test_save_button_on_hero_toggles_saved_state(open_app):
    page = _open(open_app)
    tap(page, ".hero-btn-save")
    page.wait_for_function("document.querySelector('.hero-btn-save').textContent === 'Đã lưu'")
    assert next(r for r in api(page, "GET", "/api/saved") if r["slug"] == "phim-chieu-rap-1")["saved"] is True
    tap(page, ".hero-btn-save")
    page.wait_for_function("document.querySelector('.hero-btn-save').textContent === 'Lưu'")
    assert not any(r["slug"] == "phim-chieu-rap-1" for r in api(page, "GET", "/api/saved"))
