import json

from hometv import catalog


def seed_source(c, sid, priority=10, rights="approved", enabled=1):
    c.execute("INSERT INTO sources(id, name, url, priority, enabled, rights_status) VALUES (?,?,?,?,?,?)",
              (sid, sid, "https://x/" + sid, priority, enabled, rights))


def seed_channel(c, cid, name="Kênh", category="News", logo=None, enabled=1):
    c.execute("INSERT INTO channels(id, display_name, normalized_name, category, logo_url, enabled) "
              "VALUES (?,?,?,?,?,?)", (cid, name, name.lower(), category, logo, enabled))


def seed_stream(c, sid, cid, source, url, name=None, drm=0, active=1, headers=None, quality=None):
    c.execute("INSERT INTO streams(id, channel_id, source_id, url, url_hash, http_headers, display_name, "
              "quality_hint, drm_hint, active) VALUES (?,?,?,?,?,?,?,?,?,?)",
              (sid, cid, source, url, "h%d" % sid, json.dumps(headers) if headers else None,
               name, quality, drm, active))


def test_only_approved_enabled_sources(db):
    seed_source(db, "ok"); seed_source(db, "pend", rights="pending_review")
    seed_source(db, "rej", rights="rejected"); seed_source(db, "off", enabled=0)
    for i, s in enumerate(["ok", "pend", "rej", "off"], 1):
        seed_channel(db, i); seed_stream(db, i, i, s, "https://s/%d" % i)
    assert [c["id"] for c in catalog.list_channels(1.0)] == [1]


def test_drm_inactive_and_disabled_channel_excluded(db):
    seed_source(db, "a")
    seed_channel(db, 1); seed_stream(db, 1, 1, "a", "https://s/1", drm=1)
    seed_channel(db, 2); seed_stream(db, 2, 2, "a", "https://s/2", active=0)
    seed_channel(db, 3, enabled=0); seed_stream(db, 3, 3, "a", "https://s/3")
    seed_channel(db, 4)
    assert catalog.list_channels(1.0) == []


def test_name_from_best_priority_stream(db):
    seed_source(db, "a", 20); seed_source(db, "b", 5)
    seed_channel(db, 1, "VTV1 HD")
    seed_stream(db, 1, 1, "a", "https://a/1", name="VTV1 HD")
    seed_stream(db, 2, 1, "b", "https://b/1", name="VTV 1")
    assert catalog.list_channels(1.0)[0]["name"] == "VTV 1"


def test_logo_proxied_and_null(db):
    seed_source(db, "a")
    seed_channel(db, 1, "A", logo="https://l/a b.png"); seed_stream(db, 1, 1, "a", "https://s/1")
    seed_channel(db, 2, "B"); seed_stream(db, 2, 2, "a", "https://s/2")
    r = {c["id"]: c for c in catalog.list_channels(1.0)}
    assert r[1]["logo"] == "/proxy/image?url=https%3A%2F%2Fl%2Fa%20b.png"
    assert r[2]["logo"] is None


def test_stream_order_priority_then_https(db):
    seed_source(db, "a", 10); seed_source(db, "b", 5)
    seed_channel(db, 1)
    seed_stream(db, 1, 1, "a", "http://a/1")
    seed_stream(db, 2, 1, "a", "https://a/2")
    seed_stream(db, 3, 1, "b", "http://b/3", quality="HD")
    assert [s["id"] for s in catalog.channel_streams(1)] == [3, 2, 1]
    assert catalog.channel_streams(1)[0]["quality_hint"] == "HD"
    assert catalog.channel_streams(99) == []


def test_valid_stream_parses_headers(db):
    seed_source(db, "a")
    seed_channel(db, 1)
    seed_stream(db, 1, 1, "a", "https://a/1", headers={"user_agent": "UA", "referrer": "https://r/"})
    seed_stream(db, 2, 1, "a", "https://a/2", drm=1)
    v = catalog.valid_stream(1)
    assert v == {"id": 1, "url": "https://a/1", "http_headers": {"user_agent": "UA", "referrer": "https://r/"}}
    assert catalog.valid_stream(2) is None and catalog.valid_stream(99) is None


def test_favorite_idempotent_and_invalid_channel(db):
    seed_source(db, "a"); seed_channel(db, 1); seed_stream(db, 1, 1, "a", "https://s/1")
    seed_channel(db, 2)
    assert catalog.set_favorite(1, True, 1.0) and catalog.set_favorite(1, True, 2.0)
    assert catalog.list_channels(3.0)[0]["favorite"] is True
    assert catalog.set_favorite(2, True, 1.0) is False
    assert catalog.set_favorite(1, False, 1.0) and catalog.set_favorite(1, False, 1.0)
    assert catalog.list_channels(3.0)[0]["favorite"] is False


def test_recent_keeps_20_and_reorders(db):
    seed_source(db, "a")
    for i in range(1, 23):
        seed_channel(db, i, "k%02d" % i); seed_stream(db, i, i, "a", "https://s/%d" % i)
        assert catalog.mark_recent(i, float(i))
    rows = [c for c in catalog.list_channels(99.0) if c["recent_at"] is not None]
    assert len(rows) == 20 and min(c["id"] for c in rows) == 3
    catalog.mark_recent(3, 100.0)
    assert {c["id"]: c["recent_at"] for c in catalog.list_channels(99.0)}[3] == 100.0
    assert catalog.mark_recent(999, 1.0) is False


def test_cascade_removes_favorite_when_channel_deleted(db):
    seed_source(db, "a"); seed_channel(db, 1); seed_stream(db, 1, 1, "a", "https://s/1")
    catalog.set_favorite(1, True, 1.0)
    db.execute("DELETE FROM channels WHERE id = 1")
    assert db.execute("SELECT COUNT(*) FROM favorites").fetchone()[0] == 0
