import asyncio
from types import SimpleNamespace

import pytest

from hometv import db as hdb, fetcher, geo, health, health_scheduler, health_store, registry, sources
from hometv.geo import HealthMode
from hometv.health import FfprobeMissing, HealthResult
from tests.hometv.test_hometv_catalog import seed_channel, seed_source, seed_stream
from tests.hometv.test_hometv_registry import E, PL

OK = HealthResult(ok=True, height=720)


def run(coro):
    return asyncio.run(coro)


def count(what):
    return hdb.conn().execute(f"SELECT COUNT(*) FROM {what}").fetchone()[0]


@pytest.fixture
def fake_check(monkeypatch):
    """Thay check_stream: ghi lại các URL đã kiểm và trả OK (hoặc kết quả/exception do test đặt)."""
    calls = SimpleNamespace(urls=[], result=OK, hook=None, active=0, peak=0, delay=0.0)

    async def fake(client, url, headers, *, probe=None):
        calls.urls.append(url)
        calls.active += 1
        calls.peak = max(calls.peak, calls.active)
        try:
            if calls.hook:
                calls.hook(url)
            if calls.delay:
                await asyncio.sleep(calls.delay)
            if isinstance(calls.result, Exception):
                raise calls.result
            return calls.result
        finally:
            calls.active -= 1
    monkeypatch.setattr(health, "check_stream", fake)
    return calls


def two_streams(db, host="s.example"):
    seed_source(db, "a")
    seed_channel(db, 1, "VTV1")
    seed_stream(db, 1, 1, "a", f"https://{host}/1.m3u8")
    seed_stream(db, 2, 1, "a", f"https://{host}/2.m3u8")


def test_check_source_records_each_stream_and_finishes(db, fake_check):
    two_streams(db)
    n = run(health_scheduler.check_source(None, "a", 100.0))
    assert n == 2 and count("stream_health") == 2
    assert db.execute("SELECT next_check_at FROM sources WHERE id='a'").fetchone()[0] == 100.0 + health_store.CHECK_EVERY
    assert "a" not in health_store.checking


def test_unapproved_source_stops_mid_run(db, fake_check):
    two_streams(db)
    fake_check.hook = lambda url: db.execute("UPDATE sources SET rights_status='pending_review'") if url.endswith("/2.m3u8") else None
    run(health_scheduler.check_source(None, "a", 100.0))
    assert [r[0] for r in db.execute("SELECT stream_id FROM stream_health")] == [1]


def test_exception_on_one_stream_marks_dead_and_continues(db, fake_check):
    two_streams(db)
    calls = {"n": 0}

    def boom(url):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("bất ngờ")
    fake_check.hook = boom
    run(health_scheduler.check_source(None, "a", 100.0))
    rows = {r["stream_id"]: r["ok"] for r in db.execute("SELECT stream_id, ok FROM stream_health")}
    assert sorted(rows.values()) == [0, 1] and len(rows) == 2


def test_checking_flag_cleared_even_on_ffprobe_missing(db, fake_check):
    two_streams(db)
    fake_check.result = FfprobeMissing()
    with pytest.raises(FfprobeMissing):
        run(health_scheduler.check_source(None, "a", 100.0))
    assert "a" not in health_store.checking


def test_run_due_skips_unapproved_disabled_and_future(db, fake_check):
    seed_source(db, "ok"); seed_source(db, "pend", rights="pending_review")
    seed_source(db, "off", enabled=0); seed_source(db, "later")
    db.execute("UPDATE sources SET next_check_at=999 WHERE id='later'")
    for i, s in enumerate(["ok", "pend", "off", "later"], 1):
        seed_channel(db, i); seed_stream(db, i, i, s, f"https://h{i}.example/x.m3u8")
    assert run(health_scheduler.run_due_checks(None, 100.0)) == ["ok"]
    assert fake_check.urls == ["https://h1.example/x.m3u8"]


def test_max_four_probes_in_parallel(db, fake_check):
    seed_source(db, "a")
    seed_channel(db, 1)
    for i in range(1, 9):
        seed_stream(db, i, 1, "a", f"https://h{i}.example/x.m3u8")
    fake_check.delay = 0.05
    run(health_scheduler.run_due_checks(None, 100.0))
    assert fake_check.peak == health_scheduler.MAX_PROBES == 4


def test_one_probe_per_host(db, fake_check):
    two_streams(db, host="same.example")
    fake_check.delay = 0.05
    run(health_scheduler.run_due_checks(None, 100.0))
    assert fake_check.peak == 1


