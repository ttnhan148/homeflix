from tests.ui.conftest import tap
from tests.ui.test_library import progress
from tests.ui.test_three_taps import PLAYER_ACTIVE, _open

LEAD = "#homeContinue .home-cont.is-lead"


def test_continue_section_hidden_without_history(open_app):
    page = _open(open_app)
    assert page.locator("#homeContinue").is_hidden()


def test_lead_card_shows_episode_minutes_left_and_true_progress(open_app):
    page = _open(open_app)
    progress(page, "phim-bo-1", ep=2, pos=1080, dur=2700, meta=True)
    page.evaluate("fetchSavedMovies()")
    page.wait_for_selector(LEAD)
    assert page.locator(f"{LEAD} .continue-title").inner_text() == "phim-bo-1"
    assert page.locator(f"{LEAD} .continue-ep").inner_text() == "Tập 2 · còn 27 phút"
    ratio = page.evaluate(f"""() => document.querySelector('{LEAD} .continue-progress-fill').getBoundingClientRect().width
        / document.querySelector('{LEAD} .continue-progress').getBoundingClientRect().width""")
    assert abs(ratio - 0.4) < 0.02


def test_lead_card_resumes_in_one_tap(open_app):
    page = _open(open_app)
    progress(page, "phim-bo-1", ep=2, pos=1080, dur=2700, meta=True)
    page.evaluate("fetchSavedMovies()")
    page.wait_for_selector(LEAD)
    tap(page, LEAD)
    assert page.evaluate(PLAYER_ACTIVE) and page.taps == 1
    assert page.evaluate("playbackEpisodes[currentPlaybackIndex].name") == "Tập 2"


def test_home_lists_at_most_three(open_app):
    page = _open(open_app)
    for n in (1, 2, 3, 4):
        progress(page, f"phim-le-{n}", pos=60, dur=600, meta=True)
    page.evaluate("fetchSavedMovies()")
    page.wait_for_selector(LEAD)
    assert page.locator("#homeContinue .home-cont").count() == 3
