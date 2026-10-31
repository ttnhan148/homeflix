import pytest

import hometv
from hometv import registry, sources
from hometv.m3u import Entry, Playlist

BASE = "/api/admin/hometv/sources"
URL = "https://example.com/a.m3u"


def test_disabled_404(make_client, monkeypatch):
    monkeypatch.delenv("HOMEFLIX_ADMIN_PASSWORD", raising=False)
    assert make_client().get(BASE).status_code == 404


def test_non_admin_403(pw, make_client):
    assert make_client().get(BASE).status_code == 403


def test_every_route_protected(app_module, pw, make_client):
    # Mọi route của router đều phải chặn non-admin
    c = make_client()
    routes = hometv.api.make_router(app_module.require_admin).routes
    assert len(routes) == 5
    for r in routes:
        path = r.path.replace("{source_id}", "doubo-vn")
        for m in r.methods - {"HEAD", "OPTIONS"}:
            resp = c.request(m, path, json={})
            assert resp.status_code == 403, (m, r.path)


def test_list_has_seeded_defaults(admin_client):
    r = admin_client.get(BASE)
    assert r.status_code == 200
    assert [s["id"] for s in r.json()] == ["doubo-vn", "iptv-org-vn", "dangdvd-iptv",
                                           "khanh71-iptv", "khanh71-http"]
    assert len(r.json()[0]) == 13


def test_create_and_validate(admin_client, public_dns):
    r = admin_client.post(BASE, json={"name": "Nguồn Thử", "url": URL})
    assert r.status_code == 201
    assert r.json()["id"] == "nguon-thu" and r.json()["rights_status"] == "pending_review"
    r = admin_client.post(BASE, json={"name": "", "url": URL})
    assert r.status_code == 422 and "name" in r.json()["errors"]
    r = admin_client.post(BASE, json={"name": "X"})
    assert r.status_code == 422 and "url" in r.json()["errors"]


def test_create_requires_json(admin_client):
    r = admin_client.post(BASE, data={"name": "X", "url": URL})
    assert r.status_code == 415


@pytest.mark.parametrize("method", ["post", "patch"])
@pytest.mark.parametrize("body", [[1], "x", 5])
def test_non_object_body_422(admin_client, method, body):
    path = BASE if method == "post" else BASE + "/doubo-vn"
    r = getattr(admin_client, method)(path, json=body)
    assert r.status_code == 422
    assert r.json() == {"errors": {"body": "Dữ liệu không hợp lệ"}}


def test_invalid_json_422(admin_client):
    r = admin_client.post(BASE, content=b"{bad", headers={"content-type": "application/json"})
    assert r.status_code == 422 and r.json()["errors"] == {"body": "Dữ liệu không hợp lệ"}


def test_patch_approve_and_unknown(admin_client):
    r = admin_client.patch(BASE + "/doubo-vn", json={"rights_status": "approved"})
    assert r.status_code == 200 and r.json()["rights_status"] == "approved"
    r = admin_client.patch(BASE + "/khong-co", json={"rights_status": "approved"})
    assert r.status_code == 404 and r.json() == {"detail": "Không tìm thấy nguồn"}
    r = admin_client.patch(BASE + "/doubo-vn", json={"priority": 0})
    assert r.status_code == 422 and "priority" in r.json()["errors"]


def test_bad_id_404(admin_client):
    for bad in ("Doubo", "a_b", "x" * 81):
        assert admin_client.patch(f"{BASE}/{bad}", json={}).status_code == 404
        assert admin_client.request("DELETE", f"{BASE}/{bad}", json={}).status_code == 404
        assert admin_client.post(f"{BASE}/{bad}/refresh", json={}).status_code == 404


def test_refresh_202(admin_client):
    r = admin_client.post(BASE + "/doubo-vn/refresh", json={})
    assert r.status_code == 202 and r.json() == {"queued": True}
    assert admin_client.post(BASE + "/khong-co/refresh", json={}).status_code == 404


def test_delete_cascades(admin_client):
    registry.ingest("doubo-vn", Playlist([Entry(name="VTV1 HD", url="https://x/1")], 0, True), 1.0)
    c = hometv.db.conn()
    assert c.execute("SELECT COUNT(*) FROM streams WHERE source_id='doubo-vn'").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM channels").fetchone()[0] == 1
    r = admin_client.request("DELETE", BASE + "/doubo-vn", json={})
    assert r.status_code == 204 and r.content == b""
    assert c.execute("SELECT COUNT(*) FROM streams").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM channels").fetchone()[0] == 0
    assert admin_client.request("DELETE", BASE + "/doubo-vn", json={}).status_code == 404


def test_db_unavailable_503(admin_client):
    hometv.db.close()
    for r in (admin_client.get(BASE),
              admin_client.post(BASE, json={"name": "X", "url": URL}),
              admin_client.patch(BASE + "/doubo-vn", json={}),
              admin_client.post(BASE + "/doubo-vn/refresh", json={}),
              admin_client.request("DELETE", BASE + "/doubo-vn", json={})):
        assert r.status_code == 503
        assert r.json() == {"detail": "HomeTV chưa sẵn sàng"}
