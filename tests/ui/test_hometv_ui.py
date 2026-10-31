"""Mode HomeTV: nút chuyển, lưới kênh, chip nhóm, tìm kiếm, yêu thích."""
import hometv
from tests.hometv.test_hometv_catalog import seed_channel, seed_stream
from tests.ui.conftest import tap

URL = "http://homeflix.test/"
CATS = ["National TV", "News", "Sports"]


def seed(n=6, names=None, logo=None):
    c = hometv.db.conn()
    c.execute("UPDATE sources SET rights_status='approved' WHERE id='doubo-vn'")
    for i in range(1, n + 1):
        name = (names or {}).get(i, "Kênh %03d" % i)
        seed_channel(c, i, name, CATS[i % 3], logo)
        seed_stream(c, i, i, "doubo-vn", "https://cdn/%d/index.m3u8" % i)


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
    assert page.is_visible("#hometv-root") and not page.is_visible("#search-tab")
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
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_function("document.querySelectorAll('#tvGrid .tv-card').length >= 120")
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_function("document.querySelectorAll('#tvGrid .tv-card').length === 130")


def test_chips_filter_and_hide_empty_groups(app_module, open_app):
    seed(6)
    page = enter(open_app)
    page.wait_for_selector("#tvGrid .tv-card")
    keys = page.eval_on_selector_all("#tvChips [data-chip]", "els => els.map(e => e.dataset.chip)")
    assert keys == ["all", "National TV", "News", "Sports"]
    tap(page, '#tvChips [data-chip="Sports"]')
    assert titles(page) == ["Kênh %03d" % i for i in range(1, 7) if CATS[i % 3] == "Sports"]


def test_search_folds_diacritics_and_ignores_blank(app_module, open_app):
    seed(3, names={1: "Đài Thể Thao", 2: "VTV1", 3: "HTV7"})
    page = enter(open_app)
    page.wait_for_selector("#tvGrid .tv-card")
    page.fill("#tvSearch", "dai the")
    assert titles(page) == ["Đài Thể Thao"]
    page.fill("#tvSearch", "   ")
    assert cards(page) == 3


def test_star_toggles_and_favorites_chip_appears(app_module, open_app):
    seed(3)
    page = enter(open_app)
    page.wait_for_selector("#tvGrid .tv-card")
    assert page.locator('#tvChips [data-chip="favorites"]').count() == 0
    tap(page, '#tvGrid .tv-card[data-id="2"] .tv-star')
    page.wait_for_selector('#tvChips [data-chip="favorites"]')
    assert page.get_attribute('#tvGrid .tv-card[data-id="2"] .tv-star', "aria-pressed") == "true"
    assert hometv.db.conn().execute("SELECT channel_id FROM favorites").fetchall()[0][0] == 2
    tap(page, '#tvChips [data-chip="favorites"]')
    assert titles(page) == ["Kênh 002"]
    tap(page, '#tvGrid .tv-card[data-id="2"] .tv-star')
    page.wait_for_function("!document.querySelector('#tvChips [data-chip=favorites]')")


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
