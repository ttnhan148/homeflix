import asyncio
import os
import time


def test_non_admin_sees_no_groups(pw, make_client):
    assert make_client().get("/api/settings").json() == {"admin_enabled": True, "is_admin": False, "groups": []}


def test_admin_disabled_flag(make_client, monkeypatch):
    monkeypatch.delenv("HOMEFLIX_ADMIN_PASSWORD", raising=False)
    assert make_client().get("/api/settings").json()["admin_enabled"] is False


def test_admin_patch_then_get(pw, admin_client):
    r = admin_client.patch("/api/settings", json={"cache.max_gb": 20})
    item = next(i for i in r.json()["groups"][0]["items"] if i["key"] == "cache.max_gb")
    assert r.status_code == 200 and item["value"] == 20


def test_patch_rejects_all_on_one_bad_key(pw, admin_client):
    r = admin_client.patch("/api/settings", json={"cache.max_gb": 20, "home.refresh_minutes": 1})
    assert r.status_code == 422 and r.json()["errors"] == {"home.refresh_minutes": "phải từ 5 đến 120"}
    assert admin_client.get("/api/settings").json()["groups"][0]["items"][0]["value"] == 10


def test_patch_bool_for_int_is_422(pw, admin_client):
    assert admin_client.patch("/api/settings", json={"cache.max_gb": True}).status_code == 422


def test_non_admin_patch_403(pw, make_client):
    assert make_client().patch("/api/settings", json={"cache.max_gb": 20}).status_code == 403


def test_cache_status_uses_setting(pw, admin_client):
    admin_client.patch("/api/settings", json={"cache.max_gb": 20})
    assert admin_client.get("/api/cache/status").json()["max_gb"] == 20


def test_cleanup_uses_max_age_setting(app_module, pw, admin_client):
    d = os.path.join(app_module.CACHE_DIR, "s1"); os.makedirs(d)
    old = time.time() - 2 * 3600; os.utime(d, (old, old))
    app_module._cleanup_disk(); assert os.path.exists(d)          # mặc định 6 giờ
    admin_client.patch("/api/settings", json={"cache.max_age_hours": 1})
    app_module._cleanup_disk(); assert not os.path.exists(d)


def test_prefetch_semaphore_follows_setting(app_module, pw, admin_client):
    s1 = app_module.get_prefetch_semaphore(); assert s1 is app_module.get_prefetch_semaphore()
    admin_client.patch("/api/settings", json={"prefetch.concurrency": 2})
    s2 = app_module.get_prefetch_semaphore(); assert s2 is not s1 and s2._value == 2


def test_home_warmer_reads_refresh_minutes(app_module, monkeypatch):
    slept = []

    async def fake_sleep(s):
        slept.append(s)
        raise asyncio.CancelledError

    async def noop(*a, **k):
        return None

    monkeypatch.setattr(app_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(app_module, "_warm_section", noop)
    monkeypatch.setattr(app_module, "_warm_danh_sach", noop)
    app_module.settings_store.update({"home.refresh_minutes": 15})
    try:
        asyncio.run(app_module.home_cache_warmer())
    except asyncio.CancelledError:
        pass
    assert slept == [900]


def test_startup_warns_when_admin_disabled(app_module, monkeypatch, caplog):
    import logging
    monkeypatch.delenv("HOMEFLIX_ADMIN_PASSWORD", raising=False)
    with caplog.at_level(logging.WARNING, logger=app_module.logger.name):
        app_module._warn_if_admin_disabled()
    assert "HOMEFLIX_ADMIN_PASSWORD" in caplog.text


def test_startup_no_warning_when_admin_configured(app_module, monkeypatch, caplog):
    import logging
    monkeypatch.setenv("HOMEFLIX_ADMIN_PASSWORD", "s3cret-pass-xyz")
    with caplog.at_level(logging.WARNING, logger=app_module.logger.name):
        app_module._warn_if_admin_disabled()
    assert caplog.text == ""


def test_lifespan_calls_admin_warning(app_module):
    import inspect
    assert "_warn_if_admin_disabled()" in inspect.getsource(app_module.lifespan)
