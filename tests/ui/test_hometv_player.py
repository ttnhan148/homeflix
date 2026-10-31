"""HomeTV: player, stream dự phòng, điều khiển bằng remote TV."""
import hometv
from tests.hometv.test_hometv_catalog import seed_channel, seed_stream
from tests.ui.conftest import tap

URL = "http://homeflix.test/"


def seed(channels=1, streams=2, direct=False):
    c = hometv.db.conn()
    c.execute("UPDATE sources SET rights_status='approved' WHERE id='doubo-vn'")
    sid = 0
    for ch in range(1, channels + 1):
        seed_channel(c, ch, "Kênh %03d" % ch, "News")
        for k in range(streams):
            sid += 1
            ext = "live.ts" if direct else "index.m3u8"
            seed_stream(c, sid, ch, "doubo-vn", "https://cdn/%d/%d/%s" % (ch, k, ext))


def open_tv(open_app, clock=False, **kw):
    page = open_app(**kw)
    if clock:
        page.clock.install()
    page.goto(URL)
    tap(page, "#hometvToggle")
    page.wait_for_selector("#tvGrid .tv-card")
    return page


def play(page, channel_id=1):
    tap(page, '#tvGrid .tv-card[data-id="%d"]' % channel_id)
    page.wait_for_function("window.__hlsSource !== undefined")


def source(page):
    return page.evaluate("window.__hlsSource")


def fatal(page):
    page.evaluate("window.__hls.handlers.error({}, {fatal: true, type: 'networkError', details: 'x'})")


def loaded(page):
    page.evaluate("document.getElementById('tvVideo').dispatchEvent(new Event('loadeddata'))")


def test_opens_player_and_attaches_first_stream(app_module, open_app):
    seed()
    page = open_tv(open_app)
    play(page)
    assert source(page) == "/api/hometv/play/1/index.m3u8"
    assert page.is_visible("#tvPlayer") and page.evaluate("window.__hlsAttached") is True


def test_fatal_error_falls_to_next_stream_with_toast(app_module, open_app):
    seed()
    page = open_tv(open_app)
    play(page)
    fatal(page)
    page.wait_for_function("window.__hlsSource === '/api/hometv/play/2/index.m3u8'")
    assert "Đang thử nguồn 2/2" in page.locator("#tvToast").inner_text()


def test_no_first_frame_in_8s_falls_to_next(app_module, open_app):
    seed()
    page = open_tv(open_app, clock=True)
    play(page)
    page.clock.fast_forward(8100)
    page.wait_for_function("window.__hlsSource === '/api/hometv/play/2/index.m3u8'")


def test_first_frame_cancels_timer_and_posts_recent_once(app_module, open_app):
    seed()
    page = open_tv(open_app, clock=True)
    play(page)
    loaded(page)
    page.wait_for_function("document.querySelector('#tvToast').hidden")
    page.clock.fast_forward(20000)
    assert source(page) == "/api/hometv/play/1/index.m3u8"
    assert [r for r in page.requests if r.startswith("POST /api/hometv/recent/")] == ["POST /api/hometv/recent/1"]
    assert hometv.db.conn().execute("SELECT channel_id FROM recents").fetchall()[0][0] == 1


def test_all_streams_fail_shows_retry_that_restarts_from_first(app_module, open_app):
    seed()
    page = open_tv(open_app)
    play(page)
    fatal(page)
    page.wait_for_function("window.__hlsSource === '/api/hometv/play/2/index.m3u8'")
    fatal(page)
    page.wait_for_selector("#tvPlayerError:not([hidden])")
    assert "Kênh tạm thời không phát được" in page.locator("#tvPlayerMsg").inner_text()
    tap(page, "#tvPlayerRetry")
    page.wait_for_function("window.__hlsSource === '/api/hometv/play/1/index.m3u8' && !window.__hls.destroyed")
    assert page.is_hidden("#tvPlayerError")


def test_streams_404_reports_and_reloads_list(app_module, open_app):
    seed()
    page = open_tv(open_app)
    page.route("**/api/hometv/channels/1/streams", lambda route: route.fulfill(status=404, body="{}"))
    before = len([r for r in page.requests if r == "GET /api/hometv/channels"])
    tap(page, '#tvGrid .tv-card[data-id="1"]')
    page.wait_for_selector("#tvPlayerError:not([hidden])")
    assert "Kênh không còn khả dụng" in page.locator("#tvPlayerMsg").inner_text()
    for _ in range(40):
        if len([r for r in page.requests if r == "GET /api/hometv/channels"]) > before:
            break
        page.wait_for_timeout(50)
    assert len([r for r in page.requests if r == "GET /api/hometv/channels"]) > before


