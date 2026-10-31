import json

import pytest

from admin_helpers import post


def test_admin_routes_404_when_password_unset(make_client, monkeypatch):
    monkeypatch.delenv("HOMEFLIX_ADMIN_PASSWORD", raising=False)
    c = make_client()
    assert post(c, "/api/admin/login", {"password": "x"}).status_code == 404
    assert post(c, "/api/admin/logout").status_code == 404
    assert post(c, "/api/admin/revoke-others").status_code == 404


def test_login_ok_makes_device_admin(pw, make_client, app_module):
    c = make_client()
    assert post(c, "/api/admin/login", {"password": "mk-test"}).status_code == 204
    assert app_module._db_is_admin(c.cookies["hf_device"])


def test_wrong_or_missing_password_is_401(pw, make_client):
    c = make_client()
    assert post(c, "/api/admin/login", {"password": "sai"}).status_code == 401
    assert post(c, "/api/admin/login", {}).status_code == 401
    assert post(c, "/api/admin/login", {"password": 123}).status_code == 401


def test_device_locked_after_five_failures_even_with_right_password(pw, make_client):
    c = make_client()
    for _ in range(5):
        post(c, "/api/admin/login", {"password": "sai"})
    r = post(c, "/api/admin/login", {"password": "mk-test"})
    assert r.status_code == 429 and r.json()["retry_after"] > 0


def test_ip_locked_across_devices(pw, make_client):
    for _ in range(5):
        post(make_client(ip="198.51.100.9"), "/api/admin/login", {"password": "sai"})
    assert post(make_client(ip="198.51.100.9"), "/api/admin/login", {"password": "mk-test"}).status_code == 429
    assert post(make_client(ip="198.51.100.10"), "/api/admin/login", {"password": "mk-test"}).status_code == 204


def test_lone_surrogate_password_is_401_and_counts_toward_lockout(pw, make_client):
    c = make_client()
    hdr = {"content-type": "application/json"}
    body = json.dumps({"password": "\ud800"})
    for _ in range(5):
        assert c.post("/api/admin/login", content=body, headers=hdr).status_code == 401
    assert c.post("/api/admin/login", content=body, headers=hdr).status_code == 429


def test_non_json_write_is_415(pw, make_client):
    r = make_client().post("/api/admin/login", content="password=mk-test",
                           headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 415


def test_missing_content_type_is_415(pw, make_client):
    c = make_client()
    assert c.post("/api/admin/login", content=b'{"password":"mk-test"}', headers={"content-type": ""}).status_code == 415
    assert c.patch("/api/settings", content=b"{}", headers={"content-type": "text/plain"}).status_code == 415
    assert c.delete("/api/admin/x", headers={"content-type": "text/plain"}).status_code == 415


@pytest.mark.parametrize("ct", ["application/json; charset=utf-8", "Application/JSON", "APPLICATION/JSON;charset=UTF-8"])
def test_json_content_type_variants_accepted(pw, make_client, ct):
    r = make_client().post("/api/admin/login", content=b'{"password":"mk-test"}', headers={"content-type": ct})
    assert r.status_code == 204


def test_lookalike_content_type_is_415(pw, make_client):
    r = make_client().post("/api/admin/login", content=b'{"password":"mk-test"}',
                           headers={"content-type": "application/jsonx"})
    assert r.status_code == 415


def test_logout_drops_own_rights(pw, make_client, app_module):
    c = make_client()
    post(c, "/api/admin/login", {"password": "mk-test"})
    assert app_module._db_is_admin(c.cookies["hf_device"])
    assert post(c, "/api/admin/logout").status_code == 204
    assert not app_module._db_is_admin(c.cookies["hf_device"])


def test_revoke_others_keeps_caller(pw, make_client, app_module):
    a, b = make_client(), make_client()
    post(a, "/api/admin/login", {"password": "mk-test"})
    post(b, "/api/admin/login", {"password": "mk-test"})
    assert post(a, "/api/admin/revoke-others").json() == {"revoked": 1}
    assert app_module._db_is_admin(a.cookies["hf_device"]) and not app_module._db_is_admin(b.cookies["hf_device"])


def test_revoke_others_requires_admin(pw, make_client):
    assert post(make_client(), "/api/admin/revoke-others").status_code == 403


def test_admin_count(pw, make_client, app_module):
    assert app_module._db_admin_count() == 0
    post(make_client(), "/api/admin/login", {"password": "mk-test"})
    assert app_module._db_admin_count() == 1


def test_is_admin_column_added_to_existing_db(app_module):
    app_module.init_db()
    app_module.init_db()
    cols = [r[1] for r in app_module.get_db().execute("PRAGMA table_info(devices)")]
    assert "is_admin" in cols


def test_logout_requires_admin(pw, make_client):
    assert post(make_client(), "/api/admin/logout").status_code == 403


def test_ipv6_same_64_shares_lockout(pw, make_client):
    for i in range(5):
        post(make_client(ip=f"2001:db8:1:2::{i + 1}"), "/api/admin/login", {"password": "sai"})
    assert post(make_client(ip="2001:db8:1:2:aaaa::9"), "/api/admin/login", {"password": "mk-test"}).status_code == 429
    assert post(make_client(ip="2001:db8:1:3::1"), "/api/admin/login", {"password": "mk-test"}).status_code == 204


def test_lockout_ip_key(app_module):
    k = app_module._lockout_ip_key
    assert k("203.0.113.7") == "ip:203.0.113.7"
    assert k("2001:db8:1:2::1") == k("2001:db8:1:2:ffff::2") != k("2001:db8:1:3::1")
    assert k("not-an-ip") == "ip:not-an-ip"


def test_concurrent_delayed_body_logins_still_lock(pw, app_module):
    import asyncio

    async def one(gate):
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
                 "path": "/api/admin/login", "raw_path": b"/api/admin/login", "query_string": b"",
                 "root_path": "", "scheme": "http",
                 "headers": [(b"host", b"homeflix.test"), (b"user-agent", b"pytest"),
                             (b"content-type", b"application/json")],
                 "client": ("203.0.113.50", 50000), "server": ("homeflix.test", 80)}
        sent, info = False, {}

        async def receive():
            nonlocal sent
            if not sent:
                await gate.wait()
                sent = True
                return {"type": "http.request", "body": b'{"password":"sai"}', "more_body": False}
            await asyncio.sleep(3600)

        async def send(msg):
            if msg["type"] == "http.response.start":
                info["status"] = msg["status"]

        await app_module.app(scope, receive, send)
        return info["status"]

    async def run():
        gate = asyncio.Event()
        tasks = [asyncio.create_task(one(gate)) for _ in range(30)]
        await asyncio.sleep(0.5)
        gate.set()
        return await asyncio.gather(*tasks)

    codes = asyncio.run(run())
    assert sorted(codes) == [401] * 5 + [429] * 25
