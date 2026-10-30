# Nền tảng thiết bị (Gói 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mỗi thiết bị có ID riêng (cookie), danh sách lưu và tiến độ xem riêng (kể cả vị trí theo giây), và được gom vào "nhà" theo IP, không cần đăng nhập.

**Architecture:** Hàm thuần (IP, nhà, loại thiết bị) nằm trong `devices.py` để test độc lập. Middleware trong `app.py` cấp và đọc cookie `hf_device`, ghi bảng `devices`. Bảng `device_movies` thay `saved_movies` làm nguồn dữ liệu cho `/api/saved*`; `saved_movies` giữ nguyên chỉ để nhập dữ liệu cũ một lần. `episode_downloads` và file MP4/cache vẫn dùng chung.

**Tech Stack:** FastAPI, SQLite (singleton `get_db()` + `_db_lock` có sẵn), pytest, `fastapi.testclient.TestClient`.

**Spec:** `docs/superpowers/specs/2026-10-29-device-sessions-ios26-ui-design.md` (mục "Gói 1 — Nền tảng thiết bị"). Gói 2, 3, 4 có plan riêng, không nằm ở đây.

## Global Constraints

- Cookie tên `hf_device`, giá trị UUIDv4. `Set-Cookie` có `HttpOnly; SameSite=Lax; Path=/; Max-Age=63072000`, thêm `Secure` khi `request.url.scheme == "https"`.
- Chỉ cấp cookie ở `GET /` và đường dẫn bắt đầu bằng `/api/saved`, `/api/device`, `/api/cast`. Không cấp ở `/proxy/*`, `/media/*`, `/static/*`.
- `household = sha256(salt + ip_chuẩn_hóa).hexdigest()[:16]`. `salt` sinh một lần, lưu trong DB.
- IP: dùng `request.client.host`; chỉ đọc hop đầu của `X-Forwarded-For` khi peer nằm trong biến môi trường `TRUSTED_PROXIES` (danh sách IP cách nhau bằng dấu phẩy). IPv6 cắt về /64. IP riêng (RFC1918, link-local, loopback) quy về `lan`.
- Tên mặc định: "iPhone", "TV", "Máy tính". `kind` thuộc `phone`, `tv`, `desktop`.
- Route theo thiết bị trả `Cache-Control: private, no-store` và `Vary: Cookie`. Không đổi header của `/api/home/*`, `/api/search`, `/api/movie/*`.
- `saved_movies` không bị xóa hay sửa. `episode_downloads` giữ nguyên và dùng chung.
- Thiết bị không hoạt động quá 180 ngày bị xóa cùng dữ liệu của nó.
- Không đăng nhập, không mật khẩu. Server chạy một tiến trình.
- Commit theo kiểu repo đang dùng: `feat(devices): ...`, `test(devices): ...`.

## Review Focus

Năm điểm spec ngầm yêu cầu mà dễ vỡ nhất khi dùng thật. Mỗi dòng có test ở task ghi trong ngoặc.

1. Cookie giả hoặc rác (không phải UUID) phải bị thay bằng ID mới, không gây lỗi 500 và không nhận dữ liệu của người khác (Task 2).
2. `X-Forwarded-For` từ máy không nằm trong `TRUSTED_PROXIES` phải bị bỏ qua, nếu không ai cũng giả được "cùng nhà" (Task 1).
3. Vị trí xem rác (chữ, số âm, vô cực, lớn hơn thời lượng) không được làm hỏng dữ liệu hay trả 500 (Task 5).
4. Một thiết bị xóa phim không được xóa file MP4 và cache mà thiết bị khác còn dùng (Task 4).
5. Client gửi vị trí mỗi 10 giây trên cùng một tập không được kích hoạt lại việc đánh dấu "đã xem" và lên lịch xóa file tập đang xem (Task 5).

---

## File Structure

- Create `devices.py` — hàm thuần: chuẩn hóa IP, tính nhà, suy ra loại thiết bị. Không import `app`.
- Create `requirements-dev.txt`, `pytest.ini`, `tests/conftest.py`.
- Create `tests/test_devices_helpers.py`, `tests/test_device_middleware.py`, `tests/test_device_movies.py`, `tests/test_saved_api.py`, `tests/test_progress_api.py`, `tests/test_device_prune.py`.
- Modify `app.py` — schema, middleware, route, lớp dữ liệu theo thiết bị. Giữ đúng mô hình một file `app.py` cho phần DB và route, theo `AGENTS.md`.
- Modify `templates/index.html` — gửi và khôi phục vị trí xem (Task 7).

---

### Task 1: Hàm thuần cho IP, nhà và loại thiết bị

**Files:**
- Create: `devices.py`, `requirements-dev.txt`, `pytest.ini`, `tests/test_devices_helpers.py`
- Modify: `.gitignore` (thêm `.venv/` nếu chưa có)

**Interfaces:**
- Produces (trong `devices.py`):
  - `normalize_ip(raw: str) -> str`
  - `parse_trusted_proxies(value: str | None) -> frozenset[str]`
  - `client_ip(peer: str | None, forwarded_for: str | None, trusted: frozenset[str]) -> str`
  - `household_id(ip: str, salt: str) -> str`
  - `device_kind(user_agent: str) -> str`
  - `default_device_name(kind: str, user_agent: str) -> str`

- [ ] **Step 1: Chuẩn bị môi trường test**

Tạo `requirements-dev.txt` gồm `-r requirements.txt` và `pytest`. Tạo `pytest.ini` với `[pytest]` và `testpaths = tests`. Chạy `python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`.

- [ ] **Step 2: Viết test thất bại trong `tests/test_devices_helpers.py`**

