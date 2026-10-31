import asyncio

import httpx

from hometv import db as hdb, fetcher, registry, scheduler, sources

SAMPLE = ("#EXTM3U\n" + "".join(
    f'#EXTINF:-1 tvg-id="k{i}",Kênh {i}\nhttps://s.example/{i}.m3u8\n' for i in range(10))).encode()


def run(coro):
    return asyncio.run(coro)


def _resp(status, content=b""):
    # response dạng stream như transport thật
    return httpx.Response(status, headers={"content-length": str(len(content))},
                          stream=httpx.ByteStream(content))


def client(body=SAMPLE, status=200, handler=None):
    return fetcher.make_client(httpx.MockTransport(handler or (lambda r: _resp(status, body))))


def count(what):
    with hdb.lock:
        return hdb.conn().execute(f"SELECT COUNT(*) FROM {what}").fetchone()[0]


def refresh(c, sid, now=2.0):
    return run(scheduler.refresh_source(c, sid, now=now, rand=0.0))


def test_refresh_ok_ingests_and_records(db, public_dns):
    src = sources.create("A", "https://a.example/a.m3u", 1.0)
    assert refresh(client(), src["id"]) == "ok"
    s = sources.get(src["id"])
    assert s["channel_count"] > 0 and s["last_error"] is None
    assert sources.internal(src["id"])["next_fetch_at"] == 2.0 + 21600
    assert count("jobs") == 1


def test_same_hash_is_unchanged(db, public_dns):
    src = sources.create("A", "https://a.example/a.m3u", 1.0)
    assert refresh(client(), src["id"]) == "ok"
    assert refresh(client(), src["id"], now=10.0) == "unchanged"
    assert count("source_versions") == 1
    assert sources.internal(src["id"])["next_fetch_at"] == 10.0 + 21600


def test_broken_playlist_keeps_last_good(db, public_dns):
    src = sources.create("A", "https://a.example/a.m3u", 1.0)
    refresh(client(), src["id"])
    assert refresh(client(b"<html>"), src["id"], now=10.0) == "error"
    assert sources.get(src["id"])["last_error"] == "Playlist hỏng"
    assert count("streams WHERE active=1") == 10


def test_drop_over_half_keeps_last_good(db, public_dns):
    src = sources.create("A", "https://a.example/a.m3u", 1.0)
    refresh(client(), src["id"])
    small = '#EXTM3U\n#EXTINF:-1 tvg-id="k0",Kênh 0\nhttps://s.example/0.m3u8\n'.encode()
    assert refresh(client(small), src["id"], now=10.0) == "error"
    assert sources.get(src["id"])["last_error"] == "Mất quá nửa số kênh"
    assert count("streams WHERE active=1") == 10


def test_fetch_error_schedules_retry(db, public_dns):
    src = sources.create("A", "https://a.example/a.m3u", 1.0)
    assert refresh(client(status=500), src["id"], now=5.0) == "error"
    assert sources.get(src["id"])["last_error"] == "HTTP 500"
    assert sources.internal(src["id"])["next_fetch_at"] == 5.0 + 60


def test_lock_prevents_parallel_runs(db, public_dns):
    sources.create("A", "https://a.example/a.m3u", 1.0)
    assert scheduler.acquire_job("a", 100.0) is not None
    assert scheduler.acquire_job("a", 101.0) is None


def test_stale_lock_reclaimed(db, public_dns):
    sources.create("A", "https://a.example/a.m3u", 1.0)
    old = scheduler.acquire_job("a", 100.0)
    assert scheduler.acquire_job("a", 100.0 + 601) is not None
    with hdb.lock:
        r = hdb.conn().execute("SELECT status, error_message FROM jobs WHERE id=?", (old,)).fetchone()
    assert (r[0], r[1]) == ("error", "Lượt trước bị gián đoạn")


def test_deleted_mid_fetch_writes_nothing(db, public_dns):
    src = sources.create("A", "https://a.example/a.m3u", 1.0)

    def handler(req):
        sources.delete(src["id"])
        return _resp(200, SAMPLE)
    assert refresh(client(handler=handler), src["id"]) == "gone"
    assert count("streams") == 0 and count("channels") == 0
    with hdb.lock:
        assert hdb.conn().execute("SELECT status FROM jobs").fetchone()[0] != "running"


def test_ingest_error_rolls_back_and_records(db, public_dns, monkeypatch):
    src = sources.create("A", "https://a.example/a.m3u", 1.0)

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(registry, "ingest", boom)
    assert refresh(client(), src["id"]) == "error"
    assert sources.get(src["id"])["last_error"] == "Lỗi nhập dữ liệu"
    assert count("streams") == 0
    with hdb.lock:
        assert hdb.conn().execute("SELECT status FROM jobs").fetchone()[0] == "error"


def test_run_due_parallel_limits(db, public_dns):
    for n, host in (("A", "h1"), ("B", "h1"), ("C", "h2"), ("D", "h3")):
        sources.create(n, f"https://{host}.example/{n}.m3u", 1.0)
    cur = {"all": 0, "max_all": 0}
    hosts: dict = {}
    maxh: dict = {}

    async def handler(req):
        h = req.url.host
        cur["all"] += 1
        hosts[h] = hosts.get(h, 0) + 1
        cur["max_all"] = max(cur["max_all"], cur["all"])
        maxh[h] = max(maxh.get(h, 0), hosts[h])
        await asyncio.sleep(0.05)
        cur["all"] -= 1
        hosts[h] -= 1
        return _resp(200, SAMPLE)
    c = fetcher.make_client(httpx.MockTransport(handler))
    res = run(scheduler.run_due(c, 2.0))
    assert sorted(res) == ["ok"] * 4
    assert cur["max_all"] <= 2 and max(maxh.values()) == 1


