# HomeTV — Thu thập nguồn IPTV — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Admin quản lý nguồn playlist IPTV trong Cài đặt; server tự tải, phân tích, chuẩn hóa và gộp kênh vào sổ kênh `hometv.db`.

**Architecture:** Package `hometv/` (mỗi file một việc, chỉ `api.py` biết FastAPI) với SQLite riêng `hometv.db`. `app.py` chỉ gọi `hometv.init()`, chạy `hometv_refresher()` trong `lifespan` và gắn router admin. Giao diện là file mới `static/settings-hometv.js`, được `static/settings.js` gọi để dựng nhóm "HomeTV".

**Tech Stack:** Python 3.12, FastAPI, httpx (đã có), sqlite3, JS thuần, pytest + Playwright, `node --test`.

**Spec:** `docs/superpowers/specs/2026-10-31-hometv-iptv-ingest-design.md`

## Global Constraints

- Không thêm dependency (`requirements.txt` giữ nguyên). Dùng `httpx`, `sqlite3`, thư viện chuẩn.
- Test không gọi mạng thật: upstream giả bằng `httpx.MockTransport`; DNS giả bằng cách thay `hometv.netguard.resolve`.
- Thời gian là epoch giây (`float`). Mọi hàm cần giờ nhận `now` làm tham số.
- Không bao giờ dùng hay lưu license key DRM; `#KODIPROP` chỉ bật `drm_hint`.
- Route admin dùng `require_admin` của `app.py` (`404` khi admin tắt, `403` khi không phải admin); quy tắc JSON-only `415` đã áp cho `/api/admin`.
- `hometv` hỏng không được làm HomeFlix ngừng: lỗi khởi tạo hay vòng lặp chỉ log.
- Giá trị cố định: chu kỳ mặc định 21600 giây; timeout 30 giây; tối đa 20 MB; tối đa 5 lần chuyển hướng; thử lại 60/300/900 giây + 0–20% ngẫu nhiên; giảm quá 50% số mục là hỏng; giữ 5 `source_versions` mỗi nguồn; `jobs` giữ 30 ngày; khóa chết sau 600 giây; 2 lượt tải song song, 1 lượt mỗi host; vòng lặp 60 giây; danh sách nguồn tự tải lại mỗi 3 giây khi có nguồn đang tải.
- Giới hạn đầu vào: tên nguồn 1–60 ký tự; URL `http(s)`, tối đa 2048 ký tự; `priority` 1–100; `refresh_hours` 1–168; `rights_status` ∈ {`pending_review`, `approved`, `rejected`}.
- Chữ giao diện (copy chính xác): "Chờ duyệt", "Đã duyệt", "Từ chối", "Đã tắt", "Đang tải…", "{n} kênh", "Lỗi: {lỗi} (giữ bản cũ)", "Duyệt", "Bỏ duyệt", "Bật", "Tắt", "Làm mới ngay", "Sửa", "Xoá", "+ Thêm nguồn", "Thêm", xác nhận xoá `Xoá nguồn {tên}? Các kênh chỉ có ở nguồn này sẽ mất.`
- Thông báo lỗi tải (copy chính xác): "Hết thời gian chờ", "Không kết nối được", "HTTP {mã}", "Quá {n} MB", "Quá nhiều lần chuyển hướng", "Playlist hỏng", "Mất quá nửa số kênh", "Địa chỉ bị chặn", "Lỗi nhập dữ liệu".
- Chạy test: `source .venv/bin/activate && python -m pytest -q <path>`; JS: `node --test tests/js/*.mjs`.
- Làm trên nhánh `feat/hometv-ingest`. Commit kết thúc bằng `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- Cùng một URL stream xuất hiện ở hai nguồn: giữ hai dòng `streams` (duy nhất theo `source_id` + `url_hash`), không lỗi. Test ở Task 5.
- Một playlist liệt kê cùng URL hai lần: chỉ một dòng `streams`, không `IntegrityError`. Test ở Task 5.
- Admin đổi URL nguồn trong lúc lượt tải cũ đang chạy: kết quả của URL cũ không được ghi vào nguồn. Test ở Task 8.
- Admin xoá nguồn trong lúc nó đang được tải: lượt tải kết thúc êm, không tạo lại dòng nào cho nguồn đã xoá. Test ở Task 8.
- Tên kênh chỉ gồm hậu tố chất lượng (ví dụ `HD`) chuẩn hóa thành rỗng: không được gộp các kênh tên rỗng với nhau. Test ở Task 4 và Task 5.

---

### Task 1: `hometv/db.py` — mở DB, schema, transaction

**Files:**
- Create: `hometv/__init__.py`, `hometv/db.py`
- Modify: `app.py` (`lifespan`: gọi `hometv.init` trong `try/except` có log), `tests/conftest.py` (`app_module` gọi `hometv.init(str(tmp_path / "hometv.db"))` và `hometv.db.close()` sau test; thêm fixture `db` = `hometv.db.open(str(tmp_path / "h.db"))` — chỉ schema, không thêm nguồn mặc định — rồi `close()`)
- Test: `tests/hometv/__init__.py`, `tests/hometv/test_hometv_db.py`

**Interfaces:**
- Produces:
  - `hometv.init(path: str) -> None` — mở DB, tạo schema; Task 6 thêm bước thêm nguồn mặc định vào đây.
  - `hometv.db.open(path: str) -> None`, `hometv.db.close() -> None`, `hometv.db.conn() -> sqlite3.Connection` (`RuntimeError` nếu chưa mở), `hometv.db.lock: threading.Lock`
  - `hometv.db.tx()` — context manager: giữ `lock`, `BEGIN IMMEDIATE`, commit khi thoát êm, rollback khi có ngoại lệ (ném lại), trả `Connection`.
  - `hometv.db.SCHEMA_VERSION = 1`; bảng và cột đúng mục "Dữ liệu" của spec; `conn.row_factory = sqlite3.Row`.
  - Index: `channels(tvg_id)`, `channels(normalized_name)`, `UNIQUE streams(source_id, url_hash)`, `streams(channel_id)`, `jobs(source_id, status)`, `source_versions(source_id, fetched_at)`.
  - FK: `streams.source_id`, `source_versions.source_id` → `sources(id) ON DELETE CASCADE`; `streams.channel_id` → `channels(id) ON DELETE CASCADE`.

- [ ] **Step 1: Viết test (thất bại)**

```python
def test_open_creates_schema_wal_fk(tmp_path):
    hometv.init(str(tmp_path / "h.db")); c = hometv.db.conn()
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert c.execute("SELECT version FROM schema_version").fetchone()[0] == 1
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"sources", "source_versions", "channels", "streams", "jobs", "schema_version"} <= tables
def test_init_twice_is_idempotent(tmp_path): ...  # init 2 lần, schema_version vẫn 1 dòng
def test_tx_rolls_back_on_error(tmp_path): ...    # insert trong tx rồi raise → không còn dòng
def test_delete_source_cascades(tmp_path): ...     # xoá sources → streams, source_versions của nó mất
def test_conn_before_open_raises(): ...            # sau close(): RuntimeError
def test_app_survives_hometv_init_failure(app_module, monkeypatch): ...  # monkeypatch hometv.init ném lỗi; chạy lifespan bằng `with TestClient(app_module.app)` → GET / trả 200
```

- [ ] **Step 2: Chạy, xác nhận thất bại** — `python -m pytest -q tests/hometv/test_hometv_db.py`.
- [ ] **Step 3: Viết code** theo Interfaces. Đường dẫn ở app: `os.path.join(DATA_DIR, "hometv.db")`.
- [ ] **Step 4: Chạy lại** — PASS; chạy cả bộ `python -m pytest -q` một lần.
- [ ] **Step 5: Commit** — `feat(hometv): sqlite store with schema and transactions`

---

### Task 2: `hometv/netguard.py` — chặn SSRF

**Files:**
- Create: `hometv/netguard.py`
- Modify: `tests/conftest.py` (fixture `public_dns`: `monkeypatch.setattr(hometv.netguard, "resolve", lambda host: ["185.199.108.153"])` — Task 6–10 dùng)
- Test: `tests/hometv/test_hometv_netguard.py`

**Interfaces:**
- Produces:
  - `class BlockedURL(ValueError)` có `.reason: str` (`"URL không hợp lệ"` hoặc `"Địa chỉ bị chặn"`)
  - `check_url(url: str) -> str` — trả host; ném `BlockedURL("URL không hợp lệ")` khi scheme không phải `http`/`https`, không có host, hoặc dài quá 2048.
  - `resolve(host: str) -> list[str]` — `socket.getaddrinfo`; test thay hàm này.
  - `is_blocked_ip(ip: str) -> bool` — `ipaddress`: private, loopback, link-local, multicast, reserved, unspecified, hoặc thuộc `100.64.0.0/10`; IPv4-mapped IPv6 xét theo IPv4 bên trong.
  - `check_host(host: str) -> None` — host là IP literal thì xét trực tiếp; không thì `resolve`; ném `BlockedURL("Địa chỉ bị chặn")` nếu không phân giải được hoặc **bất kỳ** địa chỉ nào bị chặn.
  - `check(url: str) -> None` = `check_host(check_url(url))`.

- [ ] **Step 1: Viết test (thất bại)**

```python
@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "172.20.0.2", "192.168.1.5", "169.254.1.1",
                                "100.64.0.1", "0.0.0.0", "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "224.0.0.1"])
