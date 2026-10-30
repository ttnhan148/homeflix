from tests.ui.test_three_taps import _open

W, H = 932, 430
SAFE = (0, 59, 21, 59)  # top, right, bottom, left khi xoay ngang trên iPhone có Dynamic Island


def _player(open_app):
    page = _open(open_app, width=W, height=H, safe=SAFE)
    page.evaluate("document.querySelector('.hero-btn-play').click()")
    page.wait_for_function("document.body.classList.contains('player-active')")
    page.evaluate("document.getElementById('airplayOverlay').style.display = 'flex'")
    return page


def box(page, sel):
    return page.evaluate("s => { const r = document.querySelector(s).getBoundingClientRect(); return {l: r.left, r: r.right, t: r.top, b: r.bottom, w: r.width, h: r.height}; }", sel)


def test_player_header_inside_landscape_safe_area(open_app):
    page = _player(open_app)
    assert box(page, "#playerHeader .btn-overlay-back")["l"] >= 59 - 0.5
    assert box(page, "#playerHeaderRight")["r"] <= W - 59 + 0.5


def test_airplay_overlay_controls_inside_safe_area(open_app):
    page = _player(open_app)
    controls = box(page, ".airplay-controls")
    assert controls["l"] >= 59 - 0.5 and controls["r"] <= W - 59 + 0.5 and controls["b"] <= H - 21 + 0.5
    disconnect = box(page, "#airplayDisconnectBtn")
    assert disconnect["r"] <= W - 59 + 0.5 and disconnect["l"] >= 59 - 0.5


def test_player_controls_meet_44pt(open_app):
    page = _player(open_app)
    for sel in ("#playerHeader .btn-overlay-back", "#airplayDisconnectBtn", "#airplayPlayPause"):
        b = box(page, sel)
        assert b["w"] >= 44 - 0.5 and b["h"] >= 44 - 0.5, sel