```python
from devices import (normalize_ip, parse_trusted_proxies, client_ip,
                     household_id, device_kind, default_device_name)

def test_private_and_loopback_ips_collapse_to_lan():
    for ip in ("192.168.1.20", "10.0.0.5", "172.16.4.4", "127.0.0.1", "169.254.1.1", "fe80::1", "::1"):
        assert normalize_ip(ip) == "lan"

def test_public_ipv4_kept():
    assert normalize_ip("203.0.113.7") == "203.0.113.7"

def test_ipv6_cut_to_64_prefix():
    assert normalize_ip("2001:db8:1:2:aaaa:bbbb:cccc:dddd") == normalize_ip("2001:db8:1:2:1:2:3:4")
    assert normalize_ip("2001:db8:1:2::1") != normalize_ip("2001:db8:1:3::1")

def test_ipv4_mapped_ipv6_becomes_ipv4():
    assert normalize_ip("::ffff:203.0.113.7") == "203.0.113.7"

def test_garbage_ip_does_not_raise():
    assert normalize_ip("testclient") == "testclient"
    assert normalize_ip("") == "unknown"

def test_trusted_proxy_forwarded_for_used():
    trusted = parse_trusted_proxies("10.0.0.2, 10.0.0.3")
    assert client_ip("10.0.0.2", "203.0.113.9, 10.0.0.2", trusted) == "203.0.113.9"

def test_forwarded_for_from_untrusted_peer_is_ignored():
    trusted = parse_trusted_proxies("10.0.0.2")
    assert client_ip("198.51.100.4", "203.0.113.9", trusted) == "198.51.100.4"

def test_no_peer_gives_unknown():
    assert client_ip(None, None, frozenset()) == "unknown"

def test_household_stable_and_salted():
    a = household_id("203.0.113.7", "s1")
    assert a == household_id("203.0.113.7", "s1") and len(a) == 16
    assert a != household_id("203.0.113.7", "s2")
    assert a != household_id("203.0.113.8", "s1")

def test_device_kind_and_default_name():
    iphone = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"
    tv = "Mozilla/5.0 (Web0S; Linux/SmartTV) AppleWebKit/537.36 webOS.TV-2023"
    desktop = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120"
    assert device_kind(iphone) == "phone" and default_device_name("phone", iphone) == "iPhone"
    assert device_kind(tv) == "tv" and default_device_name("tv", tv) == "TV"
    assert device_kind(desktop) == "desktop" and default_device_name("desktop", desktop) == "Máy tính"
    assert default_device_name("phone", "Mozilla/5.0 (Linux; Android 14)") == "Điện thoại"
```

- [ ] **Step 3: Chạy test để thấy thất bại**

Run: `.venv/bin/pytest tests/test_devices_helpers.py -v`
Expected: FAIL vì `ModuleNotFoundError: No module named 'devices'`.

- [ ] **Step 4: Implement `devices.py`**

Dùng module `ipaddress`: `ip_address(...).is_private` hoặc `is_loopback` hoặc `is_link_local` thì trả `lan`; IPv6 thường dùng `ip_network(f"{ip}/64", strict=False).network_address`; `ipv4_mapped` đổi về IPv4; chuỗi không phải IP thì trả lại đã `strip()`, chuỗi rỗng thì `unknown`. `device_kind` dùng cùng regex TV với `detectDevice()` ở `templates/index.html` (`tv|smarttv|googletv|appletv|hbbtv|netcast|webos|tizen|rokutv|ctv|box|playstation|xbox|nintendo|large-screen`), kiểm tra TV trước rồi tới `iphone|ipad|ipod|android` thì `phone`, còn lại `desktop`.

- [ ] **Step 5: Chạy test để thấy đạt**

Run: `.venv/bin/pytest tests/test_devices_helpers.py -v`
Expected: PASS (toàn bộ).

- [ ] **Step 6: Commit**

```bash
git add devices.py requirements-dev.txt pytest.ini tests/test_devices_helpers.py .gitignore
git commit -m "feat(devices): add IP, household and device-kind helpers"
```

---

### Task 2: Schema, cookie thiết bị, `GET/PATCH /api/device`

**Files:**
- Modify: `app.py` (`init_db` tại dòng 113; thêm middleware cạnh `api_cache_headers` tại dòng 729; route mới)
- Create: `tests/conftest.py`, `tests/test_device_middleware.py`

**Interfaces:**
- Consumes: mọi hàm ở Task 1.
- Produces (trong `app.py`):
  - hằng `DEVICE_COOKIE = "hf_device"`, `DEVICE_COOKIE_MAX_AGE = 63072000`, `DEVICE_COOKIE_PATHS = ("/api/saved", "/api/device", "/api/cast")`, `TRUSTED_PROXIES: frozenset[str]` (đọc từ `os.environ.get("TRUSTED_PROXIES")`)
  - `get_household_salt() -> str`
  - `_db_touch_device(device_id: str, household: str, kind: str, user_agent: str, now: float) -> dict` — thêm hàng nếu chưa có; nếu có thì chỉ ghi `last_seen` khi cũ hơn 60 giây, và cập nhật `household` nếu đổi. Trả dict gồm `id, name, kind, household, created_at, last_seen, created: bool`.
  - `request.state.device_id: str` và `request.state.household: str` khi đường dẫn thuộc diện cấp cookie
  - bảng `devices(id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, household TEXT NOT NULL, user_agent TEXT, last_seen REAL NOT NULL, created_at REAL NOT NULL)`, `app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)`, và `device_movies` (xem Task 3, tạo luôn ở đây trong `init_db`)
  - `GET /api/device` trả `{id, name, kind, created_at, last_seen}`; `PATCH /api/device` nhận `{name?, kind?}`, tên 1–40 ký tự sau `strip()`, `kind` thuộc ba giá trị hợp lệ, sai thì trả HTTP 422.

- [ ] **Step 1: Viết fixture trong `tests/conftest.py`**