def patch_loop(monkeypatch, enabled, due=None):
    async def fake_refresh(client, mode, now):
        return HealthMode(mode, enabled, "VN" if enabled else "MY", now)
    monkeypatch.setattr(geo, "refresh_mode", fake_refresh)
    monkeypatch.setattr(geo, "current", lambda now=None: HealthMode("auto", enabled, "VN", 1.0))
    ran = []

    async def fake_due(client, now, **kw):
        ran.append(now)
        if isinstance(due, Exception):
            raise due
        return []
    monkeypatch.setattr(health_scheduler, "run_due_checks", fake_due)
    return ran


def test_loop_idle_when_disabled(monkeypatch):
    ran = patch_loop(monkeypatch, enabled=False)

    async def go():
        t = asyncio.create_task(health_scheduler.hometv_health_checker(tick=0.01))
        await asyncio.sleep(0.1)
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)
    run(go())
    assert ran == []


def test_loop_runs_when_enabled(monkeypatch):
    ran = patch_loop(monkeypatch, enabled=True)

    async def go():
        t = asyncio.create_task(health_scheduler.hometv_health_checker(tick=0.01))
        await asyncio.sleep(0.1)
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)
    run(go())
    assert len(ran) >= 2


def test_loop_stops_cleanly_when_ffprobe_missing(monkeypatch):
    ran = patch_loop(monkeypatch, enabled=True, due=FfprobeMissing())
    run(asyncio.wait_for(health_scheduler.hometv_health_checker(tick=0.01), 2))
    assert len(ran) == 1   # trả về, không ném lỗi


def test_loop_cancellation_clears_checking(db, fake_check, monkeypatch):
    two_streams(db)
    fake_check.delay = 5
    monkeypatch.setattr(geo, "current", lambda now=None: HealthMode("auto", True, "VN", 1.0))

    async def fake_refresh(client, mode, now):
        return HealthMode(mode, True, "VN", now)
    monkeypatch.setattr(geo, "refresh_mode", fake_refresh)

    async def go():
        t = asyncio.create_task(health_scheduler.hometv_health_checker(tick=0.01))
        for _ in range(100):
            if health_store.checking:
                break
            await asyncio.sleep(0.01)
        assert health_store.checking == {"a"}
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)
    run(go())
    assert health_store.checking == set()


def test_approving_source_makes_it_due_now(db, public_dns):
    src = sources.create("A", "https://a.example/a.m3u", 1.0)
    db.execute("UPDATE sources SET next_check_at=9999 WHERE id=?", (src["id"],))
    sources.update(src["id"], {"rights_status": "approved"}, 50.0)
    assert db.execute("SELECT next_check_at FROM sources WHERE id=?", (src["id"],)).fetchone()[0] == 50.0
    db.execute("UPDATE sources SET next_check_at=9999 WHERE id=?", (src["id"],))
    sources.update(src["id"], {"rights_status": "approved", "priority": 5}, 60.0)   # đã duyệt sẵn: không đổi lịch
    assert db.execute("SELECT next_check_at FROM sources WHERE id=?", (src["id"],)).fetchone()[0] == 9999


def test_changed_ingest_makes_due_now_but_unchanged_does_not(db, public_dns):
    src = sources.create("A", "https://a.example/a.m3u", 1.0)
    sid = src["id"]
    stats = SimpleNamespace(channels=1, streams=1)
    sources.record_success(sid, now=10.0, content_hash="h1", body=b"x", stats=stats, entry_count=1)
    assert db.execute("SELECT next_check_at FROM sources WHERE id=?", (sid,)).fetchone()[0] == 10.0
    db.execute("UPDATE sources SET next_check_at=9999 WHERE id=?", (sid,))
    sources.record_success(sid, now=20.0, content_hash="h1", body=b"x", stats=stats, entry_count=1)
    sources.record_unchanged(sid, now=30.0)
    assert db.execute("SELECT next_check_at FROM sources WHERE id=?", (sid,)).fetchone()[0] == 9999


def test_existing_health_survives_reingest(db):
    hdb.conn().execute("INSERT INTO sources(id, name, url) VALUES ('a', 'a', 'https://x/a')")
    registry.ingest("a", PL(E("VTV1", "https://a/1"), E("VTV2", "https://a/2")), 1.0)
    stream_id = hdb.conn().execute("SELECT id FROM streams ORDER BY id").fetchone()[0]
    health_store.record(stream_id, OK, True, 2.0)
    registry.ingest("a", PL(E("VTV1", "https://a/1")), 3.0)
    assert count("stream_health") == 1
