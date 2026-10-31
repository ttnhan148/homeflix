import inspect
import logging

import pytest

import hometv


def test_open_creates_schema_wal_fk(tmp_path):
    hometv.init(str(tmp_path / "h.db"))
    c = hometv.db.conn()
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 3
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"sources", "source_versions", "channels", "streams", "jobs", "schema_version"} <= tables
    idx = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert len([i for i in idx if not i.startswith("sqlite_")]) >= 6
    hometv.db.close()


def test_init_twice_is_idempotent(tmp_path):
    p = str(tmp_path / "h.db")
    hometv.init(p)
    hometv.init(p)
    c = hometv.db.conn()
    assert c.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 1
    hometv.db.close()


def test_tx_rolls_back_on_error(db):
    with pytest.raises(ValueError):
        with hometv.db.tx() as c:
            c.execute("INSERT INTO channels(display_name) VALUES ('x')")
            raise ValueError("boom")
    assert db.execute("SELECT COUNT(*) FROM channels").fetchone()[0] == 0
    with hometv.db.tx() as c:
        c.execute("INSERT INTO channels(display_name) VALUES ('y')")
    assert db.execute("SELECT COUNT(*) FROM channels").fetchone()[0] == 1


def test_delete_source_cascades(db):
    with hometv.db.tx() as c:
        c.execute("INSERT INTO sources(id, name, url) VALUES ('s', 'S', 'http://x')")
        c.execute("INSERT INTO channels(display_name) VALUES ('c')")
        cid = c.execute("SELECT id FROM channels").fetchone()[0]
        c.execute("INSERT INTO streams(channel_id, source_id, url, url_hash) VALUES (?, 's', 'http://x/1', 'h')", (cid,))
        c.execute("INSERT INTO source_versions(source_id, content_hash, fetched_at, entry_count, body) VALUES ('s', 'h', 1.0, 1, x'00')")
    with hometv.db.tx() as c:
        c.execute("DELETE FROM sources WHERE id='s'")
    assert db.execute("SELECT COUNT(*) FROM streams").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM source_versions").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM channels").fetchone()[0] == 1


def test_delete_channel_cascades_streams(db):
    with hometv.db.tx() as c:
        c.execute("INSERT INTO sources(id, name, url) VALUES ('s', 'S', 'http://x')")
        c.execute("INSERT INTO channels(display_name) VALUES ('c')")
        cid = c.execute("SELECT id FROM channels").fetchone()[0]
        c.execute("INSERT INTO streams(channel_id, source_id, url, url_hash) VALUES (?, 's', 'u', 'h')", (cid,))
    with hometv.db.tx() as c:
        c.execute("DELETE FROM channels")
    assert db.execute("SELECT COUNT(*) FROM streams").fetchone()[0] == 0


def test_stream_unique_per_source_and_hash(db):
    import sqlite3
    with hometv.db.tx() as c:
        c.execute("INSERT INTO sources(id, name, url) VALUES ('s', 'S', 'http://x')")
        c.execute("INSERT INTO channels(display_name) VALUES ('c')")
    with pytest.raises(sqlite3.IntegrityError):
        with hometv.db.tx() as c:
            for _ in range(2):
                c.execute("INSERT INTO streams(channel_id, source_id, url, url_hash) VALUES (1, 's', 'u', 'h')")


def test_conn_before_open_raises(tmp_path):
    hometv.init(str(tmp_path / "h.db"))
    hometv.db.close()
    with pytest.raises(RuntimeError):
        hometv.db.conn()


def test_init_hometv_failure_returns_false(app_module, monkeypatch, caplog):
    def boom(path):
        raise RuntimeError("disk gone")
    monkeypatch.setattr(hometv, "init", boom)
    with caplog.at_level(logging.ERROR, logger=app_module.logger.name):
        assert app_module._init_hometv() is False
    assert "disk gone" in caplog.text


