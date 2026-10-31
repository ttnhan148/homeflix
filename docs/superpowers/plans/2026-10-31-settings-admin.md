# Trang Cài đặt và quyền admin — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Một trang Cài đặt gom cài đặt của máy, quản lý thiết bị và cài đặt hệ thống, với quyền admin bằng mật khẩu hoặc duyệt mã 6 số.

**Architecture:** `settings.py` (khai báo và lưu cài đặt hệ thống trong `app_settings`) và `admin.py` (mật khẩu, khóa khi sai, mã duyệt) là module thuần, không phụ thuộc FastAPI. `app.py` chỉ thêm route, middleware và các hàm `_db_*`. Giao diện là tab mới `settings-tab`: `static/settings-utils.js` chứa hàm thuần (test bằng Node), `static/settings.js` dựng trang và gọi API.

**Tech Stack:** Python 3.12, FastAPI, SQLite (WAL), JS thuần, pytest + Playwright (Chromium), `node --test`.

**Spec:** `docs/superpowers/specs/2026-10-31-settings-admin-design.md`

## Global Constraints

- Không thêm dependency Python hay JS mới (`requirements.txt` giữ nguyên).
- API không bao giờ trả hay nhận id thiết bị thật; luôn dùng `_cast_public_id(device_id, salt)`.
- Mật khẩu chỉ đọc từ biến môi trường `HOMEFLIX_ADMIN_PASSWORD`, đọc lại ở mỗi lần gọi (không cache lúc import). Rỗng hoặc không đặt = admin tắt.
- Admin tắt: mọi route `/api/admin/*` trả `404`; `GET /api/settings` trả `admin_enabled: false`.
- Máy chưa là admin gọi route chỉ-admin: `403`.
- `POST`/`PATCH`/`DELETE` dưới `/api/admin` và `/api/settings` thiếu `Content-Type: application/json`: `415`.
- Khóa đăng nhập: 5 lần sai trong 900 giây → khóa 900 giây, đếm riêng theo thiết bị và theo IP.
- Yêu cầu xin quyền: mã 6 số (`secrets`), hết hạn sau 600 giây, tối đa 20 yêu cầu `pending` toàn hệ thống.
- Cài đặt hệ thống (key · mặc định · min–max · bước · đơn vị): `cache.max_gb` 10 · 1–500 · 1 · GB; `cache.max_age_hours` 6 · 1–168 · 1 · giờ; `prefetch.concurrency` 4 · 1–16 · 1 · (không); `home.refresh_minutes` 10 · 5–120 · 5 · phút.
- Giá trị lưu ở `app_settings`, key `setting:<key>`, value là JSON.
- Chữ trên giao diện bằng tiếng Việt, ngắn, không emoji. Tên nhóm: "Thiết bị này", "Quản trị", "Thiết bị trong nhà", "Hệ thống", "HomeTV".
- Màu đỏ `var(--accent)` chỉ cho thao tác nguy hiểm. Mỗi dòng cao tối thiểu `var(--tap)`.
- Chạy test: `source .venv/bin/activate && python -m pytest -q -p no:warnings <path>`; JS: `node --test tests/js/*.mjs`.
- Mỗi commit kết thúc bằng dòng `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Commit lên `main` tự deploy qua hook `post-commit`, nên chỉ commit khi cả bộ test liên quan đã xanh.

## Review Focus

- `true`/`false` gửi cho cài đặt kiểu số (Python coi `bool` là `int`): phải bị `422`, không được lưu thành 1/0. Test ở Task 1.
- Số dạng chuỗi (`"10"`) hoặc số thực (`10.5`) cho cài đặt kiểu số: `422`. Test ở Task 1.
- Duyệt một yêu cầu khi máy xin đã bị xoá hoặc yêu cầu đã hết hạn: `409`, không cấp quyền cho ai. Test ở Task 5.
- Admin tự bỏ quyền mình qua `PATCH /api/admin/devices/{id-của-mình}` với `is_admin: false`: được phép nếu còn admin khác, `409` nếu là admin cuối. Test ở Task 6.
- `/api/settings` lỗi mạng hoặc `500`: nhóm "Thiết bị này" vẫn hiện và dùng được (nó không phụ thuộc API này). Test ở Task 9.

---

### Task 1: `settings.py` — khai báo, kiểm tra, lưu cài đặt hệ thống

**Files:**
- Create: `settings.py`
- Test: `tests/test_settings_store.py`

**Interfaces:**
- Produces:
  - `Setting` (dataclass frozen): `key: str, label: str, type: type, default: int | bool, min: int | None, max: int | None, step: int, unit: str, group: str`
  - `SETTINGS: tuple[Setting, ...]` — đúng 4 dòng, theo đúng thứ tự ở Global Constraints, `group="system"`, nhãn: "Dung lượng tối đa", "Giữ cache", "Luồng tải trước", "Làm mới trang chủ".
  - `GROUP_LABELS = {"system": "Hệ thống"}`
  - `class SettingsError(ValueError)` với thuộc tính `errors: dict[str, str]`
  - `check(key: str, value) -> int | bool` — ném `SettingsError({key: lý do})`
  - `class SettingsStore(connect: Callable[[], sqlite3.Connection], lock: threading.Lock)` với `get(key) -> int | bool`, `update(values: dict) -> None`, `invalidate() -> None`, `describe() -> list[dict]`
  - Lý do lỗi (copy chính xác): `"không có cài đặt này"`, `"phải là số nguyên"`, `"phải là true hoặc false"`, `"phải từ {min} đến {max}"`.

- [ ] **Step 1: Viết test (thất bại)**

Dùng `sqlite3.connect(":memory:", check_same_thread=False)` có bảng `app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)` và `threading.Lock()`.

```python
def test_get_returns_default_when_unset(store): assert store.get("cache.max_gb") == 10
def test_update_then_get(store): store.update({"cache.max_gb": 20}); assert store.get("cache.max_gb") == 20
def test_update_persists_as_json_under_prefixed_key(store, conn):
    store.update({"prefetch.concurrency": 6})
    assert conn.execute("SELECT value FROM app_settings WHERE key='setting:prefetch.concurrency'").fetchone()[0] == "6"