def test_blocked_ips(ip): assert netguard.is_blocked_ip(ip)
@pytest.mark.parametrize("ip", ["8.8.8.8", "185.199.108.153", "2606:4700::1"])
def test_public_ips(ip): assert not netguard.is_blocked_ip(ip)
@pytest.mark.parametrize("url", ["ftp://x.com/a.m3u", "file:///etc/passwd", "http://", "javascript:alert(1)", "http://x/" + "a" * 2050])
def test_bad_urls(url):
    with pytest.raises(netguard.BlockedURL) as e: netguard.check_url(url)
    assert e.value.reason == "URL không hợp lệ"
def test_host_resolving_to_any_private_ip_is_blocked(monkeypatch):
    monkeypatch.setattr(netguard, "resolve", lambda h: ["8.8.8.8", "10.0.0.5"])
    with pytest.raises(netguard.BlockedURL) as e: netguard.check("https://evil.example/x.m3u")
    assert e.value.reason == "Địa chỉ bị chặn"
def test_public_host_passes(monkeypatch): ...   # resolve → ["185.199.108.153"]; check() không ném
def test_unresolvable_host_blocked(monkeypatch): ...  # resolve ném OSError → BlockedURL("Địa chỉ bị chặn")
def test_ip_literal_not_resolved(monkeypatch): ...    # resolve bị thay bằng hàm ném; check("http://127.0.0.1/") vẫn chặn đúng
```

- [ ] **Step 2: Chạy, xác nhận thất bại.**
- [ ] **Step 3: Viết `hometv/netguard.py`.**
- [ ] **Step 4: Chạy lại** — PASS.
- [ ] **Step 5: Commit** — `feat(hometv): SSRF guard for playlist URLs`

---

### Task 3: `hometv/m3u.py` — phân tích Extended M3U

**Files:**
- Create: `hometv/m3u.py`, `tests/fixtures/hometv/sample.m3u` (nội dung ở Step 1)
- Test: `tests/hometv/test_hometv_m3u.py`

**Interfaces:**
- Produces:
  - `@dataclass Entry: name: str, url: str, tvg_id: str, tvg_name: str, tvg_logo: str, group_title: str, http_headers: dict[str, str], drm_hint: bool` (`http_headers` khoá `user_agent`, `referrer`)
  - `@dataclass Playlist: entries: list[Entry], skipped_lines: int, has_header: bool`
  - `decode(body: bytes) -> str` — bỏ BOM, UTF-8 `errors="replace"`
  - `parse(text: str) -> Playlist` — theo mục "Phân tích M3U" của spec. Giới hạn: `name`, `tvg_name`, `group_title` cắt ở 200 ký tự; mục có URL dài quá 2048 ký tự hoặc không phải `http(s)` thì bỏ và tính vào `skipped_lines`; `tvg_logo` dài quá 2048 thì để rỗng.

- [ ] **Step 1: Viết fixture và test (thất bại)**

`tests/fixtures/hometv/sample.m3u` (lấy từ dữ liệu thật, giữ đúng byte, CRLF ở dòng thứ hai trở đi không bắt buộc):

```
#EXTM3U x-tvg-url="https://example.org/epg.xml"
#-------------------VTV-------------------
#EXTINF:-1 tvg-id="vtv1hd" group-title="🇻🇳| VTV" tvg-logo="https://i.imgur.com/nfkmvAY.png",VTV1 HD
#EXTVLCOPT:http-user-agent=ReactNativeVideo/3.4.4 (Linux;Android 9) ExoPlayerLib/2.13.3
https://e2.endpoint.cdn.sctvonline.vn/hls/vtv1/index.m3u8
#EXTINF:-1 tvg-id="VTV1.vn" tvg-logo="https://i.imgur.com/x.png" group-title="General",VTV 1 (1080p)
https://example.vn/vtv1/index.m3u8
#EXTINF:-1 tvg-id='no_epg' group-title=Undefined,Kênh, có dấu phẩy
#EXTVLCOPT:http-referrer=https://ref.example/
http://plain.example/live.m3u8
#EXTINF:-1 tvg-id="kplus1",K+ SPORT 1
#KODIPROP:inputstream.adaptive.license_type=clearkey
#KODIPROP:inputstream.adaptive.license_key=abc:def
https://drm.example/kplus1.mpd
#EXTINF:broken line without url
#EXTINF:-1,Ftp sai
ftp://bad.example/x
```

```python
def test_parses_entries_and_headers():
    p = m3u.parse(open(FIX, encoding="utf-8").read())
    assert p.has_header and [e.name for e in p.entries] == ["VTV1 HD", "VTV 1 (1080p)", "Kênh, có dấu phẩy", "K+ SPORT 1"]
    e = p.entries[0]
    assert e.tvg_id == "vtv1hd" and e.group_title == "🇻🇳| VTV" and e.tvg_logo.endswith("nfkmvAY.png")
    assert e.http_headers == {"user_agent": "ReactNativeVideo/3.4.4 (Linux;Android 9) ExoPlayerLib/2.13.3"}
    assert p.entries[2].tvg_id == "no_epg" and p.entries[2].group_title == "Undefined"
    assert p.entries[2].http_headers == {"referrer": "https://ref.example/"}
    assert p.entries[3].drm_hint is True and "abc" not in repr(p.entries[3])
    assert p.skipped_lines == 2
