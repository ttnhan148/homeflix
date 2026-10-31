import json

import hometv
from tests.hometv.test_hometv_catalog import seed_channel, seed_stream


def seed(app_module):
    """Một kênh hợp lệ (nguồn doubo-vn đã duyệt) và một kênh thuộc nguồn chờ duyệt."""
    c = hometv.db.conn()
    c.execute("UPDATE sources SET rights_status='approved' WHERE id='doubo-vn'")
    seed_channel(c, 1, "VTV1", "National TV", "https://l/vtv1.png")
    seed_stream(c, 1, 1, "doubo-vn", "http://cdn/vtv1/index.m3u8", headers={"user_agent": "SECRET-UA"},
                quality="HD")
    seed_stream(c, 2, 1, "doubo-vn", "https://cdn/vtv1.ts", drm=1)
    seed_channel(c, 2, "Ẩn")
    seed_stream(c, 3, 2, "iptv-org-vn", "https://cdn/hidden/index.m3u8")


def test_channels_lists_only_valid_without_urls(app_module, make_client):
    seed(app_module)
    r = make_client().get("/api/hometv/channels")
    assert r.status_code == 200
    assert r.json() == [{"id": 1, "name": "VTV1", "category": "National TV", "favorite": False,
                         "recent_at": None, "logo": "/proxy/image?url=https%3A%2F%2Fl%2Fvtv1.png"}]
    assert "cdn" not in r.text and "SECRET" not in r.text


def test_streams_play_url_and_404(app_module, make_client):
    seed(app_module)
    c = make_client()
    r = c.get("/api/hometv/channels/1/streams")
    assert r.json() == [{"id": 1, "play_url": "/api/hometv/play/1/index.m3u8", "quality_hint": "HD"}]
    assert c.get("/api/hometv/channels/2/streams").status_code == 404


def test_direct_stream_gets_seg_play_url(app_module, make_client):
    seed(app_module)
    seed_stream(hometv.db.conn(), 4, 1, "doubo-vn", "https://cdn/live.ts")
    urls = [s["play_url"] for s in make_client().get("/api/hometv/channels/1/streams").json()]
    assert "/api/hometv/play/4/seg?u=https%3A%2F%2Fcdn%2Flive.ts" in urls


def test_favorite_and_recent_roundtrip(app_module, make_client):
    seed(app_module)
    c = make_client()
    assert c.put("/api/hometv/favorites/1").status_code == 204
    assert c.put("/api/hometv/favorites/1").status_code == 204
    assert c.post("/api/hometv/recent/1").status_code == 204
    ch = c.get("/api/hometv/channels").json()[0]
    assert ch["favorite"] is True and ch["recent_at"] is not None
    assert c.delete("/api/hometv/favorites/1").status_code == 204
    assert c.delete("/api/hometv/favorites/1").status_code == 204
    assert c.get("/api/hometv/channels").json()[0]["favorite"] is False


def test_unknown_or_hidden_channel_404(app_module, make_client):
    seed(app_module)
    c = make_client()
    assert c.put("/api/hometv/favorites/2").status_code == 404
    assert c.put("/api/hometv/favorites/999").status_code == 404
    assert c.post("/api/hometv/recent/2").status_code == 404


def test_public_api_uses_geo_enabled_for_hiding_but_play_still_works(app_module, make_client, monkeypatch):
    import httpx

    from hometv import geo, health_store
    from hometv.geo import HealthMode
    from hometv.health import HealthResult
    seed(app_module)
    for n in range(3):
        health_store.record(1, HealthResult(ok=False, error="x"), True, float(n))
    c = make_client()
    monkeypatch.setattr(geo, "current", lambda now=None: HealthMode("auto", True, "VN", 1.0))
    assert c.get("/api/hometv/channels").json() == []
    app_module.hometv_play_client = hometv.fetcher.make_client(
        httpx.MockTransport(lambda r: httpx.Response(200, content=b"#EXTM3U\n#EXTINF:4,\ns.ts\n")))
    assert c.get("/api/hometv/play/1/index.m3u8").status_code == 200   # đường phát không lọc theo sức khỏe
    monkeypatch.setattr(geo, "current", lambda now=None: HealthMode("auto", False, "MY", 1.0))
    assert len(c.get("/api/hometv/channels").json()) == 1