@pytest.mark.parametrize("value", [0, 501])
def test_out_of_range_rejected(store, value):
    with pytest.raises(SettingsError) as e: store.update({"cache.max_gb": value})
    assert e.value.errors == {"cache.max_gb": "phải từ 1 đến 500"}
@pytest.mark.parametrize("value", [True, False, "10", 10.5, None])
def test_non_int_rejected(store, value):
    with pytest.raises(SettingsError) as e: store.update({"cache.max_gb": value})
    assert e.value.errors == {"cache.max_gb": "phải là số nguyên"}
def test_unknown_key_rejected(store):
    with pytest.raises(SettingsError) as e: store.update({"nope": 1})
    assert e.value.errors == {"nope": "không có cài đặt này"}
def test_one_bad_key_saves_nothing(store):
    with pytest.raises(SettingsError): store.update({"cache.max_gb": 20, "prefetch.concurrency": 99})
    assert store.get("cache.max_gb") == 10
def test_corrupt_stored_value_falls_back_to_default(store, conn):
    conn.execute("INSERT INTO app_settings VALUES ('setting:cache.max_gb', 'not json')"); conn.commit()
    store.invalidate(); assert store.get("cache.max_gb") == 10
def test_stored_value_out_of_range_falls_back_to_default(store, conn):
    conn.execute("INSERT INTO app_settings VALUES ('setting:cache.max_gb', '9999')"); conn.commit()
    store.invalidate(); assert store.get("cache.max_gb") == 10
def test_invalidate_rereads_db(store, conn):
    store.get("cache.max_gb")
    conn.execute("INSERT INTO app_settings VALUES ('setting:cache.max_gb', '42')"); conn.commit()
    assert store.get("cache.max_gb") == 10
    store.invalidate(); assert store.get("cache.max_gb") == 42
def test_describe_shape(store):
    store.update({"home.refresh_minutes": 15})
    [g] = store.describe()
    assert g["id"] == "system" and g["label"] == "Hệ thống"
    item = next(i for i in g["items"] if i["key"] == "home.refresh_minutes")
    assert item == {"key": "home.refresh_minutes", "label": "Làm mới trang chủ", "type": "int", "value": 15,
                    "default": 10, "min": 5, "max": 120, "step": 5, "unit": "phút"}
```

- [ ] **Step 2: Chạy, xác nhận thất bại** — `python -m pytest -q tests/test_settings_store.py` → `ModuleNotFoundError: No module named 'settings'`.

- [ ] **Step 3: Viết `settings.py`**

`check` loại `bool` trước khi kiểm tra `int` (`isinstance(v, bool)`). `get` đọc bộ đệm `dict` nạp một lần từ DB (một `SELECT key, value FROM app_settings WHERE key LIKE 'setting:%'`); giá trị hỏng trả mặc định và `logging.getLogger(__name__).warning(...)` một lần cho mỗi key. `update` gọi `check` cho mọi key, gom lỗi, ném nếu có; nếu không thì ghi `INSERT ... ON CONFLICT(key) DO UPDATE` trong một transaction dưới `lock`, rồi `invalidate()`.

- [ ] **Step 4: Chạy lại** — cùng lệnh, tất cả PASS.

- [ ] **Step 5: Commit** — `git add settings.py tests/test_settings_store.py && git commit -m "feat(settings): typed system settings store"`

---

### Task 2: `admin.py` — mật khẩu, khóa khi sai, mã duyệt

**Files:**
- Create: `admin.py`
- Test: `tests/test_admin_helpers.py`

**Interfaces:**
- Produces:
  - `PASSWORD_ENV = "HOMEFLIX_ADMIN_PASSWORD"`, `REQUEST_TTL = 600`, `MAX_PENDING = 20`
  - `configured() -> bool`
  - `password_ok(candidate) -> bool` — `False` khi chưa cấu hình hoặc `candidate` không phải `str`; so bằng `hmac.compare_digest` trên SHA-256 của hai chuỗi.
  - `class LoginLimiter(max_failures=5, window=900, lockout=900, clock=time.time)` với `retry_after(keys: Iterable[str]) -> int`, `record_failure(keys) -> None`, `reset(keys) -> None`
  - `new_code() -> str` — đúng 6 chữ số, có số 0 ở đầu nếu cần.

- [ ] **Step 1: Viết test (thất bại)**

```python
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
```

- [ ] **Step 2: Chạy, xác nhận thất bại** — `python -m pytest -q tests/test_admin_helpers.py` → `ModuleNotFoundError`.

- [ ] **Step 3: Viết `admin.py`** — `LoginLimiter` giữ `dict[key, list[float]]` các mốc thất bại trong cửa sổ và `dict[key, float]` thời điểm hết khóa; `retry_after` trả `ceil` số giây còn lại lớn nhất trong các key. `new_code` = `f"{secrets.randbelow(10**6):06d}"`.

- [ ] **Step 4: Chạy lại** — PASS.

- [ ] **Step 5: Commit** — `git add admin.py tests/test_admin_helpers.py && git commit -m "feat(admin): password check, login limiter, approval codes"`

---

### Task 3: Quyền admin trong app — cột `is_admin`, đăng nhập, đăng xuất, chặn request

**Files:**
- Modify: `app.py` (`init_db`, `DEVICE_COOKIE_PATHS`, `device_middleware`, route mới)
- Modify: `tests/conftest.py` (`app_module` đặt `admin_login_limiter` mới cho mỗi test; thêm fixture `pw`)
- Create: `tests/admin_helpers.py` (`post(client, path, body=None)` gửi `json=body or {}`; `TV_UA` chép từ `tests/ui/conftest.py`)
- Test: `tests/test_admin_api.py`

**Interfaces:**
- Consumes: `admin.configured()`, `admin.password_ok()`, `admin.LoginLimiter`
- Produces (trong `app.py`):
  - `admin_login_limiter: admin.LoginLimiter`
  - `request.state.ip: str` (middleware gán cạnh `device_id`)
  - `_db_is_admin(device_id) -> bool`, `_db_set_admin(device_id, on: bool) -> None`, `_db_admin_count() -> int`
  - Dependency `require_admin_enabled()` (404 khi `not admin.configured()`), `require_admin(request)` (404 như trên, 403 khi máy không là admin)
  - Route `POST /api/admin/login` (`{password}` → `204` | `401` | `429 {"retry_after": int}`), `POST /api/admin/logout` (`204`), `POST /api/admin/revoke-others` (`{"revoked": int}`)

- [ ] **Step 1: Viết test (thất bại)**

Fixture `pw` (trong `tests/conftest.py`) đặt `monkeypatch.setenv("HOMEFLIX_ADMIN_PASSWORD", "mk-test")`. Test import `post`, `TV_UA` từ `tests/admin_helpers.py`.

```python
def test_admin_routes_404_when_password_unset(make_client, monkeypatch):
    monkeypatch.delenv("HOMEFLIX_ADMIN_PASSWORD", raising=False)
    assert post(make_client(), "/api/admin/login", {"password": "x"}).status_code == 404