```python
import pytest
from fastapi.testclient import TestClient

IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"

@pytest.fixture
def app_module(tmp_path, monkeypatch):
    import app as m
    monkeypatch.setattr(m, "DB_FILE", str(tmp_path / "t.db"))
    monkeypatch.setattr(m, "DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setattr(m, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(m, "_db_conn", None)
    (tmp_path / "downloads").mkdir()
    (tmp_path / "cache").mkdir()
    m.init_db()
    yield m
    if m._db_conn is not None:
        m._db_conn.close()
        m._db_conn = None

@pytest.fixture
def make_client(app_module):
    def _make(ip="203.0.113.7", ua=IPHONE):
        return TestClient(app_module.app, client=(ip, 50000), headers={"user-agent": ua})
    return _make
```

Fixture không dùng `with TestClient(...)` nên lifespan (worker nền, gọi `phimapi.com`) không chạy.

- [ ] **Step 2: Viết test thất bại trong `tests/test_device_middleware.py`**

```python
import re, uuid

def test_first_request_sets_cookie_with_required_attributes(make_client):
    r = make_client().get("/api/device")
    assert r.status_code == 200
    sc = r.headers["set-cookie"]
    assert sc.startswith("hf_device=")
    for part in ("HttpOnly", "SameSite=lax", "Path=/", "Max-Age=63072000"):
        assert part.lower() in sc.lower()
    assert "secure" not in sc.lower()
    uuid.UUID(re.match(r"hf_device=([^;]+)", sc).group(1), version=4)

def test_second_request_keeps_same_device_without_new_cookie(make_client):
    c = make_client()
    first = c.get("/api/device").json()
    r = c.get("/api/device")
    assert r.json()["id"] == first["id"] and "set-cookie" not in r.headers

def test_forged_cookie_is_replaced_with_new_device(make_client):
    c = make_client()
    r = c.get("/api/device", cookies={"hf_device": "../../etc/passwd"})
    assert r.status_code == 200
    assert "hf_device=" in r.headers["set-cookie"]
    uuid.UUID(r.json()["id"], version=4)

def test_valid_uuid_cookie_unknown_to_server_is_registered_as_given(make_client):
    c = make_client()
    gid = str(uuid.uuid4())
    assert c.get("/api/device", cookies={"hf_device": gid}).json()["id"] == gid

def test_secure_flag_on_https(app_module):
    from fastapi.testclient import TestClient
    c = TestClient(app_module.app, base_url="https://testserver", client=("203.0.113.7", 1))
    assert "secure" in c.get("/api/device").headers["set-cookie"].lower()

def test_no_cookie_on_proxy_and_static_paths(make_client):
    c = make_client()
    for path in ("/proxy/ts", "/static/logo.png", "/media/x/y.mp4"):
        assert "hf_device" not in c.get(path).headers.get("set-cookie", "")

def test_root_sets_cookie(make_client):
    assert "hf_device=" in make_client().get("/").headers["set-cookie"]

def test_default_name_and_rename(make_client):
    c = make_client()
    assert c.get("/api/device").json()["name"] == "iPhone"
    assert c.patch("/api/device", json={"name": "  Điện thoại bố  "}).json()["name"] == "Điện thoại bố"
    assert c.get("/api/device").json()["name"] == "Điện thoại bố"
    assert c.patch("/api/device", json={"name": "   "}).status_code == 422
    assert c.patch("/api/device", json={"name": "x" * 41}).status_code == 422
    assert c.patch("/api/device", json={"kind": "toaster"}).status_code == 422
    assert c.patch("/api/device", json={"kind": "tv"}).json()["kind"] == "tv"

def test_cache_headers_are_private(make_client):
    r = make_client().get("/api/device")
    assert r.headers["cache-control"] == "private, no-store"
    assert "cookie" in r.headers["vary"].lower()

def test_same_ip_same_household_different_ip_different(app_module, make_client):
    a, b, c3 = make_client(ip="203.0.113.7"), make_client(ip="203.0.113.7"), make_client(ip="198.51.100.9")
    ids = [x.get("/api/device").json()["id"] for x in (a, b, c3)]
    rows = {i: app_module.db_query_one("SELECT household FROM devices WHERE id=?", (i,))[0] for i in ids}
    assert rows[ids[0]] == rows[ids[1]] != rows[ids[2]]

def test_last_seen_is_not_rewritten_within_60_seconds(app_module):
    d = app_module._db_touch_device("11111111-1111-4111-8111-111111111111", "h", "phone", "ua", 1000.0)
    again = app_module._db_touch_device(d["id"], "h", "phone", "ua", 1030.0)
    assert again["last_seen"] == 1000.0 and again["created"] is False
    later = app_module._db_touch_device(d["id"], "h", "phone", "ua", 1061.0)
    assert later["last_seen"] == 1061.0
```

- [ ] **Step 3: Chạy test để thấy thất bại**

Run: `.venv/bin/pytest tests/test_device_middleware.py -v`
Expected: FAIL (route `/api/device` chưa có, trả 404 hoặc `AttributeError`).

- [ ] **Step 4: Implement trong `app.py`**

