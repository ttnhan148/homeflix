"""Admin password stored in the DB (scrypt hash): generated on first start, changeable from Settings.
HOMEFLIX_ADMIN_PASSWORD, when set, still wins and locks the password against changes."""
import json
import logging

import pytest

import admin
from admin_helpers import post

ENV = "HOMEFLIX_ADMIN_PASSWORD"


@pytest.fixture
def no_env(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)


# --- hashing (admin.py, pure) ---
def test_hash_roundtrip_and_salted():
    h1, h2 = admin.hash_password("mật khẩu dài"), admin.hash_password("mật khẩu dài")
    assert h1 != h2 and h1.startswith("scrypt$")
    assert admin.verify_password("mật khẩu dài", h1) and admin.verify_password("mật khẩu dài", h2)
    assert not admin.verify_password("mat khau dai", h1)


@pytest.mark.parametrize("candidate", [None, 123, "", "\ud800"])
def test_verify_rejects_bad_candidates(candidate):
    assert not admin.verify_password(candidate, admin.hash_password("abcdefgh"))


@pytest.mark.parametrize("stored", [None, "", "plain", "scrypt$x$y", "md5$a$b$c$d$e"])
def test_verify_rejects_bad_stored(stored):
    assert not admin.verify_password("abcdefgh", stored)


def test_generated_password_is_long_and_random():
    a, b = admin.generate_password(), admin.generate_password()
    assert a != b and len(a) >= 16 and a.isascii()


# --- first start ---
def test_first_start_generates_and_logs_password_once(app_module, no_env, caplog):
    with caplog.at_level(logging.WARNING, logger=app_module.logger.name):
        pw = app_module.ensure_admin_password()
    assert pw and pw in caplog.text
    stored = app_module._db_admin_hash()
    assert admin.verify_password(pw, stored)
    caplog.clear()
    assert app_module.ensure_admin_password() is None      # second start: keep it, print nothing
    assert app_module._db_admin_hash() == stored and pw not in caplog.text


def test_first_start_with_env_generates_nothing(app_module, pw):
    assert app_module.ensure_admin_password() is None
    assert app_module._db_admin_hash() is None


def test_login_with_stored_password(app_module, no_env, make_client):
    pw = app_module.ensure_admin_password()
    c = make_client()
    assert post(c, "/api/admin/login", {"password": "wrong-one"}).status_code == 401
    assert post(c, "/api/admin/login", {"password": pw}).status_code == 204
    assert app_module._db_is_admin(c.cookies["hf_device"])


def test_env_wins_over_stored_password(app_module, no_env, make_client, monkeypatch):
    stored_pw = app_module.ensure_admin_password()
    monkeypatch.setenv(ENV, "env-password-1")
    c = make_client()
    assert post(c, "/api/admin/login", {"password": stored_pw}).status_code == 401
    assert post(c, "/api/admin/login", {"password": "env-password-1"}).status_code == 204


def test_reset_admin_password(app_module, no_env):
    old = app_module.ensure_admin_password()
    new = app_module.reset_admin_password()
    assert new != old
    assert admin.verify_password(new, app_module._db_admin_hash())
    assert not admin.verify_password(old, app_module._db_admin_hash())


# --- change from Settings ---
@pytest.fixture
def stored_admin(app_module, no_env, make_client):
    pw = app_module.ensure_admin_password()
    c = make_client()
    assert post(c, "/api/admin/login", {"password": pw}).status_code == 204
    return c, pw


def test_settings_reports_whether_password_is_env_managed(app_module, stored_admin, monkeypatch):
    c, _ = stored_admin
    assert c.get("/api/settings").json()["password_env"] is False
    monkeypatch.setenv(ENV, "env-password-1")
    assert c.get("/api/settings").json()["password_env"] is True


def test_change_password(app_module, stored_admin, make_client):
    c, pw = stored_admin
    r = post(c, "/api/admin/password", {"current": pw, "new": "mật khẩu mới 2026"})
    assert r.status_code == 204, r.text
    other = make_client(ip="203.0.113.9")
    assert post(other, "/api/admin/login", {"password": pw}).status_code == 401
    assert post(other, "/api/admin/login", {"password": "mật khẩu mới 2026"}).status_code == 204


def test_change_password_wrong_current(app_module, stored_admin):
    c, pw = stored_admin
    r = post(c, "/api/admin/password", {"current": "not-it", "new": "abcdefgh12"})
    assert r.status_code == 401
    assert admin.verify_password(pw, app_module._db_admin_hash())


def test_change_password_wrong_current_counts_toward_lockout(app_module, stored_admin):
    c, pw = stored_admin
    for _ in range(5):
        post(c, "/api/admin/password", {"current": "not-it", "new": "abcdefgh12"})
    r = post(c, "/api/admin/password", {"current": pw, "new": "abcdefgh12"})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0


@pytest.mark.parametrize("new", ["short", "", None, 12345678, "x" * 129, "\ud800abcdefgh"])
def test_change_password_rejects_bad_new(app_module, stored_admin, new):
    c, pw = stored_admin
    # raw JSON: a lone surrogate arrives as the escape "\\ud800", which httpx's json= cannot encode
    raw = json.dumps({"current": pw, "new": new})
    r = c.post("/api/admin/password", content=raw, headers={"content-type": "application/json"})
    assert r.status_code == 422
    assert "new" in r.json()["errors"]
    assert admin.verify_password(pw, app_module._db_admin_hash())


def test_change_password_needs_admin(app_module, stored_admin, make_client):
    _, pw = stored_admin
    assert post(make_client(ip="203.0.113.9"), "/api/admin/password", {"current": pw, "new": "abcdefgh12"}).status_code == 403


def test_change_password_refused_when_env_managed(app_module, admin_client):
    r = post(admin_client, "/api/admin/password", {"current": "mk-test", "new": "abcdefgh12"})
    assert r.status_code == 409