def test_login_ok_makes_device_admin(pw, make_client, app_module):
    c = make_client(); assert post(c, "/api/admin/login", {"password": "mk-test"}).status_code == 204
    assert app_module._db_is_admin(c.cookies["hf_device"])
def test_wrong_or_missing_password_is_401(pw, make_client):
    c = make_client()
    assert post(c, "/api/admin/login", {"password": "sai"}).status_code == 401
    assert post(c, "/api/admin/login", {}).status_code == 401
def test_device_locked_after_five_failures_even_with_right_password(pw, make_client):
    c = make_client()
    for _ in range(5): post(c, "/api/admin/login", {"password": "sai"})
    r = post(c, "/api/admin/login", {"password": "mk-test"})
    assert r.status_code == 429 and r.json()["retry_after"] > 0
def test_ip_locked_across_devices(pw, make_client):
    for _ in range(5): post(make_client(ip="198.51.100.9"), "/api/admin/login", {"password": "sai"})
    assert post(make_client(ip="198.51.100.9"), "/api/admin/login", {"password": "mk-test"}).status_code == 429
    assert post(make_client(ip="198.51.100.10"), "/api/admin/login", {"password": "mk-test"}).status_code == 204
def test_non_json_write_is_415(pw, make_client):
    r = make_client().post("/api/admin/login", content="password=mk-test", headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 415
def test_logout_drops_own_rights(pw, make_client, app_module): ...  # login, logout → 204, _db_is_admin False
def test_revoke_others_keeps_caller(pw, make_client, app_module):
    a, b = make_client(), make_client()
    post(a, "/api/admin/login", {"password": "mk-test"}); post(b, "/api/admin/login", {"password": "mk-test"})
    assert post(a, "/api/admin/revoke-others").json() == {"revoked": 1}
    assert app_module._db_is_admin(a.cookies["hf_device"]) and not app_module._db_is_admin(b.cookies["hf_device"])
def test_revoke_others_requires_admin(pw, make_client):
    assert post(make_client(), "/api/admin/revoke-others").status_code == 403
def test_is_admin_column_added_to_existing_db(app_module): ...  # init_db() chạy hai lần không lỗi; PRAGMA table_info(devices) có is_admin
```

- [ ] **Step 2: Chạy, xác nhận thất bại** — `python -m pytest -q tests/test_admin_api.py` → 404/AttributeError.

- [ ] **Step 3: Sửa `app.py`**
  - `init_db`: `ALTER TABLE devices ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0` khi `PRAGMA table_info(devices)` chưa có cột.
  - `DEVICE_COOKIE_PATHS` thêm `"/api/settings"`, `"/api/admin"`; middleware gán `request.state.ip = ip`.
  - Middleware mới (đặt trước `device_middleware`): method `POST`/`PATCH`/`DELETE`, path bắt đầu bằng `/api/admin` hoặc `/api/settings`, `content-type` không bắt đầu bằng `application/json` → `JSONResponse({"detail": "application/json required"}, 415)`.
  - Login: khóa theo `[f"d:{device_id}", f"ip:{ip}"]`; kiểm `retry_after` trước, sai thì `record_failure`, đúng thì `reset` + `_db_set_admin(..., True)`. Không log mật khẩu.
- Sửa `tests/conftest.py` `app_module`: `monkeypatch.setattr(m, "admin_login_limiter", admin.LoginLimiter())`.

- [ ] **Step 4: Chạy lại** — `python -m pytest -q tests/test_admin_api.py tests/test_device_middleware.py` → PASS.

- [ ] **Step 5: Commit** — `git commit -m "feat(admin): admin flag per device, password login with lockout"`

---

### Task 4: Cài đặt hệ thống có hiệu lực + `GET/PATCH /api/settings`

**Files:**
- Modify: `app.py` (hằng số `MAX_CACHE_SIZE`, `MAX_CACHE_AGE`, `PREFETCH_CONCURRENCY`, `prefetch_semaphore`, `WARM_CACHE_INTERVAL` và chỗ dùng ở `_cleanup_disk`, `prefetch` (`async with prefetch_semaphore`), `get_cache_status`, `home_cache_warmer`)
- Test: `tests/test_settings_api.py`

**Interfaces:**
- Consumes: `settings.SettingsStore`, `settings.SettingsError`, `require_admin`
- Produces:
  - `settings_store = settings.SettingsStore(get_db, _db_lock)`; `init_db()` gọi `settings_store.invalidate()` ở cuối
  - `setting(key: str) -> int | bool`
  - `get_prefetch_semaphore() -> asyncio.Semaphore` — trả semaphore hiện tại; tạo mới khi `setting("prefetch.concurrency")` khác giá trị lúc tạo
  - `GET /api/settings` → `{"admin_enabled": bool, "is_admin": bool, "groups": settings_store.describe() nếu là admin, ngược lại []}`
  - `PATCH /api/settings` (admin) → như GET; lỗi → `422 {"errors": {...}}`

- [ ] **Step 1: Viết test (thất bại)**

```python
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
```

Thêm fixture `admin_client(pw, make_client)` vào `tests/conftest.py`: tạo client, gọi `POST /api/admin/login` với `mk-test`, trả client. Task 5, 6 dùng chung.

Thêm một test cho trình làm mới trang chủ: monkeypatch `asyncio.sleep` trong `app` để ghi lại số giây rồi ném `asyncio.CancelledError`; monkeypatch `_warm_section` và `_warm_danh_sach` thành coroutine rỗng; đặt `home.refresh_minutes` = 15; chạy `home_cache_warmer()` → số giây ghi được là `900`.

- [ ] **Step 2: Chạy, xác nhận thất bại** — `python -m pytest -q tests/test_settings_api.py`.

- [ ] **Step 3: Sửa `app.py`** — bỏ 4 hằng số và `prefetch_semaphore` toàn cục; mọi chỗ dùng đọc `setting(...)` (GB × 1024³, giờ × 3600, phút × 60). `_cleanup_disk` đọc hai giá trị một lần ở đầu hàm. `get_prefetch_semaphore` giữ `(n, Semaphore)` trong một biến module.

- [ ] **Step 4: Chạy lại** — `python -m pytest -q tests/test_settings_api.py tests/test_hardening.py tests/test_proxy_m3u8.py` → PASS.

- [ ] **Step 5: Commit** — `git commit -m "feat(settings): system settings API, cache/prefetch/warmer read live values"`

---

### Task 5: Xin quyền admin bằng mã 6 số

**Files:**
- Modify: `app.py` (bảng `admin_requests` trong `init_db`, hàm `_db_*`, route)
- Test: `tests/test_admin_requests.py`

**Interfaces:**
- Consumes: `admin.new_code()`, `admin.REQUEST_TTL`, `admin.MAX_PENDING`, `require_admin_enabled`, `require_admin`, `_db_set_admin`, `_cast_public_id`
- Produces:
  - `_now() -> float` (trả `time.time()`); mọi hàm và route của task này lấy giờ qua `_now()` để test thay được
  - Bảng `admin_requests(id TEXT PRIMARY KEY, device_id TEXT NOT NULL, code TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL)`; `id` = `secrets.token_urlsafe(9)`
  - `_db_create_admin_request(device_id, now) -> dict | None` (`None` khi đã có 20 `pending` còn hạn, không tính yêu cầu của chính máy đó)
  - `_db_my_admin_request(device_id, now) -> dict` (`{"status": "none"|"pending"|"approved"|"denied"|"expired", "code"?, "expires_at"?}`)
  - `_db_pending_admin_requests(now) -> list[dict]`
  - `_db_decide_admin_request(request_id, approve: bool, now) -> bool` (`False` khi không có, không `pending`, hết hạn, hoặc thiết bị xin không còn)
  - Route: `POST /api/admin/requests` → `{"code", "expires_at"}` hoặc `429`; `GET /api/admin/requests/mine`; `GET /api/admin/requests` (admin) → `[{"id", "device": {"id": public, "name", "kind"}, "code", "expires_at"}]`; `POST /api/admin/requests/{id}/approve|deny` (admin) → `204` hoặc `409`

- [ ] **Step 1: Viết test (thất bại)**

```python
def test_request_then_approve(pw, make_client, admin_client, app_module):
    tv = make_client(ua=TV_UA); r = post(tv, "/api/admin/requests").json()
    assert len(r["code"]) == 6 and tv.get("/api/admin/requests/mine").json()["status"] == "pending"
    [req] = admin_client.get("/api/admin/requests").json()
    assert req["code"] == r["code"] and req["device"]["kind"] == "tv"
    assert req["device"]["id"] != tv.cookies["hf_device"]
    assert post(admin_client, f"/api/admin/requests/{req['id']}/approve").status_code == 204
    assert tv.get("/api/admin/requests/mine").json()["status"] == "approved"
    assert app_module._db_is_admin(tv.cookies["hf_device"])
def test_deny(pw, make_client, admin_client): ...  # mine == "denied", không thành admin
def test_new_request_replaces_old(pw, make_client, admin_client):
    tv = make_client(); post(tv, "/api/admin/requests"); post(tv, "/api/admin/requests")
    assert len(admin_client.get("/api/admin/requests").json()) == 1
def test_expired_request(pw, make_client, admin_client, monkeypatch, app_module):
    tv = make_client(); post(tv, "/api/admin/requests")
    [req] = admin_client.get("/api/admin/requests").json()
    later = time.time() + 601; monkeypatch.setattr(app_module, "_now", lambda: later)
    assert tv.get("/api/admin/requests/mine").json()["status"] == "expired"
    assert admin_client.get("/api/admin/requests").json() == []
    assert post(admin_client, f"/api/admin/requests/{req['id']}/approve").status_code == 409
def test_approve_after_requester_deleted_is_409(pw, make_client, admin_client, app_module):
    tv = make_client(); post(tv, "/api/admin/requests")
    [req] = admin_client.get("/api/admin/requests").json()
    app_module.db_execute("DELETE FROM devices WHERE id = ?", (tv.cookies["hf_device"],))
    assert post(admin_client, f"/api/admin/requests/{req['id']}/approve").status_code == 409
def test_pending_cap_is_20(pw, make_client):
    for _ in range(20): assert post(make_client(), "/api/admin/requests").status_code == 200
    assert post(make_client(), "/api/admin/requests").status_code == 429
def test_list_and_decide_require_admin(pw, make_client): ...  # GET list và approve từ máy thường → 403
def test_mine_none_without_request(pw, make_client): ...  # {"status": "none"}
```

- [ ] **Step 2: Chạy, xác nhận thất bại** — `python -m pytest -q tests/test_admin_requests.py`.

- [ ] **Step 3: Viết bảng, hàm `_db_*`, route** — "hết hạn" tính lúc đọc (`created_at + REQUEST_TTL <= now`), không cần job dọn; khi tạo yêu cầu mới thì xoá các yêu cầu cũ hơn 1 ngày.

- [ ] **Step 4: Chạy lại** — PASS.

- [ ] **Step 5: Commit** — `git commit -m "feat(admin): request admin rights with a 6-digit code"`

---

### Task 6: Quản lý thiết bị và xoá cache cho admin

**Files:**
- Modify: `app.py` (`_db_prune_inactive_devices`, route thiết bị, `/api/cache/clear`)
- Test: `tests/test_admin_devices.py`

**Interfaces:**
- Consumes: `require_admin`, `_db_admin_count`, `_db_set_admin`, `_cast_public_id`, `get_household_salt`
- Produces:
  - `_db_delete_device(conn, device_id) -> None` — tách từ thân vòng lặp của `_db_prune_inactive_devices` (giữ nguyên hành vi hàm đó), thêm `DELETE FROM admin_requests WHERE device_id = ?`
  - `_resolve_public_device(public_id) -> dict | None` — quét mọi thiết bị, so bằng `hmac.compare_digest`
  - `GET /api/admin/devices` → `[{"id", "name", "kind", "household": 8 ký tự đầu, "last_seen", "is_admin", "is_self"}]`, sắp theo `household` rồi `last_seen` giảm dần
  - `PATCH /api/admin/devices/{id}` `{name?, is_admin?}` → thiết bị sau khi sửa; tên 1–40 ký tự (như `PATCH /api/device`); thu quyền admin cuối → `409`
  - `DELETE /api/admin/devices/{id}` → `204`; xoá chính mình → `409`; id lạ → `404`
  - `POST /api/admin/cache/clear` → như `/api/cache/clear` cũ; route cũ bị bỏ

- [ ] **Step 1: Viết test (thất bại)**

```python
def test_list_never_contains_real_ids(pw, make_client, admin_client):
    other = make_client(); other.get("/api/device")
    body = admin_client.get("/api/admin/devices").text
    assert other.cookies["hf_device"] not in body and admin_client.cookies["hf_device"] not in body
def test_list_marks_self_and_admin(pw, admin_client): ...  # đúng một dòng is_self True, dòng đó is_admin True
def test_rename(pw, make_client, admin_client): ...  # PATCH name "TV phòng khách" → GET thấy tên mới; tên rỗng → 422
def test_grant_and_revoke(pw, make_client, admin_client, app_module): ...
def test_cannot_revoke_last_admin_even_self(pw, admin_client):
    me = next(d for d in admin_client.get("/api/admin/devices").json() if d["is_self"])
    assert admin_client.patch(f"/api/admin/devices/{me['id']}", json={"is_admin": False}).status_code == 409
def test_self_revoke_allowed_when_another_admin_exists(pw, make_client, admin_client, app_module):
    b = make_client(); post(b, "/api/admin/login", {"password": "mk-test"})
    me = next(d for d in admin_client.get("/api/admin/devices").json() if d["is_self"])
    assert admin_client.patch(f"/api/admin/devices/{me['id']}", json={"is_admin": False}).status_code == 200
    assert not app_module._db_is_admin(admin_client.cookies["hf_device"])
def test_delete_removes_device_and_its_library(pw, make_client, admin_client, app_module):
    other = make_client(); other.post("/api/saved", json={"slug": "phim-a", "name": "A", "episodes": []})
    target = next(d for d in admin_client.get("/api/admin/devices").json() if not d["is_self"])
    assert admin_client.request("DELETE", f"/api/admin/devices/{target['id']}", json={}).status_code == 204
    assert app_module.db_query_one("SELECT 1 FROM device_movies WHERE device_id = ?", (other.cookies["hf_device"],)) is None
def test_cannot_delete_self(pw, admin_client): ...  # 409
def test_unknown_id_404(pw, admin_client): ...
def test_cache_clear_moved_to_admin(pw, make_client, admin_client):
    assert make_client().post("/api/cache/clear").status_code in (404, 405)
    assert post(make_client(), "/api/admin/cache/clear").status_code == 403
    assert post(admin_client, "/api/admin/cache/clear").json()["status"] == "success"
def test_prune_inactive_devices_unchanged(app_module): ...  # chạy lại test hiện có tests/test_device_prune.py
```

- [ ] **Step 2: Chạy, xác nhận thất bại** — `python -m pytest -q tests/test_admin_devices.py`.

- [ ] **Step 3: Viết code** theo Interfaces.

- [ ] **Step 4: Chạy lại** — `python -m pytest -q tests/test_admin_devices.py tests/test_device_prune.py` → PASS.

- [ ] **Step 5: Commit** — `git commit -m "feat(admin): manage devices, admin-only cache clear"`

---

### Task 7: Deploy đọc mật khẩu từ `deploy/.env`

**Files:**
- Modify: `deploy/deploy.sh` (sau `git archive ... | tar -x`), `deploy/docker-compose.yml`
- Test: `tests/test_deploy_config.py`

**Interfaces:** không có.

- [ ] **Step 1: Viết test (thất bại)**

```python
def test_compose_reads_optional_env_file():
    svc = Path("deploy/docker-compose.yml").read_text()  # đọc text, không thêm dependency YAML
    assert "env_file:" in svc and "path: .env" in svc and "required: false" in svc
def test_deploy_copies_env_into_build_tree():
    sh = Path("deploy/deploy.sh").read_text()
    assert 'cp "$REPO/deploy/.env" "$SRC/deploy/.env"' in sh
    assert sh.index("git -C \"$REPO\" archive") < sh.index('cp "$REPO/deploy/.env"') < sh.index("docker compose build")
```

- [ ] **Step 2: Chạy, xác nhận thất bại.**

- [ ] **Step 3: Sửa** — `deploy.sh`: `[ -f "$REPO/deploy/.env" ] && cp "$REPO/deploy/.env" "$SRC/deploy/.env"`. Compose: `env_file: [{path: .env, required: false}]` dưới service `homeflix`.

- [ ] **Step 4: Chạy lại** — PASS; `docker compose -f deploy/docker-compose.yml config >/dev/null` thoát 0.

- [ ] **Step 5: Commit** — `git commit -m "chore(deploy): pass deploy/.env (admin password) into the container"`

---

### Task 8: `static/settings-utils.js` — hàm thuần cho giao diện

**Files:**
- Create: `static/settings-utils.js` (kiểu IIFE như `home-utils.js`, gán `root.HFSettingsUtils`, export CommonJS)
- Test: `tests/js/settings-utils.test.mjs`

**Interfaces:**
- Produces (`HFSettingsUtils`):
  - `formatValue(item) -> string` — `"10 GB"`, `"6 giờ"`, `"4"` (không đơn vị thì chỉ số)
  - `stepValue(item, value, dir: 1 | -1) -> number` — cộng `item.step`, kẹp `[min, max]`
  - `groupDevices(devices) -> [{household, devices}]` — giữ thứ tự xuất hiện của nhà; nhà chứa máy `is_self` đứng đầu
  - `lastSeenText(lastSeen, now) -> string` — `"vừa xong"` (< 60 giây), `"N phút trước"`, `"N giờ trước"`, `"N ngày trước"`
  - `countdownText(expiresAt, now) -> string` — `"m:ss"`, không âm (`"0:00"`)
  - `gridMove(lengths: number[], row, col, key) -> [row, col]` — `ArrowDown`/`ArrowUp` sang dòng kề, cột kẹp theo độ dài dòng mới; `ArrowLeft`/`ArrowRight` trong dòng, kẹp; phím khác trả nguyên vị trí

- [ ] **Step 1: Viết test (thất bại)**

```js
test('formatValue', () => {
  assert.equal(U.formatValue({ value: 10, unit: 'GB' }), '10 GB');
  assert.equal(U.formatValue({ value: 4, unit: '' }), '4');
});
test('stepValue clamps', () => {
  const it = { min: 5, max: 120, step: 5 };
  assert.equal(U.stepValue(it, 10, 1), 15); assert.equal(U.stepValue(it, 120, 1), 120); assert.equal(U.stepValue(it, 5, -1), 5);
});
test('groupDevices puts own household first', () => {
  const g = U.groupDevices([{ household: 'a', id: 1 }, { household: 'b', id: 2, is_self: true }, { household: 'a', id: 3 }]);
  assert.deepEqual(g.map(x => [x.household, x.devices.map(d => d.id)]), [['b', [2]], ['a', [1, 3]]]);
});
test('lastSeenText', () => {
  assert.equal(U.lastSeenText(1000, 1030), 'vừa xong'); assert.equal(U.lastSeenText(0, 125), '2 phút trước');
  assert.equal(U.lastSeenText(0, 7200), '2 giờ trước'); assert.equal(U.lastSeenText(0, 172800), '2 ngày trước');
});
test('countdownText', () => { assert.equal(U.countdownText(700, 100), '10:00'); assert.equal(U.countdownText(100, 200), '0:00'); });
test('gridMove', () => {
  assert.deepEqual(U.gridMove([1, 3, 1], 1, 2, 'ArrowDown'), [2, 0]);
  assert.deepEqual(U.gridMove([1, 3], 0, 0, 'ArrowUp'), [0, 0]);
  assert.deepEqual(U.gridMove([1, 3], 1, 2, 'ArrowRight'), [1, 2]);
  assert.deepEqual(U.gridMove([1, 3], 1, 1, 'ArrowLeft'), [1, 0]);
  assert.deepEqual(U.gridMove([1, 3], 1, 1, 'Enter'), [1, 1]);
});
```

- [ ] **Step 2: Chạy** — `node --test tests/js/*.mjs` → thất bại vì thiếu file.
- [ ] **Step 3: Viết `static/settings-utils.js`.**
- [ ] **Step 4: Chạy lại** — mọi test JS PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(settings): pure helpers for the settings page"`

---

### Task 9: Trang Cài đặt — lối vào và nhóm "Thiết bị này"

**Files:**
- Create: `static/settings.js` (gán `window.HFSettings = { open }`)
- Modify: `templates/index.html` (header: thay khối `#castSettingsBtn` bằng `<button id="settingsBtn" class="settings-btn" aria-label="Cài đặt">` icon bánh răng; xoá `#castSettings`; thêm `#settings-tab`; nạp `asset('settings-utils.js')` cạnh `home-utils.js` và `asset('settings.js')` sau `cast.js`; `switchTab('settings-tab')` gọi `HFSettings.open()`)
- Modify: `static/cast.js` (xoá khối "cài đặt: công tắc ..." dòng 512–524)
- Modify: `static/hf.css` (xoá quy tắc `.cast-settings-btn`; thêm `.settings-btn`, `.settings-page`, `.set-*`)
- Test: `tests/ui/test_settings.py`

**Interfaces:**
- Consumes: `HFSettingsUtils.*`, `HFPlayerAuto.getPrefs()/setPrefs()`, sự kiện `hf-prefs`, `GET/PATCH /api/device`, `GET/PATCH /api/cast/settings`
- Produces (DOM mà Task 10–11 và test dùng):
  - `#settings-tab .settings-page` > `.settings-head` (`#settingsBack` nút ‹, `<h2>Cài đặt</h2>`) + `#settingsBody`
  - Mỗi nhóm: `<section class="set-group" data-group="device|admin|devices|system|hometv">` > `<h3 class="set-group-title">` + `<div class="set-list">` > các `.set-row[data-key]`
  - Công tắc: `<button type="button" role="switch" class="set-switch" aria-checked="true|false">`
  - Bước số: `.set-stepper` > `button.set-step[data-dir="-1"]`, `span.set-value`, `button.set-step[data-dir="1"]`
  - Lỗi: `.set-error` trong dòng; nút nguy hiểm: `button.set-danger`
  - `data-key` nhóm device: `device.name`, `cast.allow_control`, `player.autoNext`, `player.autoIntro`, `player.introSeconds`
  - Bước số `player.introSeconds` dùng item `{label: "Mở đầu kết thúc", min: 10, max: 300, step: 5, unit: "giây"}`; lưu bằng `HFPlayerAuto.setPrefs({introSeconds})`, không gọi server
  - Hàm nội bộ dùng chung cho Task 10: `renderGroup(id, title, rows)`, `switchRow(key, label, value, onChange)`, `stepperRow(item, onSave)` (lưu sau 400ms, lỗi thì trả giá trị cũ và hiện `.set-error`)

- [ ] **Step 1: Viết test UI (thất bại)**

```python
def test_gear_opens_settings_on_phone_and_tv(open_app): ...  # cả 2 thiết bị: tap #settingsBtn → #settings-tab.active, thấy group "device"
def test_regular_device_sees_only_device_and_admin_groups(pw, open_app):
    page = _settings(open_app)
    assert groups(page) == ["device", "admin"]
def test_admin_group_hidden_when_admin_disabled(open_app, monkeypatch):
    monkeypatch.delenv("HOMEFLIX_ADMIN_PASSWORD", raising=False)
    assert groups(_settings(open_app)) == ["device"]
def test_device_name_edit_persists(open_app): ...  # sửa tên → reload → tên mới; "PATCH /api/device" trong page.requests
def test_remote_control_switch_persists(open_app): ...  # tắt → reload → aria-checked false
def test_player_prefs_shared_with_player_panel(open_app):
    page = _settings(open_app)
    tap(page, '[data-key="player.autoNext"] .set-switch')
    assert page.evaluate("HFPlayerAuto.getPrefs().autoNext") is False
    page.evaluate("HFPlayerAuto.setPrefs({introSeconds: 120})")
    assert page.locator('[data-key="player.introSeconds"] .set-value').inner_text() == "120 giây"
def test_stepper_saves_once_after_400ms(open_app): ...  # bấm (+) 3 lần nhanh → introSeconds tăng 3 bước, chỉ một lần ghi
def test_back_returns_to_previous_tab(open_app): ...  # từ saved-tab mở cài đặt → ‹ → saved-tab.active
def test_device_group_works_when_settings_api_fails(open_app):
    page = open_app(); page.route("**/api/settings", lambda r: r.fulfill(status=500, body="x")); page.goto(URL)
    tap(page, "#settingsBtn")
    assert groups(page)[0] == "device" and page.console_errors == []
def test_no_horizontal_scroll(open_app): ...  # document.documentElement.scrollWidth <= innerWidth
```

`_settings(open_app, **kw)`: mở trang, `tap(page, "#settingsBtn")`, đợi `.set-group[data-group="device"]`. `groups(page)`: danh sách `data-group` theo thứ tự.

- [ ] **Step 2: Chạy, xác nhận thất bại** — `python -m pytest -q tests/ui/test_settings.py`.

- [ ] **Step 3: Viết code** — bố cục theo spec mục "Bố cục"; trang rộng tối đa 640px, căn giữa trên máy tính và TV; công tắc nền trắng khi bật; nút `#settingsBtn` hiện trên mọi thiết bị. Mở tab thì `syncFloatTabs` không tô nút nào. Panel `#playerPrefs` giữ nguyên.

- [ ] **Step 4: Chạy lại** — `python -m pytest -q tests/ui/test_settings.py tests/ui/test_cast_receiver.py tests/ui/test_cast_hardening_ui.py tests/ui/test_player_auto.py tests/test_static_versioning.py` → PASS (`tests/ui/test_cast_receiver.py` đang dùng `#castSettings`/`#castAllowControl`: sửa sang `[data-key="cast.allow_control"] .set-switch` trong trang Cài đặt, giữ nguyên điều test kiểm tra).

- [ ] **Step 5: Commit** — `git commit -m "feat(settings): settings page with this-device group, gear in header"`

---

### Task 10: Nhóm Quản trị, Thiết bị trong nhà, Hệ thống, HomeTV

**Files:**
- Modify: `static/settings.js`, `static/hf.css`
- Test: `tests/ui/test_settings_admin.py`

**Interfaces:**
- Consumes: mọi route ở Task 3–6, `HFSettingsUtils.*`, hàm dựng dòng của Task 9
- Produces: `data-group` `admin`, `devices`, `system`, `hometv`; `data-key` hệ thống trùng key ở Global Constraints; dòng `[data-key="admin.login"]` (chạm mở `#adminPassword`, Enter để gửi), `[data-key="admin.request"]`, `[data-key="admin.logout"]`; `#adminPassword` (ô mật khẩu), `#adminCode` (mã đang chờ), `.set-request[data-id]` (yêu cầu chờ, có nút `.set-approve` và `.set-deny`), `.set-device[data-id]`

- [ ] **Step 1: Viết test UI (thất bại)**

```python
def test_login_reveals_admin_groups(pw, open_app):
    page = _settings(open_app); login(page, "mk-test")
    assert groups(page) == ["device", "admin", "devices", "system", "hometv"]
def test_wrong_password_shows_error_and_lockout_time(pw, open_app): ...  # 5 lần sai → .set-error chứa "phút"
def test_system_stepper_saves(pw, open_app):
    page = admin_page(open_app); tap(page, '[data-key="cache.max_gb"] .set-step[data-dir="1"]')
    page.wait_for_function("document.querySelector('[data-key=\"cache.max_gb\"] .set-value').textContent === '11 GB'")
    page.wait_for_timeout(600); assert page.requests.count("PATCH /api/settings") == 1
def test_save_error_reverts_value(pw, open_app): ...  # route PATCH /api/settings → 422; giá trị quay về "10 GB", có .set-error
def test_403_reloads_settings(pw, open_app, app_module): ...  # thu quyền trong DB rồi bấm (+) → trang dựng lại, chỉ còn device/admin
def test_tv_requests_phone_approves(pw, open_app):
    tv = _settings(open_app, tv=True); tap(tv, '[data-key="admin.request"]')
    code = tv.locator("#adminCode").inner_text()
    phone = admin_page(open_app)
    phone.wait_for_selector(f'.set-request:has-text("{code}")')
    tap(phone, ".set-request .set-approve")
    tv.wait_for_selector('.set-group[data-group="system"]', timeout=8000)
def test_devices_grouped_and_rename(pw, open_app): ...  # máy mình đứng đầu nhóm nhà mình, đổi tên máy khác thành công
def test_delete_device_asks_confirmation(pw, open_app): ...  # page.on("dialog") trả dismiss → không có DELETE; accept → có "DELETE /api/admin/devices/..."
def test_clear_cache_asks_confirmation(pw, open_app): ...  # tương tự với "POST /api/admin/cache/clear"
def test_hometv_placeholder(pw, open_app): ...  # nhóm hometv có đúng một dòng "Nguồn IPTV · có ở bản sau"
def test_logout_hides_admin_groups(pw, open_app): ...
```

Các test cần mật khẩu dùng fixture `pw` từ `tests/conftest.py` (Task 3). `login(page, password)`: chạm `[data-key="admin.login"]`, điền `#adminPassword`, nhấn Enter. `admin_page(open_app, **kw)`: `_settings` + `login(page, "mk-test")`.

- [ ] **Step 2: Chạy, xác nhận thất bại** — `python -m pytest -q tests/ui/test_settings_admin.py`.

- [ ] **Step 3: Viết code** — xác nhận bằng `confirm()` với câu: "Xoá {tên}? Tủ phim và tiến độ xem của máy này sẽ mất." và "Xoá toàn bộ cache?". Khi trang mở và là admin: tải lại `GET /api/admin/requests` mỗi 5 giây; khi đang chờ duyệt: gọi `GET /api/admin/requests/mine` mỗi 3 giây và hiện `countdownText`. Dừng mọi bộ đếm khi rời tab. `approved` → dựng lại trang; `denied` → "Bị từ chối"; `expired` → "Hết hạn, thử lại".

- [ ] **Step 4: Chạy lại** — `python -m pytest -q tests/ui/test_settings.py tests/ui/test_settings_admin.py` → PASS.

- [ ] **Step 5: Commit** — `git commit -m "feat(settings): admin login/approval, devices, system and HomeTV groups"`

---

### Task 11: Điều hướng TV, ảnh chụp màn hình, toàn bộ test

**Files:**
- Modify: `static/settings.js` (bắt `keydown` trong `#settings-tab` khi `body.device-tv`, dùng `HFSettingsUtils.gridMove`; mỗi `.set-row` là một dòng, các phần tử focus được trong dòng là các cột)
- Modify: `static/sw.js` chỉ khi `tests/ui/test_screenshots.py` yêu cầu tăng phiên bản cache
- Test: `tests/ui/test_settings.py` (thêm), `tests/ui/test_screenshots.py` (thêm kích thước cho trang Cài đặt)

**Interfaces:** Consumes `HFSettingsUtils.gridMove`.

- [ ] **Step 1: Viết test (thất bại)**

```python
def test_tv_arrows_move_between_rows_and_controls(open_app):
    page = _settings(open_app, tv=True)
    page.focus('[data-key="device.name"] button')
    page.keyboard.press("ArrowDown")
    assert page.evaluate("document.activeElement.closest('.set-row').dataset.key") == "cast.allow_control"
    for _ in range(3): page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowRight")
    assert page.evaluate("document.activeElement.dataset.dir") == "1"
@pytest.mark.parametrize("w,h,tv", SIZES)
def test_settings_screenshot(pw, open_app, tmp_path, w, h, tv): ...  # chụp sau khi đăng nhập admin; file > 20 KB; không cuộn ngang
```

- [ ] **Step 2: Chạy, xác nhận thất bại.**
- [ ] **Step 3: Viết code.**
- [ ] **Step 4: Chạy toàn bộ** — `python -m pytest -q -p no:warnings` và `node --test tests/js/*.mjs` → tất cả PASS. Xem ảnh chụp bằng mắt (`SCREENSHOT_DIR=<scratchpad>`), sửa chỗ lệch bố cục nếu có.
- [ ] **Step 5: Commit** — `git commit -m "feat(settings): TV arrow navigation, screenshots"`

Sau Task 11: đặt `HOMEFLIX_ADMIN_PASSWORD` trong `deploy/.env` trên server **trước** khi commit cuối lên `main` (hook tự deploy); không có file này thì admin tắt, trang Cài đặt chỉ còn "Thiết bị này".
