import gzip

import pytest

from hometv import db as hdb, sources
from hometv.m3u import Entry, Playlist
from hometv.registry import IngestStats

ENTRY = Entry(name="A", url="https://x/1")
URL = "https://example.com/a.m3u"


def row(sid):
    return hdb.conn().execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone()


def test_seed_defaults_once(db):
    assert sources.seed_defaults(1.0) == 5 and sources.seed_defaults(2.0) == 0
    assert sources.delete("doubo-vn") is True
    assert sources.seed_defaults(3.0) == 0
    s = sources.internal("iptv-org-vn")
    assert s["rights_status"] == "pending_review" and s["enabled"] and s["priority"] == 20
    assert s["refresh_interval"] == 21600 and s["next_fetch_at"] == 1.0
    assert sources.get("iptv-org-vn")["enabled"] is True
    assert [d["priority"] for d in sources.DEFAULT_SOURCES] == [10, 20, 30, 40, 50]


def test_seed_defaults_no_dns(db, monkeypatch):
    import hometv.netguard

    def boom(host):
        raise AssertionError("không được phân giải DNS")
    monkeypatch.setattr(hometv.netguard, "resolve", boom)
    assert sources.seed_defaults(1.0) == 5


def test_create_validates(db, public_dns):
    with pytest.raises(sources.ValidationError) as e:
        sources.create("", "ftp://x", 1.0)
    assert set(e.value.errors) == {"name", "url"}
    assert e.value.errors["name"] == "Tên phải từ 1 đến 60 ký tự"
    assert hdb.conn().execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0


def test_create_ok(db, public_dns):
    s = sources.create("Nguồn Thử", URL, 5.0)
    assert s["id"] == "nguon-thu" and s["rights_status"] == "pending_review"
    assert s["enabled"] is True and s["refresh_hours"] == 6
    assert sources.internal("nguon-thu")["next_fetch_at"] == 5.0


def test_create_blocks_private_url(db, monkeypatch):
    import hometv.netguard
    monkeypatch.setattr(hometv.netguard, "resolve", lambda host: ["10.0.0.1"])
    with pytest.raises(sources.ValidationError) as e:
        sources.create("X", URL, 1.0)
    assert e.value.errors == {"url": "Địa chỉ bị chặn"}


@pytest.mark.parametrize("changes,field", [
    ({"priority": 0}, "priority"), ({"priority": 101}, "priority"),
    ({"priority": True}, "priority"), ({"priority": "5"}, "priority"), ({"priority": 5.0}, "priority"),
    ({"refresh_hours": 0}, "refresh_hours"), ({"refresh_hours": 169}, "refresh_hours"),
    ({"refresh_hours": 1.5}, "refresh_hours"),
    ({"rights_status": "ok"}, "rights_status"), ({"enabled": "yes"}, "enabled"),
    ({"enabled": 1}, "enabled"), ({"name": "x" * 61}, "name"), ({"name": ""}, "name"),
    ({"url": "ftp://x"}, "url"), ({"bogus": 1}, "bogus")])
def test_update_validates(db, public_dns, changes, field):
    sources.create("A", URL, 1.0)
    with pytest.raises(sources.ValidationError) as e:
        sources.update("a", {**changes, "name": changes.get("name", "B")}, 2.0)
    assert field in e.value.errors
    assert sources.get("a")["name"] == "A"


def test_update_collects_all_errors_and_unknown(db, public_dns):
    sources.create("A", URL, 1.0)
    with pytest.raises(sources.ValidationError) as e:
        sources.update("a", {"priority": 0, "enabled": "x", "zzz": 1}, 2.0)
    assert e.value.errors == {"priority": "Ưu tiên phải từ 1 đến 100",
                              "enabled": "Phải là true hoặc false", "zzz": "không hỗ trợ"}


def test_update_ok(db, public_dns):
    sources.create("A", URL, 1.0)
    s = sources.update("a", {"name": "B", "priority": 7, "refresh_hours": 12, "enabled": False,
                             "rights_status": "approved"}, 2.0)
    assert (s["name"], s["priority"], s["refresh_hours"], s["enabled"], s["rights_status"]) == \
        ("B", 7, 12, False, "approved")
    assert s["id"] == "a" and sources.internal("a")["updated_at"] == 2.0


