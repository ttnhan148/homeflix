import json

import pytest

from cast import CastHub
from tests.test_cast_link_api import OTHER_IP, Rig


def approved(m, make_client, **kw):
    r = Rig(m, make_client, **kw)
    r.connect(); r.answer(True)
    r.tv_events(); r.phone_events()
    return r


def command(r, body, client=None, target=None):
    return (client or r.phone).post(f"/api/cast/{target or r.tv_pid}/command", json=body)


def raw_command(r, raw):
    return r.phone.post(f"/api/cast/{r.tv_pid}/command", content=raw, headers={"content-type": "application/json"})


def test_command_reaches_only_the_target_tv(app_module, make_client):
    r = approved(app_module, make_client)
    tv2 = make_client()
    tv2_id = tv2.get("/api/device").json()["id"]
    tv2.post("/api/devices/heartbeat", json={"kind": "tv"})
    sub2 = app_module.cast_hub.subscribe(tv2_id)
    r.phone.post(f"/api/cast/{app_module._cast_public_id(tv2_id)}/connect"); tv2.post(f"/api/cast/requests/{r.phone_pid}", json={"allow": True})
    sub2.drain()
    assert command(r, {"type": "pause"}).json() == {"status": "sent"}
    assert [e for e in r.tv_events() if e[0] == "command"] == [("command", {"type": "pause"})]
    assert [e for e in sub2.drain() if e[0] == "command"] == []


def test_play_command_is_normalised_and_delivered(app_module, make_client):
    r = approved(app_module, make_client)
    command(r, {"type": "play", "slug": "phim-a", "junk": 1})
    assert r.tv_events() == [("command", {"type": "play", "slug": "phim-a", "episode_url": None, "position": None})]


def test_command_requires_allowance(app_module, make_client):
    r = Rig(app_module, make_client)
    assert command(r, {"type": "pause"}).status_code == 403
    r.connect()                                  # mới xin, TV chưa cho phép
    assert command(r, {"type": "pause"}).status_code == 403
    assert [e for e, _ in r.tv_events()] == ["connect_request"]


def test_command_other_household_and_switch_off_are_403(app_module, make_client):
    r = approved(app_module, make_client)
    stranger = make_client(ip=OTHER_IP)
    stranger.get("/api/device")
    assert command(r, {"type": "pause"}, client=stranger).status_code == 403
    r.tv.patch("/api/cast/settings", json={"allow_control": False})
    r.tv_events()
    assert command(r, {"type": "pause"}).status_code == 403
    assert r.tv_events() == []


def test_command_to_offline_tv_is_409(app_module, make_client):
    r = approved(app_module, make_client)
    app_module.cast_hub.unsubscribe(r.sub)
    res = command(r, {"type": "pause"})
    assert res.status_code == 409 and res.json()["detail"] == "target offline"


def test_command_auto_links_after_server_memory_reset(app_module, make_client):
    r = approved(app_module, make_client)
    app_module.cast_hub = CastHub()                       # server khởi động lại: bộ nhớ trống, DB còn quyền
    tv_sub = app_module.cast_hub.subscribe(r.tv_id)
    phone_sub = app_module.cast_hub.subscribe(r.phone_id)
    r.phone.post("/api/devices/heartbeat", json={"kind": "phone"})   # nhịp tim kế tiếp của iPhone sau khi server khởi động lại
    assert app_module.cast_hub.linked_target(r.phone_id) is None
    assert command(r, {"type": "resume"}).json() == {"status": "sent"}
    # TV được báo controller vừa được lập lại liên kết, nên sẽ báo trạng thái trở lại
    assert tv_sub.drain() == [("controller_joined", {"controller_id": r.phone_pid, "name": "iPhone"}),
                              ("command", {"type": "resume"})]
    assert r.tv.post("/api/cast/state", json={"slug": None, "playing": False}).status_code == 200
    assert [e for e, _ in phone_sub.drain()] == ["state"]


BAD = [
    {"type": "boom"}, {"type": "play", "slug": ".."}, {"type": "play", "slug": "a/b"},
    {"type": "play", "slug": "a", "episode_url": "file:///etc/passwd"},
    {"type": "play", "slug": "a", "episode_url": "http://x/" + "a" * 3000},
    {"type": "seek", "delta": 1, "position": 1}, {"type": "episode", "delta": 2}, {"type": "options"},
]


@pytest.mark.parametrize("body", BAD, ids=range(len(BAD)))
def test_command_rejects_bad_payloads(app_module, make_client, body):
    r = approved(app_module, make_client)
    assert command(r, body).status_code == 422
    assert r.tv_events() == []


def test_command_rejects_non_object_and_nan_bodies(app_module, make_client):
    r = approved(app_module, make_client)
    assert raw_command(r, json.dumps([1, 2])).status_code == 422
    assert raw_command(r, json.dumps('"x"')).status_code == 422
    assert raw_command(r, '{"type": "seek", "position": NaN}').status_code == 422
    assert raw_command(r, '{"type": "play", "slug": "a", "position": Infinity}').status_code == 422
    assert r.tv_events() == []


STATE = {"slug": "phim-a", "title": "Phim A", "episode": "Tập 1", "position": 10, "duration": 100, "playing": True}


def test_state_fans_out_to_linked_controllers_only(app_module, make_client):
    r = approved(app_module, make_client)
    other = make_client()
    other_id = other.get("/api/device").json()["id"]
    other_sub = app_module.cast_hub.subscribe(other_id)          # cùng nhà nhưng chưa liên kết
    assert r.tv.post("/api/cast/state", json=STATE).json() == {"status": "ok"}
    assert r.phone_events() == [("state", {**STATE, "position": 10.0, "duration": 100.0, "target_id": r.tv_pid})]
    assert other_sub.drain() == []


def test_state_rejects_bad_payload_and_non_tv_caller(app_module, make_client):
    r = approved(app_module, make_client)
    assert r.tv.post("/api/cast/state", json={**STATE, "slug": "a/b"}).status_code == 422
    assert r.tv.post("/api/cast/state", json={**STATE, "playing": "yes"}).status_code == 422
    nan = r.tv.post("/api/cast/state", content='{"slug": "a", "position": NaN, "playing": true}', headers={"content-type": "application/json"})
    assert nan.status_code == 422
    assert r.phone.post("/api/cast/state", json=STATE).status_code == 403     # iPhone không phải TV
    assert r.phone_events() == []


def test_get_state_returns_latest_or_empty_and_enforces_access(app_module, make_client):
    r = approved(app_module, make_client)
    assert r.phone.get(f"/api/cast/state/{r.tv_pid}").json() == {}
    r.tv.post("/api/cast/state", json=STATE)
    got = r.phone.get(f"/api/cast/state/{r.tv_pid}").json()
    assert got["slug"] == "phim-a" and got["target_id"] == r.tv_pid
    stranger = make_client(ip=OTHER_IP)
    assert stranger.get(f"/api/cast/state/{r.tv_pid}").status_code == 403
    unapproved = make_client()
    assert unapproved.get(f"/api/cast/state/{r.tv_pid}").status_code == 403


def test_switching_control_off_unlinks_and_blocks_next_command(app_module, make_client):
    r = approved(app_module, make_client)
    assert r.tv.patch("/api/cast/settings", json={"allow_control": False}).status_code == 200
    assert r.tv_events() == [("controller_left", {"controller_id": r.phone_pid})]
    assert app_module.cast_hub.linked_target(r.phone_id) is None
    assert command(r, {"type": "pause"}).status_code == 403