- `init_db`: thêm `CREATE TABLE IF NOT EXISTS` cho `devices`, `app_settings`, `device_movies` (cột của `device_movies` theo Task 3) và các index: `devices(household)`, `devices(last_seen)`, `device_movies(device_id, updated_at DESC)`, `device_movies(slug)`.
- `get_household_salt`: đọc `app_settings` khóa `household_salt`; chưa có thì sinh bằng `secrets.token_hex(16)` và ghi, dưới `_db_lock`.
- Middleware `device_middleware` dùng `@app.middleware("http")`: chỉ xử lý khi `path == "/"` hoặc bắt đầu bằng một phần tử của `DEVICE_COOKIE_PATHS`. Cookie hợp lệ là chuỗi parse được bằng `uuid.UUID(...)` với `version == 4`; ngược lại sinh `uuid.uuid4()`. Tính `household` bằng `household_id(client_ip(...), get_household_salt())`, gọi `_db_touch_device` qua `asyncio.to_thread`, gán `request.state`, gọi `call_next`, rồi `response.set_cookie(...)` chỉ khi cookie ban đầu thiếu hoặc không hợp lệ. Với đường dẫn `/api/*` thuộc diện này, đặt `Cache-Control: private, no-store` và `Vary: Cookie` (không đụng tới `GET /`).
- `_db_touch_device`: dùng `get_db()` và `_db_lock` thủ công (khóa không tái nhập, đừng gọi `db_execute` bên trong). Tên mặc định lấy từ `default_device_name`.
- Route `GET /api/device` và `PATCH /api/device` trả HTTP 422 bằng `HTTPException(422, ...)` khi dữ liệu sai.

- [ ] **Step 5: Chạy test để thấy đạt**

Run: `.venv/bin/pytest tests/ -v`
Expected: PASS (Task 1 và Task 2).

- [ ] **Step 6: Commit**

```bash
git add app.py tests/conftest.py tests/test_device_middleware.py
git commit -m "feat(devices): issue hf_device cookie, register devices, add /api/device"
```

---

### Task 3: Lớp dữ liệu `device_movies` và nhập dữ liệu cũ

**Files:**
- Modify: `app.py` (thay các hàm `_db_get_all_movies`, `_db_get_movie`, `_db_save_movie`, `_db_delete_movie` và wrapper async ở dòng 227–295; sửa `_db_touch_device`)
- Test: `tests/test_device_movies.py`

**Interfaces:**
- Consumes: `_db_touch_device`, `db_execute`, `db_query`, `db_query_one`.
- Produces: bảng `device_movies(device_id TEXT NOT NULL, slug TEXT NOT NULL, name TEXT NOT NULL, poster_url TEXT, year TEXT, episode_current TEXT, total_episodes INTEGER, last_watched_episode TEXT, last_watched_url TEXT, position_sec REAL, duration_sec REAL, episodes TEXT, episode_states TEXT, saved INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL, PRIMARY KEY(device_id, slug))` (đã tạo ở Task 2). Dict phim trả ra có khóa `slug, name, poster_url, year, episode_current, total_episodes, last_watched_episode, last_watched_url, episodes (list), episode_states (dict), position (float|None), duration (float|None), saved (bool), updated_at (str)`.
  - `_db_list_device_movies(device_id: str) -> list[dict]` — phim có `saved = 1` hoặc có `last_watched_url` khác rỗng hoặc `position_sec` khác NULL; sắp theo `updated_at` giảm dần.
  - `_db_get_device_movie(device_id: str, slug: str) -> dict | None`
  - `_db_upsert_device_movie(device_id: str, movie: dict) -> None` — khóa `saved` mặc định `True`, `position`/`duration` mặc định giữ nguyên giá trị cũ khi không truyền.
  - `_db_delete_device_movie(device_id: str, slug: str) -> bool`
  - `_db_other_devices_hold(device_id: str, slug: str) -> bool`
  - `_db_all_device_movies() -> list[dict]` — mỗi `slug` một bản, lấy bản `updated_at` mới nhất (cho `download_worker`).
  - Các wrapper async: `get_device_movies(device_id)`, `get_device_movie(device_id, slug)`, `save_device_movie(device_id, movie)`, `delete_device_movie(device_id, slug)`.

- [ ] **Step 1: Viết test thất bại trong `tests/test_device_movies.py`**

```python
A, B = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

def movie(slug="phim-a", **kw):
    base = {"slug": slug, "name": "Phim A", "poster_url": "/p.jpg", "year": "2026",
            "episodes": [{"name": "Tập 1", "link_m3u8": "http://x/1.m3u8"}], "episode_states": {}}
    base.update(kw); return base

def test_devices_do_not_see_each_others_movies(app_module):
    m = app_module
    m._db_upsert_device_movie(A, movie())
    assert [x["slug"] for x in m._db_list_device_movies(A)] == ["phim-a"]
    assert m._db_list_device_movies(B) == []
    assert m._db_get_device_movie(B, "phim-a") is None

def test_roundtrip_types(app_module):
    m = app_module
    m._db_upsert_device_movie(A, movie(position=61.5, duration=2700.0, last_watched_url="http://x/1.m3u8"))
    got = m._db_get_device_movie(A, "phim-a")
    assert got["saved"] is True and got["position"] == 61.5 and got["duration"] == 2700.0
    assert got["episodes"][0]["name"] == "Tập 1" and got["episode_states"] == {}

def test_upsert_without_position_keeps_old_position(app_module):
    m = app_module
    m._db_upsert_device_movie(A, movie(position=10.0, duration=100.0))
    m._db_upsert_device_movie(A, movie(name="Đổi tên"))
    got = m._db_get_device_movie(A, "phim-a")
    assert got["name"] == "Đổi tên" and got["position"] == 10.0

def test_list_includes_unsaved_movie_with_progress_only(app_module):
    m = app_module
    m._db_upsert_device_movie(A, movie("chi-xem", saved=False, position=5.0))
    m._db_upsert_device_movie(A, movie("khong-gi", saved=False))
    assert [x["slug"] for x in m._db_list_device_movies(A)] == ["chi-xem"]

def test_delete_and_other_devices_hold(app_module):
    m = app_module
    m._db_upsert_device_movie(A, movie()); m._db_upsert_device_movie(B, movie())
    assert m._db_other_devices_hold(A, "phim-a") is True
    assert m._db_delete_device_movie(A, "phim-a") is True
    assert m._db_other_devices_hold(B, "phim-a") is False
    assert m._db_delete_device_movie(A, "phim-a") is False

def test_all_device_movies_one_per_slug_latest(app_module):
    m = app_module
    m._db_upsert_device_movie(A, movie(name="Cũ")); m._db_upsert_device_movie(B, movie(name="Mới"))
    rows = m._db_all_device_movies()
    assert len(rows) == 1 and rows[0]["name"] == "Mới"

def _legacy(m, slug):
    m.db_execute("INSERT INTO saved_movies (slug,name,poster_url,year,episode_current,total_episodes,"
                 "last_watched_episode,last_watched_url,episodes,episode_states,updated_at) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 (slug, "Cũ "+slug, "/p", "2020", "Full", 1, "Tập 1", "u", '[{"name":"Tập 1","link_m3u8":"u"}]', '{"u":"watching"}', "2026-01-01T00:00:00"))

def test_first_device_adopts_legacy_movies_once(app_module, make_client):
    m = app_module
    _legacy(m, "cu-1"); _legacy(m, "cu-2")
    first = make_client(ip="203.0.113.7"); first.get("/api/device")
    got = {x["slug"]: x for x in m._db_list_device_movies(first.get("/api/device").json()["id"])}
    assert set(got) == {"cu-1", "cu-2"} and got["cu-1"]["saved"] is True
    assert got["cu-1"]["episode_states"] == {"u": "watching"} and got["cu-1"]["position"] is None
    second = make_client(ip="198.51.100.9")
    assert m._db_list_device_movies(second.get("/api/device").json()["id"]) == []
    assert m.db_query_one("SELECT COUNT(*) FROM saved_movies")[0] == 2

def test_no_adoption_when_no_legacy_rows(app_module, make_client):
    c = make_client(); c.get("/api/device")
    assert app_module.db_query_one("SELECT COUNT(*) FROM device_movies")[0] == 0
```

