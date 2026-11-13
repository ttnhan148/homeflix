"""HomeTV mode: switch button, tab bar, Home tab (recently watched, Group filter, alive-only grid), favorites."""
import hometv
from hometv import geo, health_store
from hometv.geo import HealthMode
from hometv.health import HealthResult
from tests.hometv.test_hometv_catalog import seed_channel, seed_stream
from tests.ui.conftest import tap

URL = "http://homeflix.test/"
CATS = ["National TV", "News", "Sports"]


def set_geo(monkeypatch, enabled):
    monkeypatch.setattr(geo, "current", lambda now=None: HealthMode("auto", enabled, "VN" if enabled else "MY", 1.0))


def seed(n=6, names=None, logo=None, health=None):
    """health: {channel_id: True|False} -> stream_health row for the stream with the same id (stream i belongs to channel i)."""
    c = hometv.db.conn()
    c.execute("UPDATE sources SET rights_status='approved' WHERE id='doubo-vn'")
    for i in range(1, n + 1):
        name = (names or {}).get(i, "Kênh %03d" % i)
        seed_channel(c, i, name, CATS[i % 3], logo)
        seed_stream(c, i, i, "doubo-vn", "https://cdn/%d/index.m3u8" % i)
    for cid, ok in (health or {}).items():
        health_store.record(cid, HealthResult(ok=ok), True, 1.0)


def enter(open_app, **kw):
    page = open_app(**kw)
    page.goto(URL)
    tap(page, "#hometvToggle")
    page.wait_for_selector("body.mode-hometv")
    return page


def cards(page):
    return page.locator("#tvGrid .tv-card").count()


def titles(page):
    return page.eval_on_selector_all("#tvGrid .tv-card .movie-title", "els => els.map(e => e.textContent)")


def test_toggle_enters_and_remembers_mode(app_module, open_app):
    seed()
    page = enter(open_app)
    page.wait_for_selector("#tvGrid .tv-card")
    assert page.is_visible("#tv-home-tab") and not page.is_visible("#search-tab")
    page.reload()
    page.wait_for_selector("body.mode-hometv #tvGrid .tv-card")
    tap(page, "#hometvToggle")
    page.wait_for_selector("body:not(.mode-hometv)")
    assert page.is_visible("#search-tab") and not page.is_visible("#hometv-root")


def test_brand_shows_mode_name_with_exchange_toggle(app_module, open_app):
    page = open_app()
    page.goto(URL)
    assert page.locator(".brand-mark").count() == 0
    assert page.locator(".brand #hometvToggle").count() == 1
    assert page.inner_text("#appHeader h1") == "HomeFlix"
    tap(page, "#hometvToggle")
    page.wait_for_selector("body.mode-hometv")
    assert page.inner_text("#appHeader h1") == "HomeTV"
    page.reload()
    page.wait_for_selector("body.mode-hometv")
    assert page.inner_text("#appHeader h1") == "HomeTV"
    tap(page, "#hometvToggle")
    page.wait_for_selector("body:not(.mode-hometv)")
    assert page.inner_text("#appHeader h1") == "HomeFlix"


def test_empty_state(app_module, open_app):
    page = enter(open_app)
    assert "Chưa có kênh" in page.locator("#tvEmpty").inner_text()


def test_grid_reuses_movie_card_classes(app_module, open_app):
    seed(2, logo="https://l/a.png")
    page = enter(open_app)
    page.wait_for_selector("#tvGrid .tv-card")
    card = page.locator("#tvGrid .tv-card").first
    assert "movie-card" in card.get_attribute("class")
    assert card.locator(".poster-wrap img").count() == 1
    assert card.locator(".movie-title").inner_text() == "Kênh 001"
    assert card.locator(".movie-meta").inner_text() == "Tin tức"


def test_missing_logo_shows_initial(app_module, open_app):
    seed(1, names={1: "đài số 1"})
    page = enter(open_app)
    page.wait_for_selector("#tvGrid .tv-card")
    assert page.locator("#tvGrid .tv-card .poster-wrap img").count() == 0
    assert page.locator("#tvGrid .tv-card .tv-initial").inner_text() == "Đ"


def test_renders_in_batches_of_60(app_module, open_app):
    seed(130)
    page = enter(open_app)
    page.wait_for_selector("#tvGrid .tv-card")
    assert cards(page) == 60
    # scroll again on each poll: IntersectionObserver only fires when an element changes state, a single scroll may miss it
    def count_after_scroll():
        return page.evaluate("() => { window.scrollTo(0, document.body.scrollHeight); return document.querySelectorAll('#tvGrid .tv-card').length; }")
    for want in (120, 130):
        for _ in range(50):
            if count_after_scroll() >= want:
                break
            page.wait_for_timeout(100)
    assert cards(page) == 130