def test_decode_strips_bom_and_handles_crlf():
    p = m3u.parse(m3u.decode(b"\xef\xbb\xbf#EXTM3U\r\n#EXTINF:-1,A\r\nhttps://a.example/x.m3u8\r\n"))
    assert p.has_header and p.entries[0].url == "https://a.example/x.m3u8"
def test_no_header(): assert m3u.parse("#EXTINF:-1,A\nhttps://a/x\n").has_header is False
def test_invalid_utf8_does_not_raise(): assert m3u.parse(m3u.decode(b"#EXTM3U\n#EXTINF:-1,\xff\xfe\nhttps://a/x\n")).entries
def test_long_fields_capped():
    p = m3u.parse(f'#EXTM3U\n#EXTINF:-1 tvg-logo="https://l/{"a"*3000}",{"N"*500}\nhttps://a/x\n#EXTINF:-1,B\nhttps://a/{"u"*3000}\n')
    assert len(p.entries) == 1 and len(p.entries[0].name) == 200 and p.entries[0].tvg_logo == "" and p.skipped_lines == 1
```

- [ ] **Step 2: Chạy, xác nhận thất bại.**
- [ ] **Step 3: Viết `hometv/m3u.py`** — thuộc tính đọc bằng regex `([\w-]+)=("[^"]*"|'[^']*'|[^\s,]+)` trên phần trước dấu phẩy cuối cùng *nằm ngoài nháy*; tên là phần sau dấu phẩy đó.
- [ ] **Step 4: Chạy lại** — PASS.
- [ ] **Step 5: Commit** — `feat(hometv): extended M3U parser`

---

### Task 4: `hometv/normalize.py` — chuẩn hóa tên, đài, nhóm, chất lượng

**Files:**
- Create: `hometv/normalize.py`
- Test: `tests/hometv/test_hometv_normalize.py`