- [ ] **Step 2: Chạy test để thấy thất bại**

Run: `.venv/bin/pytest tests/test_device_movies.py -v`
Expected: FAIL với `AttributeError: module 'app' has no attribute '_db_upsert_device_movie'`.

- [ ] **Step 3: Implement các hàm trong `app.py`**

Thay các hàm `saved_movies` cũ bằng các hàm ở khối Interfaces, dùng cùng kiểu `json.dumps(..., ensure_ascii=False)` cho `episodes` và `episode_states` và cùng `datetime.now().isoformat()` cho `updated_at` như code hiện tại. `_db_upsert_device_movie` dùng `INSERT ... ON CONFLICT(device_id, slug) DO UPDATE`; với `position`/`duration` dùng `COALESCE(excluded.position_sec, device_movies.position_sec)` để giữ giá trị cũ khi truyền NULL. `_db_all_device_movies` lấy bản `updated_at` lớn nhất cho mỗi `slug`.

- [ ] **Step 4: Nhập dữ liệu cũ trong `_db_touch_device`**

Khi thiết bị vừa được tạo và `SELECT COUNT(*) FROM devices` bằng 1 (đây là thiết bị đầu tiên), chạy một `INSERT INTO device_movies (...) SELECT ... FROM saved_movies` trong cùng khối `_db_lock`, đặt `saved = 1`, `position_sec`/`duration_sec` NULL, giữ nguyên `last_watched_*`, `episodes`, `episode_states`, `updated_at`. Không đụng bảng `saved_movies`.

- [ ] **Step 5: Chạy toàn bộ test**

Run: `.venv/bin/pytest tests/ -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_device_movies.py
git commit -m "feat(devices): add per-device movie storage and legacy adoption"
```

---

### Task 4: `/api/saved` (GET, POST, DELETE) theo thiết bị

**Files:**
- Modify: `app.py` (`get_saved_movies_api` dòng 1052, `save_movie_api`, `delete_saved_movie_api`, `download_worker` dòng 419)
- Test: `tests/test_saved_api.py`

**Interfaces:**
- Consumes: wrapper async ở Task 3, `request.state.device_id`.
- Produces: `GET /api/saved` → danh sách dict phim như Task 3 (có thêm khóa `saved`, `position`, `duration`); `POST /api/saved` (body `movie`) → `{"status":"success","movie":...}` và đặt `saved = true`, `total_episodes = len(episodes)` khi có `episodes`; `DELETE /api/saved/{slug}` → xóa hàng của thiết bị này; chỉ xóa `episode_downloads`, cache phân đoạn và thư mục tải của phim khi `_db_other_devices_hold` trả `False`. Hợp đồng trả về giữ như cũ: thiếu `slug` trả `{"status":"error","message":"Missing slug"}`, không thấy phim trả `{"status":"error","message":"Movie not found"}`. `download_worker` đọc phim bằng `_db_all_device_movies()` thay cho `get_all_movies_from_db()`.

- [ ] **Step 1: Viết test thất bại trong `tests/test_saved_api.py`**

