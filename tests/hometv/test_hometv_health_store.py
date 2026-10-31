from hometv import health_store
from hometv.health import HealthResult
from tests.hometv.test_hometv_catalog import seed_channel, seed_source, seed_stream

OK = HealthResult(ok=True, height=720, width=1280, vcodec="h264", throughput_kbps=3000, bitrate_kbps=1000)
DEAD = HealthResult(ok=False, error="HTTP 403")


def row(db, stream_id):
    return db.execute("SELECT * FROM stream_health WHERE stream_id=?", (stream_id,)).fetchone()


def setup(db, streams=2):
    seed_source(db, "a")
    seed_channel(db, 1, "VTV1")
    for i in range(1, streams + 1):
        seed_stream(db, i, 1, "a", "https://s/%d.m3u8" % i)


def test_record_fail_streak_counts_and_resets(db):
    setup(db)
    for n in (1, 2, 3):
        health_store.record(1, DEAD, True, float(n))
        assert row(db, 1)["fail_streak"] == n
    health_store.record(1, OK, True, 4.0)
    assert row(db, 1)["fail_streak"] == 0 and row(db, 1)["ok"] == 1


def test_record_sets_score_and_fields(db):
    setup(db)
    health_store.record(1, OK, True, 1.0)
    r = row(db, 1)
    assert (r["score"], r["height"], r["vcodec"], r["checked_at"]) == (50 + 18 + 15 + 10, 720, "h264", 1.0)
    health_store.record(2, DEAD, False, 1.0)
    assert row(db, 2)["score"] == 0 and row(db, 2)["error"] == "HTTP 403"


def test_record_missing_stream_ignored(db):
    setup(db)
    health_store.record(999, OK, True, 1.0)
    assert db.execute("SELECT COUNT(*) FROM stream_health").fetchone()[0] == 0


def test_eligible_rules(db):
    seed_source(db, "ok"); seed_source(db, "pend", rights="pending_review"); seed_source(db, "off", enabled=0)
    assert [health_store.eligible(s) for s in ("ok", "pend", "off", "none")] == [True, False, False, False]


def test_streams_to_check_skips_inactive_and_drm(db):
    seed_source(db, "a"); seed_channel(db, 1)
    seed_stream(db, 1, 1, "a", "https://s/1", headers={"user_agent": "UA"})
    seed_stream(db, 2, 1, "a", "http://s/2")
    seed_stream(db, 3, 1, "a", "https://s/3", drm=1)
    seed_stream(db, 4, 1, "a", "https://s/4", active=0)
    got = {s["id"]: s for s in health_store.streams_to_check("a")}
    assert set(got) == {1, 2}
    assert got[1]["http_headers"] == {"user_agent": "UA"} and got[1]["https"] is True and got[2]["https"] is False


def test_due_sources_null_and_future(db):
    seed_source(db, "a", 10); seed_source(db, "b", 20); seed_source(db, "c", 30); seed_source(db, "d", 40, rights="rejected")
    db.execute("UPDATE sources SET next_check_at=50 WHERE id='b'")
    db.execute("UPDATE sources SET next_check_at=500 WHERE id='c'")
    assert health_store.due_sources(100.0) == ["a", "b"]


def test_finish_source_sets_next_check(db):
    seed_source(db, "a")
    health_store.finish_source("a", 1000.0)
    r = db.execute("SELECT last_check_at, next_check_at FROM sources WHERE id='a'").fetchone()
    assert (r[0], r[1]) == (1000.0, 1000.0 + health_store.CHECK_EVERY)


def test_request_check_rejects_unapproved_or_disabled(db):
    seed_source(db, "ok"); seed_source(db, "pend", rights="pending_review"); seed_source(db, "off", enabled=0)
    db.execute("UPDATE sources SET next_check_at=9999 WHERE id='ok'")
    assert health_store.request_check("ok", 5.0) is True
    assert db.execute("SELECT next_check_at FROM sources WHERE id='ok'").fetchone()[0] == 5.0
    assert [health_store.request_check(s, 5.0) for s in ("pend", "off", "none")] == [False] * 3