def test_tabs_labels_and_order_in_both_bars(app_module, open_app):
    seed()
    page = enter(open_app)
    header = "#appTabsNav .tab-btn"
    fab = "#floatTabBar .fab-tab span"
    texts = lambda sel: page.eval_on_selector_all(sel, "els => els.map(e => e.textContent.trim())")  # noqa: E731
    assert texts(header) == ["Home", "Tủ kênh", "Tìm kiếm"]
    assert texts(fab) == ["Home", "Tìm kiếm", "Tủ kênh"]
    tap(page, "#hometvToggle")
    page.wait_for_selector("body:not(.mode-hometv)")
    assert texts(header) == ["Home", "Tủ phim", "Nhập link"]
    assert texts(fab) == ["Home", "Tìm kiếm", "Tủ phim"]


def test_recent_row_shows_latest_three_lead_first(app_module, open_app):
    seed(5)
    c = hometv.db.conn()
    for cid, at in ((1, 10.0), (2, 50.0), (3, 30.0), (4, 40.0)):
        c.execute("INSERT INTO recents(channel_id, viewed_at) VALUES (?,?)", (cid, at))
    page = enter(open_app)
    page.wait_for_selector("#tvGrid .tv-card")
    names = page.eval_on_selector_all("#tvRecentList .home-cont .continue-title", "els => els.map(e => e.textContent)")
    assert names == ["Kênh 002", "Kênh 004", "Kênh 003"]
    assert page.eval_on_selector_all("#tvRecentList .home-cont", "els => els.map(e => e.classList.contains('is-lead'))") == [True, False, False]
    assert page.locator("#tvRecentList .swipe-act, #tvRecentList .continue-progress").count() == 0
    assert page.is_visible("#tvRecent")


def test_recent_row_hidden_without_recents(app_module, open_app):
    seed(2)
    page = enter(open_app)
    page.wait_for_selector("#tvGrid .tv-card")
    assert page.is_hidden("#tvRecent")


def test_grid_shows_only_alive_with_checker_on(app_module, open_app, monkeypatch):
    seed(3, health={1: True, 2: False})   # 3: never checked
    set_geo(monkeypatch, True)
    page = enter(open_app)
    page.wait_for_selector("#tvGrid .tv-card")
    assert titles(page) == ["Kênh 001"]
    assert page.locator("[data-tvfilter]").count() == 0   # no separate All/Alive filter


def test_grid_shows_all_when_checker_off(app_module, open_app, monkeypatch):
    seed(2)
    set_geo(monkeypatch, False)
    page = enter(open_app)
    page.wait_for_selector("#tvGrid .tv-card")
    assert cards(page) == 2


def test_empty_message_when_nothing_alive(app_module, open_app, monkeypatch):
    seed(2, health={1: False, 2: False})
    set_geo(monkeypatch, True)
    page = enter(open_app)
    page.wait_for_function("document.getElementById('tvEmpty').textContent.includes('đang sống')")
    assert cards(page) == 0


def test_group_tabs_filter_like_movie_type_tabs(app_module, open_app):
    seed(6)
    page = enter(open_app)
    page.wait_for_selector("#tvGrid .tv-card")
    tabs = "#tvGroupTabs .grid-tab-btn"
    assert page.locator(tabs).all_inner_texts() == ["Tất cả", "Quốc gia", "Tin tức", "Thể thao"]
    assert page.locator("#filterBtn-group").count() == 0 and page.locator("#tvClearBtn").count() == 0
    tap(page, '#tvGroupTabs .grid-tab-btn:has-text("Thể thao")')
    assert titles(page) == ["Kênh %03d" % i for i in range(1, 7) if CATS[i % 3] == "Sports"]
    assert page.locator("#tvGroupTabs .grid-tab-btn.active").inner_text() == "Thể thao"
    assert page.inner_text("#tvResultCount") == "2 kênh"
    tap(page, '#tvGroupTabs .grid-tab-btn:has-text("Tất cả")')
    assert cards(page) == 6


def test_hero_shows_featured_channels_with_logos(app_module, open_app):
    seed(3, logo="https://l/a.png")
    page = enter(open_app)
    page.wait_for_selector("#tvHero .hero-slide")
    assert page.inner_text("#tvHeroHeading") == "Kênh nổi bật"
    assert page.eval_on_selector_all("#tvHero .hero-title", "els => els.map(e => e.textContent)") == ["Kênh 001", "Kênh 002", "Kênh 003"]
    assert page.locator("#tvHero .hero-slide .hero-meta").first.inner_text() == "Tin tức"
    assert page.locator("#tvHero .hero-btn-play").first.inner_text() == "Phát"
    assert page.locator("#tvHero .hero-btn-save").first.inner_text() == "Yêu thích"


