"""Khung player dùng chung (macro player_shell) và CSS theatre cho tv-player-active."""
URL = "http://homeflix.test/"


def test_global_player_markup_unchanged_by_macro(app_module, open_app):
    page = open_app()
    page.goto(URL)
    for sel in ("#globalPlayerContainer > #playerHeader.player-header > button.btn-overlay-back",
                "#playerHeader > #playerEpTitle.player-ep-title", "#playerHeader > #playerHeaderRight > #playerPrefsBtn",
                "#globalPlayerContainer > .player-wrapper > video#videoPlayer", ".player-wrapper > #playerError.player-error",
                ".player-wrapper > #skipIntroBtn", ".player-wrapper > #autoNextOverlay", ".player-wrapper > #airplayOverlay"):
        assert page.locator(sel).count() == 1, sel
    assert page.get_attribute(".btn-overlay-back", "onclick") == "exitPlayer()"
    assert page.get_attribute("#videoPlayer", "controls") is not None


def test_theatre_rules_apply_to_tv_player_active(app_module, open_app):
    page = open_app()
    page.goto(URL)
    before = page.evaluate("getComputedStyle(document.querySelector('.container')).paddingLeft")
    page.evaluate("document.body.classList.add('tv-player-active')")
    got = page.evaluate("""() => {
        const cs = getComputedStyle(document.querySelector('.container'));
        return {maxWidth: cs.maxWidth, padding: cs.paddingLeft};
    }""")
    assert got["padding"] == "0px" and before != "0px"
