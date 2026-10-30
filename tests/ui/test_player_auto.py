import json
import re

from tests.ui.conftest import tap
from tests.ui.test_cast_receiver import FAST, PLAYER_ACTIVE, URL, cast_play, fake_playhead

CFG = "window.__HF_PLAYER_CONFIG = {tickMs: 20};"
OVERLAY = "#autoNextOverlay"


def open_player(open_app, prefs=None, episodes=None):
    """TV đang phát phim-le-1 tập 1 (3 tập). `episodes` rút gọn danh sách tập để thử phim lẻ/tập cuối."""
    page = open_app(tv=True)
    page.add_init_script(FAST + CFG)
    if prefs is not None:
        page.add_init_script("localStorage.setItem('hf_player_prefs', %s);" % json.dumps(json.dumps(prefs)))
    page.goto(URL)
    page.wait_for_function("window.HFCast && HFCast.started === true && window.HFPlayerAuto")
    cast_play(page)
    if episodes is not None:
        page.evaluate("n => { playbackEpisodes = playbackEpisodes.slice(0, n); }", episodes)
    return page


def ended(page):
    page.evaluate("video.dispatchEvent(new Event('ended'))")


def index(page):
    return page.evaluate("currentPlaybackIndex")


def test_countdown_then_next(open_app):
    page = open_player(open_app)
    ended(page)
    page.wait_for_selector(OVERLAY + ":not([hidden])")
    assert re.fullmatch(r"Tập kế sau \d+ giây", page.locator("#autoNextText").inner_text())
    page.wait_for_function("currentPlaybackIndex === 1")
    assert page.locator(OVERLAY).is_hidden()


def test_cancel_stops_next(open_app):
    page = open_player(open_app)
    ended(page)
    page.wait_for_selector(OVERLAY + ":not([hidden])")
    tap(page, "#autoNextCancel")
    assert page.locator(OVERLAY).is_hidden()
    page.wait_for_timeout(400)  # 10 tick x 20ms đã qua rất nhiều lần
    assert index(page) == 0


def test_play_now_skips_wait(open_app):
    page = open_player(open_app)
    ended(page)
    page.wait_for_selector(OVERLAY + ":not([hidden])")
    tap(page, "#autoNextNow")
    assert index(page) == 1 and page.locator(OVERLAY).is_hidden()


def test_last_episode_no_countdown(open_app):
    page = open_player(open_app, episodes=1)
    ended(page)
    page.wait_for_timeout(100)
    assert page.locator(OVERLAY).is_hidden() and index(page) == 0 and page.console_errors == []


def test_single_movie_no_countdown(open_app):
    page = open_player(open_app, episodes=1)
    ended(page)
    page.wait_for_timeout(300)
    assert page.locator(OVERLAY).is_hidden() and page.evaluate(PLAYER_ACTIVE)


def test_manual_seek_cancels(open_app):
    page = open_player(open_app)
    ended(page)
    page.wait_for_selector(OVERLAY + ":not([hidden])")
    page.evaluate("video.dispatchEvent(new Event('seeking'))")
    assert page.locator(OVERLAY).is_hidden()
    page.wait_for_timeout(400)
    assert index(page) == 0


def test_exit_player_cancels(open_app):
    page = open_player(open_app)
    ended(page)
    page.wait_for_selector(OVERLAY + ":not([hidden])")
    page.evaluate("exitPlayer()")
    page.wait_for_timeout(400)
    assert not page.evaluate(PLAYER_ACTIVE) and page.locator(OVERLAY).is_hidden()


def test_manual_episode_change_cancels(open_app):
    page = open_player(open_app)
    ended(page)
    page.wait_for_selector(OVERLAY + ":not([hidden])")
    page.evaluate("play(playbackEpisodes[2].link_m3u8, playbackEpisodes[2].name, 2)")
    page.wait_for_timeout(400)
    assert index(page) == 2 and page.locator(OVERLAY).is_hidden()


def test_autonext_off_no_countdown(open_app):
    page = open_player(open_app, prefs={"autoNext": False})
    ended(page)
    page.wait_for_timeout(300)
    assert page.locator(OVERLAY).is_hidden() and index(page) == 0


SKIP = "#skipIntroBtn"


def set_time(page, t, duration=1400):
    page.evaluate("""([t, d]) => {
        Object.defineProperty(video, 'duration', {configurable: true, value: d});
        Object.defineProperty(video, 'currentTime', {configurable: true, writable: true, value: t});
        video.dispatchEvent(new Event('timeupdate'));
    }""", [t, duration])


def test_skip_button_visible_first_90s(open_app):
    page = open_player(open_app)
    set_time(page, 10)
    assert page.locator(SKIP).is_visible()
    set_time(page, 95)
    assert page.locator(SKIP).is_hidden()


def test_skip_jumps_to_85(open_app):
    page = open_player(open_app)
    set_time(page, 10)
    tap(page, SKIP)
    assert page.evaluate("video.currentTime") == 85
    assert page.locator(SKIP).is_hidden()


def test_skip_uses_custom_seconds(open_app):
    page = open_player(open_app, prefs={"introSeconds": 60})
    set_time(page, 10)
    tap(page, SKIP)
    assert page.evaluate("video.currentTime") == 60


def test_short_video_hides_button(open_app):
    page = open_player(open_app)
    set_time(page, 10, duration=60)
    assert page.locator(SKIP).is_hidden()


def test_button_hides_when_no_metadata(open_app):
    page = open_player(open_app)
    page.evaluate("video.dispatchEvent(new Event('timeupdate'))")  # duration chưa có (NaN)
    assert page.locator(SKIP).is_hidden()


def test_auto_intro_skips_once_per_episode(open_app):
    page = open_player(open_app, prefs={"autoIntro": True})
    set_time(page, 3)
    assert page.evaluate("video.currentTime") == 85
    set_time(page, 5)                                    # tua lùi: không nhảy lần hai
    assert page.evaluate("video.currentTime") == 5
    page.evaluate("play(playbackEpisodes[1].link_m3u8, playbackEpisodes[1].name, 1)")
    set_time(page, 3)
    assert page.evaluate("video.currentTime") == 85