**Interfaces:**
- Produces:
  - `CATEGORIES = ("General", "National TV", "Local TV", "News", "Entertainment", "Movies", "Sports", "Kids", "Music", "International", "Unknown")`
  - `NATIONAL_BROADCASTERS`, `PROVINCES` (63 tỉnh/thành, không dấu, viết thường), `COUNTRIES` — đúng như spec.
  - `fold(text: str) -> str` — NFKC, viết thường, bỏ dấu (`đ`→`d`), gộp khoảng trắng.
  - `normalize_name(name: str) -> str` — các bước của spec; có thể trả `""`.
  - `quality_hint(name: str) -> str` — hậu tố đầu tiên tìm thấy, dạng gốc in hoa cho chữ (`"HD"`, `"FHD"`, `"4K"`) và giữ nguyên cho số (`"1080p"`); không có thì `""`.
  - `clean_tvg_id(tvg_id: str) -> str` — viết thường, bỏ khoảng trắng; id giả (`""`, `no_epg`, `none`, `null`, `undefined`) thành `""`.
  - `strip_group(group_title: str) -> str` — bỏ emoji, ký tự ký hiệu và tiền tố dạng `xx| `.
  - `broadcaster(normalized: str, group_title: str) -> str` — theo thứ tự của spec; `""` nếu không rõ.
  - `category(group_title: str, broadcaster: str) -> str` — theo bảng từ khóa của spec, so trên `fold(strip_group(group_title))`; luôn thuộc `CATEGORIES`.

- [ ] **Step 1: Viết test (thất bại)**

```python
@pytest.mark.parametrize("raw,norm", [("VTV1 HD", "vtv1"), ("VTV1 FHD", "vtv1"), ("VTV 1", "vtv1"),
    ("HTV1 (720p)", "htv1"), ("HTV Thể Thao HD", "htv the thao"), ("THVL1 (1080i)", "thvl1"),
    ("Đồng Nai 1", "dong nai1"), ("VTV3 (Backup)", "vtv3"), ("HD", ""), ("  ", "")])
def test_normalize_name(raw, norm): assert normalize.normalize_name(raw) == norm
def test_quality_hint():
    assert normalize.quality_hint("VTV1 HD") == "HD" and normalize.quality_hint("HTV1 (720p)") == "720p"
    assert normalize.quality_hint("ONE Muz") == ""
@pytest.mark.parametrize("raw,clean", [("VTV1.vn", "vtv1.vn"), (" vtv1hd ", "vtv1hd"), ("no_epg", ""), ("NULL", ""), ("", "")])
def test_clean_tvg_id(raw, clean): assert normalize.clean_tvg_id(raw) == clean
def test_strip_group(): assert normalize.strip_group("🇻🇳| Địa phương") == "Địa phương" and normalize.strip_group("⚽| Thể thao") == "Thể thao"
@pytest.mark.parametrize("name,group,b", [("vtv1", "", "vtv"), ("htv the thao", "", "htv"), ("dong nai1", "", "dong nai"),
    ("kenh x", "🇻🇳| SCTV", "sctv"), ("one muz", "General", "")])
def test_broadcaster(name, group, b): assert normalize.broadcaster(name, group) == b
@pytest.mark.parametrize("group,b,cat", [("🇻🇳| Địa phương", "", "Local TV"), ("", "dong nai", "Local TV"),
    ("🇻🇳| VTV", "vtv", "National TV"), ("📰| Tin tức", "", "News"), ("⚽| Thể thao", "", "Sports"),
    ("🌈| Hoạt hình", "", "Kids"), ("🎬| Phim truyện", "", "Movies"), ("📻| Radio", "", "Music"),
    ("🌿| Khám phá", "", "Entertainment"), ("🇷🇺| Nga và Cộng đồng các Quốc gia Độc lập", "", "International"),
    ("General", "", "General"), ("Undefined", "", "Unknown")])
def test_category(group, b, cat): assert normalize.category(group, b) == cat
def test_provinces_count(): assert len(normalize.PROVINCES) == 63
```

- [ ] **Step 2: Chạy, xác nhận thất bại.**
- [ ] **Step 3: Viết `hometv/normalize.py`.**
- [ ] **Step 4: Chạy lại** — PASS.
- [ ] **Step 5: Commit** — `feat(hometv): channel name normalization, broadcaster and category`

---

### Task 5: `hometv/registry.py` — nhập một bản playlist vào sổ kênh

**Files:**
- Create: `hometv/registry.py`
- Test: `tests/hometv/test_hometv_registry.py`

**Interfaces:**
- Consumes: `hometv.db.tx()`, `m3u.Entry`, `m3u.Playlist`, `normalize.*`
- Produces:
  - `@dataclass IngestStats: channels: int, streams: int, new_streams: int, deactivated: int` (`channels`, `streams` = số đang active của nguồn sau lượt nhập)
  - `ingest(source_id: str, playlist: Playlist, now: float) -> IngestStats` — một `db.tx()`; ném lại lỗi sau khi rollback. Nếu nguồn không còn trong `sources` thì không ghi gì, trả `IngestStats(0, 0, 0, 0)`.
  - `prune_orphan_channels(conn) -> int` — xoá kênh không còn stream nào; trả số kênh đã xoá.
- Quy tắc gộp, cập nhật `display_name`/`logo_url` theo nguồn ưu tiên cao nhất, giữ `category_source='manual'`: đúng mục "Chuẩn hóa và gộp kênh" của spec. Kênh nhận `normalized_name=""` không bao giờ khớp theo tên (chỉ theo `tvg-id`, hoặc với kênh cùng `display_name` gốc do chính nguồn đó tạo).

- [ ] **Step 1: Viết test (thất bại)** — helper `E(name, url, tvg_id="", group="")` tạo `Entry`; `seed_source(id, priority)` chèn thẳng một dòng `sources`.

