import time

from admin_helpers import TV_UA, post


def test_request_then_approve(pw, make_client, admin_client, app_module):
    tv = make_client(ua=TV_UA)
    r = post(tv, "/api/admin/requests").json()
    assert len(r["code"]) == 6 and tv.get("/api/admin/requests/mine").json()["status"] == "pending"
    [req] = admin_client.get("/api/admin/requests").json()
    assert req["code"] == r["code"] and req["device"]["kind"] == "tv"
    assert req["device"]["id"] != tv.cookies["hf_device"]
    assert tv.cookies["hf_device"] not in admin_client.get("/api/admin/requests").text
    assert post(admin_client, f"/api/admin/requests/{req['id']}/approve").status_code == 204
    assert tv.get("/api/admin/requests/mine").json()["status"] == "approved"
    assert app_module._db_is_admin(tv.cookies["hf_device"])
    # duyệt lần hai thua
    assert post(admin_client, f"/api/admin/requests/{req['id']}/approve").status_code == 409


def test_deny(pw, make_client, admin_client, app_module):
    tv = make_client(ua=TV_UA)
    post(tv, "/api/admin/requests")
    [req] = admin_client.get("/api/admin/requests").json()
    assert post(admin_client, f"/api/admin/requests/{req['id']}/deny").status_code == 204
    assert tv.get("/api/admin/requests/mine").json()["status"] == "denied"
    assert not app_module._db_is_admin(tv.cookies["hf_device"])
    assert post(admin_client, f"/api/admin/requests/{req['id']}/approve").status_code == 409
    assert not app_module._db_is_admin(tv.cookies["hf_device"])


def test_new_request_replaces_old(pw, make_client, admin_client):
    tv = make_client()
    post(tv, "/api/admin/requests")
    post(tv, "/api/admin/requests")
    assert len(admin_client.get("/api/admin/requests").json()) == 1


def test_expired_request(pw, make_client, admin_client, monkeypatch, app_module):
    tv = make_client()
    post(tv, "/api/admin/requests")
    [req] = admin_client.get("/api/admin/requests").json()
    later = time.time() + 601
    monkeypatch.setattr(app_module, "_now", lambda: later)
    assert tv.get("/api/admin/requests/mine").json()["status"] == "expired"
    assert admin_client.get("/api/admin/requests").json() == []
    assert post(admin_client, f"/api/admin/requests/{req['id']}/approve").status_code == 409


def test_approve_after_requester_deleted_is_409(pw, make_client, admin_client, app_module):
    tv = make_client()
    post(tv, "/api/admin/requests")
    [req] = admin_client.get("/api/admin/requests").json()
    app_module.db_execute("DELETE FROM devices WHERE id = ?", (tv.cookies["hf_device"],))
    assert post(admin_client, f"/api/admin/requests/{req['id']}/approve").status_code == 409


def test_pending_cap_is_20(pw, make_client):
    for _ in range(20):
        assert post(make_client(), "/api/admin/requests").status_code == 200
    assert post(make_client(), "/api/admin/requests").status_code == 429


def test_cap_ignores_own_request(pw, make_client):
    clients = [make_client() for _ in range(20)]
    for c in clients:
        assert post(c, "/api/admin/requests").status_code == 200
    assert post(clients[0], "/api/admin/requests").status_code == 200


def test_list_and_decide_require_admin(pw, make_client, admin_client):
    tv = make_client()
    post(tv, "/api/admin/requests")
    [req] = admin_client.get("/api/admin/requests").json()
    other = make_client()
    assert other.get("/api/admin/requests").status_code == 403
    assert post(other, f"/api/admin/requests/{req['id']}/approve").status_code == 403
    assert post(other, f"/api/admin/requests/{req['id']}/deny").status_code == 403


def test_mine_none_without_request(pw, make_client):
    assert make_client().get("/api/admin/requests/mine").json() == {"status": "none"}


def test_disabled_is_404(make_client, monkeypatch):
    monkeypatch.delenv("HOMEFLIX_ADMIN_PASSWORD", raising=False)
    c = make_client()
    assert post(c, "/api/admin/requests").status_code == 404
    assert c.get("/api/admin/requests/mine").status_code == 404
    assert c.get("/api/admin/requests").status_code == 404
    assert post(c, "/api/admin/requests/x/approve").status_code == 404
