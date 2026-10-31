import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ("home-utils.js", "cast-utils.js", "player-utils.js", "player-auto.js", "cast.js", "hf.css",
          "settings-utils.js", "hometv-utils.js", "settings-hometv.js", "settings.js", "hometv.js")


def _page(make_client):
    return make_client().get("/").text


def test_page_links_every_script_and_style_with_a_content_version(make_client):
    html = _page(make_client)
    for name in ASSETS:
        assert re.search(rf'/static/{re.escape(name)}\?v=[0-9a-f]{{10}}"', html), name
        assert f'/static/{name}"' not in html, name


def test_version_follows_file_content(make_client, app_module, tmp_path, monkeypatch):
    (tmp_path / "a.js").write_text("one")
    monkeypatch.setattr(app_module, "STATIC_DIR", str(tmp_path))
    first = app_module.asset_url("a.js")
    (tmp_path / "a.js").write_text("two!")
    assert app_module.asset_url("a.js") != first


def test_versioned_assets_are_immutable_unversioned_revalidate(make_client):
    c = make_client()
    assert "immutable" in c.get("/static/home-utils.js?v=abc").headers["cache-control"]
    assert c.get("/static/home-utils.js").headers["cache-control"] == "no-cache"
    assert c.get("/static/sw.js?v=abc").headers["cache-control"] == "no-cache"


def test_service_worker_does_not_precache_versioned_assets():
    sw = (ROOT / "static" / "sw.js").read_text()
    assert "homeflix-shell-v6" in sw
    shell = re.search(r"SHELL_URLS = \[(.*?)\]", sw).group(1)
    for name in ASSETS:
        assert name not in shell, name