```python
def test_merge_by_tvg_id_across_sources(db):
    seed_source("a", 10); seed_source("b", 20)
    registry.ingest("a", PL(E("VTV1 HD", "https://a/1", "vtv1hd")), 1.0)
    registry.ingest("b", PL(E("Kênh Một", "https://b/1", "VTV1HD")), 2.0)
    assert count("channels") == 1 and count("streams") == 2
def test_merge_by_name_and_broadcaster(db): ...      # "VTV1 HD" (a) + "VTV 1" (b), không tvg-id → 1 kênh
def test_no_merge_without_broadcaster(db): ...       # "ONE Muz" ở a và b → 2 kênh
def test_placeholder_tvg_id_ignored(db): ...         # cùng tvg-id "no_epg", tên khác nhau, không rõ đài → 2 kênh
def test_empty_normalized_names_never_merge(db): ... # "HD" (a) và "SD" (b) → 2 kênh
def test_same_source_reuses_its_channel(db): ...     # nhập "ONE Muz" của a hai lần → vẫn 1 kênh, 1 stream
def test_same_url_in_two_sources_kept_twice(db): ... # cùng URL ở a và b → 2 dòng streams
def test_duplicate_url_in_one_playlist(db): ...      # playlist a có cùng URL 2 lần → 1 stream, không lỗi
def test_missing_stream_deactivated_not_deleted(db):
    seed_source("a", 10)
    registry.ingest("a", PL(E("VTV1", "https://a/1"), E("VTV2", "https://a/2")), 1.0)
    s = registry.ingest("a", PL(E("VTV1", "https://a/1")), 2.0)
    assert s.deactivated == 1 and count("streams") == 2 and count("streams WHERE active=1") == 1
def test_display_name_from_highest_priority_source(db): ...  # a(10) "VTV1 HD", b(5) "VTV 1" → display_name "VTV 1"
def test_manual_category_not_overwritten(db): ...    # đặt category='Kids', category_source='manual' rồi nhập lại → giữ Kids
def test_headers_quality_drm_stored(db): ...         # http_headers JSON, quality_hint "HD", drm_hint 1, protocol http/https
def test_error_rolls_back_whole_ingest(db, monkeypatch): ...  # normalize.category ném ở mục thứ 2 → không còn dòng nào của lượt đó
def test_ingest_for_deleted_source_writes_nothing(db): ...    # source không tồn tại → IngestStats(0,0,0,0), bảng trống
def test_prune_orphan_channels(db): ...              # xoá nguồn → prune_orphan_channels xoá kênh mồ côi
```

- [ ] **Step 2: Chạy, xác nhận thất bại.**
- [ ] **Step 3: Viết `hometv/registry.py`.** `url_hash` = SHA-256 hex của URL; `INSERT ... ON CONFLICT(source_id, url_hash) DO UPDATE` để giữ `id` stream ổn định.
- [ ] **Step 4: Chạy lại** — PASS.
- [ ] **Step 5: Commit** — `feat(hometv): channel registry ingest with merge rules`

---

### Task 6: `hometv/sources.py` — nguồn, kiểm tra đầu vào, quy tắc giữ bản tốt, lịch thử lại

**Files:**
- Create: `hometv/sources.py`
- Modify: `hometv/__init__.py` (`init` gọi `sources.seed_defaults(time.time())` sau khi mở DB)
- Test: `tests/hometv/test_hometv_sources.py`

**Interfaces:**
- Consumes: `hometv.db`, `netguard.check`, `netguard.BlockedURL`, `registry.prune_orphan_channels`, `m3u.Playlist`
- Produces:
  - `DEFAULT_SOURCES` — 5 dòng đúng bảng "Nguồn mặc định" của spec; `DEFAULT_REFRESH = 21600`; `FETCH_STALE = 600`; `RETRY_DELAYS = (60, 300, 900)`; `JITTER = 0.2`; `MAX_DROP = 0.5`; `KEEP_VERSIONS = 5`
  - `class ValidationError(ValueError)` có `.errors: dict[str, str]`
  - `seed_defaults(now: float) -> int` — chỉ khi bảng `sources` trống
  - `slugify(name: str) -> str` — không dấu, chữ thường, `-`; trùng thì thêm `-2`, `-3`…
  - `list_sources(now: float) -> list[dict]` — đúng shape API của spec (`refresh_hours` = `refresh_interval // 3600`, `fetching` = có job `running` chưa quá `FETCH_STALE` giây), sắp theo `priority`, `name`
  - `get(source_id: str) -> dict | None`
  - `create(name: str, url: str, now: float) -> dict` — `pending_review`, `enabled=1`, `next_fetch_at=now`
  - `update(source_id: str, changes: dict, now: float) -> dict` — `KeyError` khi id lạ; đổi URL thì `content_hash=None`, `next_fetch_at=now`, `retry_count=0`, `last_error=None`
  - `delete(source_id: str) -> bool` — xoá (cascade) rồi `prune_orphan_channels`
  - `request_refresh(source_id: str, now: float) -> bool`
  - `due(now: float) -> list[dict]` — đang bật, `next_fetch_at <= now`, sắp theo `priority`
  - `check_version(prev_count: int | None, playlist: Playlist) -> str | None` — `"Playlist hỏng"` | `"Mất quá nửa số kênh"` | `None`
  - `next_retry_delay(retry_count: int, rand: float) -> float | None` — `RETRY_DELAYS[retry_count] * (1 + JITTER * rand)`, `None` khi `retry_count >= 3`
  - `record_success(source_id, *, now, content_hash, body: bytes, stats, entry_count) -> None` — lưu `source_versions` (gzip) khi hash đổi, cắt còn 5 bản; cập nhật `last_success_at`, `last_fetch_at`, `channel_count`, `stream_count`, `retry_count=0`, `last_error=None`, `next_fetch_at = now + refresh_interval`
  - `record_unchanged(source_id, *, now) -> None`
  - `record_failure(source_id, *, now, message: str, retryable: bool, rand: float) -> None` — `last_error=message`; `retryable` thì theo `next_retry_delay`, hết lượt thì `retry_count=0` và `now + refresh_interval`; không `retryable` (URL bị chặn) thì `next_fetch_at = None` (chỉ chạy lại khi URL đổi hoặc bấm làm mới)
  - `last_good_entry_count(source_id) -> int | None`

- [ ] **Step 1: Viết test (thất bại)**

