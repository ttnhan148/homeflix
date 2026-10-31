from tests.cast_helpers import real_id
OTHER_IP = "198.51.100.9"


class Rig:
    """Một iPhone và một TV cùng nhà; `sub` đóng vai kết nối SSE của TV."""

    def __init__(self, m, make_client, tv_ip="203.0.113.7", tv_sse=True):
        self.m = m
        self.phone, self.tv = make_client(), make_client(ip=tv_ip)
        self.phone_id = real_id(self.phone)
        self.tv_id = real_id(self.tv)
        self.tv_pid = m._cast_public_id(self.tv_id)          # id công khai: thứ duy nhất xuất hiện trên đường truyền
        self.phone_pid = m._cast_public_id(self.phone_id)
        self.tv.post("/api/devices/heartbeat", json={"kind": "tv"})
        self.phone.post("/api/devices/heartbeat", json={"kind": "phone"})   # controller còn sống (không bị coi là đã rời)
        self.sub = m.cast_hub.subscribe(self.tv_id) if tv_sse else None
        self.phone_sub = m.cast_hub.subscribe(self.phone_id)

    def connect(self, client=None):
        return (client or self.phone).post(f"/api/cast/{self.tv_pid}/connect")

    def answer(self, allow=True, client=None, controller_id=None):
        return (client or self.tv).post(f"/api/cast/requests/{controller_id or self.phone_pid}", json={"allow": allow})

    def tv_events(self):
        return self.sub.drain()

    def phone_events(self):
        return self.phone_sub.drain()


def test_first_connect_is_pending_and_tv_gets_request(app_module, make_client):
    r = Rig(app_module, make_client)
    res = r.connect()
    assert res.status_code == 202 and res.json() == {"status": "pending"}
    (event, data), = r.tv_events()
    assert event == "connect_request" and data == {"controller_id": r.phone_pid, "controller_name": "iPhone"}


def test_repeated_connect_while_pending_does_not_spam_tv(app_module, make_client):
    r = Rig(app_module, make_client)
    for _ in range(5):
        assert r.connect().status_code == 202
    assert [e for e, _ in r.tv_events()] == ["connect_request"]


def test_tv_allow_links_and_remembers(app_module, make_client):
    r = Rig(app_module, make_client)
    r.connect(); r.tv_events()
    assert r.answer(True).json() == {"status": "ok"}
    assert ("connect_result", {"target_id": r.tv_pid, "allowed": True}) in r.phone_events()
    assert ("controller_joined", {"controller_id": r.phone_pid, "name": "iPhone"}) in r.tv_events()
    assert app_module.cast_hub.linked_target(r.phone_id) == r.tv_id
    app_module.cast_hub.unlink(r.phone_id)
    again = r.connect()
    assert again.status_code == 200 and again.json() == {"status": "connected"}
    assert [e for e, _ in r.tv_events()] == ["controller_joined"]


def test_tv_deny_does_not_link_and_is_not_remembered(app_module, make_client):
    r = Rig(app_module, make_client)
    r.connect(); r.tv_events()
    assert r.answer(False).status_code == 200
    assert ("connect_result", {"target_id": r.tv_pid, "allowed": False}) in r.phone_events()
    assert app_module.cast_hub.linked_target(r.phone_id) is None
    assert r.connect().status_code == 202


def test_connect_other_household_is_403(app_module, make_client):
    r = Rig(app_module, make_client, tv_ip=OTHER_IP)
    assert r.connect().status_code == 403
    assert r.tv_events() == []


def test_connect_unknown_or_non_tv_target_or_self_is_403(app_module, make_client):
    r = Rig(app_module, make_client)
    assert r.phone.post("/api/cast/not-a-device/connect").status_code == 403
    other_phone = make_client()
    other_id = real_id(other_phone)
    assert r.phone.post(f"/api/cast/{app_module._cast_public_id(other_id)}/connect").status_code == 403
    assert r.tv.post(f"/api/cast/{r.tv_pid}/connect").status_code == 403


def test_connect_when_tv_has_no_sse_is_409(app_module, make_client):
    r = Rig(app_module, make_client, tv_sse=False)
    res = r.connect()
    assert res.status_code == 409 and res.json()["detail"] == "target offline"


def test_connect_when_control_switch_off_is_403(app_module, make_client):
    r = Rig(app_module, make_client)
    r.tv.patch("/api/cast/settings", json={"allow_control": False})
    assert r.connect().status_code == 403
    assert r.tv_events() == []


def test_answer_without_pending_or_from_other_household_is_403(app_module, make_client):
    r = Rig(app_module, make_client)
    assert r.answer(True).status_code == 403                    # chưa ai xin
    r.connect(); r.tv_events()
    stranger = make_client(ip=OTHER_IP)
    stranger.get("/api/device")
    assert r.answer(True, client=stranger).status_code == 403   # người lạ không duyệt thay TV
    assert r.answer("yes").status_code == 422                   # allow phải là bool
    assert r.answer(True).status_code == 200                    # yêu cầu thật vẫn còn


def test_disconnect_unlinks_and_tv_gets_controller_left(app_module, make_client):
    r = Rig(app_module, make_client)
    r.connect(); r.answer(True); r.tv_events()
    assert r.phone.delete("/api/cast/link").json() == {"status": "ok"}
    assert r.tv_events() == [("controller_left", {"controller_id": r.phone_pid})]
    assert app_module.cast_hub.linked_target(r.phone_id) is None
    assert r.phone.delete("/api/cast/link").status_code == 200   # không có liên kết vẫn ok
    assert r.tv_events() == []
