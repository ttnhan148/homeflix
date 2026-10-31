"""Thẻ kênh HomeTV dùng đúng ngôn ngữ thiết kế của lưới phim."""
from tests.ui.test_hometv_ui import enter, seed


def test_card_uses_movie_card_styling(app_module, open_app):
    seed(1, logo="https://l/a.png")
    page = enter(open_app)
    page.wait_for_selector("#tvGrid .tv-card")
    got = page.evaluate("""() => {
        const wrap = document.querySelector('#tvGrid .tv-card .poster-wrap');
        const card = document.querySelector('#tvGrid .tv-card');
        const r = wrap.getBoundingClientRect();
        return {radius: getComputedStyle(wrap).borderRadius, ratio: r.width / r.height,
                bg: getComputedStyle(card).backgroundColor, border: getComputedStyle(card).borderTopWidth,
                title: !!card.querySelector('.movie-title'), meta: !!card.querySelector('.movie-meta')};
    }""")
    assert got["radius"] == "6px" and abs(got["ratio"] - 16 / 9) < 0.02
    assert got["bg"] == "rgba(0, 0, 0, 0)" and got["border"] == "0px"
    assert got["title"] and got["meta"]