```python
def test_seed_defaults_once(db):
    assert sources.seed_defaults(1.0) == 5 and sources.seed_defaults(2.0) == 0
    sources.delete("doubo-vn"); assert sources.seed_defaults(3.0) == 0
    s = sources.get("iptv-org-vn")
    assert s["rights_status"] == "pending_review" and s["enabled"] and s["priority"] == 20
def test_create_validates(db, public_dns):
    with pytest.raises(sources.ValidationError) as e: sources.create("", "ftp://x", 1.0)
    assert set(e.value.errors) == {"name", "url"}
def test_create_blocks_private_url(db, monkeypatch): ...    # resolve → ["10.0.0.1"] → errors["url"] == "Địa chỉ bị chặn"
@pytest.mark.parametrize("changes,field", [({"priority": 0}, "priority"), ({"priority": 101}, "priority"),
    ({"refresh_hours": 0}, "refresh_hours"), ({"refresh_hours": 169}, "refresh_hours"),
    ({"rights_status": "ok"}, "rights_status"), ({"enabled": "yes"}, "enabled"), ({"name": "x" * 61}, "name")])
def test_update_validates(db, public_dns, changes, field): ...
def test_update_url_resets_fetch_state(db, public_dns): ...
def test_update_unknown_raises_keyerror(db): ...
def test_slug_unique(db, public_dns): ...                  # tạo "IPTV Org Vietnam" lần hai → "iptv-org-vietnam-2"
def test_check_version():
    assert sources.check_version(None, Playlist([], 0, False)) == "Playlist hỏng"
    assert sources.check_version(None, Playlist([], 0, True)) == "Playlist hỏng"
    assert sources.check_version(100, Playlist([ENTRY] * 49, 0, True)) == "Mất quá nửa số kênh"
    assert sources.check_version(100, Playlist([ENTRY] * 50, 0, True)) is None
def test_next_retry_delay():
    assert sources.next_retry_delay(0, 0.0) == 60 and sources.next_retry_delay(1, 1.0) == 360
    assert sources.next_retry_delay(2, 0.5) == 990 and sources.next_retry_delay(3, 0.0) is None
def test_record_failure_schedule(db): ...                  # 3 lần thất bại → 60/300/900 (+jitter), lần 4 → now + refresh_interval
def test_blocked_failure_stops_retrying(db): ...           # retryable=False → next_fetch_at None, không có trong due()
def test_versions_trimmed_to_five(db): ...
def test_due_skips_disabled(db): ...
def test_list_sources_fetching_flag(db): ...               # job running 100 giây trước → True; 700 giây trước → False
```

- [ ] **Step 2: Chạy, xác nhận thất bại.**
- [ ] **Step 3: Viết `hometv/sources.py`**, sửa `hometv.init`.
- [ ] **Step 4: Chạy lại** — PASS (`tests/hometv`).
- [ ] **Step 5: Commit** — `feat(hometv): sources CRUD, defaults, keep-last-good and retry rules`

---

### Task 7: `hometv/fetcher.py` — tải playlist an toàn

**Files:**
- Create: `hometv/fetcher.py`
- Test: `tests/hometv/test_hometv_fetcher.py`

**Interfaces:**
- Consumes: `netguard.check_url`, `netguard.check_host`, `netguard.BlockedURL`
- Produces:
  - `class FetchError(Exception)` có `.message: str` (copy ở Global Constraints), `.retryable: bool` (`False` chỉ với "Địa chỉ bị chặn")
  - `async def fetch_playlist(client: httpx.AsyncClient, url: str, *, timeout: float, max_bytes: int, max_redirects: int = 5) -> bytes` — `client` phải tạo với `follow_redirects=False`; tự đi theo `Location` (URL tương đối ghép với URL hiện tại); mỗi bước gọi `check_url` + `check_host` (chạy `check_host` bằng `asyncio.to_thread`); đọc stream, dừng khi vượt `max_bytes`; `timeout` là tổng thời gian cả lượt.
  - `def make_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient` — `follow_redirects=False`, `headers={"user-agent": "HomeTV/1.0"}`.

- [ ] **Step 1: Viết test (thất bại)** — upstream giả bằng `httpx.MockTransport`; fixture `public_dns` (tests/conftest.py). Repo không có `pytest-asyncio`: test là hàm thường, chạy coroutine bằng `asyncio.run` (như tests/test_cast_hub.py); `fetch(...)` dưới đây là helper của file test gọi `asyncio.run(fetcher.fetch_playlist(...))`.

```python
def test_ok_body(public_dns): assert fetch(handler=lambda r: httpx.Response(200, content=b"#EXTM3U\n")) == b"#EXTM3U\n"
def test_http_error_message(public_dns): ...      # 404 → FetchError.message == "HTTP 404", retryable True
def test_too_large(public_dns): ...               # body 3 MB, max_bytes 2 MB → "Quá 2 MB"
def test_timeout(public_dns): ...                  # handler ném httpx.ReadTimeout → "Hết thời gian chờ"
def test_connect_error(public_dns): ...            # httpx.ConnectError → "Không kết nối được"
def test_redirect_followed_and_checked(monkeypatch): ...  # 302 tới host khác; resolve host đó → "10.0.0.9" → "Địa chỉ bị chặn", retryable False
def test_relative_redirect(public_dns): ...        # Location: /b.m3u → tải được
def test_too_many_redirects(public_dns): ...       # 6 lần 302 → "Quá nhiều lần chuyển hướng"
def test_blocked_initial_url(monkeypatch): ...     # resolve → ["127.0.0.1"] → không gửi request nào, "Địa chỉ bị chặn"
```

- [ ] **Step 2: Chạy, xác nhận thất bại.**
- [ ] **Step 3: Viết `hometv/fetcher.py`.**
- [ ] **Step 4: Chạy lại** — PASS.
- [ ] **Step 5: Commit** — `feat(hometv): bounded playlist fetcher with per-hop SSRF checks`

