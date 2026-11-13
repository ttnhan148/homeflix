"""Episodes drawer under the player: long series must stay reachable, collapse must still work."""
from tests.ui.phimapi_fake import LONG_SERIES
from tests.ui.test_three_taps import _open

TRANSITION_DONE = "!document.getElementById('episodesDrawerEl').getAnimations().length"


def _play_long_series(open_app, **kw):
    page = _open(open_app, **kw)
    page.evaluate(f"showMovieDetailBySlug('{LONG_SERIES}')")
    page.wait_for_selector("#detailEpisodeList .ep-item")
    page.evaluate("document.querySelector('.detail-action-play').click()")
    page.wait_for_function("document.body.classList.contains('player-active')")
    page.wait_for_function("document.querySelectorAll('#drawerEpGrid .ep-item').length === 80")
    return page


def test_last_episode_reachable_in_expanded_drawer(open_app):
    page = _play_long_series(open_app)
    page.wait_for_function(TRANSITION_DONE)
    # Scroll like a user (wheel over the episode list), not scrollIntoView: script can scroll
    # overflow:hidden boxes, users cannot. The last episode must become visible inside the drawer.
    box = page.locator("#drawerEpGrid").bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 60)
    for _ in range(15):
        page.mouse.wheel(0, 400)
    page.wait_for_timeout(300)
    visible = page.evaluate("""() => {
        const items = document.querySelectorAll('#drawerEpGrid .ep-item');
        const r = items[items.length - 1].getBoundingClientRect();
        const d = document.getElementById('episodesDrawerEl').getBoundingClientRect();
        return r.bottom <= Math.min(d.bottom, innerHeight) + 0.5 && r.top >= d.top - 0.5;
    }""")
    assert visible


def test_collapsed_drawer_shows_only_toggle(open_app):
    page = _play_long_series(open_app)
    page.click(".episodes-drawer-toggle")
    page.wait_for_function("document.getElementById('episodesDrawerEl').classList.contains('collapsed')")
    page.wait_for_function(TRANSITION_DONE)
    drawer_h = page.evaluate("document.getElementById('episodesDrawerEl').getBoundingClientRect().height")
    toggle_h = page.evaluate("document.querySelector('.episodes-drawer-toggle').getBoundingClientRect().height")
    assert abs(drawer_h - toggle_h) <= 1  # exactly the toggle: nothing below it, and the toggle itself not clipped
    assert page.get_attribute(".episodes-drawer-toggle", "aria-expanded") == "false"


def test_rebuilt_drawer_toggles_on_first_tap(open_app):
    page = _play_long_series(open_app)
    page.click(".episodes-drawer-toggle")   # collapse, then rebuild (next episode / new playback)
    page.evaluate("playEpisodesListDirect(playbackEpisodes, 1)")   # rebuilds the drawer (expanded)
    page.wait_for_function("document.getElementById('episodesDrawerEl').classList.contains('expanded')")
    page.click(".episodes-drawer-toggle")
    assert page.evaluate("document.getElementById('episodesDrawerEl').classList.contains('collapsed')")


def test_expanded_toggle_reports_aria_expanded(open_app):
    page = _play_long_series(open_app)
    assert page.get_attribute(".episodes-drawer-toggle", "aria-expanded") == "true"
