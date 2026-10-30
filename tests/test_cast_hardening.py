import asyncio
import json

from cast import CastHub
from tests.cast_helpers import asgi_stream
from tests.test_cast_link_api import OTHER_IP, Rig

EPS = [{"name": "Tập 1", "link_m3u8": "http://x/1.m3u8"}]


def sse_text(app_module, client, until=lambda c: True):
    cookies = {"hf_device": client.cookies.get("hf_device")}
    _, _, chunks, _ = asyncio.run(asgi_stream(app_module.app, "/api/cast/events", cookies=cookies, until=until))
    return b"".join(chunks).decode()


def test_nearby_returns_public_ids_not_cookie_ids(app_module, make_client):
    r = Rig(app_module, make_client)
    (item,) = r.phone.get("/api/devices/nearby").json()
    assert item["id"] != r.tv_id and item["id"] == r.tv_pid


def test_stranger_cannot_impersonate_tv_with_nearby_id(app_module, make_client):
    r = Rig(app_module, make_client)
    r.tv.post("/api/saved", json={"slug": "secret-movie", "name": "Secret", "poster_url": "", "year": "2026", "episodes": EPS})
    stranger = make_client()
    (item,) = stranger.get("/api/devices/nearby").json()
    stranger.cookies.set("hf_device", item["id"])           # thử dùng id lấy từ danh sách làm cookie
    assert stranger.get("/api/saved").json() == []
    assert stranger.get("/api/device").json()["id"] != r.tv_id
    # đồng thời id thật của TV không lọt vào bất kỳ thứ gì stranger nhìn thấy
    assert r.tv_id not in json.dumps(stranger.get("/api/devices/nearby").json())


def test_events_and_hello_never_contain_cookie_ids(app_module, make_client):
    r = Rig(app_module, make_client)
    r.connect()
    sent = json.dumps(r.tv_events())
    assert r.phone_id not in sent and r.phone_pid in sent
    r.answer(True)
    tv_hello = sse_text(app_module, r.tv)
    assert r.phone_id not in tv_hello and r.phone_pid in tv_hello
    assert r.tv_id not in tv_hello


def test_public_id_does_not_resolve_in_another_household(app_module, make_client):
    r = Rig(app_module, make_client)
    outsider = make_client(ip=OTHER_IP)
    outsider.get("/api/device")
    assert outsider.post(f"/api/cast/{r.tv_pid}/connect").status_code == 403
    assert outsider.get(f"/api/cast/state/{r.tv_pid}").status_code == 403


def test_switch_off_notifies_controllers_and_clears_pending(app_module, make_client):
    r = Rig(app_module, make_client)
    other = make_client(ip="203.0.113.7")
    other_id = other.get("/api/device").json()["id"]
    other.post("/api/devices/heartbeat", json={"kind": "phone"})
    r.connect(); r.answer(True); r.tv_events(); r.phone_events()
    other.post(f"/api/cast/{r.tv_pid}/connect")             # một yêu cầu khác đang chờ
    r.tv_events()
    r.tv.patch("/api/cast/settings", json={"allow_control": False})
    assert ("unlinked", {"target_id": r.tv_pid}) in r.phone_events()
    r.tv.patch("/api/cast/settings", json={"allow_control": True})
    assert r.answer(True, controller_id=app_module._cast_public_id(other_id)).status_code == 403   # yêu cầu cũ đã bị xóa


def test_command_reaches_newest_tab_only(app_module, make_client):
    r = Rig(app_module, make_client)
    r.connect(); r.answer(True)
    newer = app_module.cast_hub.subscribe(r.tv_id)
    r.tv_events(); newer.drain()
    assert r.phone.post(f"/api/cast/{r.tv_pid}/command", json={"type": "pause"}).status_code == 200
    assert [e for e, _ in r.tv_events()] == []
    assert ("command", {"type": "pause"}) in newer.drain()


def test_connect_request_goes_to_newest_tab_only(app_module, make_client):
    r = Rig(app_module, make_client)
    newer = app_module.cast_hub.subscribe(r.tv_id)
    r.connect()
    assert r.tv_events() == []
    assert [e for e, _ in newer.drain()] == ["connect_request"]


def test_hello_includes_pending_requests_so_a_reloaded_tv_can_ask_again(app_module, make_client):
    r = Rig(app_module, make_client)
    r.connect()
    text = sse_text(app_module, r.tv)
    assert '"pending": [{"controller_id": "%s", "controller_name": "iPhone"}]' % r.phone_pid in text


def test_stale_controller_is_swept_on_tv_heartbeat(app_module, make_client):
    t = [100.0]
    app_module.cast_hub = CastHub(clock=lambda: t[0])
    r = Rig(app_module, make_client)
    r.connect(); r.answer(True); r.tv_events()
    t[0] += 30
    r.tv.post("/api/devices/heartbeat", json={"kind": "tv"})
    assert app_module.cast_hub.linked_target(r.phone_id) == r.tv_id          # iPhone còn trong cửa sổ 40 giây
    t[0] += 20                                                             # iPhone im lặng 50 giây
    r.tv.post("/api/devices/heartbeat", json={"kind": "tv"})
    assert app_module.cast_hub.linked_target(r.phone_id) is None
    assert ("controller_left", {"controller_id": r.phone_pid}) in r.tv_events()


def test_connect_is_republished_to_tv_after_retry_interval(app_module, make_client):
    t = [100.0]
    app_module.cast_hub = CastHub(clock=lambda: t[0])
    r = Rig(app_module, make_client)
    r.connect(); r.tv_events()
    t[0] += 5
    r.connect()
    assert r.tv_events() == []                       # quá sớm: không phát lại
    t[0] += 6
    assert r.connect().status_code == 202
    assert [e for e, _ in r.tv_events()] == ["connect_request"]
