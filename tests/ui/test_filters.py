from tests.ui.conftest import tap
from tests.ui.phimapi_fake import FakePhimapi

URL = "http://homeflix.test/"
CARDS = "#homeGridContent .movie-card"
SLUGS = f"[...document.querySelectorAll('{CARDS}')].map(c => c.dataset.slug)"
FAIL_TYPES = ["danh-sach/phim-le", "danh-sach/phim-bo", "danh-sach/tv-shows"]


def wait_request(page, needle, timeout=4000):
    for _ in range(timeout // 50):
        if any(needle in r for r in page.requests):
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"no request containing {needle!r}; saw {page.requests}")


def count(page, needle):
    return sum(needle in r for r in page.requests)


def _open(open_app, **kw):
    page = open_app(**kw)
    page.goto(URL)
    page.wait_for_selector(CARDS)
    return page


def pick(page, chip, label):
    page.wait_for_function("filterData.categories && filterData.countries")
    tap(page, f"#filterBtn-{chip}")
    tap(page, f".filter-menu.open .filter-option:has-text('{label}')")


def test_default_is_all_and_uses_latest_endpoint(open_app):
    page = _open(open_app)
    assert any(r.startswith("GET /api/home/latest?page=1") for r in page.requests)
    assert not any(t in r for r in page.requests for t in ("/api/home/phim-le", "/api/home/phim-bo", "/api/home/tv-shows"))
    assert page.evaluate("document.querySelector('.grid-tab-btn[data-tab=all]').classList.contains('active')")
    assert page.locator("#resultCount").inner_text() == "24 phim"


def test_type_switch_calls_dedicated_endpoint(open_app):
    page = _open(open_app)
    tap(page, ".grid-tab-btn[data-tab=phim-le]")
    wait_request(page, "GET /api/home/phim-le?page=1")
    page.wait_for_function(f"{SLUGS}.every(s => s.startsWith('phim-le') || s === 'phim-chung')")


def test_country_filter_under_all_fans_out_to_three_endpoints_and_dedupes(open_app):
    page = _open(open_app)
    pick(page, "country", "Hàn Quốc")
    for t in ("phim-le", "phim-bo", "tv-shows"):
        wait_request(page, f"GET /api/home/{t}?page=1&country=han-quoc")
    page.wait_for_function(f"{SLUGS}.includes('phim-le-han-quoc-1') && {SLUGS}.includes('phim-bo-han-quoc-1') && {SLUGS}.includes('tv-shows-han-quoc-1')")
    slugs = page.evaluate(SLUGS)
    assert len(slugs) == len(set(slugs))
    assert page.evaluate("document.getElementById('filterBtn-country').classList.contains('active')")
    assert "Hàn Quốc" in page.locator("#filterBtn-country").inner_text()
    assert page.is_visible("#filterClearBtn")


def test_clear_filters_returns_to_latest(open_app):
    page = _open(open_app)
    pick(page, "country", "Mỹ")
    page.wait_for_function(f"{SLUGS}.some(s => s.includes('-my-'))")
    tap(page, "#filterClearBtn")
    page.wait_for_function(f"{SLUGS}.every(s => s.startsWith('latest'))")
    assert count(page, "GET /api/home/latest?page=1") == 2
    assert not page.is_visible("#filterClearBtn")


def test_fanout_with_one_failing_endpoint_still_renders_others(open_app):
    page = _open(open_app, fake=FakePhimapi(fail=["danh-sach/phim-bo"]))
    pick(page, "country", "Hàn Quốc")
    page.wait_for_function(f"{SLUGS}.includes('phim-le-han-quoc-1') && {SLUGS}.includes('tv-shows-han-quoc-1')")
    assert page.locator(".skeleton-card").count() == 0
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    wait_request(page, "GET /api/home/phim-le?page=2&country=han-quoc")
    page.wait_for_timeout(300)
    assert count(page, "GET /api/home/phim-bo?page=2") == 0


def test_all_endpoints_failing_shows_error_message_not_skeleton(open_app):
    page = _open(open_app, fake=FakePhimapi(fail=FAIL_TYPES))
    pick(page, "country", "Hàn Quốc")
    page.wait_for_selector(".filter-empty")
    assert "Không tải được" in page.locator(".filter-empty").inner_text()
    assert page.locator(".skeleton-card").count() == 0