def test_hero_hidden_without_logos(app_module, open_app):
    seed(2)
    page = enter(open_app)
    page.wait_for_selector("#tvGrid .tv-card")
    assert page.is_hidden("#tvHero")


def test_hero_favorite_button_switches_rail_to_favorites(app_module, open_app):
    seed(3, logo="https://l/a.png")
    page = enter(open_app)
    page.wait_for_selector("#tvHero .hero-slide")
    tap(page, "#tvHero .hero-slide:nth-child(2) .hero-btn-save")
    page.wait_for_function("document.getElementById('tvHeroHeading').textContent === 'Kênh yêu thích'")
    assert page.eval_on_selector_all("#tvHero .hero-title", "els => els.map(e => e.textContent)") == ["Kênh 002"]
    assert page.inner_text("#tvHero .hero-btn-save") == "Đã thích"
    assert [r[0] for r in hometv.db.conn().execute("SELECT channel_id FROM favorites")] == [2]


def test_hero_play_opens_channel(app_module, open_app):
    seed(2, logo="https://l/a.png")
    page = enter(open_app)
    page.wait_for_selector("#tvHero .hero-slide")
    tap(page, "#tvHero .hero-slide:nth-child(2) .hero-btn-play")
    page.wait_for_selector("#tvPlayer:not([hidden])")
    assert page.inner_text("#tvPlayerTitle") == "Kênh 002"


def test_no_status_dot_on_cards_or_playlist(app_module, open_app, monkeypatch):
    seed(3, health={1: True, 2: False, 3: True})
    set_geo(monkeypatch, True)
    page = enter(open_app)
    page.wait_for_selector("#tvGrid .tv-card")
    assert cards(page) == 2
    tap(page, '#tvGrid .tv-card[data-id="1"]')
    page.wait_for_selector("#tvPlaylist .ep-item")
    assert page.locator(".tv-dot, [data-status]").count() == 0


def test_grid_shows_all_after_health_goes_off(app_module, open_app, monkeypatch):
    seed(3, health={1: True})
    set_geo(monkeypatch, True)
    page = enter(open_app)
    page.wait_for_selector("#tvGrid .tv-card")
    assert cards(page) == 1
    set_geo(monkeypatch, False)
    tap(page, "#hometvToggle")
    page.wait_for_selector("body:not(.mode-hometv)")
    tap(page, "#hometvToggle")
    page.wait_for_function("document.querySelectorAll('#tvGrid .tv-card').length === 3")


def test_switch_tab_safe_before_hometv_init(app_module, open_app):
    page = open_app()
    page.goto(URL)
    got = page.evaluate("""() => { const h = window.HFTV; delete window.HFTV;
        try { switchTab('saved-tab'); return document.getElementById('saved-tab').classList.contains('active'); }
        finally { window.HFTV = h; } }""")
    assert got is True


def test_star_toggles_and_card_reflects_it(app_module, open_app):
    seed(3)
    page = enter(open_app)
    page.wait_for_selector("#tvGrid .tv-card")
    assert page.get_attribute('#tvGrid .tv-card[data-id="2"] .tv-star', "aria-pressed") == "false"
    tap(page, '#tvGrid .tv-card[data-id="2"] .tv-star')
    page.wait_for_function("document.querySelector('#tvGrid .tv-card[data-id=\"2\"] .tv-star').getAttribute('aria-pressed') === 'true'")
    assert hometv.db.conn().execute("SELECT channel_id FROM favorites").fetchall()[0][0] == 2
    tap(page, '#tvGrid .tv-card[data-id="2"] .tv-star')
    page.wait_for_function("document.querySelector('#tvGrid .tv-card[data-id=\"2\"] .tv-star').getAttribute('aria-pressed') === 'false'")
    assert hometv.db.conn().execute("SELECT COUNT(*) FROM favorites").fetchone()[0] == 0


def test_error_state_and_retry(app_module, open_app):
    seed(2)
    page = open_app()
    page.route("**/api/hometv/channels", lambda route: route.fulfill(status=500, body="x"))
    page.goto(URL)
    tap(page, "#hometvToggle")
    page.wait_for_selector("#tvRetry")
    assert "Không tải được danh sách kênh" in page.locator("#tvEmpty").inner_text()
    page.unroute("**/api/hometv/channels")
    tap(page, "#tvRetry")
    page.wait_for_selector("#tvGrid .tv-card")


def go(page, fab):
    tap(page, "#" + fab)
    page.wait_for_function("document.querySelector('#hometv-root .tab-content.active') !== null")


