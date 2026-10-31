import hometv
from hometv import geo, health_store
from hometv.geo import HealthMode
from hometv.health import HealthResult
from tests.hometv.test_hometv_catalog import seed_channel, seed_stream

BASE = "/api/admin/hometv"


def enabled(monkeypatch, on=True, country="VN"):
    monkeypatch.setattr(geo, "current", lambda now=None: HealthMode("auto", on, country, 1.0))


def seed_streams():
    c = hometv.db.conn()
    c.execute("UPDATE sources SET rights_status='approved' WHERE id='doubo-vn'")
    seed_channel(c, 1, "VTV1 <b>")
    seed_stream(c, 1, 1, "doubo-vn", "https://secret.example/1.m3u8")
    seed_stream(c, 2, 1, "doubo-vn", "https://secret.example/2.m3u8")
    health_store.record(1, HealthResult(ok=True, height=720), True, 1.0)
    health_store.record(2, HealthResult(ok=False, error="HTTP 403"), True, 1.0)


def test_health_routes_protected(app_module, pw, make_client):
    c = make_client()
    for method, path in (("GET", f"{BASE}/health/status"), ("POST", f"{BASE}/sources/doubo-vn/check"),
                         ("GET", f"{BASE}/sources/doubo-vn/health")):
        assert c.request(method, path, json={}).status_code == 403, path


def test_health_routes_404_when_admin_disabled(app_module, make_client, monkeypatch):
    monkeypatch.delenv("HOMEFLIX_ADMIN_PASSWORD", raising=False)
    assert make_client().get(f"{BASE}/health/status").status_code == 404


def test_status_reports_geo(admin_client, monkeypatch):
    enabled(monkeypatch, True, "VN")
    assert admin_client.get(f"{BASE}/health/status").json() == {"mode": "auto", "enabled": True, "country": "VN"}
    enabled(monkeypatch, False, None)
    assert admin_client.get(f"{BASE}/health/status").json() == {"mode": "auto", "enabled": False, "country": None}


def test_sources_list_has_health_counts(admin_client, app_module):
    seed_streams()
    src = {s["id"]: s for s in admin_client.get(f"{BASE}/sources").json()}
    assert src["doubo-vn"]["health"] == {"checked": 2, "alive": 1, "dead": 1, "unknown": 0, "checking": False}
    assert src["iptv-org-vn"]["health"]["checked"] == 0


def test_check_202_sets_next_check(admin_client, app_module, monkeypatch):
    seed_streams()
    enabled(monkeypatch)
    hometv.db.conn().execute("UPDATE sources SET next_check_at=9e12 WHERE id='doubo-vn'")
    r = admin_client.post(f"{BASE}/sources/doubo-vn/check", json={})
    assert r.status_code == 202 and r.json() == {"queued": True}
    assert hometv.db.conn().execute("SELECT next_check_at FROM sources WHERE id='doubo-vn'").fetchone()[0] < 9e12


def test_check_409_when_disabled(admin_client, app_module, monkeypatch):
    seed_streams()
    enabled(monkeypatch, False, "MY")
    r = admin_client.post(f"{BASE}/sources/doubo-vn/check", json={})
    assert r.status_code == 409 and "không ở Việt Nam" in r.json()["detail"]


def test_check_404_unknown_or_unapproved(admin_client, app_module, monkeypatch):
    enabled(monkeypatch)
    assert admin_client.post(f"{BASE}/sources/iptv-org-vn/check", json={}).status_code == 404   # chờ duyệt
    r = admin_client.post(f"{BASE}/sources/nope/check", json={})
    assert r.status_code == 404 and r.json()["detail"] == "Nguồn chưa duyệt hoặc đang tắt"
    assert admin_client.post(f"{BASE}/sources/BAD ID/check", json={}).status_code == 404


def test_post_check_requires_json_415(admin_client, app_module, monkeypatch):
    enabled(monkeypatch)
    assert admin_client.post(f"{BASE}/sources/doubo-vn/check", data={"a": "b"}).status_code == 415


def test_health_list_filters_dead_without_urls(admin_client, app_module):
    seed_streams()
    dead = admin_client.get(f"{BASE}/sources/doubo-vn/health?status=dead&limit=50").json()
    assert [d["stream_id"] for d in dead] == [2]
    assert dead[0]["channel_name"] == "VTV1 <b>" and dead[0]["error"] == "HTTP 403" and dead[0]["fail_streak"] == 1
    assert "secret" not in admin_client.get(f"{BASE}/sources/doubo-vn/health?status=all").text
    assert len(admin_client.get(f"{BASE}/sources/doubo-vn/health?status=all").json()) == 2


def test_health_list_validates_params(admin_client, app_module):
    for q in ("status=bogus", "limit=0", "limit=201", "limit=abc"):
        assert admin_client.get(f"{BASE}/sources/doubo-vn/health?{q}").status_code == 422, q
    assert admin_client.get(f"{BASE}/sources/nope/health").status_code == 404
