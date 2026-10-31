import json

import pytest

from hometv import db as hdb, normalize, registry
from hometv.m3u import Entry, Playlist


def E(name, url, tvg_id="", group="", **kw):
    return Entry(name=name, url=url, tvg_id=tvg_id, group_title=group, **kw)


def PL(*entries):
    return Playlist(list(entries), 0, True)


def seed_source(sid, priority=50):
    hdb.conn().execute("INSERT INTO sources(id, name, url, priority) VALUES (?,?,?,?)",
                       (sid, sid, "https://x/" + sid, priority))


def count(what):
    return hdb.conn().execute(f"SELECT COUNT(*) FROM {what}").fetchone()[0]


def test_merge_by_tvg_id_across_sources(db):
    seed_source("a", 10); seed_source("b", 20)
    registry.ingest("a", PL(E("VTV1 HD", "https://a/1", "vtv1hd")), 1.0)
    registry.ingest("b", PL(E("Kênh Một", "https://b/1", "VTV1HD")), 2.0)
    assert count("channels") == 1 and count("streams") == 2


def test_merge_by_name_and_broadcaster(db):
    seed_source("a", 10); seed_source("b", 20)
    registry.ingest("a", PL(E("VTV1 HD", "https://a/1")), 1.0)
    registry.ingest("b", PL(E("VTV 1", "https://b/1")), 2.0)
    assert count("channels") == 1 and count("streams") == 2


def test_no_merge_without_broadcaster(db):
    seed_source("a", 10); seed_source("b", 20)
    registry.ingest("a", PL(E("ONE Muz", "https://a/1")), 1.0)
    registry.ingest("b", PL(E("ONE Muz", "https://b/1")), 2.0)
    assert count("channels") == 2


def test_placeholder_tvg_id_ignored(db):
    seed_source("a", 10); seed_source("b", 20)
    registry.ingest("a", PL(E("Alpha TV", "https://a/1", "no_epg")), 1.0)
    registry.ingest("b", PL(E("Beta TV", "https://b/1", "no_epg")), 2.0)
    assert count("channels") == 2


def test_empty_normalized_names_never_merge(db):
    seed_source("a", 10); seed_source("b", 20)
    registry.ingest("a", PL(E("HD", "https://a/1")), 1.0)
    registry.ingest("b", PL(E("SD", "https://b/1")), 2.0)
    assert count("channels") == 2


def test_same_source_reuses_its_channel(db):
    seed_source("a", 10)
    registry.ingest("a", PL(E("ONE Muz", "https://a/1")), 1.0)
    registry.ingest("a", PL(E("ONE Muz", "https://a/1")), 2.0)
    assert count("channels") == 1 and count("streams") == 1


def test_same_source_reuses_channel_for_new_url(db):
    seed_source("a", 10)
    registry.ingest("a", PL(E("ONE Muz", "https://a/1")), 1.0)
    registry.ingest("a", PL(E("ONE Muz", "https://a/2")), 2.0)
    assert count("channels") == 1 and count("streams") == 2


def test_same_url_in_two_sources_kept_twice(db):
    seed_source("a", 10); seed_source("b", 20)
    registry.ingest("a", PL(E("ONE Muz", "https://s/1")), 1.0)
    registry.ingest("b", PL(E("ONE Muz", "https://s/1")), 2.0)
    assert count("streams") == 2


def test_duplicate_url_in_one_playlist(db):
    seed_source("a", 10)
    s = registry.ingest("a", PL(E("ONE Muz", "https://a/1"), E("ONE Muz 2", "https://a/1")), 1.0)
    assert count("streams") == 1 and s.streams == 1 and s.new_streams == 1


def test_missing_stream_deactivated_not_deleted(db):
    seed_source("a", 10)
    registry.ingest("a", PL(E("VTV1", "https://a/1"), E("VTV2", "https://a/2")), 1.0)
    s = registry.ingest("a", PL(E("VTV1", "https://a/1")), 2.0)
    assert s.deactivated == 1 and count("streams") == 2 and count("streams WHERE active=1") == 1
    assert (s.channels, s.streams, s.new_streams) == (1, 1, 0)
    # liệt kê lại thì active=1, id giữ nguyên
    ids = [r[0] for r in hdb.conn().execute("SELECT id FROM streams ORDER BY id")]
    registry.ingest("a", PL(E("VTV1", "https://a/1"), E("VTV2", "https://a/2")), 3.0)
    assert count("streams WHERE active=1") == 2
    assert ids == [r[0] for r in hdb.conn().execute("SELECT id FROM streams ORDER BY id")]
    assert hdb.conn().execute("SELECT MIN(last_seen_at) FROM streams").fetchone()[0] == 3.0


def test_display_name_from_highest_priority_source(db):
    seed_source("a", 10); seed_source("b", 5)
    registry.ingest("a", PL(E("VTV1 HD", "https://a/1")), 1.0)
    registry.ingest("b", PL(E("VTV 1", "https://b/1")), 2.0)
    assert hdb.conn().execute("SELECT display_name FROM channels").fetchone()[0] == "VTV 1"


