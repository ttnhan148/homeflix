"""Menu lọc và khối Home dùng chung (HomeFlix và HomeTV)."""
URL = "http://homeflix.test/"


def test_filter_menu_renders_options_and_marks_current(app_module, open_app):
    page = open_app()
    page.goto(URL)
    got = page.evaluate("""() => {
        const menu = document.body.appendChild(document.createElement('div'));
        const picked = [];
        renderFilterMenu(menu, [{label: 'Tất cả', value: ''}, {label: 'A', value: 'a'}, {label: 'B', value: 'b'}], 'b', (v) => picked.push(v));
        const btns = [...menu.querySelectorAll('button.filter-option')];
        btns[1].click();
        return {labels: btns.map(b => b.textContent), active: btns.filter(b => b.classList.contains('active')).map(b => b.textContent), picked};
    }""")
    assert got == {"labels": ["Tất cả", "A", "B"], "active": ["B"], "picked": ["a"]}


def test_custom_provider_opens_shared_dropdown(app_module, open_app):
    page = open_app()
    page.goto(URL)
    page.evaluate("""() => {
        document.body.insertAdjacentHTML('beforeend',
            '<div class="filter-dropdown" id="demoDd"><button class="filter-btn" id="filterBtn-demo" onclick="toggleFilterDropdown(\\'demo\\')">Demo</button>' +
            '<div class="filter-menu" id="filterMenu-demo"></div></div>');
        window.__picked = [];
        filterProviders.demo = () => ({options: [{label: 'A', value: 'a'}], current: 'a', onPick: (v) => { window.__picked.push(v); closeAllFilterDropdowns(); }});
    }""")
    page.evaluate("document.getElementById('filterBtn-demo').click()")
    assert page.locator("#filterMenu-demo").evaluate("m => m.classList.contains('open')")
    assert page.locator("#filterMenu-demo .filter-option").all_inner_texts() == ["Tất cả", "A"]
    assert page.locator("#filterMenu-demo .filter-option.active").inner_text() == "A"
    page.evaluate("document.querySelector('#filterMenu-demo .filter-option').click()")
    assert page.evaluate("window.__picked") == [""]
    assert not page.locator("#filterMenu-demo").evaluate("m => m.classList.contains('open')")
    page.evaluate("document.getElementById('filterBtn-demo').click()")
    page.evaluate("document.body.click()")
    assert not page.locator("#filterMenu-demo").evaluate("m => m.classList.contains('open')")


def test_macro_markup_ids_unchanged(app_module, open_app):
    page = open_app()
    page.goto(URL)
    for sel in ("section#homeContinue.home-section", "#homeContinueTitle", "#homeContinueList.home-continue",
                "#homeContinue .sec-head .sec-link", "#filterBtn-category", "#filterMenu-category",
                "#filterBtn-country", "#filterMenu-country", "#filterBtn-year", "#filterMenu-year"):
        assert page.locator(sel).count() == 1, sel
    assert page.locator("#filterBar .filter-dropdown").count() == 3
