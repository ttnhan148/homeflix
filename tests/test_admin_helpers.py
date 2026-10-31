import admin


def test_not_configured_when_env_missing_or_empty(monkeypatch):
    monkeypatch.delenv("HOMEFLIX_ADMIN_PASSWORD", raising=False); assert not admin.configured()
    monkeypatch.setenv("HOMEFLIX_ADMIN_PASSWORD", ""); assert not admin.configured()


def test_password_ok(monkeypatch):
    monkeypatch.setenv("HOMEFLIX_ADMIN_PASSWORD", "mật khẩu")
    assert admin.password_ok("mật khẩu") and not admin.password_ok("mat khau")
    assert not admin.password_ok(None) and not admin.password_ok(123)


def test_password_never_ok_when_not_configured(monkeypatch):
    monkeypatch.delenv("HOMEFLIX_ADMIN_PASSWORD", raising=False); assert not admin.password_ok("")


def test_limiter_locks_after_five_failures_per_key():
    t = [1000.0]; lim = admin.LoginLimiter(clock=lambda: t[0])
    for _ in range(4): lim.record_failure(["d:a", "ip:1"])
    assert lim.retry_after(["d:a"]) == 0
    lim.record_failure(["d:a", "ip:1"])
    assert lim.retry_after(["d:a"]) == 900 and lim.retry_after(["ip:1"]) == 900 and lim.retry_after(["d:b"]) == 0
    t[0] += 900; assert lim.retry_after(["d:a"]) == 0


def test_failures_outside_window_do_not_count():
    t = [0.0]; lim = admin.LoginLimiter(clock=lambda: t[0])
    for _ in range(4): lim.record_failure(["d:a"])
    t[0] = 901; lim.record_failure(["d:a"]); assert lim.retry_after(["d:a"]) == 0


def test_reset_clears_failures():
    lim = admin.LoginLimiter()
    for _ in range(4): lim.record_failure(["d:a"])
    lim.reset(["d:a"]); lim.record_failure(["d:a"]); assert lim.retry_after(["d:a"]) == 0


def test_new_code_is_six_digits():
    codes = {admin.new_code() for _ in range(200)}
    assert all(len(c) == 6 and c.isdigit() for c in codes) and len(codes) > 150