def test_update_url_resets_fetch_state(db, public_dns):
    sources.create("A", URL, 1.0)
    hdb.conn().execute("UPDATE sources SET content_hash='h', retry_count=2, last_error='e', "
                       "next_fetch_at=NULL WHERE id='a'")
    s = sources.update("a", {"url": "https://example.com/b.m3u"}, 9.0)
    assert s["url"].endswith("/b.m3u") and s["last_error"] is None
    s = sources.internal("a")
    assert s["content_hash"] is None
    assert s["next_fetch_at"] == 9.0 and s["retry_count"] == 0 and s["last_error"] is None


def test_update_unknown_raises_keyerror(db):
    with pytest.raises(KeyError):
        sources.update("nope", {"name": "x"}, 1.0)


def test_slug_unique(db, public_dns):
    assert sources.create("IPTV Org Vietnam", URL, 1.0)["id"] == "iptv-org-vietnam"
    assert sources.create("IPTV Org Vietnam", URL, 1.0)["id"] == "iptv-org-vietnam-2"
    assert sources.create("IPTV Org Vietnam", URL, 1.0)["id"] == "iptv-org-vietnam-3"
    assert sources.slugify("Đài Truyền Hình!") == "dai-truyen-hinh"


def test_delete_prunes_orphans(db, public_dns):
    sources.create("A", URL, 1.0)
    c = hdb.conn()
    c.execute("INSERT INTO channels(id, display_name) VALUES (1, 'k')")
    c.execute("INSERT INTO streams(channel_id, source_id, url, url_hash) VALUES (1,'a','u','h')")
    assert sources.delete("a") is True and sources.delete("a") is False
    assert c.execute("SELECT COUNT(*) FROM channels").fetchone()[0] == 0


def test_check_version():
    assert sources.check_version(None, Playlist([], 0, False)) == "Playlist hỏng"
    assert sources.check_version(None, Playlist([], 0, True)) == "Playlist hỏng"
    assert sources.check_version(None, Playlist([ENTRY], 0, False)) == "Playlist hỏng"
    assert sources.check_version(100, Playlist([ENTRY] * 49, 0, True)) == "Mất quá nửa số kênh"
    assert sources.check_version(100, Playlist([ENTRY] * 50, 0, True)) is None
    assert sources.check_version(None, Playlist([ENTRY], 0, True)) is None


def test_next_retry_delay():
    assert sources.next_retry_delay(0, 0.0) == 60 and sources.next_retry_delay(1, 1.0) == 360
    assert sources.next_retry_delay(2, 0.5) == 990 and sources.next_retry_delay(3, 0.0) is None


def test_record_failure_schedule(db, public_dns):
    sources.create("A", URL, 0.0)
    for i, d in enumerate((60, 300, 900)):
        sources.record_failure("a", now=100.0, message="lỗi", retryable=True, rand=0.5)
        r = row("a")
        assert r["next_fetch_at"] == pytest.approx(100 + d * 1.1) and r["retry_count"] == i + 1
        assert r["last_error"] == "lỗi" and r["last_fetch_at"] == 100.0
    sources.record_failure("a", now=100.0, message="lỗi", retryable=True, rand=0.5)
    r = row("a")
    assert r["next_fetch_at"] == 100 + 21600 and r["retry_count"] == 0


def test_blocked_failure_stops_retrying(db, public_dns):
    sources.create("A", URL, 0.0)
    sources.record_failure("a", now=5.0, message="Địa chỉ bị chặn", retryable=False, rand=0.0)
    assert row("a")["next_fetch_at"] is None and sources.due(1e12) == []
    assert sources.request_refresh("a", 50.0) is True
    assert [s["id"] for s in sources.due(50.0)] == ["a"]
    sources.record_failure("a", now=5.0, message="x", retryable=False, rand=0.0)
    sources.update("a", {"url": "https://example.com/c.m3u"}, 60.0)
    assert [s["id"] for s in sources.due(60.0)] == ["a"]
    assert sources.request_refresh("nope", 1.0) is False


def stats():
    return IngestStats(channels=3, streams=4, new_streams=4, deactivated=0)


def test_record_success_and_unchanged(db, public_dns):
    sources.create("A", URL, 0.0)
    sources.record_failure("a", now=1.0, message="e", retryable=True, rand=0.0)
    sources.record_success("a", now=10.0, content_hash="h1", body=b"#EXTM3U\n", stats=stats(), entry_count=4)
    r = row("a")
    assert (r["content_hash"], r["channel_count"], r["stream_count"], r["retry_count"], r["last_error"]) == \
        ("h1", 3, 4, 0, None)
    assert r["last_success_at"] == 10.0 and r["next_fetch_at"] == 10.0 + 21600
    v = hdb.conn().execute("SELECT * FROM source_versions").fetchall()
    assert len(v) == 1 and gzip.decompress(v[0]["body"]) == b"#EXTM3U\n" and v[0]["entry_count"] == 4
    assert sources.last_good_entry_count("a") == 4
    sources.record_success("a", now=11.0, content_hash="h1", body=b"#EXTM3U\n", stats=stats(), entry_count=4)
    assert hdb.conn().execute("SELECT COUNT(*) FROM source_versions").fetchone()[0] == 1
    sources.record_unchanged("a", now=20.0)
    r = row("a")
    assert r["last_fetch_at"] == 20.0 and r["last_success_at"] == 20.0 and r["next_fetch_at"] == 20.0 + 21600