def test_grid_works_when_hero_sources_fail(open_app):
    page = _open(open_app, fake=FakePhimapi(fail=["danh-sach/phim-chieu-rap", "danh-sach/phim-le"]))
    assert page.locator(CARDS).count() > 0
    assert page.console_errors == []


def test_rapid_filter_changes_keep_only_latest_result(open_app):
    slow = "/api/home/phim-le?page=1&country=han-quoc"
    page = _open(open_app, delay={slow: 1.0})
    pick(page, "country", "Hàn Quốc")
    pick(page, "country", "Mỹ")
    page.wait_for_function(f"{SLUGS}.includes('phim-le-my-1')")
    page.wait_for_timeout(1500)
    assert not any("han-quoc" in s for s in page.evaluate(SLUGS))
    assert page.evaluate(SLUGS)[0].endswith("-1")


def test_rapid_type_switch_keeps_only_last_type(open_app):
    page = _open(open_app, delay={"/api/home/phim-le": 0.8})
    tap(page, ".grid-tab-btn[data-tab=phim-le]")
    tap(page, ".grid-tab-btn[data-tab=phim-bo]")
    page.wait_for_function(f"{SLUGS}.length > 0 && {SLUGS}.every(s => s.startsWith('phim-bo') || s === 'phim-chung')")
    page.wait_for_timeout(1200)
    assert not any(s.startswith("phim-le-") for s in page.evaluate(SLUGS))


def test_filter_bar_stays_below_safe_top_when_scrolled(open_app):
    page = _open(open_app, safe=(59, 0, 34, 0))
    page.evaluate("window.scrollTo(0, 1600)")
    page.wait_for_timeout(200)
    top = page.evaluate("document.getElementById('filterBar').getBoundingClientRect().top")
    assert 59 - 1 <= top <= 59 + 44 + 3


def test_sheet_closes_after_pick_and_controls_meet_44pt(open_app):
    page = _open(open_app)
    for sel in ("#filterBtn-category", "#filterBtn-country", "#filterBtn-year", ".grid-tab-btn[data-tab=all]"):
        assert page.locator(sel).bounding_box()["height"] >= 44 - 0.5, sel
    pick(page, "year", "2024")
    assert page.locator(".filter-menu.open").count() == 0
    tap(page, "#filterBtn-category")
    box = page.locator(".filter-menu.open").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 430 and box["y"] >= 0 and box["y"] + box["height"] <= 932
    assert page.locator(".filter-menu.open .filter-option").first.bounding_box()["height"] >= 44 - 0.5


def test_iphone_grid_has_three_columns(open_app):
    page = _open(open_app)
    tops = page.evaluate(f"[...document.querySelectorAll('{CARDS}')].slice(0, 4).map(c => Math.round(c.getBoundingClientRect().top))")
    assert tops[0] == tops[1] == tops[2] and tops[3] > tops[0]


def test_tv_filter_menu_keyboard_and_single_row(open_app):
    page = _open(open_app, tv=True)
    tops = page.evaluate(f"[...document.querySelectorAll('{CARDS}')].slice(0, 3).map(c => Math.round(c.getBoundingClientRect().top))")
    assert tops[0] == tops[1] == tops[2]
    page.wait_for_function("filterData.categories")
    page.focus("#filterBtn-category")
    page.keyboard.press("Enter")
    page.wait_for_selector(".filter-menu.open")
    page.focus(".filter-menu.open .filter-option")
    page.wait_for_timeout(400)
    assert page.evaluate("getComputedStyle(document.activeElement).outlineWidth") == "4px"
    page.keyboard.press("Escape")
    assert page.locator(".filter-menu.open").count() == 0


def test_movie_card_title_with_quotes_opens_detail(open_app):
    name = "O'Brien \"X\" `Y`"
    page = _open(open_app, fake=FakePhimapi(names={"latest-1": name}))
    assert page.evaluate(f"document.querySelector('{CARDS}').querySelector('.movie-title').textContent") == name
    tap(page, CARDS)
    wait_request(page, "GET /api/movie/latest-1")
    page.wait_for_selector("#movieDetailContainer .detail-hero-title")
    assert page.locator("#movieDetailContainer .detail-hero-title").inner_text() == name
