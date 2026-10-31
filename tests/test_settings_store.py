import sqlite3
import threading

import pytest

from settings import SettingsError, SettingsStore


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.execute("CREATE TABLE app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    return c


@pytest.fixture
def store(conn):
    return SettingsStore(lambda: conn, threading.Lock())


def test_get_returns_default_when_unset(store):
    assert store.get("cache.max_gb") == 10


def test_update_then_get(store):
    store.update({"cache.max_gb": 20})
    assert store.get("cache.max_gb") == 20


def test_update_persists_as_json_under_prefixed_key(store, conn):
    store.update({"prefetch.concurrency": 6})
    row = conn.execute("SELECT value FROM app_settings WHERE key='setting:prefetch.concurrency'").fetchone()
    assert row[0] == "6"


@pytest.mark.parametrize("value", [0, 501])
def test_out_of_range_rejected(store, value):
    with pytest.raises(SettingsError) as e:
        store.update({"cache.max_gb": value})
    assert e.value.errors == {"cache.max_gb": "phải từ 1 đến 500"}


@pytest.mark.parametrize("value", [True, False, "10", 10.5, None])
def test_non_int_rejected(store, value):
    with pytest.raises(SettingsError) as e:
        store.update({"cache.max_gb": value})
    assert e.value.errors == {"cache.max_gb": "phải là số nguyên"}


def test_unknown_key_rejected(store):
    with pytest.raises(SettingsError) as e:
        store.update({"nope": 1})
    assert e.value.errors == {"nope": "không có cài đặt này"}


def test_one_bad_key_saves_nothing(store):
    with pytest.raises(SettingsError):
        store.update({"cache.max_gb": 20, "prefetch.concurrency": 99})
    assert store.get("cache.max_gb") == 10


def test_corrupt_stored_value_falls_back_to_default(store, conn):
    conn.execute("INSERT INTO app_settings VALUES ('setting:cache.max_gb', 'not json')")
    conn.commit()
    store.invalidate()
    assert store.get("cache.max_gb") == 10


def test_stored_value_out_of_range_falls_back_to_default(store, conn):
    conn.execute("INSERT INTO app_settings VALUES ('setting:cache.max_gb', '9999')")
    conn.commit()
    store.invalidate()
    assert store.get("cache.max_gb") == 10


def test_invalidate_rereads_db(store, conn):
    store.get("cache.max_gb")
    conn.execute("INSERT INTO app_settings VALUES ('setting:cache.max_gb', '42')")
    conn.commit()
    assert store.get("cache.max_gb") == 10
    store.invalidate()
    assert store.get("cache.max_gb") == 42


def test_describe_shape(store):
    store.update({"home.refresh_minutes": 15})
    [g] = store.describe()
    assert g["id"] == "system" and g["label"] == "Hệ thống"
    item = next(i for i in g["items"] if i["key"] == "home.refresh_minutes")
    assert item == {"key": "home.refresh_minutes", "label": "Làm mới trang chủ", "type": "int", "value": 15,
                    "default": 10, "min": 5, "max": 120, "step": 5, "unit": "phút"}