def test_run_due_never_raises(db, public_dns, monkeypatch):
    sources.create("A", "https://a.example/a.m3u", 1.0)

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(scheduler, "acquire_job", boom)
    assert run(scheduler.run_due(client(), 2.0)) == ["error"]


def test_cleanup(db, public_dns):
    sources.create("A", "https://a.example/a.m3u", 1.0)
    day = 86400
    now = 100 * day
    with hdb.tx() as c:
        c.execute("INSERT INTO jobs(job_type, source_id, status, started_at) VALUES ('fetch','a','ok',?)",
                  (now - 31 * day,))
        c.execute("INSERT INTO jobs(job_type, source_id, status, started_at) VALUES ('fetch','a','ok',?)",
                  (now - 1 * day,))
        for i in range(8):
            c.execute("INSERT INTO source_versions(source_id, content_hash, fetched_at, entry_count, body) "
                      "VALUES ('a',?,?,1,x'00')", (f"h{i}", float(i)))
    scheduler.cleanup(now)
    assert count("jobs") == 1 and count("source_versions") == 5
    with hdb.lock:
        low = hdb.conn().execute("SELECT MIN(fetched_at) FROM source_versions").fetchone()[0]
    assert low == 3.0


def test_refresher_survives_errors(db, monkeypatch):
    calls = []

    async def fake_run_due(c, now):
        calls.append(now)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return []
    monkeypatch.setattr(scheduler, "run_due", fake_run_due)

    async def main():
        t = asyncio.create_task(scheduler.hometv_refresher(tick=0.01))
        await asyncio.sleep(0.15)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
    run(main())
    assert len(calls) >= 2


def test_event_loop_not_blocked_by_sibling_ingest(db, public_dns):
    # ingest lớn giữ db.lock trong luồng; nguồn kia lỗi giữa chừng không được làm đứng event loop
    import time
    big = ("#EXTM3U\n" + "".join(
        f'#EXTINF:-1 tvg-id="id{i}",Kênh {i}\nhttps://s.example/{i}.m3u8\n' for i in range(20000))).encode()
    sources.create("A", "https://h1.example/a.m3u", 1.0)
    sources.create("B", "https://h2.example/b.m3u", 1.0)

    async def handler(req):
        if req.url.host == "h1.example":
            return _resp(200, big)
        await asyncio.sleep(0.5)
        return _resp(500)
    gaps = []

    async def main():
        stop = False

        async def beat():
            last = time.monotonic()
            while not stop:
                await asyncio.sleep(0.01)
                t = time.monotonic()
                gaps.append(t - last)
                last = t
        hb = asyncio.create_task(beat())
        res = await scheduler.run_due(fetcher.make_client(httpx.MockTransport(handler)), time.time())
        stop = True
        await hb
        return res
    assert sorted(run(main())) == ["error", "ok"]
    assert max(gaps) < 0.3


def test_job_duration_is_real(db, public_dns):
    src = sources.create("A", "https://a.example/a.m3u", 1.0)

    async def handler(req):
        await asyncio.sleep(0.05)
        return _resp(200, SAMPLE)
    assert refresh(fetcher.make_client(httpx.MockTransport(handler)), src["id"], now=1000.0) == "ok"
    with hdb.lock:
        r = hdb.conn().execute("SELECT started_at, completed_at, duration_ms FROM jobs").fetchone()
    assert r[0] == 1000.0 and r[2] >= 40 and r[1] >= 1000.04


def test_dns_failure_schedules_retry(db, public_dns, monkeypatch):
    import socket
    import hometv.netguard
    src = sources.create("A", "https://a.example/a.m3u", 1.0)

    def boom(host):
        raise socket.gaierror(-3, "Temporary failure")
    monkeypatch.setattr(hometv.netguard, "resolve", boom)
    assert refresh(client(), src["id"], now=5.0) == "error"
    assert sources.get(src["id"])["last_error"] == "Không kết nối được"
    assert sources.internal(src["id"])["next_fetch_at"] == 5.0 + 60


def test_manual_refresh_bypasses_drop_check(db, public_dns):
    src = sources.create("A", "https://a.example/a.m3u", 1.0)
    refresh(client(), src["id"])
    small = ("#EXTM3U\n" + "".join(
        f'#EXTINF:-1 tvg-id="k{i}",Kênh {i}\nhttps://s.example/{i}.m3u8\n' for i in range(4))).encode()
    assert refresh(client(small), src["id"], now=10.0) == "error"
    sources.request_refresh(src["id"], 20.0)
    assert refresh(client(small), src["id"], now=20.0) == "ok"
    assert count("streams WHERE active=1") == 4


def test_cancel_run_due_closes_jobs_first(db, public_dns, monkeypatch):
    # hủy run_due giữa lúc đang tải: khi nó trả về, job phải đã đóng (không còn running)
    import time
    sources.create("A", "https://a.example/a.m3u", 1.0)
    sources.create("B", "https://b.example/b.m3u", 1.0)
    real = scheduler.finish_job
    calls = []

    def slow(*a, **k):
        calls.append(1)
        if len(calls) == 1:  # job đầu đóng chậm, job sau đóng ngay
            time.sleep(0.3)
        real(*a, **k)
    monkeypatch.setattr(scheduler, "finish_job", slow)

    async def handler(req):
        await asyncio.sleep(30)

    async def main():
        c = client(handler=handler)
        t = asyncio.create_task(scheduler.run_due(c, 2.0))
        await asyncio.sleep(0.3)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        n = count("jobs WHERE status='running'")
        await c.aclose()
        return n
    assert run(main()) == 0