def test_last_good_entry_count_none(db, public_dns):
    sources.create("A", URL, 0.0)
    assert sources.last_good_entry_count("a") is None


def test_versions_trimmed_to_five(db, public_dns):
    sources.create("A", URL, 0.0)
    for i in range(8):
        sources.record_success("a", now=float(i), content_hash=f"h{i}", body=b"x", stats=stats(), entry_count=i)
    rows = hdb.conn().execute("SELECT entry_count FROM source_versions ORDER BY fetched_at").fetchall()
    assert [r[0] for r in rows] == [3, 4, 5, 6, 7] and sources.last_good_entry_count("a") == 7


def test_due_skips_disabled(db, public_dns):
    sources.create("A", URL, 0.0)
    sources.create("B", URL, 0.0)
    sources.update("a", {"priority": 90}, 0.0)
    assert [s["id"] for s in sources.due(1.0)] == ["b", "a"]
    sources.update("b", {"enabled": False}, 0.0)
    assert [s["id"] for s in sources.due(1.0)] == ["a"]
    assert sources.due(-1.0) == []


def test_list_sources_fetching_flag(db, public_dns):
    sources.create("A", URL, 0.0)
    c = hdb.conn()
    c.execute("INSERT INTO jobs(job_type, source_id, status, started_at) VALUES ('fetch','a','running',900)")
    s = sources.list_sources(1000.0)[0]
    assert s["fetching"] is True and s["refresh_hours"] == 6 and s["enabled"] is True
    assert set(s) == {"id", "name", "url", "enabled", "priority", "rights_status", "refresh_hours",
                      "last_fetch_at", "last_success_at", "last_error", "channel_count",
                      "stream_count", "fetching"}
    assert sources.list_sources(1600.0)[0]["fetching"] is False  # 700 giây trước


def test_init_seeds(app_module):
    assert len(sources.list_sources(1.0)) == 5


def test_get_create_update_list_same_shape(db, public_dns):
    created = sources.create("A", URL, 1.0)
    got = sources.get("a", 1.0)
    updated = sources.update("a", {"priority": 3}, 2.0)
    listed = sources.list_sources(2.0)[0]
    keys = ["id", "name", "url", "enabled", "priority", "rights_status", "refresh_hours", "last_fetch_at",
            "last_success_at", "last_error", "channel_count", "stream_count", "fetching"]
    for d in (created, got, updated, listed):
        assert list(d) == keys
        assert d["enabled"] is True and d["fetching"] is False
    assert updated == listed
    assert sources.get("nope") is None and sources.internal("nope") is None
    assert {"content_hash", "retry_count", "timeout", "max_size_mb", "next_fetch_at",
            "refresh_interval"} <= set(sources.internal("a"))


def seed_version(sid, n=100):
    sources.record_success(sid, now=1.0, content_hash="h", body=b"x", stats=stats(), entry_count=n)


def test_url_change_drops_old_baseline(db, public_dns):
    sources.create("A", URL, 0.0)
    seed_version("a")
    assert sources.last_good_entry_count("a") == 100
    sources.update("a", {"url": "https://example.com/new.m3u"}, 5.0)
    assert sources.last_good_entry_count("a") is None
    assert hdb.conn().execute("SELECT COUNT(*) FROM source_versions").fetchone()[0] == 0
    assert sources.check_version(sources.last_good_entry_count("a"), Playlist([ENTRY] * 10, 0, True)) is None


def test_same_url_keeps_state(db, public_dns):
    sources.create("A", URL, 0.0)
    seed_version("a")
    sources.record_failure("a", now=2.0, message="e", retryable=True, rand=0.0)
    before = sources.internal("a")
    sources.update("a", {"url": URL, "priority": 9}, 7.0)
    after = sources.internal("a")
    for k in ("content_hash", "retry_count", "last_error", "next_fetch_at"):
        assert after[k] == before[k]
    assert after["priority"] == 9 and sources.last_good_entry_count("a") == 100
