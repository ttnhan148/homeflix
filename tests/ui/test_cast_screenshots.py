import os
from pathlib import Path

from tests.ui.conftest import tap
from tests.ui.test_cast_controller import Tv, open_phone, open_sheet
from tests.ui.test_cast_remote import link_phone, open_remote, push_state

ROOT = Path(__file__).resolve().parents[2]


def out_dir(tmp_path):
    out = Path(os.environ.get("SCREENSHOT_DIR", tmp_path))
    out.mkdir(parents=True, exist_ok=True)
    return out


def test_page_loads_cast_files_with_content_version(open_app):
    page = open_app()
    page.goto("http://homeflix.test/")
    srcs = page.evaluate("[...document.scripts].map(s => s.getAttribute('src') || '')")
    for f in ("cast.js", "cast-utils.js", "player-utils.js", "player-auto.js"):
        assert any(s.startswith(f"/static/{f}?v=") for s in srcs), f


def test_cast_sheet_and_remote_render(app_module, make_client, open_app, tmp_path):
    Tv(app_module, make_client, name="Phòng khách")
    page = open_phone(open_app)
    open_sheet(page)
    sheet = out_dir(tmp_path) / "cast-sheet-430x932.png"
    page.screenshot(path=str(sheet))
    assert sheet.stat().st_size > 15_000 and page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_cast_remote_panel_renders(app_module, make_client, open_app, tmp_path):
    page, tv = link_phone(app_module, make_client, open_app)
    push_state(page, tv)
    open_remote(page)
    shot = out_dir(tmp_path) / "cast-remote-430x932.png"
    page.screenshot(path=str(shot))
    assert shot.stat().st_size > 15_000 and page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_tv_confirm_dialog_renders(open_app, tmp_path):
    page = open_app(tv=True)
    page.goto("http://homeflix.test/")
    page.wait_for_function("window.HFCast && HFCast.started === true")
    page.es_emit("connect_request", {"controller_id": "c1", "controller_name": "iPhone"})
    page.wait_for_selector("#castConfirm:not([hidden])")
    shot = out_dir(tmp_path) / "cast-confirm-1920x1080.png"
    page.screenshot(path=str(shot))
    assert shot.stat().st_size > 15_000 and page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    for sel in ("#castAllow", "#castDeny"):
        assert page.evaluate("s => parseFloat(getComputedStyle(document.querySelector(s)).fontSize)", sel) >= 20