def grid_titles(page, grid):
    return page.eval_on_selector_all("%s .tv-card .movie-title" % grid, "els => els.map(e => e.textContent)")


def test_search_tab_folds_diacritics_and_ignores_blank(app_module, open_app):
    seed(3, names={1: "Đài Thể Thao", 2: "VTV1", 3: "HTV7"})
    page = enter(open_app)
    page.wait_for_selector("#tvGrid .tv-card")
    go(page, "fabTabSearch")
    assert page.is_visible("#tv-search-tab") and page.is_hidden("#tv-home-tab")
    page.fill("#tvSearch", "dai the")
    assert grid_titles(page, "#tvSearchGrid") == ["Đài Thể Thao"]
    page.fill("#tvSearch", "zzz")
    assert page.is_visible("#tvSearchEmpty") and "Không có kênh nào khớp." in page.inner_text("#tvSearchEmpty")
    page.fill("#tvSearch", "   ")
    assert page.locator("#tvSearchGrid .tv-card").count() == 0 and page.is_hidden("#tvSearchEmpty")


def test_search_only_finds_alive_channels(app_module, open_app, monkeypatch):
    seed(3, health={1: True, 2: False})
    set_geo(monkeypatch, True)
    page = enter(open_app)
    page.wait_for_selector("#tvGrid .tv-card")
    go(page, "fabTabSearch")
    page.fill("#tvSearch", "kenh")
    assert grid_titles(page, "#tvSearchGrid") == ["Kênh 001"]


def test_library_shows_favorites_and_recents_then_empty_text(app_module, open_app):
    seed(4)
    c = hometv.db.conn()
    c.execute("INSERT INTO favorites(channel_id, created_at) VALUES (2, 1.0)")
    for cid, at in ((1, 10.0), (3, 30.0), (4, 20.0)):
        c.execute("INSERT INTO recents(channel_id, viewed_at) VALUES (?,?)", (cid, at))
    page = enter(open_app)
    page.wait_for_selector("#tvGrid .tv-card")
    go(page, "fabTabLibrary")
    assert grid_titles(page, "#tvFavGrid") == ["Kênh 002"]
    assert grid_titles(page, "#tvLibRecentGrid") == ["Kênh 003", "Kênh 004", "Kênh 001"]
    assert page.is_hidden("#tvLibraryEmpty")


def test_library_empty_text(app_module, open_app):
    seed(2)
    page = enter(open_app)
    page.wait_for_selector("#tvGrid .tv-card")
    go(page, "fabTabLibrary")
    assert page.inner_text("#tvLibraryEmpty") == "Tủ kênh trống"
    assert page.is_hidden("#tvFavSection") and page.is_hidden("#tvLibRecentSection")


def test_library_redraws_after_favorite_toggled_in_home(app_module, open_app):
    seed(3)
    page = enter(open_app)
    page.wait_for_selector("#tvGrid .tv-card")
    go(page, "fabTabLibrary")
    assert page.is_visible("#tvLibraryEmpty")
    go(page, "fabTabExplore")
    tap(page, '#tvGrid .tv-card[data-id="3"] .tv-star')
    page.wait_for_function("document.querySelector('#tvGrid .tv-card[data-id=\"3\"] .tv-star').getAttribute('aria-pressed') === 'true'")
    go(page, "fabTabLibrary")
    assert grid_titles(page, "#tvFavGrid") == ["Kênh 003"] and page.is_hidden("#tvLibraryEmpty")


def test_settings_back_from_hometv_lands_on_homeflix_home(app_module, open_app):
    seed(2)
    page = enter(open_app)
    page.wait_for_selector("#tvGrid .tv-card")
    tap(page, "#settingsBtn")
    page.wait_for_selector("#settings-tab", state="visible")
    tap(page, "#settingsBack")
    page.wait_for_selector("#search-tab", state="visible")
    assert page.evaluate("document.body.classList.contains('mode-hometv')") is False
    assert page.is_hidden("#hometv-root")


def test_home_stars_resync_after_unstar_in_library(app_module, open_app):
    seed(3)
    page = enter(open_app)
    page.wait_for_selector("#tvGrid .tv-card")
    star = '#tvGrid .tv-card[data-id="2"] .tv-star'
    tap(page, star)
    page.wait_for_function("document.querySelector('%s').getAttribute('aria-pressed') === 'true'" % star)
    go(page, "fabTabLibrary")
    tap(page, '#tvFavGrid .tv-card[data-id="2"] .tv-star')
    page.wait_for_function("document.querySelectorAll('#tvFavGrid .tv-card').length === 0")
    go(page, "fabTabExplore")
    assert page.get_attribute(star, "aria-pressed") == "false"