def test_init_hometv_success_returns_true(app_module, monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(hometv, "init", lambda p: called.append(p))
    assert app_module._init_hometv() is True
    assert called and called[0].endswith("hometv.db")


def test_lifespan_calls_init_hometv(app_module):
    assert "_init_hometv()" in inspect.getsource(app_module.lifespan)


def test_source_refresh_interval_defaults_to_6h(db):
    with hometv.db.tx() as c:
        c.execute("INSERT INTO sources(id, name, url) VALUES ('s', 'S', 'http://x')")
    assert db.execute("SELECT refresh_interval FROM sources").fetchone()[0] == 21600


def test_favorites_recents_tables_cascade(db):
    db.execute("INSERT INTO channels(id, display_name) VALUES (1, 'a')")
    db.execute("INSERT INTO favorites(channel_id, created_at) VALUES (1, 1.0)")
    db.execute("INSERT INTO recents(channel_id, viewed_at) VALUES (1, 1.0)")
    db.execute("DELETE FROM channels WHERE id = 1")
    assert db.execute("SELECT COUNT(*) FROM favorites").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM recents").fetchone()[0] == 0


def test_open_upgrades_v1_db(tmp_path):
    import sqlite3
    p = str(tmp_path / "old.db")
    old = sqlite3.connect(p)
    old.executescript("CREATE TABLE schema_version (version INTEGER NOT NULL); INSERT INTO schema_version VALUES (1);")
    old.commit()
    old.close()
    hometv.db.open(p)
    c = hometv.db.conn()
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 3
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"favorites", "recents"} <= tables
    hometv.db.close()


def test_stream_health_cascades_with_stream(db):
    db.execute("INSERT INTO sources(id, name, url) VALUES ('a', 'a', 'https://x/a')")
    db.execute("INSERT INTO channels(id, display_name) VALUES (1, 'k')")
    db.execute("INSERT INTO streams(id, channel_id, source_id, url, url_hash) VALUES (1, 1, 'a', 'https://x', 'h')")
    db.execute("INSERT INTO stream_health(stream_id, checked_at, ok) VALUES (1, 1.0, 1)")
    db.execute("DELETE FROM streams WHERE id = 1")
    assert db.execute("SELECT COUNT(*) FROM stream_health").fetchone()[0] == 0


def test_open_upgrades_v2_db_adds_columns(tmp_path):
    import sqlite3
    p = str(tmp_path / "v2.db")
    old = sqlite3.connect(p)
    old.executescript(
        "CREATE TABLE schema_version (version INTEGER NOT NULL); INSERT INTO schema_version VALUES (2);"
        "CREATE TABLE sources (id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL,"
        " priority INTEGER NOT NULL DEFAULT 50, enabled INTEGER NOT NULL DEFAULT 1,"
        " rights_status TEXT NOT NULL DEFAULT 'pending_review', refresh_interval INTEGER NOT NULL DEFAULT 21600,"
        " timeout INTEGER NOT NULL DEFAULT 30, max_size_mb INTEGER NOT NULL DEFAULT 20, last_fetch_at REAL,"
        " last_success_at REAL, last_error TEXT, content_hash TEXT, channel_count INTEGER NOT NULL DEFAULT 0,"
        " stream_count INTEGER NOT NULL DEFAULT 0, retry_count INTEGER NOT NULL DEFAULT 0, next_fetch_at REAL,"
        " created_at REAL, updated_at REAL);"
        "INSERT INTO sources(id, name, url) VALUES ('keep', 'Giữ', 'https://x/k');")
    old.commit()
    old.close()
    hometv.db.open(p)
    c = hometv.db.conn()
    cols = {r[1] for r in c.execute("PRAGMA table_info(sources)")}
    assert {"next_check_at", "last_check_at"} <= cols
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 3
    assert "stream_health" in {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert c.execute("SELECT name FROM sources WHERE id='keep'").fetchone()[0] == "Giữ"
    hometv.db.close()