---

### Task 8: `hometv/scheduler.py` — lượt tải, khóa, vòng lặp, dọn dẹp

**Files:**
- Create: `hometv/scheduler.py`
- Modify: `app.py` (`lifespan`: `_bg_tasks.append(asyncio.create_task(hometv.scheduler.hometv_refresher()))` chỉ khi `hometv.init` thành công)
- Test: `tests/hometv/test_hometv_scheduler.py`

**Interfaces:**
- Consumes: `fetcher.fetch_playlist`, `fetcher.make_client`, `fetcher.FetchError`, `m3u.decode`, `m3u.parse`, `sources.*`, `registry.ingest`, `hometv.db`
- Produces:
  - `LOCK_STALE = sources.FETCH_STALE`, `MAX_PARALLEL = 2`, `TICK = 60`
  - `acquire_job(source_id: str, now: float) -> int | None` — trong `db.tx()`: có job `running` của nguồn đó trẻ hơn 600 giây thì `None`; job cũ hơn thì đánh `error` ("Lượt trước bị gián đoạn") rồi tạo job mới
  - `finish_job(job_id: int, *, status: str, now: float, records: int = 0, skipped: int = 0, error: str | None = None) -> None`
  - `async def refresh_source(client, source_id: str, *, now: float, rand: float) -> str` — trả `"ok" | "unchanged" | "error" | "skipped" | "gone"`. Trình tự: `acquire_job` → đọc nguồn và **ghi nhớ URL** → `fetch_playlist` → `decode`/`parse` → hash trùng `content_hash` thì `record_unchanged` → `check_version` → `registry.ingest` → `record_success`. **Trước khi ghi kết quả**, đọc lại nguồn: đã bị xoá thì `"gone"` (không ghi gì); URL đã đổi thì bỏ kết quả, `"skipped"`. `FetchError`/lỗi nhập → `record_failure` (lỗi nhập dùng "Lỗi nhập dữ liệu", `retryable=True`) và `"error"`. Luôn `finish_job`.
  - `async def run_due(client, now: float) -> list[str]` — lấy `sources.due(now)`, chạy tối đa 2 lượt song song, không bao giờ 2 lượt cùng host
  - `cleanup(now: float) -> None` — xoá `jobs` cũ hơn 30 ngày; cắt `source_versions` còn 5 bản mỗi nguồn
  - `async def hometv_refresher(tick: float = TICK) -> None` — vòng lặp: `run_due`, mỗi 24 giờ `cleanup`; mọi ngoại lệ chỉ log rồi chạy tiếp; đóng client khi bị cancel

- [ ] **Step 1: Viết test (thất bại)** — client giả: `fetcher.make_client(httpx.MockTransport(handler))`; `public_dns`; nguồn tạo bằng `sources.create`.

```python
def test_refresh_ok_ingests_and_records(db, public_dns):
    src = sources.create("A", "https://a.example/a.m3u", 1.0)
    assert run(scheduler.refresh_source(client(body=SAMPLE), src["id"], now=2.0, rand=0.0)) == "ok"
    s = sources.get(src["id"]); assert s["channel_count"] > 0 and s["last_error"] is None and s["next_fetch_at"] == 2.0 + 21600
def test_same_hash_is_unchanged(db, public_dns): ...
def test_broken_playlist_keeps_last_good(db, public_dns): ...      # lần 2 trả "<html>" → "Playlist hỏng", kênh cũ vẫn active
def test_drop_over_half_keeps_last_good(db, public_dns): ...
def test_fetch_error_schedules_retry(db, public_dns): ...          # 500 → last_error "HTTP 500", next_fetch_at = now + 60
def test_lock_prevents_parallel_runs(db): ...                       # acquire_job 2 lần liền → lần 2 None
def test_stale_lock_reclaimed(db): ...                              # job running từ 601 giây trước → acquire được
def test_url_changed_mid_fetch_discards_result(db, public_dns): ... # handler gọi sources.update(url mới) trước khi trả body → "skipped", không có stream
def test_deleted_mid_fetch_writes_nothing(db, public_dns): ...      # handler gọi sources.delete → "gone", bảng streams/channels trống, không ngoại lệ
def test_ingest_error_rolls_back_and_records(db, public_dns, monkeypatch): ...  # registry.ingest ném → "error", last_error "Lỗi nhập dữ liệu"
def test_run_due_parallel_limits(db, public_dns): ...               # 4 nguồn, 2 cùng host; handler đo số lượt đồng thời: tổng ≤ 2, cùng host ≤ 1
def test_cleanup(db): ...
def test_refresher_survives_errors(db, monkeypatch): ...            # run_due ném lần đầu; vòng sau vẫn chạy (tick nhỏ, cancel sau 2 vòng)
```

- [ ] **Step 2: Chạy, xác nhận thất bại.**
- [ ] **Step 3: Viết `hometv/scheduler.py`**, nối vào `lifespan`.
- [ ] **Step 4: Chạy lại** — `tests/hometv` PASS; cả bộ `python -m pytest -q` PASS.
- [ ] **Step 5: Commit** — `feat(hometv): refresh scheduler with job locks and retries`

---

### Task 9: `hometv/api.py` — route admin

**Files:**
- Create: `hometv/api.py`
- Modify: `app.py` (sau khi định nghĩa `require_admin`: `app.include_router(hometv.api.make_router(require_admin))`)
- Test: `tests/hometv/test_hometv_api.py`

**Interfaces:**
- Consumes: `sources.*`, `sources.ValidationError`
- Produces: `make_router(require_admin: Callable) -> APIRouter` với 5 route đúng bảng API của spec. Lỗi `ValidationError` → `422 {"errors": {...}}`; `KeyError` → `404`; `POST` tạo → `201`; refresh → `202 {"queued": true}`; delete → `204`. Route chạy hàm DB bằng `asyncio.to_thread`.