```python
import os

EP = [{"name": "Tập 1", "link_m3u8": "http://x/1.m3u8"}]
def body(slug="phim-a"): return {"slug": slug, "name": "Phim A", "poster_url": "/p", "year": "2026", "episodes": EP}

def test_save_and_list_are_isolated_per_device(make_client):
    a, b = make_client(), make_client()
    r = a.post("/api/saved", json=body())
    assert r.json()["status"] == "success" and r.json()["movie"]["total_episodes"] == 1
    got = a.get("/api/saved").json()
    assert [m["slug"] for m in got] == ["phim-a"] and got[0]["saved"] is True
    assert b.get("/api/saved").json() == []

def test_missing_slug_keeps_old_error_shape(make_client):
    assert make_client().post("/api/saved", json={"name": "x"}).json() == {"status": "error", "message": "Missing slug"}

def test_saved_responses_are_private(make_client):
    r = make_client().get("/api/saved")
    assert r.headers["cache-control"] == "private, no-store"

def test_delete_unknown_movie_keeps_old_error_shape(make_client):
    assert make_client().delete("/api/saved/none").json() == {"status": "error", "message": "Movie not found"}

def _plant_files(app_module, slug):
    d = os.path.join(app_module.DOWNLOAD_DIR, slug); os.makedirs(d)
    open(os.path.join(d, "Tap-1.mp4"), "wb").write(b"x")
    sid = __import__("hashlib").md5(b"http://x/1.m3u8").hexdigest()
    s = os.path.join(app_module.CACHE_DIR, sid); os.makedirs(s)
    app_module._db_update_download_status(slug, "Tập 1", "completed")
    return d, s

def test_delete_keeps_shared_files_while_another_device_holds_movie(app_module, make_client):
    a, b = make_client(), make_client()
    a.post("/api/saved", json=body()); b.post("/api/saved", json=body())
    dl, cache = _plant_files(app_module, "phim-a")
    assert a.delete("/api/saved/phim-a").json()["status"] == "success"
    assert os.path.exists(dl) and os.path.exists(cache)
    assert app_module._db_get_download_status("phim-a", "Tập 1")["status"] == "completed"
    assert b.delete("/api/saved/phim-a").json()["status"] == "success"
    assert not os.path.exists(dl) and not os.path.exists(cache)
    assert app_module._db_get_download_status("phim-a", "Tập 1")["status"] == "not_started"
```

- [ ] **Step 2: Chạy test để thấy thất bại**

Run: `.venv/bin/pytest tests/test_saved_api.py -v`
Expected: FAIL (thiết bị B thấy dữ liệu của A, hoặc `NameError` do hàm cũ đã bị gỡ).

- [ ] **Step 3: Implement**

Lấy `device_id = request.state.device_id` trong từng route (thêm tham số `request: Request`). `POST` gộp với phim sẵn có của thiết bị như logic `existing.update(movie)` hiện tại rồi gọi `save_device_movie`. `DELETE` giữ nguyên 3 bước dọn dẹp hiện có nhưng bọc trong `if not await asyncio.to_thread(_db_other_devices_hold, device_id, slug)`, và phải xóa hàng của thiết bị trước khi kiểm tra "ai còn giữ".

- [ ] **Step 4: Chạy toàn bộ test**

Run: `.venv/bin/pytest tests/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_saved_api.py
git commit -m "feat(devices): scope /api/saved to the calling device"
```

---

### Task 5: `/api/saved/progress` với vị trí theo giây

**Files:**
- Modify: `app.py` (`save_movie_progress_api` dòng 1082)
- Test: `tests/test_progress_api.py`

**Interfaces:**
- Consumes: `get_device_movie`, `save_device_movie`, `_db_other_devices_hold`, `_db_update_download_status`, `clean_filename`.
- Produces: `POST /api/saved/progress` nhận `{slug, last_watched_episode, last_watched_url, position?, duration?, name?, poster_url?, year?, episodes?}`.
  - Phim chưa có hàng của thiết bị: nếu body có `name` thì tạo hàng `saved = False` (dùng `poster_url`, `year`, `episodes` nếu có), nếu không có thì trả `{"status":"error","message":"Movie not in saved list"}` như cũ.
  - `last_watched_url` trùng `last_watched_url` đang lưu: chỉ cập nhật `position`/`duration`, không đổi `episode_states`, không lên lịch xóa file.
  - `last_watched_url` khác: chạy logic chuyển trạng thái hiện có (`watching` → `watched`, tập mới → `watching`), đặt `position` theo body hoặc 0. Chỉ lên lịch xóa file tập đã xem sau 3 giờ (`time.time() + 10800`) khi `_db_other_devices_hold(device_id, slug)` là `False`.
  - Giá trị `position`: không phải số hữu hạn → `{"status":"error","message":"Invalid position"}`, không ghi gì; số âm → 0; lớn hơn `duration` (khi `duration > 0`) → bằng `duration`. `duration` âm hoặc không hữu hạn → bỏ qua `duration` (lưu NULL).

- [ ] **Step 1: Viết test thất bại trong `tests/test_progress_api.py`**

