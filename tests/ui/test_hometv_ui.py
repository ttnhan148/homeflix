"""Mode HomeTV: nút chuyển, thanh tab, tab Home (xem gần đây, lọc Alive/Nhóm, lưới), yêu thích."""
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
    """health: {channel_id: True|False} -> hàng stream_health của stream cùng id (stream i thuộc kênh i)."""
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
    # cuộn lại ở mỗi lần thăm dò: IntersectionObserver chỉ báo khi phần tử đổi trạng thái, một lần cuộn có thể lỡ
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


def test_alive_button_filters_and_hides_when_health_off(app_module, open_app, monkeypatch):
    seed(3, health={1: True, 2: False})
    set_geo(monkeypatch, True)
    page = enter(open_app)
    page.wait_for_selector("#tvGrid .tv-card")
    assert cards(page) == 3
    tap(page, '[data-tvfilter="alive"]')
    assert titles(page) == ["Kênh 001"]
    tap(page, '[data-tvfilter="all"]')
    assert cards(page) == 3


def test_alive_button_hidden_when_checker_off(app_module, open_app, monkeypatch):
    seed(2)
    set_geo(monkeypatch, False)
    page = enter(open_app)
    page.wait_for_selector("#tvGrid .tv-card")
    assert page.is_hidden('[data-tvfilter="alive"]')


def test_group_dropdown_filters_and_clear(app_module, open_app):
    seed(6)
    page = enter(open_app)
    page.wait_for_selector("#tvGrid .tv-card")
    assert page.is_hidden("#tvClearBtn")
    tap(page, "#filterBtn-group")
    assert page.locator("#filterMenu-group .filter-option").all_inner_texts() == ["Tất cả", "Quốc gia", "Tin tức", "Thể thao"]
    tap(page, '#filterMenu-group .filter-option:has-text("Thể thao")')
    assert titles(page) == ["Kênh %03d" % i for i in range(1, 7) if CATS[i % 3] == "Sports"]
    assert page.inner_text("#filterBtn-group") == "Thể thao" and page.is_visible("#tvClearBtn")
    assert page.inner_text("#tvResultCount") == "2 kênh"
    tap(page, "#tvClearBtn")
    assert cards(page) == 6 and page.inner_text("#filterBtn-group") == "Nhóm"


def test_status_dot_per_card_and_hidden_without_health(app_module, open_app, monkeypatch):
    seed(3, health={1: True, 2: False})
    set_geo(monkeypatch, True)
    page = enter(open_app)
    page.wait_for_selector("#tvGrid .tv-card")
    got = page.eval_on_selector_all("#tvGrid .tv-card", "els => els.map(e => { const d = e.querySelector('.tv-dot'); return d && [d.dataset.status, d.getAttribute('aria-label')]; })")
    assert got == [["alive", "Đang sống"], ["dead", "Đã chết"], ["unchecked", "Chưa kiểm"]]


def test_no_status_dot_when_checker_off(app_module, open_app, monkeypatch):
    seed(2)
    set_geo(monkeypatch, False)
    page = enter(open_app)
    page.wait_for_selector("#tvGrid .tv-card")
    assert page.locator(".tv-dot").count() == 0


def test_alive_filter_resets_when_health_goes_off(app_module, open_app, monkeypatch):
    seed(3, health={1: True})
    set_geo(monkeypatch, True)
    page = enter(open_app)
    page.wait_for_selector("#tvGrid .tv-card")
    tap(page, '[data-tvfilter="alive"]')
    assert cards(page) == 1
    set_geo(monkeypatch, False)
    tap(page, "#hometvToggle")
    page.wait_for_selector("body:not(.mode-hometv)")
    tap(page, "#hometvToggle")
    page.wait_for_function("document.querySelectorAll('#tvGrid .tv-card').length === 3")
    assert page.is_hidden('[data-tvfilter="alive"]')


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


def test_search_respects_alive_filter(app_module, open_app, monkeypatch):
    seed(3, health={1: True, 2: False})
    set_geo(monkeypatch, True)
    page = enter(open_app)
    page.wait_for_selector("#tvGrid .tv-card")
    tap(page, '[data-tvfilter="alive"]')
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