def test_source_health_counts(db):
    setup(db, 4)
    health_store.record(1, OK, True, 1.0)
    health_store.record(2, DEAD, True, 1.0)
    health_store.checking.add("a")
    try:
        h = health_store.source_health(2.0)["a"]
    finally:
        health_store.checking.discard("a")
    assert h == {"checked": 2, "alive": 1, "dead": 1, "unknown": 2, "checking": True}
    assert health_store.source_health(2.0)["a"]["checking"] is False


def test_dead_streams_filters_and_has_no_url(db):
    setup(db, 3)
    health_store.record(1, OK, True, 1.0)
    for n in range(3):
        health_store.record(2, DEAD, True, float(n))
    health_store.record(3, DEAD, True, 1.0)
    dead = health_store.dead_streams("a", "dead", 50)
    assert [d["stream_id"] for d in dead] == [2, 3]
    assert dead[0]["channel_name"] and dead[0]["fail_streak"] == 3 and dead[0]["error"] == "HTTP 403"
    assert all("url" not in d for d in dead)
    assert len(health_store.dead_streams("a", "all", 50)) == 3
    assert len(health_store.dead_streams("a", "dead", 1)) == 1


def test_reingested_stream_has_no_health_row(db):
    setup(db, 1)
    health_store.record(1, DEAD, True, 1.0)
    db.execute("DELETE FROM streams WHERE id=1")
    seed_stream(db, 7, 1, "a", "https://s/new.m3u8")
    assert row(db, 7) is None
    assert health_store.source_health(2.0)["a"]["unknown"] == 1


# --- catalog dùng sức khỏe (Task 6) ---
from hometv import catalog  # noqa: E402


def graded(db, scores):
    """Một kênh, mỗi stream một nguồn khác nhau điểm: scores = {stream_id: HealthResult|None}."""
    seed_source(db, "a", 10); seed_source(db, "b", 20)
    seed_channel(db, 1, "VTV1")
    for sid, res in scores.items():
        seed_stream(db, sid, 1, "a" if sid % 2 else "b", "https://s/%d.m3u8" % sid)
        if res is not None:
            health_store.record(sid, res, True, 1.0)


def test_streams_sorted_by_score_when_enabled(db):
    low = HealthResult(ok=True, height=360)
    high = HealthResult(ok=True, height=1080)
    graded(db, {1: low, 2: high, 3: DEAD})
    assert [s["id"] for s in catalog.channel_streams(1, use_health=True)] == [2, 1, 3]


def test_unchecked_streams_rank_at_50(db):
    graded(db, {1: HealthResult(ok=True, height=360), 2: None, 3: DEAD})  # 1 -> 64+? > 50 ; 2 -> 50 ; 3 -> 0
    assert [s["id"] for s in catalog.channel_streams(1, use_health=True)] == [1, 2, 3]


def test_streams_order_unchanged_when_disabled(db):
    graded(db, {1: DEAD, 2: HealthResult(ok=True, height=1080)})
    assert [s["id"] for s in catalog.channel_streams(1)] == [1, 2]  # priority a (10) trước b (20)


def test_channel_hidden_when_all_streams_failed_3x(db):
    graded(db, {1: None, 2: None})
    for n in range(3):
        for sid in (1, 2):
            health_store.record(sid, DEAD, True, float(n))
    assert catalog.list_channels(9.0, use_health=True) == []


def test_channel_visible_with_one_unchecked_stream(db):
    graded(db, {1: None, 2: None})
    for n in range(3):
        health_store.record(1, DEAD, True, float(n))
    assert [c["id"] for c in catalog.list_channels(9.0, use_health=True)] == [1]


def test_channel_visible_after_one_success(db):
    graded(db, {1: None})
    for n in range(3):
        health_store.record(1, DEAD, True, float(n))
    assert catalog.list_channels(9.0, use_health=True) == []
    health_store.record(1, OK, True, 5.0)
    assert len(catalog.list_channels(9.0, use_health=True)) == 1


def test_stale_health_ignored_when_disabled(db):
    graded(db, {1: None})
    for n in range(9):
        health_store.record(1, DEAD, True, float(n))
    assert len(catalog.list_channels(9.0)) == 1
    assert len(catalog.list_channels(9.0, use_health=False)) == 1