def test_close_destroys_hls_and_clears_timer(app_module, open_app):
    seed()
    page = open_tv(open_app, clock=True)
    play(page)
    tap(page, "#tvPlayerClose")
    assert page.is_hidden("#tvPlayer") and page.evaluate("window.__hls.destroyed") is True
    page.clock.fast_forward(20000)
    assert source(page) == "/api/hometv/play/1/index.m3u8"


def test_direct_stream_uses_video_src(app_module, open_app):
    seed(streams=1, direct=True)
    page = open_tv(open_app)
    tap(page, '#tvGrid .tv-card[data-id="1"]')
    page.wait_for_function("(document.getElementById('tvVideo').getAttribute('src') || '').includes('/api/hometv/play/1/seg?u=')")
    assert page.evaluate("window.__hlsSource") is None


def test_tv_remote_grid_navigation_enter_and_star(app_module, open_app):
    seed(channels=3, streams=1)
    page = open_tv(open_app, tv=True)
    page.focus('#tvGrid .tv-card[data-id="1"]')
    page.keyboard.press("ArrowRight")
    assert page.evaluate("document.activeElement.dataset.id") == "2"
    page.keyboard.press("f")
    page.wait_for_selector('#tvChips [data-chip="favorites"]')
    assert hometv.db.conn().execute("SELECT channel_id FROM favorites").fetchall()[0][0] == 2
    page.keyboard.press("Enter")
    page.wait_for_function("window.__hlsSource !== undefined")
    assert page.is_visible("#tvPlayer")


def test_tv_up_down_changes_channel_debounced_and_escape_restores_focus(app_module, open_app):
    seed(channels=4, streams=1)
    page = open_tv(open_app, tv=True)
    page.focus('#tvGrid .tv-card[data-id="1"]')
    page.keyboard.press("Enter")
    page.wait_for_function("window.__hlsSource === '/api/hometv/play/1/index.m3u8'")
    n = len([r for r in page.requests if "/streams" in r])
    for _ in range(3):
        page.keyboard.press("ArrowDown")
    page.wait_for_function("window.__hlsSource === '/api/hometv/play/4/index.m3u8'")
    assert len([r for r in page.requests if "/streams" in r]) == n + 1
    page.keyboard.press("Escape")
    assert page.is_hidden("#tvPlayer")
    assert page.evaluate("document.activeElement.dataset.id") == "4"


def test_enter_inside_player_does_not_reopen_grid_channel(app_module, open_app):
    seed(channels=3, streams=1)
    page = open_tv(open_app, tv=True)
    page.focus('#tvGrid .tv-card[data-id="1"]')
    page.keyboard.press("Enter")
    page.wait_for_function("window.__hlsSource === '/api/hometv/play/1/index.m3u8'")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    page.wait_for_function("window.__hlsSource === '/api/hometv/play/3/index.m3u8'")
    n = len([r for r in page.requests if "/streams" in r])
    page.keyboard.press("Enter")
    page.wait_for_timeout(500)
    assert source(page) == "/api/hometv/play/3/index.m3u8"
    assert len([r for r in page.requests if "/streams" in r]) == n


def test_one_enter_on_grid_card_opens_once(app_module, open_app):
    seed(channels=2, streams=1)
    page = open_tv(open_app, tv=True)
    page.focus('#tvGrid .tv-card[data-id="1"]')
    page.keyboard.press("Enter")
    page.wait_for_function("window.__hlsSource !== undefined")
    page.wait_for_timeout(300)
    assert len([r for r in page.requests if "/streams" in r]) == 1


def test_settings_button_works_in_hometv_mode(app_module, open_app):
    seed()
    page = open_tv(open_app)
    tap(page, "#settingsBtn")
    page.wait_for_selector("#settings-tab", state="visible")
    assert page.is_hidden("#hometv-root")


def test_cast_playback_leaves_hometv_mode(app_module, open_app):
    seed()
    page = open_tv(open_app)
    play(page)
    page.evaluate("playEpisodesListDirect([{name: 'T1', link_m3u8: 'https://x/a.m3u8'}], 0)")
    assert page.evaluate("document.body.classList.contains('mode-hometv')") is False
    assert page.is_hidden("#tvPlayer") and page.is_hidden("#hometv-root")
