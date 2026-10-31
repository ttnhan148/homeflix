import inspect
import logging

import pytest

import hometv


def test_open_creates_schema_wal_fk(tmp_path):
    hometv.init(str(tmp_path / "h.db"))
    c = hometv.db.conn()
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 1
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
