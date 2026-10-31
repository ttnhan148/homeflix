"""renderEpisodeList nhận thêm tuỳ chọn dòng, đơn vị, móc decorate (HomeTV dùng làm danh sách phát)."""
URL = "http://homeflix.test/"

RENDER = """(opts) => {
    const box = document.createElement('div');
    box.id = 'epbox';
    document.body.appendChild(box);
    const episodes = Array.from({length: 40}, (_, i) => ({name: 'K' + (i + 1), link_m3u8: String(i + 1)}));
    renderEpisodeList(box, Object.assign({episodes}, opts));
}"""


def test_rows_option_caps_list_at_ten_rows_and_scrolls(app_module, open_app):
    page = open_app()
    page.goto(URL)
    page.evaluate("""() => renderEpisodeList(document.body.appendChild(Object.assign(document.createElement('div'), {id: 'epbox'})), {
        episodes: Array.from({length: 40}, (_, i) => ({name: 'K' + (i + 1), link_m3u8: String(i + 1)})),
        rows: 10, unit: 'kênh', decorate: (b) => { b.dataset.x = '1'; }})""")
    assert page.locator("#epbox .ep-list-title").inner_text() == "40 kênh"
    assert page.locator("#epbox .ep-list-pager").count() == 0
    got = page.evaluate("""() => {
        const g = document.querySelector('#epbox .ep-list-grid');
        const item = g.querySelector('.ep-item').getBoundingClientRect().height;
        const gap = parseFloat(getComputedStyle(g).rowGap) || 0;
        return {scroll: g.scrollHeight > g.clientHeight, h: g.clientHeight, ten: 10 * item + 9 * gap,
                allX: [...g.querySelectorAll('.ep-item')].every((b) => b.dataset.x === '1')};
    }""")
    assert got["scroll"] and got["allX"]
    assert abs(got["h"] - got["ten"]) <= 12


def test_default_options_unchanged(app_module, open_app):
    page = open_app()
    page.goto(URL)
    page.evaluate(RENDER, {})
    assert page.locator("#epbox .ep-list-title").inner_text() == "40 tập"
    assert page.locator("#epbox .ep-list-pager").count() == 0
    assert page.locator("#epbox .ep-list-rows").count() == 0
    cols = page.evaluate("getComputedStyle(document.querySelector('#epbox .ep-list-grid')).gridTemplateColumns.split(' ').length")
    assert cols > 1
