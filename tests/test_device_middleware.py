from tests.cast_helpers import real_id
import re, uuid

def test_first_request_sets_cookie_with_required_attributes(make_client):
    r = make_client().get("/api/device")
    assert r.status_code == 200
    sc = r.headers["set-cookie"]
    assert sc.startswith("hf_device=")
    for part in ("HttpOnly", "SameSite=lax", "Path=/", "Max-Age=63072000"):
        assert part.lower() in sc.lower()
    assert "secure" not in sc.lower()
    uuid.UUID(re.match(r"hf_device=([^;]+)", sc).group(1), version=4)

def test_second_request_keeps_same_device_without_new_cookie(make_client):
    c = make_client()
    c.get("/api/device"); first = c.cookies["hf_device"]
    r = c.get("/api/device")
    assert c.cookies["hf_device"] == first and "set-cookie" not in r.headers

def test_forged_cookie_is_replaced_with_new_device(make_client):
    c = make_client()
    r = c.get("/api/device", cookies={"hf_device": "../../etc/passwd"})
    assert r.status_code == 200
    assert "hf_device=" in r.headers["set-cookie"]
    uuid.UUID(c.cookies["hf_device"], version=4)
    assert c.cookies["hf_device"] != "../../etc/passwd"

def test_valid_uuid_cookie_unknown_to_server_is_registered_as_given(app_module, make_client):
    c = make_client()
    gid = str(uuid.uuid4())
    assert c.get("/api/device", cookies={"hf_device": gid}).status_code == 200
    assert app_module.db_query_one("SELECT 1 FROM devices WHERE id=?", (gid,)) is not None

def test_secure_flag_on_https(app_module):
    from fastapi.testclient import TestClient
    c = TestClient(app_module.app, base_url="https://testserver", client=("203.0.113.7", 1))
    assert "secure" in c.get("/api/device").headers["set-cookie"].lower()

def test_no_cookie_on_proxy_and_static_paths(make_client):
    c = make_client()
    for path in ("/proxy/ts", "/static/logo.png", "/media/x/y.mp4"):
        assert "hf_device" not in c.get(path).headers.get("set-cookie", "")

def test_root_sets_cookie(make_client):
    assert "hf_device=" in make_client().get("/").headers["set-cookie"]

def test_default_name_and_rename(make_client):
    c = make_client()
    assert c.get("/api/device").json()["name"] == "iPhone"
    assert c.patch("/api/device", json={"name": "  Điện thoại bố  "}).json()["name"] == "Điện thoại bố"
    assert c.get("/api/device").json()["name"] == "Điện thoại bố"
    assert c.patch("/api/device", json={"name": "   "}).status_code == 422
    assert c.patch("/api/device", json={"name": "x" * 41}).status_code == 422
    assert c.patch("/api/device", json={"kind": "toaster"}).status_code == 422
    assert c.patch("/api/device", json={"kind": "tv"}).json()["kind"] == "tv"

def test_cache_headers_are_private(make_client):
    r = make_client().get("/api/device")
    assert r.headers["cache-control"] == "private, no-store"
    assert "cookie" in r.headers["vary"].lower()

def test_same_ip_same_household_different_ip_different(app_module, make_client):
    a, b, c3 = make_client(ip="203.0.113.7"), make_client(ip="203.0.113.7"), make_client(ip="198.51.100.9")
    ids = [real_id(x) for x in (a, b, c3)]
    rows = {i: app_module.db_query_one("SELECT household FROM devices WHERE id=?", (i,))[0] for i in ids}
    assert rows[ids[0]] == rows[ids[1]] != rows[ids[2]]

def test_last_seen_is_not_rewritten_within_60_seconds(app_module):
    d = app_module._db_touch_device("11111111-1111-4111-8111-111111111111", "h", "phone", "ua", 1000.0)
    again = app_module._db_touch_device(d["id"], "h", "phone", "ua", 1030.0)
    assert again["last_seen"] == 1000.0 and again["created"] is False
    later = app_module._db_touch_device(d["id"], "h", "phone", "ua", 1061.0)
    assert later["last_seen"] == 1061.0

def test_device_api_never_returns_id_or_cookie_value(make_client):
    c = make_client()
    bodies = [c.get("/api/device"), c.patch("/api/device", json={"name": "Máy A"})]
    cookie = c.cookies["hf_device"]
    for r in bodies:
        assert "id" not in r.json()
        assert cookie not in r.text