- [ ] **Step 1: Viết test (thất bại)** — dùng `admin_client`, `make_client`, `pw` (tests/conftest.py), `post` (tests/admin_helpers.py), `public_dns`.

```python
def test_disabled_404(make_client, monkeypatch):
    monkeypatch.delenv("HOMEFLIX_ADMIN_PASSWORD", raising=False)
    assert make_client().get("/api/admin/hometv/sources").status_code == 404
def test_non_admin_403(pw, make_client): assert make_client().get("/api/admin/hometv/sources").status_code == 403
def test_list_has_seeded_defaults(admin_client):
    ids = [s["id"] for s in admin_client.get("/api/admin/hometv/sources").json()]
    assert ids == ["doubo-vn", "iptv-org-vn", "dangdvd-iptv", "khanh71-iptv", "khanh71-http"]
def test_create_and_validate(admin_client, public_dns): ...   # 201 + pending_review; tên rỗng → 422 errors["name"]
def test_create_requires_json(admin_client): ...              # form-urlencoded → 415
def test_patch_approve_and_unknown(admin_client): ...         # rights_status approved → 200; id lạ → 404
def test_refresh_202(admin_client): ...
def test_delete_cascades(admin_client, db): ...               # nhập dữ liệu cho nguồn rồi DELETE → 204, streams và kênh mồ côi mất
```

- [ ] **Step 2: Chạy, xác nhận thất bại.**
- [ ] **Step 3: Viết `hometv/api.py`**, gắn router.
- [ ] **Step 4: Chạy lại** — PASS; cả bộ PASS.
- [ ] **Step 5: Commit** — `feat(hometv): admin API for IPTV sources`

---

### Task 10: Giao diện nguồn IPTV trong Cài đặt

**Files:**
- Create: `static/settings-hometv.js` (gán `window.HFSettingsHometv = { renderGroup }`)
- Modify: `static/settings.js` (thay dòng `hometv.iptv` bằng lời gọi `HFSettingsHometv.renderGroup(ui)`; `ui` là object chứa các helper settings.js cần chia sẻ — `api`, `every`, `render`, các hàm dựng dòng/công tắc/stepper/inline edit, `lastSeenText` — đặt tên theo hàm đang có), `templates/index.html` (nạp `asset('settings-hometv.js')` trước `settings.js`), `static/hf.css`
- Test: `tests/ui/test_settings_hometv.py`

**Interfaces:**
- Consumes: 5 route của Task 9; helper của `static/settings.js`; lưới điều hướng TV của `settings.js` (dòng mới phải là `.set-row` có phần tử focus được)
- Produces: DOM `.set-group[data-group="hometv"]` gồm `.set-source[data-id]` mỗi nguồn và dòng `[data-key="hometv.add"]`; thao tác mở dọc `.set-source-actions` với nút `[data-act="approve|unapprove|reject|enable|disable|refresh|edit|delete"]`; form thêm có `#hometvName`, `#hometvUrl`.

- [ ] **Step 1: Viết test UI (thất bại)** — admin đăng nhập như `tests/ui/test_settings_admin.py`; DNS public bằng `monkeypatch.setattr(hometv.netguard, "resolve", ...)`; vòng lặp nền không chạy trong test UI (TestClient không chạy lifespan), nên "đang tải" giả bằng một job `running` chèn thẳng vào DB.

```python
def test_hometv_lists_default_sources(pw, open_app):
    page = admin_page(open_app)
    assert page.locator('.set-group[data-group="hometv"] .set-source').count() == 5
    assert "Chờ duyệt" in page.locator('.set-source[data-id="iptv-org-vn"]').inner_text()
def test_add_source(pw, open_app, public_dns): ...     # + Thêm nguồn → điền → Thêm → dòng mới "Chờ duyệt"; 422 hiện lỗi dưới ô
def test_approve_and_disable(pw, open_app): ...         # Duyệt → "Đã duyệt"; Tắt → dòng mờ, "Đã tắt"
def test_refresh_polls_until_done(pw, open_app, app_module): ...  # job running → "Đang tải…"; xoá job → trong ≤ 4 giây dòng hết "Đang tải…"; không còn poll sau đó
def test_error_shown_in_danger_color(pw, open_app): ... # last_error "HTTP 404" → "Lỗi: HTTP 404 (giữ bản cũ)", màu --danger
def test_delete_confirms(pw, open_app): ...             # dismiss → không DELETE; accept → DELETE /api/admin/hometv/sources/doubo-vn
def test_edit_priority_and_interval(pw, open_app): ...  # Sửa → (+) ưu tiên, (+) chu kỳ → PATCH với priority/refresh_hours mới
def test_tv_every_hometv_row_reachable(pw, open_app): ...  # tv=True: ArrowDown từ đầu trang tới được [data-key="hometv.add"]
def test_close_stops_polling(pw, open_app): ...         # rời tab Cài đặt khi đang có job running → không còn GET sources sau đó
```

- [ ] **Step 2: Chạy, xác nhận thất bại.**
- [ ] **Step 3: Viết code.** Chữ giao diện đúng Global Constraints. Không dùng `innerHTML` cho dữ liệu từ server.
- [ ] **Step 4: Chạy lại** — `tests/ui/test_settings_hometv.py`, `tests/ui/test_settings.py`, `tests/ui/test_settings_admin.py` PASS; cả bộ Python và JS PASS. Chụp nhóm HomeTV ở 430×932 và 1920×1080, xem bằng mắt, sửa bố cục nếu lệch.
- [ ] **Step 5: Commit** — `feat(settings): manage IPTV sources in the HomeTV group`