```python
import os, pytest

U1, U2 = "http://x/1.m3u8", "http://x/2.m3u8"
EPS = [{"name": "Tập 1", "link_m3u8": U1}, {"name": "Tập 2", "link_m3u8": U2}]

def saved(c, slug="phim-a"):
    c.post("/api/saved", json={"slug": slug, "name": "Phim A", "poster_url": "/p", "year": "2026", "episodes": EPS})

def prog(c, url, pos=None, dur=None, slug="phim-a", ep=None, **extra):
    body = {"slug": slug, "last_watched_episode": ep or ("Tập 1" if url == U1 else "Tập 2"), "last_watched_url": url, **extra}
    if pos is not None: body["position"] = pos
    if dur is not None: body["duration"] = dur
    return c.post("/api/saved/progress", json=body).json()

def current(c, slug="phim-a"): return next(m for m in c.get("/api/saved").json() if m["slug"] == slug)

def test_progress_stores_position_and_states(make_client):
    c = make_client(); saved(c)
    prog(c, U1, 30, 2700); prog(c, U2, 5, 2700)
    m = current(c)
    assert m["episode_states"] == {U1: "watched", U2: "watching"}
    assert m["last_watched_url"] == U2 and m["position"] == 5 and m["duration"] == 2700

def test_same_episode_heartbeat_only_updates_position(app_module, make_client):
    c = make_client(); saved(c)
    prog(c, U1, 1, 2700); prog(c, U2, 1, 2700)
    d = os.path.join(app_module.DOWNLOAD_DIR, "phim-a"); os.makedirs(d)
    open(os.path.join(d, "Tap-2.mp4"), "wb").write(b"x")
    app_module._db_update_download_status("phim-a", "Tập 2", "completed")
    for pos in (10, 20, 30):
        assert prog(c, U2, pos, 2700)["status"] == "success"
    st = app_module._db_get_download_status("phim-a", "Tập 2")
    assert st["delete_at"] is None
    m = current(c)
    assert m["episode_states"] == {U1: "watched", U2: "watching"} and m["position"] == 30

def test_switching_episode_schedules_deletion_of_finished_one(app_module, make_client):
    c = make_client(); saved(c); prog(c, U1, 1, 2700)
    d = os.path.join(app_module.DOWNLOAD_DIR, "phim-a"); os.makedirs(d)
    open(os.path.join(d, "Tap-1.mp4"), "wb").write(b"x")
    app_module._db_update_download_status("phim-a", "Tập 1", "completed")
    prog(c, U2, 0, 2700)
    assert app_module._db_get_download_status("phim-a", "Tập 1")["delete_at"] is not None

def test_no_deletion_scheduled_when_other_device_holds_movie(app_module, make_client):
    a, b = make_client(), make_client(); saved(a); saved(b); prog(a, U1, 1, 2700)
    d = os.path.join(app_module.DOWNLOAD_DIR, "phim-a"); os.makedirs(d)
    open(os.path.join(d, "Tap-1.mp4"), "wb").write(b"x")
    app_module._db_update_download_status("phim-a", "Tập 1", "completed")
    prog(a, U2, 0, 2700)
    assert app_module._db_get_download_status("phim-a", "Tập 1")["delete_at"] is None

def test_progress_is_per_device(make_client):
    a, b = make_client(), make_client(); saved(a); saved(b)
    prog(a, U2, 99, 2700)
    assert current(b)["last_watched_url"] in ("", None) and current(b)["position"] is None

@pytest.mark.parametrize("bad", ["abc", None, float("nan"), float("inf"), [1], {"a": 1}])
def test_garbage_position_rejected_without_writing(make_client, bad):
    c = make_client(); saved(c); prog(c, U1, 40, 2700)
    r = c.post("/api/saved/progress", json={"slug": "phim-a", "last_watched_episode": "Tập 1",
                                            "last_watched_url": U1, "position": bad, "duration": 2700})
    assert r.status_code == 200
    if bad is None:
        assert r.json()["status"] == "success"
    else:
        assert r.json() == {"status": "error", "message": "Invalid position"}
    assert current(c)["position"] == 40

def test_negative_and_oversized_position_are_clamped(make_client):
    c = make_client(); saved(c)
    prog(c, U1, -5, 2700); assert current(c)["position"] == 0
    prog(c, U1, 99999, 2700); assert current(c)["position"] == 2700

def test_unsaved_movie_with_name_creates_history_row(make_client):
    c = make_client()
    r = prog(c, U1, 12, 2700, name="Phim B", poster_url="/b", episodes=EPS)
    assert r["status"] == "success"
    row = current(c); assert row["saved"] is False and row["position"] == 12

def test_unsaved_movie_without_name_keeps_old_error(make_client):
    assert prog(make_client(), U1, 1, 2700) == {"status": "error", "message": "Movie not in saved list"}
```

Với `position=None` trong tham số hóa: body gửi `"position": null`, hợp lệ và coi như không gửi vị trí (không đổi `position` đã lưu).

- [ ] **Step 2: Chạy test để thấy thất bại**

Run: `.venv/bin/pytest tests/test_progress_api.py -v`
Expected: FAIL (route cũ đọc `saved_movies`, không có `position`).

- [ ] **Step 3: Implement trong `app.py`**

Tách một hàm thuần `_clean_position(value, duration) -> tuple[float | None, float | None] | None` (trả `None` khi vị trí không hợp lệ) để route gọn; kiểm tra bằng `isinstance(value, (int, float))`, loại `bool`, rồi `math.isfinite`. Giữ nguyên logic xóa file sau 3 giờ hiện có, thêm điều kiện `_db_other_devices_hold`.

- [ ] **Step 4: Chạy toàn bộ test**

Run: `.venv/bin/pytest tests/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_progress_api.py
git commit -m "feat(devices): store playback position per device and make progress idempotent"
```

---

### Task 6: Dọn thiết bị không hoạt động

**Files:**
- Modify: `app.py` (hàm `prune_cache`, vòng lặp hằng giờ)
- Test: `tests/test_device_prune.py`

**Interfaces:**
- Consumes: bảng `devices`, `device_movies`.
- Produces: `_db_prune_inactive_devices(now: float, max_age_days: int = 180) -> int` — xóa thiết bị có `last_seen < now - max_age_days*86400` cùng mọi hàng `device_movies` của chúng, trả số thiết bị đã xóa. `prune_cache` gọi hàm này mỗi vòng bằng `asyncio.to_thread`, lỗi chỉ ghi log, không làm dừng vòng dọn cache.

- [ ] **Step 1: Viết test thất bại trong `tests/test_device_prune.py`**

```python
def test_prune_removes_old_devices_and_their_movies_only(app_module):
    m = app_module
    old = m._db_touch_device("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "h", "phone", "ua", 1000.0)["id"]
    new = m._db_touch_device("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "h", "phone", "ua", 1000.0 + 181*86400)["id"]
    mv = {"slug": "s", "name": "S", "episodes": [], "episode_states": {}}
    m._db_upsert_device_movie(old, mv); m._db_upsert_device_movie(new, mv)
    assert m._db_prune_inactive_devices(now=1000.0 + 181*86400 + 1) == 1
    assert m._db_list_device_movies(old) == [] and len(m._db_list_device_movies(new)) == 1
    assert m.db_query_one("SELECT COUNT(*) FROM devices")[0] == 1

def test_prune_keeps_device_exactly_at_limit(app_module):
    m = app_module
    d = m._db_touch_device("cccccccc-cccc-4ccc-8ccc-cccccccccccc", "h", "tv", "ua", 1000.0)["id"]
    assert m._db_prune_inactive_devices(now=1000.0 + 180*86400) == 0
    assert m.db_query_one("SELECT COUNT(*) FROM devices WHERE id=?", (d,))[0] == 1
```

