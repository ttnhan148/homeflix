import asyncio
import json
import threading
import time

import pytest

from cast import (MAX_SUBSCRIBERS_PER_DEVICE, PRESENCE_TTL, QUEUE_LIMIT, CastHub, format_sse,
                  validate_command, validate_state)

slug_ok = lambda s: isinstance(s, str) and 0 < len(s) <= 200 and s not in (".", "..") and not any(c in s for c in "/\\\x00")


def hub_with_clock():
    t = [1000.0]
    return CastHub(clock=lambda: t[0]), t


def test_publish_reaches_only_target_device_subscribers():
    hub, _ = hub_with_clock()
    a, b = hub.subscribe("a"), hub.subscribe("b")
    assert hub.publish("a", "command", {"type": "pause"}) == 1
    assert a.drain() == [("command", {"type": "pause"})]
    assert b.drain() == []
    assert hub.publish("nobody", "command", {}) == 0


def test_presence_expires_after_40_seconds():
    hub, t = hub_with_clock()
    assert PRESENCE_TTL == 40
    hub.heartbeat("tv", "tv")
    t[0] += 39
    assert hub.is_online("tv")
    t[0] += 2
    assert not hub.is_online("tv")
    assert not hub.is_online("never-seen")


def test_fifth_subscriber_closes_the_oldest():
    hub, _ = hub_with_clock()
    assert MAX_SUBSCRIBERS_PER_DEVICE == 4
    subs = [hub.subscribe("tv") for _ in range(5)]
    assert subs[0].closed and not any(s.closed for s in subs[1:])
    assert hub.has_subscribers("tv")
    assert len(hub._subs["tv"]) == 4


def test_unsubscribe_removes_and_has_subscribers_false():
    hub, _ = hub_with_clock()
    sub = hub.subscribe("tv")
    hub.unsubscribe(sub)
    assert not hub.has_subscribers("tv")
    assert sub.closed


def test_queue_limit_drops_oldest():
    hub, _ = hub_with_clock()
    sub = hub.subscribe("tv")
    for i in range(150):
        sub.put("state", {"n": i})
    got = sub.drain()
    assert QUEUE_LIMIT == 100 and len(got) == 100 and got[0][1]["n"] == 50 and got[-1][1]["n"] == 149


def test_drain_waits_for_put_from_another_thread():
    hub, _ = hub_with_clock()
    sub = hub.subscribe("tv")
    threading.Timer(0.05, lambda: sub.put("x", {"a": 1})).start()
    start = time.monotonic()
    assert sub.drain(timeout=2) == [("x", {"a": 1})]
    assert time.monotonic() - start < 1


def test_next_times_out_empty_and_wakes_on_put():
    hub, _ = hub_with_clock()
    sub = hub.subscribe("tv")

    async def scenario():
        assert await sub.next(0.05) == []
        threading.Timer(0.05, lambda: sub.put("x", {"a": 2})).start()
        return await sub.next(2)

    assert asyncio.run(scenario()) == [("x", {"a": 2})]


def test_link_unlink_controllers_of():
    hub, _ = hub_with_clock()
    hub.link("p1", "tv"); hub.link("p2", "tv")
    assert sorted(hub.controllers_of("tv")) == ["p1", "p2"] and hub.linked_target("p1") == "tv"
    hub.link("p1", "tv2")  # một controller chỉ liên kết một TV
    assert hub.controllers_of("tv") == ["p2"] and hub.linked_target("p1") == "tv2"
    assert hub.unlink("p2") == "tv" and hub.unlink("p2") is None
    assert hub.controllers_of("tv") == []


def test_publish_newest_only_reaches_last_subscriber():
    hub, _ = hub_with_clock()
    old, new = hub.subscribe("tv"), hub.subscribe("tv")
    assert hub.publish("tv", "command", {"type": "pause"}, newest_only=True) == 1
    assert old.drain() == [] and new.drain() == [("command", {"type": "pause"})]
    assert hub.publish("tv", "state", {}, newest_only=False) == 2


def test_pending_retry_after_interval_pending_for_and_clear():
    hub, t = hub_with_clock()
    assert hub.add_pending("tv", "p") is True
    t[0] += 5
    assert hub.add_pending("tv", "p") is False            # dưới 10 giây: không phát lại
    t[0] += 6
    assert hub.add_pending("tv", "p") is True             # quá 10 giây: cho phép phát lại yêu cầu
    hub.add_pending("tv", "q")
    assert sorted(hub.pending_for("tv")) == ["p", "q"] and hub.pending_for("other") == []
    t[0] += 601
    assert hub.pending_for("tv") == []                    # yêu cầu quá cũ bị bỏ
    hub.add_pending("tv", "r")
    hub.clear_pending("tv")
    assert hub.pending_for("tv") == [] and hub.add_pending("tv", "r") is True


def test_presence_dict_is_purged_of_long_gone_devices():
    hub, t = hub_with_clock()
    for i in range(600):
        hub.heartbeat(f"d{i}", "tv")
    t[0] += PRESENCE_TTL * 11
    hub.heartbeat("fresh", "tv")
    assert len(hub._presence) < 50 and hub.is_online("fresh")


