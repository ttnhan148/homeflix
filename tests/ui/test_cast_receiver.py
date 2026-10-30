import json

from tests.ui.conftest import tap
from tests.ui.test_library import api, progress

URL = "http://homeflix.test/"
HOST = "http://homeflix.test"
FAST = "window.__HF_CAST_CONFIG = {heartbeatMs: 100000, reconnectBaseMs: 20, reconnectMaxMs: 80, silenceMs: 400, stateMs: 50};"
PLAYER_ACTIVE = "document.body.classList.contains('player-active')"
EVIL = '<img src=x onerror="window.__pwn=1">'


def open_tv(open_app, tv=True):
    page = open_app(tv=tv)
    page.add_init_script(FAST)
    page.seen = []
    page.on("request", lambda r: page.seen.append((r.method, r.url.replace(HOST, ""), r.post_data)))
    page.goto(URL)
    page.wait_for_function("window.HFCast && HFCast.started === true")
    return page


def posts(page, path):
    return [json.loads(b) if b else None for m, u, b in page.seen if m in ("POST", "PATCH") and u == path]


def wait_posts(page, path, count=1, timeout=3000):
    for _ in range(timeout // 50):
        if len(posts(page, path)) >= count:
            return posts(page, path)
        page.wait_for_timeout(50)
    raise AssertionError(f"expected {count} POST/PATCH to {path}, saw {posts(page, path)}")


def cast_play(page, slug="phim-le-1", episode_url=None, position=None):
    page.es_emit("command", {"type": "play", "slug": slug, "episode_url": episode_url, "position": position})
    page.wait_for_function(PLAYER_ACTIVE)


def fake_playhead(page, time=100):
    page.evaluate("t => { Object.defineProperty(video, 'currentTime', {configurable: true, value: t, writable: true}); }", time)


def test_play_command_starts_playback_from_tv_own_progress(open_app):
    page = open_app(tv=True)
    page.goto(URL)
    progress(page, "phim-le-1", ep=2, pos=120, dur=2700, meta=True)
    page = open_tv_existing(page)
    cast_play(page)
    assert page.evaluate("playbackEpisodes[currentPlaybackIndex].name") == "Tập 2"
    fake_playhead(page, 0)
    page.evaluate("video.dispatchEvent(new Event('loadedmetadata'))")
    assert page.evaluate("video.currentTime") == 120


def open_tv_existing(page):
    page.add_init_script(FAST)
    page.seen = []
    page.on("request", lambda r: page.seen.append((r.method, r.url.replace(HOST, ""), r.post_data)))
    page.reload()
    page.wait_for_function("window.HFCast && HFCast.started === true")
    return page


def test_play_command_with_episode_and_position_overrides(open_app):
    page = open_tv(open_app)
    cast_play(page, episode_url="http://media.test/phim-le-1/3.m3u8", position=30)
    assert page.evaluate("playbackEpisodes[currentPlaybackIndex].name") == "Tập 3"
    fake_playhead(page, 0)
    page.evaluate("video.dispatchEvent(new Event('loadedmetadata'))")
    assert page.evaluate("video.currentTime") == 30
    assert page.evaluate("castStartAt") is None


def test_pause_resume_seek_episode_stop_commands(open_app):
    page = open_tv(open_app)
    cast_play(page)
    page.es_emit("command", {"type": "pause"})
    assert page.evaluate("window.__pauses") == 1
    plays = page.evaluate("window.__plays || 0")
    page.es_emit("command", {"type": "resume"})
    assert page.evaluate("window.__plays || 0") == plays + 1
    fake_playhead(page, 100)
    page.es_emit("command", {"type": "seek", "delta": 10})
    assert page.evaluate("video.currentTime") == 110
    page.es_emit("command", {"type": "seek", "delta": -500})
    assert page.evaluate("video.currentTime") == 0
    page.es_emit("command", {"type": "seek", "position": 42})
    assert page.evaluate("video.currentTime") == 42
    page.es_emit("command", {"type": "episode", "delta": 1})
    assert page.evaluate("playbackEpisodes[currentPlaybackIndex].name") == "Tập 2"
    page.es_emit("command", {"type": "episode", "delta": -1})
    page.es_emit("command", {"type": "episode", "delta": -1})
    assert page.evaluate("playbackEpisodes[currentPlaybackIndex].name") == "Tập 1"
    for _ in range(4):
        page.es_emit("command", {"type": "episode", "delta": 1})
    assert page.evaluate("playbackEpisodes[currentPlaybackIndex].name") == "Tập 3"
    page.es_emit("command", {"type": "stop"})
    assert not page.evaluate(PLAYER_ACTIVE)


def test_commands_without_playback_are_harmless(open_app):
    page = open_tv(open_app)
    for cmd in ({"type": "pause"}, {"type": "seek", "delta": 5}, {"type": "episode", "delta": 1}, {"type": "stop"}):
        page.es_emit("command", cmd)
    assert not page.evaluate(PLAYER_ACTIVE) and page.console_errors == []


def test_options_command_updates_player_prefs(open_app):
    page = open_tv(open_app)
    page.es_emit("command", {"type": "options", "autoNext": True})
    page.es_emit("command", {"type": "options", "autoIntro": True, "autoNext": False})
    prefs = page.evaluate("HFPlayerAuto.getPrefs()")
    assert [prefs["autoNext"], prefs["autoIntro"]] == [False, True]


def test_autoplay_block_shows_prompt_and_reports_not_playing(open_app):
    page = open_tv(open_app)
    page.es_emit("controller_joined", {"controller_id": "c1", "name": "iPhone"})
    cast_play(page)
    page.evaluate("HTMLMediaElement.prototype.play = function () { return Promise.reject(new DOMException('blocked', 'NotAllowedError')); }; 0")
    page.evaluate("video.play().catch(() => {})")
    page.wait_for_selector("#castAutoplayPrompt:not([hidden])")
    assert page.evaluate("document.activeElement.id") == "castAutoplayBtn"
    states = wait_posts(page, "/api/cast/state")
    assert states[-1]["playing"] is False
    page.evaluate("HTMLMediaElement.prototype.play = function () { window.__plays = (window.__plays || 0) + 1; return Promise.resolve(); }; 0")
    before = page.evaluate("window.__plays || 0")
    tap(page, "#castAutoplayBtn")
    assert page.evaluate("window.__plays || 0") == before + 1
    assert page.locator("#castAutoplayPrompt").is_hidden()


def test_connect_request_shows_confirm_with_text_only_and_answers(open_app):
    page = open_tv(open_app)
    page.es_emit("connect_request", {"controller_id": "ctl-1", "controller_name": EVIL})
    page.wait_for_selector("#castConfirm:not([hidden])")
    assert EVIL in page.locator("#castConfirmText").inner_text() and "muốn kết nối" in page.locator("#castConfirmText").inner_text()
    assert page.evaluate("window.__pwn === undefined")
    assert page.evaluate("document.activeElement.id") == "castAllow"
    tap(page, "#castAllow")
    assert posts(page, "/api/cast/requests/ctl-1") == [{"allow": True}]
    assert page.locator("#castConfirm").is_hidden()
    page.es_emit("connect_request", {"controller_id": "ctl-2", "controller_name": "Máy khác"})
    page.wait_for_selector("#castConfirm:not([hidden])")
    tap(page, "#castDeny")
    assert posts(page, "/api/cast/requests/ctl-2") == [{"allow": False}]


def test_second_request_waits_for_first_answer(open_app):
    page = open_tv(open_app)
    page.es_emit("connect_request", {"controller_id": "a", "controller_name": "A"})
    page.es_emit("connect_request", {"controller_id": "b", "controller_name": "B"})
    assert "A" in page.locator("#castConfirmText").inner_text() and "B muốn" not in page.locator("#castConfirmText").inner_text()
    tap(page, "#castAllow")
    page.wait_for_function("document.getElementById('castConfirmText').textContent.startsWith('B')")


def test_label_follows_controllers_and_playback_continues_when_they_leave(open_app):
    page = open_tv(open_app)
    cast_play(page)
    assert page.locator("#castLabel").is_hidden()
    page.es_emit("controller_joined", {"controller_id": "c1", "name": "iPhone"})
    assert page.locator("#castLabel").inner_text() == "iPhone đang kết nối"
    page.es_emit("controller_left", {"controller_id": "c1"})
    assert page.locator("#castLabel").is_hidden()
    assert page.evaluate(PLAYER_ACTIVE)


def test_hello_restores_label_for_existing_controllers(open_app):
    page = open_tv(open_app)
    page.es_emit("hello", {"device_id": "tv", "controllers": [{"id": "c1", "name": "iPhone"}]})
    assert page.locator("#castLabel").inner_text() == "iPhone đang kết nối"


def test_state_is_reported_while_playing_with_controller_and_not_without(open_app):
    page = open_tv(open_app)
    cast_play(page)
    page.evaluate("Object.defineProperty(video, 'paused', {configurable: true, get: () => false})")
    page.wait_for_timeout(300)
    assert posts(page, "/api/cast/state") == []                       # chưa có controller: không báo
    page.es_emit("controller_joined", {"controller_id": "c1", "name": "iPhone"})
    page.wait_for_timeout(300)
    states = posts(page, "/api/cast/state")
    assert len(states) >= 3 and states[-1]["slug"] == "phim-le-1" and states[-1]["episode"] == "Tập 1" and states[-1]["playing"] is True
    assert states[-1]["title"] == "phim-le-1"
    page.es_emit("command", {"type": "stop"})
    page.wait_for_timeout(100)
    assert posts(page, "/api/cast/state")[-1]["slug"] is None and posts(page, "/api/cast/state")[-1]["playing"] is False


def test_progress_goes_to_tv_device_not_phone(open_app):
    page = open_tv(open_app)
    cast_play(page)
    page.wait_for_function("fetch('/api/saved').then(r => r.json()).then(rows => rows.some(r => r.slug === 'phim-le-1'))")
    assert any(m == "POST" and u == "/api/saved/progress" for m, u, _ in page.seen)
    assert next(r for r in api(page, "GET", "/api/saved") if r["slug"] == "phim-le-1")["saved"] is False


def test_settings_toggle_roundtrip_and_tv_focus_ring(open_app):
    page = open_tv(open_app)
    tap(page, "#castSettingsBtn")
    page.wait_for_selector("#castSettings:not([hidden])")
    page.wait_for_function("document.getElementById('castAllowControl').checked === true")
    tap(page, "#castAllowControl")
    assert wait_posts(page, "/api/cast/settings") == [{"allow_control": False}]
    assert api(page, "GET", "/api/cast/settings") == {"allow_control": False}
    page.focus("#castAllowControl")
    page.wait_for_timeout(400)
    assert page.evaluate("getComputedStyle(document.activeElement).outlineWidth") == "4px"


def test_receiver_inactive_on_iphone(open_app):
    page = open_tv(open_app, tv=False)
    page.evaluate("HFCast.emit('command', {type: 'play', slug: 'phim-le-1', episode_url: null, position: null})")
    page.wait_for_timeout(300)
    assert not page.evaluate(PLAYER_ACTIVE)
    assert page.locator("#castSettingsBtn").is_hidden()