- [ ] **Step 2: Chạy test để thấy thất bại**

Run: `.venv/bin/pytest tests/test_device_prune.py -v`
Expected: FAIL (`AttributeError` thiếu `_db_prune_inactive_devices`).

- [ ] **Step 3: Implement và nối vào `prune_cache`**

Xóa `device_movies` trước, rồi `devices`, trong một khối `_db_lock`.

- [ ] **Step 4: Chạy toàn bộ test**

Run: `.venv/bin/pytest tests/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_device_prune.py
git commit -m "feat(devices): prune devices inactive for 180 days"
```

---

### Task 7: Client gửi và khôi phục vị trí xem

**Files:**
- Modify: `templates/index.html` (hàm `play(url, title, idx)` dòng 3120; nơi gắn sự kiện của `video`)

**Interfaces:**
- Consumes: `POST /api/saved/progress` (Task 5), `GET /api/saved` (`savedMoviesList` đã có trong file, mục có `position`, `duration`, `last_watched_url`).
- Produces: hàm `reportPosition(useBeacon: boolean)` trong `templates/index.html`.

Ghi chú: file này chưa có hạ tầng test JavaScript. Task này kiểm thử thủ công, và chỉ dùng được sau khi Task 1–6 xong.

- [ ] **Step 1: Thêm `reportPosition`**

Gửi `{slug, last_watched_episode, last_watched_url, position: video.currentTime, duration: video.duration}` tới `/api/saved/progress`. Bỏ qua khi `currentMovie` rỗng, `video.duration` không hữu hạn hoặc `video.currentTime` chưa đến 1 giây. Khi `useBeacon` là `true` dùng `navigator.sendBeacon('/api/saved/progress', new Blob([JSON.stringify(body)], {type: 'application/json'}))`, ngược lại dùng `fetch` với `keepalive: true`.

- [ ] **Step 2: Gắn thời điểm gửi**

Gửi mỗi 10 giây khi đang phát (một `setInterval` chỉ chạy lúc `!video.paused`), khi sự kiện `pause`, và `reportPosition(true)` khi `pagehide` và khi `visibilitychange` sang `hidden`.

- [ ] **Step 3: Khôi phục vị trí**

Trong `play(...)`, khi tập sắp phát trùng `last_watched_url` của mục tương ứng trong `savedMoviesList` và `position` thỏa `5 < position < duration - 30` (`duration` lấy từ mục đã lưu), đặt `video.currentTime = position` một lần sau sự kiện `loadedmetadata`. Không khôi phục cho tập khác.

- [ ] **Step 4: Kiểm tra thủ công**

Chạy `.venv/bin/uvicorn app:app --host 127.0.0.1 --port 6969`. Dùng một trình duyệt: lưu một phim, phát tập đang có, xem quá 10 giây rồi tải lại trang và phát lại. Expected: video bắt đầu gần đúng giây đã dừng. Mở cửa sổ ẩn danh (cookie khác), `GET /api/saved` trả danh sách rỗng. Kiểm tra DB: `sqlite3 homeflix.db "select device_id, slug, position_sec from device_movies"` có hai thiết bị khác nhau khi cả hai cùng xem.

- [ ] **Step 5: Commit**

```bash
git add templates/index.html
git commit -m "feat(devices): report and restore playback position from the client"
```

---

## Self-Review

**Độ phủ spec (mục Gói 1):** cookie và phạm vi đường dẫn (Task 2); nhóm nhà, `TRUSTED_PROXIES`, IPv6 /64, IP riêng (Task 1, 2); `devices`, `device_movies` (Task 2, 3); nhập dữ liệu cũ một lần, giữ `saved_movies` (Task 3); `/api/saved*` (Task 4, 5); `GET/PATCH /api/device` (Task 2); `position`/`duration` và gửi mỗi 10 giây, khi tạm dừng, khi rời trang (Task 5, 7); header `private, no-store` và `Vary: Cookie` (Task 2); dọn thiết bị 180 ngày (Task 6). Spec ghi "Cache-Control" cho nhóm route theo thiết bị; plan áp dụng cho mọi `/api/*` thuộc diện cookie.

**Chỗ plan quyết định thay spec:**
- Spec nói `GET /api/saved` trả phim có `saved=1` hoặc có tiến độ; plan giữ nguyên và thêm khóa `saved` để giao diện phân biệt.
- `DELETE /api/saved/{slug}` xóa cả hàng (lưu và tiến độ) của thiết bị đó, giống hành vi cũ.
- Cùng `last_watched_url` thì `/api/saved/progress` chỉ cập nhật vị trí. Spec không nói rõ; hành vi này cần để gửi vị trí mỗi 10 giây không làm hỏng trạng thái đã xem.
- Phim chỉ có tiến độ (chưa lưu) cần client gửi kèm `name`; việc UI dùng khả năng này thuộc Gói 2.

**Nhất quán tên:** `_db_touch_device`, `_db_upsert_device_movie`, `_db_list_device_movies`, `_db_get_device_movie`, `_db_delete_device_movie`, `_db_other_devices_hold`, `_db_all_device_movies`, `_db_prune_inactive_devices` dùng đúng một tên ở mọi task. Wrapper async `get_device_movies` ở Task 3 cho `GET /api/saved` ở Task 4.

**Ngoài plan này:** heartbeat, `GET /api/devices/nearby`, SSE và cast (Gói 3); giao diện iOS 26 (Gói 2); tự qua tập và bỏ qua mở đầu (Gói 4).
