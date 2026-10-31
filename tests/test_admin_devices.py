import threading

from admin_helpers import post


def _devices(c):
    return c.get("/api/admin/devices").json()


def _me(c):
    return next(d for d in _devices(c) if d["is_self"])


def _other(c):
    return next(d for d in _devices(c) if not d["is_self"])


def _patch(c, pid, body):
    return c.patch(f"/api/admin/devices/{pid}", json=body)


def test_list_never_contains_real_ids(pw, make_client, admin_client):
    other = make_client(); other.get("/api/device")
    body = admin_client.get("/api/admin/devices").text
    assert other.cookies["hf_device"] not in body and admin_client.cookies["hf_device"] not in body


def test_list_marks_self_and_admin(pw, make_client, admin_client):
    other = make_client(); other.get("/api/device")
    rows = _devices(admin_client)
    assert len(rows) >= 2
    selves = [d for d in rows if d["is_self"]]
    assert len(selves) == 1 and selves[0]["is_admin"] is True
    assert set(selves[0]) == {"id", "name", "kind", "household", "last_seen", "is_admin", "is_self"}
    assert len(selves[0]["household"]) == 8


def test_non_admin_forbidden_and_disabled_404(pw, make_client, admin_client, monkeypatch):
    assert make_client().get("/api/admin/devices").status_code == 403
    monkeypatch.setenv("HOMEFLIX_ADMIN_PASSWORD", "")
    assert admin_client.get("/api/admin/devices").status_code == 404


def test_rename(pw, make_client, admin_client):
    other = make_client(); other.get("/api/device")
    t = _other(admin_client)
    r = _patch(admin_client, t["id"], {"name": "TV phòng khách"})
    assert r.status_code == 200 and r.json()["name"] == "TV phòng khách"
    assert any(d["name"] == "TV phòng khách" for d in _devices(admin_client))
    assert _patch(admin_client, t["id"], {"name": "  "}).status_code == 422
    assert _patch(admin_client, t["id"], {"name": "x" * 41}).status_code == 422


def test_grant_and_revoke(pw, make_client, admin_client, app_module):
    other = make_client(); other.get("/api/device")
    t = _other(admin_client)
    assert _patch(admin_client, t["id"], {"is_admin": True}).json()["is_admin"] is True
    assert app_module._db_is_admin(other.cookies["hf_device"])
    assert _patch(admin_client, t["id"], {"is_admin": False}).json()["is_admin"] is False
    assert not app_module._db_is_admin(other.cookies["hf_device"])
    assert _patch(admin_client, t["id"], {"is_admin": "yes"}).status_code == 422


def test_cannot_revoke_last_admin_even_self(pw, admin_client):
    me = _me(admin_client)
    assert _patch(admin_client, me["id"], {"is_admin": False}).status_code == 409


def test_self_revoke_allowed_when_another_admin_exists(pw, make_client, admin_client, app_module):
    b = make_client(); post(b, "/api/admin/login", {"password": "mk-test"})
    me = _me(admin_client)
    assert _patch(admin_client, me["id"], {"is_admin": False}).status_code == 200
    assert not app_module._db_is_admin(admin_client.cookies["hf_device"])


def test_last_admin_check_atomic(pw, make_client, admin_client, app_module):
    b = make_client(); post(b, "/api/admin/login", {"password": "mk-test"})
    ida, idb = admin_client.cookies["hf_device"], b.cookies["hf_device"]
    pa, pb = app_module._cast_public_id(ida), app_module._cast_public_id(idb)
    results = []
    def go(c, target):
        results.append(_patch(c, target, {"is_admin": False}).status_code)
    ts = [threading.Thread(target=go, args=(admin_client, pb)), threading.Thread(target=go, args=(b, pa))]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert app_module._db_admin_count() >= 1


def test_delete_removes_device_and_its_data(pw, make_client, admin_client, app_module):
    other = make_client()
    other.post("/api/saved", json={"slug": "phim-a", "name": "A", "episodes": []})
    oid = other.cookies["hf_device"]
    app_module.db_execute("INSERT INTO admin_requests (id, device_id, code, status, created_at) "
                          "VALUES ('r1', ?, '123456', 'pending', ?)", (oid, app_module._now()))
    app_module.db_execute("INSERT INTO cast_allowed (target_id, controller_id, created_at) VALUES (?, 'zz', 1)", (oid,))
    app_module.db_execute("INSERT INTO app_settings (key, value) VALUES (?, '1')", (app_module._cast_allow_key(oid),))
    target = next(d for d in _devices(admin_client) if not d["is_self"])
    assert admin_client.request("DELETE", f"/api/admin/devices/{target['id']}", json={}).status_code == 204
    q = app_module.db_query_one
    assert q("SELECT 1 FROM devices WHERE id = ?", (oid,)) is None
    assert q("SELECT 1 FROM device_movies WHERE device_id = ?", (oid,)) is None
    assert q("SELECT 1 FROM admin_requests WHERE device_id = ?", (oid,)) is None
    assert q("SELECT 1 FROM cast_allowed WHERE target_id = ?", (oid,)) is None
    assert q("SELECT 1 FROM app_settings WHERE key = ?", (app_module._cast_allow_key(oid),)) is None


def test_cannot_delete_self(pw, admin_client):
    me = _me(admin_client)
    assert admin_client.request("DELETE", f"/api/admin/devices/{me['id']}", json={}).status_code == 409


def test_unknown_id_404(pw, admin_client):
    assert admin_client.request("DELETE", "/api/admin/devices/nope", json={}).status_code == 404
    assert _patch(admin_client, "nope", {"name": "x"}).status_code == 404


def test_cache_clear_moved_to_admin(pw, make_client, admin_client):
    assert make_client().post("/api/cache/clear").status_code in (404, 405)
    assert post(make_client(), "/api/admin/cache/clear").status_code == 403
    assert post(admin_client, "/api/admin/cache/clear").json()["status"] == "success"