def test_pending_is_deduplicated_per_pair():
    hub, _ = hub_with_clock()
    assert hub.add_pending("tv", "p") is True
    assert hub.add_pending("tv", "p") is False
    assert hub.add_pending("tv", "q") is True
    assert hub.pop_pending("tv", "p") is True and hub.pop_pending("tv", "p") is False


def test_state_roundtrip_and_forget_clears_everything():
    hub, _ = hub_with_clock()
    hub.set_state("tv", {"slug": "a"})
    assert hub.get_state("tv") == {"slug": "a"} and hub.get_state("other") is None
    hub.heartbeat("tv", "tv"); hub.link("p", "tv"); hub.add_pending("tv", "q"); sub = hub.subscribe("tv")
    hub.forget("tv")
    assert hub.get_state("tv") is None and not hub.is_online("tv") and hub.linked_target("p") is None
    assert hub.add_pending("tv", "q") is True and not hub.has_subscribers("tv") and sub.closed


GOOD = [
    {"type": "play", "slug": "phim-a", "episode_url": "https://x/1.m3u8", "position": 12.5},
    {"type": "play", "slug": "phim-a", "episode_url": None, "position": None},
    {"type": "play", "slug": "phim-a"},
    {"type": "pause"}, {"type": "resume"}, {"type": "stop"},
    {"type": "seek", "delta": -10}, {"type": "seek", "position": 30},
    {"type": "episode", "delta": 1}, {"type": "episode", "delta": -1},
    {"type": "options", "autoNext": True}, {"type": "options", "autoNext": False, "autoIntro": True},
]


@pytest.mark.parametrize("body", GOOD)
def test_validate_command_accepts_each_type(body):
    out = validate_command({**body, "junk": 1}, slug_ok)
    assert out["type"] == body["type"] and "junk" not in out


def test_validate_command_normalises_play_defaults():
    assert validate_command({"type": "play", "slug": "a"}, slug_ok) == {"type": "play", "slug": "a", "episode_url": None, "position": None}


BAD = [
    "not a dict", [1], None, {}, {"type": "boom"}, {"type": 5},
    {"type": "play"}, {"type": "play", "slug": ".."}, {"type": "play", "slug": "a/b"}, {"type": "play", "slug": 5},
    {"type": "play", "slug": "a", "episode_url": "file:///etc/passwd"},
    {"type": "play", "slug": "a", "episode_url": "http://x/" + "a" * 3000},
    {"type": "play", "slug": "a", "position": float("nan")}, {"type": "play", "slug": "a", "position": float("inf")},
    {"type": "play", "slug": "a", "position": True}, {"type": "play", "slug": "a", "position": -1},
    {"type": "play", "slug": "a", "position": 86401},
    {"type": "seek"}, {"type": "seek", "delta": 5, "position": 5}, {"type": "seek", "delta": 4000},
    {"type": "seek", "delta": "5"}, {"type": "seek", "position": -2},
    {"type": "episode", "delta": 2}, {"type": "episode"},
    {"type": "options"}, {"type": "options", "autoNext": "yes"}, {"type": "options", "autoNext": 1},
]


@pytest.mark.parametrize("body", BAD, ids=range(len(BAD)))
def test_validate_command_rejects(body):
    with pytest.raises(ValueError):
        validate_command(body, slug_ok)


def test_validate_state_ok_and_normalised():
    out = validate_state({"slug": "a", "title": "T", "episode": "Tập 1", "position": 3, "duration": 10, "playing": True, "x": 1}, slug_ok)
    assert out == {"slug": "a", "title": "T", "episode": "Tập 1", "position": 3.0, "duration": 10.0, "playing": True}
    idle = validate_state({"slug": None, "playing": False}, slug_ok)
    assert idle == {"slug": None, "title": None, "episode": None, "position": None, "duration": None, "playing": False}


def test_validate_state_keeps_only_boolean_prefs():
    out = validate_state({"slug": "a", "playing": True, "autoNext": False, "autoIntro": "yes"}, slug_ok)
    assert out["autoNext"] is False and "autoIntro" not in out


@pytest.mark.parametrize("body", [
    {"playing": "no"}, {"slug": "a/b", "playing": True}, {"slug": "a", "position": -1, "playing": True},
    {"slug": "a", "position": float("nan"), "playing": True}, {"slug": "a", "title": "x" * 201, "playing": True},
    {"slug": "a", "episode": 5, "playing": True}, "str", None, {},
])
def test_validate_state_rejects(body):
    with pytest.raises(ValueError):
        validate_state(body, slug_ok)


def test_format_sse_shape():
    assert format_sse("state", {"a": 1}) == 'event: state\ndata: {"a": 1}\n\n'
    framed = format_sse("x", {"t": "a\nb\r\nc"})
    assert framed.count("\n") == 3 and json.loads(framed.split("data: ")[1].strip()) == {"t": "a\nb\r\nc"}
