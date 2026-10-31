from tests.cast_helpers import real_id
from cast import CastHub

PHONE_IP, OTHER_IP = "203.0.113.7", "198.51.100.9"


def device_id(client):
    return real_id(client)


def make_tv(make_client, hub_clock=None, ip=PHONE_IP, kind="tv"):
    tv = make_client(ip=ip)
    tv_id = device_id(tv)
    assert tv.post("/api/devices/heartbeat", json={"kind": kind}).status_code == 200
    return tv, tv_id


def test_heartbeat_updates_kind_and_marks_online(app_module, make_client):
    tv = make_client()
    tv_id = device_id(tv)
    assert tv.get("/api/device").json()["kind"] == "phone"
    assert tv.post("/api/devices/heartbeat", json={"kind": "tv"}).json() == {"status": "ok"}
    assert tv.get("/api/device").json()["kind"] == "tv"
    assert app_module.cast_hub.is_online(tv_id)


def test_heartbeat_without_body_still_marks_online(app_module, make_client):
    c = make_client()
    cid = device_id(c)
    assert c.post("/api/devices/heartbeat").status_code == 200
    assert app_module.cast_hub.is_online(cid)


def test_heartbeat_rejects_unknown_kind_and_keeps_name(app_module, make_client):
    c = make_client()
    name = c.get("/api/device").json()["name"]
    assert c.post("/api/devices/heartbeat", json={"kind": "toaster"}).status_code == 422
    assert c.post("/api/devices/heartbeat", json={"kind": "tv", "name": "Hacked"}).status_code == 200
    assert c.get("/api/device").json()["name"] == name


def test_nearby_lists_only_same_household_tv_or_desktop_online(app_module, make_client):
    me = make_client()
    me.post("/api/devices/heartbeat", json={"kind": "phone"})
    _, tv_id = make_tv(make_client)
    _, desk_id = make_tv(make_client, kind="desktop")
    make_tv(make_client, ip=OTHER_IP)                       # TV nhà khác
    phone2 = make_client(); phone2.post("/api/devices/heartbeat", json={"kind": "phone"})   # điện thoại cùng nhà
    silent = make_client(); device_id(silent)               # TV cùng nhà nhưng chưa heartbeat
    silent.patch("/api/device", json={"kind": "tv"})
    ids = sorted(d["id"] for d in me.get("/api/devices/nearby").json())
    assert ids == sorted(app_module._cast_public_id(i) for i in (tv_id, desk_id))


def test_nearby_excludes_self_even_if_tv(app_module, make_client):
    tv, tv_id = make_tv(make_client)
    assert tv.get("/api/devices/nearby").json() == []


def test_nearby_drops_tv_after_40_seconds(app_module, make_client):
    t = [100.0]
    app_module.cast_hub = CastHub(clock=lambda: t[0])
    me = make_client()
    _, tv_id = make_tv(make_client)
    assert [d["id"] for d in me.get("/api/devices/nearby").json()] == [app_module._cast_public_id(tv_id)]
    t[0] += 41
    assert me.get("/api/devices/nearby").json() == []


def test_nearby_does_not_leak_private_fields(app_module, make_client):
    me = make_client()
    make_tv(make_client)
    (item,) = me.get("/api/devices/nearby").json()
    assert set(item) == {"id", "name", "kind", "online"} and item["online"] is True


def test_settings_default_and_patch(app_module, make_client):
    c = make_client()
    assert c.get("/api/cast/settings").json() == {"allow_control": True}
    assert c.patch("/api/cast/settings", json={"allow_control": False}).json() == {"allow_control": False}
    assert c.get("/api/cast/settings").json() == {"allow_control": False}
    assert c.patch("/api/cast/settings", json={"allow_control": "x"}).status_code == 422
    assert c.patch("/api/cast/settings", json={}).status_code == 422