def test_display_name_recomputed_when_best_source_stream_goes_away(db):
    seed_source("a", 10); seed_source("b", 5)
    registry.ingest("a", PL(E("VTV1 HD", "https://a/1")), 1.0)
    registry.ingest("b", PL(E("VTV 1", "https://b/1")), 2.0)
    registry.ingest("b", PL(E("Khác", "https://b/9")), 3.0)
    assert hdb.conn().execute(
        "SELECT display_name FROM channels WHERE id=1").fetchone()[0] == "VTV1 HD"


def test_logo_fills_only_when_empty(db):
    seed_source("a", 10); seed_source("b", 5)
    registry.ingest("a", PL(E("VTV1", "https://a/1", tvg_logo="https://l/a.png")), 1.0)
    registry.ingest("b", PL(E("VTV1", "https://b/1", tvg_logo="https://l/b.png")), 2.0)
    assert hdb.conn().execute("SELECT logo_url FROM channels").fetchone()[0] == "https://l/a.png"


def test_manual_category_not_overwritten(db):
    seed_source("a", 10)
    registry.ingest("a", PL(E("VTV1", "https://a/1", group="Tin tức")), 1.0)
    hdb.conn().execute("UPDATE channels SET category='Kids', category_source='manual'")
    registry.ingest("a", PL(E("VTV1", "https://a/1", group="Tin tức")), 2.0)
    assert hdb.conn().execute("SELECT category FROM channels").fetchone()[0] == "Kids"


def test_headers_quality_drm_stored(db):
    seed_source("a", 10)
    registry.ingest("a", PL(
        E("VTV1 HD", "https://a/1", http_headers={"user_agent": "UA", "referrer": "r" * 2000},
          drm_hint=True),
        E("VTV2", "http://a/2")), 1.0)
    r = hdb.conn().execute("SELECT * FROM streams WHERE url='https://a/1'").fetchone()
    assert json.loads(r["http_headers"]) == {"user_agent": "UA"}
    assert (r["quality_hint"], r["drm_hint"], r["protocol"]) == ("HD", 1, "https")
    r2 = hdb.conn().execute("SELECT * FROM streams WHERE url='http://a/2'").fetchone()
    assert r2["protocol"] == "http" and r2["drm_hint"] == 0


def test_empty_name_fallbacks(db):
    seed_source("a", 10)
    registry.ingest("a", PL(Entry(name="", url="https://a/1", tvg_name="Tên TVG"),
                            Entry(name="", url="https://host.example/2")), 1.0)
    names = {r[0] for r in hdb.conn().execute("SELECT display_name FROM streams")}
    assert names == {"Tên TVG", "host.example"}


def test_error_rolls_back_whole_ingest(db, monkeypatch):
    seed_source("a", 10)
    real = normalize.category
    n = {"i": 0}

    def boom(*a):
        n["i"] += 1
        if n["i"] == 2:
            raise RuntimeError("boom")
        return real(*a)

    monkeypatch.setattr(normalize, "category", boom)
    with pytest.raises(RuntimeError):
        registry.ingest("a", PL(E("VTV1", "https://a/1"), E("VTV2", "https://a/2")), 1.0)
    assert count("channels") == 0 and count("streams") == 0


def test_ingest_for_deleted_source_writes_nothing(db):
    s = registry.ingest("zzz", PL(E("VTV1", "https://a/1")), 1.0)
    assert s == registry.IngestStats(0, 0, 0, 0)
    assert count("channels") == 0 and count("streams") == 0


def test_prune_orphan_channels(db):
    seed_source("a", 10)
    registry.ingest("a", PL(E("VTV1", "https://a/1")), 1.0)
    hdb.conn().execute("DELETE FROM sources WHERE id='a'")
    with hdb.lock:
        assert registry.prune_orphan_channels(hdb.conn()) == 1
    assert count("channels") == 0


def test_stream_stays_in_channel_when_key_unchanged(db):
    # a tạo kênh theo tên; b gộp theo tên (kênh nhận tvg-id); b đổi tên nhưng giữ tvg-id; a nhập lại
    seed_source("a", 10); seed_source("b", 20)
    registry.ingest("a", PL(E("VTV1", "https://a/1")), 1.0)
    registry.ingest("b", PL(E("VTV 1", "https://b/1", "vtv1")), 2.0)
    registry.ingest("b", PL(E("Kênh X", "https://b/1", "vtv1")), 3.0)
    registry.ingest("a", PL(E("VTV1", "https://a/1")), 4.0)
    assert count("channels") == 1 and count("streams") == 2


def test_stream_moves_when_its_key_changes(db):
    seed_source("a", 10)
    registry.ingest("a", PL(E("VTV1", "https://a/1"), E("VTV2", "https://a/2")), 1.0)
    registry.ingest("a", PL(E("VTV1", "https://a/1"), E("VTV3", "https://a/2")), 2.0)
    chans = hdb.conn().execute("SELECT channel_id FROM streams").fetchall()
    assert len({r[0] for r in chans}) == 2
    assert count("channels") == 2  # kênh VTV2 cũ trống nên bị xoá, VTV3 mới
    assert hdb.conn().execute("SELECT normalized_name FROM channels ORDER BY id").fetchall()[1][0] == "vtv3"
