# HomeTV — Kiểm tra sức khỏe và chấm điểm stream Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Server tự kiểm tra stream của nguồn đã duyệt bằng ffprobe, chấm điểm, xếp stream tốt lên đầu, ẩn kênh chết hẳn; bộ kiểm chỉ chạy khi máy chủ ở Việt Nam.

**Architecture:** `geo.py` dò quốc gia của IP công khai và quyết định bật bộ kiểm. `health.py` đo một stream (Python tải playlist và đoạn đầu, `ffprobe` đọc file tạm). `scoring.py` chấm điểm (hàm thuần). `health_store.py` ghi và truy vấn `stream_health`. `health_scheduler.py` là vòng lặp nền. `catalog.py` nhận cờ `use_health` để sắp thứ tự và ẩn kênh. `health_api.py` và `settings-hometv.js` là phần admin.

**Tech Stack:** FastAPI, httpx, sqlite3, m3u8 (đã có), `ffprobe` (đã có trong image), pytest + Playwright, `node --test`.

**Spec:** `docs/superpowers/specs/2026-10-31-hometv-health-design.md`. Gói trước: `2026-10-31-hometv-ui-design.md`. Làm trên nhánh `feat/hometv-health` cắt từ `main`.

## Global Constraints

- TDD (CLAUDE.md): test trước, thấy FAIL, rồi mới viết code. Dùng `.venv/bin/python -m pytest` (python hệ thống thiếu thư viện); chạy Node bằng `node --test tests/js/*.test.mjs` (không truyền thư mục). Chạy cả bộ `pytest -q` mất khoảng 5 phút: chạy nền, ghi ra file, đọc phần đuôi.
- Test gọn cho một người dùng; không mạng thật (`httpx.MockTransport`, `probe` và `detect_country` được tiêm giả); chỉ một test chạy `ffprobe` thật, bỏ qua nếu thiếu binary.
- Tên file test theo mẫu hiện có: `tests/hometv/test_hometv_<tên>.py`; chuỗi giao diện và lỗi bằng tiếng Việt, comment tiếng Việt.
- Bộ kiểm: timeout kết nối 10 giây; tải tối đa 2 MB đoạn đầu (1 MB cho stream `direct`) trong 10 giây; `ffprobe` timeout 15 giây rồi kill, chạy với `-v error -nostdin -print_format json -show_streams -show_format`; tối đa 4 `ffprobe` song song, mỗi host 1 lượt; chu kỳ 6 giờ; vòng lặp tick 60 giây; dò quốc gia lại mỗi 1 giờ, kết quả cũ quá 24 giờ thì coi là không biết.
- Điểm: chết 0; sống 50 cộng bậc độ phân giải (≥1080 cộng 25, ≥720 cộng 18, ≥480 cộng 10, còn lại 4), cộng tốc độ tải so với bitrate (tỷ lệ ≥2 cộng 15, ≥1 cộng 8, không rõ bitrate hoặc chậm hơn cộng 0), cộng 10 nếu `https`; tối đa 100. Chưa kiểm: 50.
- Ẩn kênh khi mọi stream hợp lệ của nó có `fail_streak >= 3`; stream chưa kiểm hoặc `fail_streak < 3` giữ kênh hiện. Khi `use_health=False` bỏ qua hoàn toàn dữ liệu sức khỏe. Đường phát `valid_stream` không lọc theo sức khỏe.
- `HOMETV_HEALTH`: `auto` (mặc định) | `on` | `off`; giá trị lạ là `auto`. API công khai không bao giờ trả URL stream; API admin sức khỏe cũng không.
- Hoãn (không làm): SSRF guard và giới hạn tài nguyên cho đường tải của bộ kiểm. Dùng `play.open_upstream` hiện có.
- Chuỗi từ server hiển thị bằng `textContent`. File tĩnh mới (nếu có) phải qua `{{ asset('...') }}` và có trong `ASSETS` của `tests/test_static_versioning.py`.

## Review Focus

1. Playlist con (variant) trả 404 hoặc rỗng: stream chết với lý do, không ném lỗi (Task 4).
2. `ffprobe` treo hoặc in rác không phải JSON: kill sau timeout, stream chết "Không đọc được video" (Task 4).
3. Máy chủ rời Việt Nam: `fail_streak` cũ không được ẩn kênh hay đổi thứ tự (Task 6).
4. Kênh có một stream `fail_streak=3` và một stream chưa kiểm: kênh vẫn hiện (Task 6).
5. Nguồn bị gỡ duyệt giữa lúc kiểm: dừng ngay, không ghi thêm dòng nào; thiếu binary `ffprobe`: vòng lặp dừng sạch, HomeFlix không bị ảnh hưởng (Task 7).

---

### Task 1: Schema v3 và cột lịch kiểm

**Files:**
- Modify: `hometv/db.py` (`SCHEMA_VERSION`, `_SCHEMA`, migrate trong `open`)
- Test: `tests/hometv/test_hometv_db.py`

**Interfaces:**
- Produces: bảng `stream_health(stream_id INTEGER PRIMARY KEY REFERENCES streams(id) ON DELETE CASCADE, checked_at REAL, ok INTEGER NOT NULL, error TEXT, latency_ms INTEGER, throughput_kbps INTEGER, width INTEGER, height INTEGER, vcodec TEXT, bitrate_kbps INTEGER, fail_streak INTEGER NOT NULL DEFAULT 0, score INTEGER NOT NULL DEFAULT 50)`; cột `sources.next_check_at REAL` và `sources.last_check_at REAL`; `schema_version` = 3.

- [ ] **Step 1: Viết test lỗi.** Đổi các chỗ kiểm `schema_version == 2` thành `== 3`; thêm `test_stream_health_cascades_with_stream` (xoá stream thì dòng sức khỏe mất) và `test_open_upgrades_v2_db_adds_columns` (tạo file DB bằng schema v2 có bảng `sources` không có hai cột mới, gọi `db.open`, kiểm có cột `next_check_at`, `last_check_at`, bảng `stream_health` và version 3, dữ liệu nguồn cũ còn nguyên).
- [ ] **Step 2: Chạy** `.venv/bin/python -m pytest tests/hometv/test_hometv_db.py -q`. Mong đợi: FAIL.
- [ ] **Step 3: Sửa `hometv/db.py`:** `SCHEMA_VERSION = 3`; thêm hai cột vào `CREATE TABLE sources` (cho DB mới) và `CREATE TABLE IF NOT EXISTS stream_health`; trong `open()`, sau `executescript`, với mỗi cột mới chưa có trong `PRAGMA table_info(sources)` chạy `ALTER TABLE sources ADD COLUMN ...` (SQLite không tự thêm cột vào bảng đã tồn tại).
- [ ] **Step 4: Chạy** `.venv/bin/python -m pytest tests/hometv -q`. Mong đợi: PASS.
- [ ] **Step 5: Commit** `feat(hometv): schema v3 stream_health, lịch kiểm`.

### Task 2: `hometv/geo.py` — máy chủ có ở Việt Nam không

**Files:**
- Create: `hometv/geo.py`
- Test: `tests/hometv/test_hometv_geo.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class HealthMode: mode: str; enabled: bool; country: str | None; checked_at: float`
  - `parse_mode(value: str | None) -> str` trả `"auto" | "on" | "off"`, giá trị lạ hoặc `None` là `"auto"` (không phân biệt hoa thường).
  - `async def detect_country(client: httpx.AsyncClient) -> str | None`: thử `https://ipinfo.io/country` (thân là mã, có thể kèm xuống dòng, hoa hoặc thường) rồi `https://api.country.is` (JSON `{"country": "VN"}`), mỗi nơi timeout 5 giây; trả mã 2 chữ hoa hoặc `None`.
  - `async def refresh_mode(client, mode: str, now: float) -> HealthMode`: cập nhật và trả trạng thái module. `auto`: `enabled = country == "VN"`; dò thất bại thì giữ kết quả cũ (không đổi `checked_at`); `on`/`off`: `enabled` theo biến, `country` vẫn dò để hiển thị.
  - `current(now: float | None = None) -> HealthMode`: trạng thái hiện tại; trước lần dò đầu là `HealthMode("auto", False, None, 0.0)`; mode `auto` mà `now - checked_at > 86400` thì trả `enabled=False`.
  - `REFRESH_EVERY = 3600`, `STALE = 86400`; `def reset() -> None` đặt lại trạng thái (cho test).

- [ ] **Step 1: Viết test lỗi** (upstream giả bằng `httpx.MockTransport`): `test_detect_reads_ipinfo_plain_text` (`"vn\n"` → `"VN"`), `test_detect_falls_back_to_country_is`, `test_detect_none_when_both_fail`, `test_parse_mode`, `test_auto_enabled_only_for_vn` (VN bật; MY, None tắt), `test_on_off_override_detection`, `test_failed_detect_keeps_previous_until_stale` (dò VN rồi dò lỗi: vẫn bật; `current(now+86401)` tắt), `test_current_before_first_detect_is_disabled`.
- [ ] **Step 2: Chạy** `.venv/bin/python -m pytest tests/hometv/test_hometv_geo.py -q`. Mong đợi: FAIL (module chưa có).
- [ ] **Step 3: Cài `hometv/geo.py`.** Trạng thái là một biến mức module, ghi/đọc không cần khóa (gán nguyên tử một đối tượng bất biến).
- [ ] **Step 4: Chạy** `.venv/bin/python -m pytest tests/hometv -q`. Mong đợi: PASS.
- [ ] **Step 5: Commit** `feat(hometv): dò máy chủ có ở Việt Nam để bật bộ kiểm`.

### Task 3: `hometv/scoring.py`

**Files:**
- Create: `hometv/scoring.py` (và `HealthResult` ở `hometv/health.py`, xem Task 4; tạo `health.py` chỉ với dataclass ở bước này)
- Test: `tests/hometv/test_hometv_scoring.py`

**Interfaces:**
- Produces: `@dataclass class HealthResult: ok: bool; error: str | None = None; latency_ms: int | None = None; throughput_kbps: int | None = None; width: int | None = None; height: int | None = None; vcodec: str | None = None; bitrate_kbps: int | None = None` trong `hometv/health.py`; `scoring.score(result: HealthResult, https: bool) -> int`; `scoring.UNKNOWN = 50`.

- [ ] **Step 1: Viết test lỗi:** `test_dead_is_zero`, `test_resolution_tiers` (height 1080/720/480/360/None → +25/+18/+10/+4/+4 trên nền 50, https=False, không tốc độ), `test_throughput_tiers` (tỷ lệ `throughput_kbps / bitrate_kbps`: 2.0 → +15, 1.0 → +8, 0.5 → +0; thiếu bitrate → +0), `test_https_bonus_and_cap` (tổng không quá 100), `test_unknown_constant`.
- [ ] **Step 2: Chạy** `.venv/bin/python -m pytest tests/hometv/test_hometv_scoring.py -q`. Mong đợi: FAIL.
- [ ] **Step 3: Cài** `HealthResult` và `score`.
- [ ] **Step 4: Chạy** `.venv/bin/python -m pytest tests/hometv -q`. Mong đợi: PASS.
- [ ] **Step 5: Commit** `feat(hometv): chấm điểm stream`.

### Task 4: `hometv/health.py` — đo một stream

**Files:**
- Modify: `hometv/health.py`
- Test: `tests/hometv/test_hometv_health.py`

**Interfaces:**
- Consumes: `play.open_upstream(client, url, headers) -> (httpx.Response, final_url)`, `play.is_direct_url`, `fetcher.FetchError`.
- Produces:
  - `class FfprobeMissing(Exception)`; `class ProbeError(Exception)` (lý do ngắn).
  - `PROBE_TIMEOUT = 15.0`, `SEGMENT_CAP = 2 * 1024 * 1024`, `DIRECT_CAP = 1024 * 1024`, `DOWNLOAD_TIMEOUT = 10.0`.
  - `async def ffprobe(data: bytes) -> dict`: ghi `data` ra file tạm (xoá trong `finally`), chạy lệnh `ffprobe` đúng cờ ở Global Constraints bằng `asyncio.create_subprocess_exec`, kill khi quá `PROBE_TIMEOUT`, trả JSON đã parse. Thiếu binary → `FfprobeMissing`; quá giờ, thoát khác 0 hoặc JSON hỏng → `ProbeError("Không đọc được video")`. Đường dẫn lệnh nằm ở hằng `FFPROBE = "ffprobe"` để test thay thế.
  - `async def check_stream(client, url: str, headers: dict[str, str], *, probe=ffprobe) -> HealthResult`: quy trình ở spec mục "Bộ kiểm" (playlist, chọn variant `BANDWIDTH` thấp nhất có `resolution` khai báo hoặc variant đầu, đoạn đầu, đo `latency_ms` và `throughput_kbps`, `probe`). Không bao giờ ném lỗi, ngoại trừ để `FfprobeMissing` lan ra. `bitrate_kbps` lấy từ `format.bit_rate`/luồng video, thiếu thì ước từ số byte và `seg.duration`.

- [ ] **Step 1: Viết test lỗi** (upstream giả `httpx.MockTransport` với response dựng bằng `httpx.ByteStream`; `probe` giả trả `{"streams": [{"codec_type": "video", "width": 1280, "height": 720, "codec_name": "h264"}], "format": {"bit_rate": "1500000"}}`): `test_media_playlist_ok_fills_fields`, `test_master_picks_lowest_bandwidth_variant`, `test_not_a_playlist_is_dead`, `test_http_error_is_dead_with_reason` ("HTTP 403"), `test_variant_404_is_dead_not_exception` (Review Focus 1), `test_empty_segment_is_dead`, `test_no_video_stream_is_dead`, `test_probe_error_is_dead`, `test_direct_stream_skips_playlist`, `test_timeout_is_dead` ("Hết thời gian chờ"), `test_ffprobe_missing_propagates`. Cho `ffprobe`: `test_ffprobe_timeout_kills_process` (đặt `FFPROBE` thành lệnh `sleep` qua một script ngắn và `PROBE_TIMEOUT = 0.5`; kiểm `ProbeError` và không còn tiến trình con) và `test_ffprobe_garbage_output_is_probe_error` (Review Focus 2; `FFPROBE` trỏ tới script in rác), `test_ffprobe_real_on_generated_clip` (bỏ qua nếu `shutil.which("ffprobe")` rỗng: sinh clip mpegts 1 giây bằng `ffmpeg -f lavfi -i testsrc=size=320x240:rate=10 -t 1`, kiểm `width == 320`).
- [ ] **Step 2: Chạy** `.venv/bin/python -m pytest tests/hometv/test_hometv_health.py -q`. Mong đợi: FAIL.
- [ ] **Step 3: Cài** `ffprobe` và `check_stream`. Dùng `m3u8.loads(text, uri=final_url)` để chọn variant và giải URI đoạn.
- [ ] **Step 4: Chạy** `.venv/bin/python -m pytest tests/hometv -q`. Mong đợi: PASS.
- [ ] **Step 5: Commit** `feat(hometv): đo stream bằng ffprobe`.

### Task 5: `hometv/health_store.py`

**Files:**
- Create: `hometv/health_store.py`
- Test: `tests/hometv/test_hometv_health_store.py`

**Interfaces:**
- Consumes: `db.tx`, `db.conn`, `db.lock`, `HealthResult`, `scoring.score`.
- Produces (các hàm đồng bộ, gọi qua `asyncio.to_thread`):
  - `CHECK_EVERY = 6 * 3600`, `FAIL_HIDE = 3`; `checking: set[str]` (nguồn đang có lượt kiểm, do scheduler thêm/bớt).
  - `eligible(source_id: str) -> bool`: nguồn tồn tại, `enabled=1`, `rights_status='approved'`.
  - `streams_to_check(source_id: str) -> list[dict]`: `{"id", "url", "http_headers": dict, "https": bool}` của stream `active=1`, `drm_hint=0` thuộc nguồn.
  - `record(stream_id: int, result: HealthResult, https: bool, now: float) -> None`: ghi đè dòng; `ok` thì `fail_streak = 0`, ngược lại cộng 1; tính `score` bằng `scoring.score`. Stream đã bị xoá giữa chừng thì bỏ qua êm.
  - `due_sources(now: float) -> list[str]`: id nguồn `eligible` có `next_check_at IS NULL OR <= now`, sắp theo `priority`.
  - `finish_source(source_id: str, now: float) -> None`: `last_check_at = now`, `next_check_at = now + CHECK_EVERY`.
  - `request_check(source_id: str, now: float) -> bool`: `False` nếu không `eligible`; ngược lại đặt `next_check_at = now`, trả `True`.
  - `source_health(now: float) -> dict[str, dict]`: id nguồn → `{"checked", "alive", "dead", "unknown", "checking"}` (đếm trên stream hợp lệ của nguồn; `checking` từ tập `checking`).
  - `dead_streams(source_id: str, status: str, limit: int) -> list[dict]`: `status` là `"dead"` (`ok=0`) hoặc `"all"`; mỗi dòng `{"stream_id", "channel_name", "ok", "error", "fail_streak", "width", "height", "score"}`, sắp `fail_streak` giảm dần rồi `stream_id`; không có URL.

- [ ] **Step 1: Viết test lỗi** (fixture `db`, dựng nguồn/kênh/stream bằng helper `seed_*` của `tests/hometv/test_hometv_catalog.py`): `test_record_fail_streak_counts_and_resets`, `test_record_sets_score`, `test_record_missing_stream_ignored`, `test_eligible_rules`, `test_streams_to_check_skips_inactive_and_drm`, `test_due_sources_null_and_future`, `test_finish_source_sets_next_check`, `test_request_check_rejects_unapproved_or_disabled`, `test_source_health_counts` (đủ bốn loại, `checking`), `test_dead_streams_filters_and_has_no_url`, `test_reingested_stream_has_no_health_row` (stream mới sau nạp lại là `unknown`).
- [ ] **Step 2: Chạy** `.venv/bin/python -m pytest tests/hometv/test_hometv_health_store.py -q`. Mong đợi: FAIL.
- [ ] **Step 3: Cài** các hàm, dùng một lượt SQL cho `source_health`.
- [ ] **Step 4: Chạy** `.venv/bin/python -m pytest tests/hometv -q`. Mong đợi: PASS.
- [ ] **Step 5: Commit** `feat(hometv): lưu và truy vấn sức khỏe stream`.

### Task 6: `catalog` dùng sức khỏe để sắp thứ tự và ẩn kênh

**Files:**
- Modify: `hometv/catalog.py` (`list_channels`, `channel_streams`), `hometv/public_api.py` (truyền `use_health`)
- Test: `tests/hometv/test_hometv_health_store.py` (thêm) và `tests/hometv/test_hometv_public_api.py` (thêm)

**Interfaces:**
- Consumes: `geo.current().enabled`, bảng `stream_health`.
- Produces: `catalog.list_channels(now: float, use_health: bool = False)` và `catalog.channel_streams(channel_id: int, use_health: bool = False)`. Mặc định `False` giữ nguyên hành vi gói 3.

- [ ] **Step 1: Viết test lỗi:** `test_streams_sorted_by_score_when_enabled` (điểm 90 trước 50 trước 0, hoà thì `priority` rồi `https`), `test_unchecked_streams_rank_at_50`, `test_streams_order_unchanged_when_disabled`, `test_channel_hidden_when_all_streams_failed_3x`, `test_channel_visible_with_one_unchecked_stream` (Review Focus 4), `test_channel_visible_after_one_success`, `test_stale_health_ignored_when_disabled` (Review Focus 3: `fail_streak=9` nhưng `use_health=False` kênh vẫn hiện), `test_public_api_uses_geo_enabled` (monkeypatch `geo.current` trả `enabled=True/False`, kiểm `/api/hometv/channels` ẩn hoặc hiện; `/play` vẫn phát stream `fail_streak=9`).
- [ ] **Step 2: Chạy** `.venv/bin/python -m pytest tests/hometv -q`. Mong đợi: FAIL ở các test mới.
- [ ] **Step 3: Sửa `catalog.py`:** thêm `LEFT JOIN stream_health h` vào truy vấn khi `use_health`; ẩn kênh bằng điều kiện `EXISTS` một stream hợp lệ có `h.stream_id IS NULL OR h.fail_streak < 3`; `ORDER BY COALESCE(h.score, 50) DESC, src.priority, (s.url LIKE 'https:%') DESC, s.id`. Dùng alias ngoài khác alias trong subquery (bài học gói 3: `ch` trong subquery che alias ngoài). `public_api.py` truyền `geo.current().enabled`.
- [ ] **Step 4: Chạy** `.venv/bin/python -m pytest tests/hometv -q`. Mong đợi: PASS.
- [ ] **Step 5: Commit** `feat(hometv): xếp stream theo điểm, ẩn kênh chết`.

### Task 7: Vòng lặp nền `hometv/health_scheduler.py`

**Files:**
- Create: `hometv/health_scheduler.py`
- Modify: `hometv/__init__.py` (export `geo`, `health`, `health_store`, `health_scheduler`, `scoring`), `hometv/sources.py` (`update`, `record_success`), `app.py` (`lifespan`)
- Test: `tests/hometv/test_hometv_health_scheduler.py`

**Interfaces:**
- Consumes: Task 2 đến 5.
- Produces:
  - `MAX_PROBES = 4`, `TICK = 60`.
  - `async def check_source(client, source_id: str, now: float, *, probe=health.ffprobe, sem: asyncio.Semaphore | None = None) -> int`: thêm vào `health_store.checking`; với mỗi stream của `streams_to_check` kiểm `eligible` trước (hết điều kiện thì dừng, không ghi thêm); `check_stream` rồi `record`; xong `finish_source`; luôn bỏ khỏi `checking` trong `finally`. Trả số stream đã ghi. Lỗi bất ngờ trên một stream: ghi chết "Không đọc được video" và đi tiếp. `FfprobeMissing` lan ra.
  - `async def run_due_checks(client, now: float, *, probe=health.ffprobe) -> list[str]`: chọn `due_sources`, tối đa `MAX_PROBES` stream song song toàn bộ, mỗi host một lượt, không chạy trùng nguồn đang trong `checking`.
  - `async def hometv_health_checker(tick: float = TICK) -> None`: vòng lặp: đọc `HOMETV_HEALTH`, gọi `geo.refresh_mode` khi chưa dò hoặc quá `REFRESH_EVERY`, và nếu `geo.current().enabled` thì `run_due_checks`. Lỗi chỉ log. `FfprobeMissing`: log một lần và thoát vòng lặp (không treo HomeFlix). Đóng client khi bị huỷ.
- `sources.update`: khi `rights_status` đổi sang `approved` từ giá trị khác thì đặt `next_check_at = now`. `sources.record_success`: khi `content_hash` đổi thì đặt `next_check_at = now` (nạp lại playlist không xoá dòng `stream_health` còn tồn tại).
- `app.py`: trong `lifespan`, nếu `hometv_ok`, `_bg_tasks.append(asyncio.create_task(hometv.health_scheduler.hometv_health_checker()))` cạnh `hometv_refresher`.

- [ ] **Step 1: Viết test lỗi** (client giả, `probe` giả, `geo` chỉnh bằng monkeypatch): `test_check_source_records_each_stream_and_finishes`, `test_unapproved_source_stops_mid_run` (Review Focus 5: `probe` giả gỡ duyệt nguồn sau stream đầu; chỉ một dòng được ghi), `test_exception_on_one_stream_marks_dead_and_continues`, `test_checking_flag_cleared_even_on_error`, `test_run_due_skips_unapproved_disabled_and_future`, `test_max_four_probes_in_parallel` (đếm đồng thời lớn nhất bằng `probe` giả có `asyncio.sleep`), `test_one_probe_per_host`, `test_loop_idle_when_disabled` (`enabled=False`: `check_stream` không được gọi), `test_loop_stops_cleanly_when_ffprobe_missing` (Review Focus 5), `test_loop_cancellation_clears_checking`, `test_approving_source_makes_it_due_now`, `test_changed_ingest_makes_due_now_but_unchanged_does_not`, `test_existing_health_survives_reingest`.
- [ ] **Step 2: Chạy** `.venv/bin/python -m pytest tests/hometv/test_hometv_health_scheduler.py -q`. Mong đợi: FAIL.
- [ ] **Step 3: Cài** scheduler, hai chỗ sửa trong `sources.py`, và dòng khởi chạy trong `app.py`.
- [ ] **Step 4: Chạy** `.venv/bin/python -m pytest tests/hometv tests/test_admin_api.py -q`. Mong đợi: PASS.
- [ ] **Step 5: Commit** `feat(hometv): vòng lặp nền kiểm tra stream`.

### Task 8: API admin `health_api.py`

**Files:**
- Create: `hometv/health_api.py`
- Modify: `hometv/api.py` (`list_all` gộp `health`), `app.py` (include router), `tests/hometv/test_hometv_api.py` (`len(r.json()[0]) == 13` thành `14`)
- Test: `tests/hometv/test_hometv_health_api.py`

**Interfaces:**
- Consumes: `health_store.*`, `geo.current`, `require_admin` (truyền vào như `api.make_router`).
- Produces: `make_health_router(require_admin: Callable) -> APIRouter` với:
  - `GET /api/admin/hometv/health/status` → `{"mode", "enabled", "country"}` từ `geo.current()`.
  - `POST /api/admin/hometv/sources/{id}/check` → `202 {"queued": true}`; `409 {"detail": "Kiểm tra stream đang tắt (máy chủ không ở Việt Nam)"}` khi `geo.current().enabled` là `False`; `404 {"detail": "Nguồn chưa duyệt hoặc đang tắt"}` khi `request_check` trả `False` (cũng dùng cho id lạ).
  - `GET /api/admin/hometv/sources/{id}/health?status=dead&limit=50` → `health_store.dead_streams`; `status` ngoài `dead|all` hoặc `limit` ngoài 1–200 là `422`; id lạ `404`.
  - `GET /api/admin/hometv/sources` (sửa trong `api.py`): mỗi nguồn thêm khoá `health` từ `health_store.source_health`.
  - Quy tắc như router admin hiện có: id theo `ID_RE`, `503 {"detail": "HomeTV chưa sẵn sàng"}` khi DB đóng, `require_admin` cho mọi route.

- [ ] **Step 1: Viết test lỗi:** `test_health_routes_protected` (mọi route `403` với non-admin, `404` khi admin tắt), `test_status_reports_geo`, `test_sources_list_has_health_counts`, `test_check_202_sets_next_check`, `test_check_409_when_disabled`, `test_check_404_unknown_or_unapproved`, `test_health_list_filters_dead_without_urls`, `test_health_list_validates_params`, `test_post_check_requires_json_415`.
- [ ] **Step 2: Chạy** `.venv/bin/python -m pytest tests/hometv/test_hometv_health_api.py -q`. Mong đợi: FAIL.
- [ ] **Step 3: Cài** router, sửa `list_all`, include trong `app.py` ngay sau router admin hiện có.
- [ ] **Step 4: Chạy** `.venv/bin/python -m pytest tests/hometv -q`. Mong đợi: PASS.
- [ ] **Step 5: Commit** `feat(hometv): API admin sức khỏe stream`.

### Task 9: Giao diện Cài đặt → HomeTV

**Files:**
- Modify: `static/settings-hometv.js`, `static/hf.css` (chỉ nếu cần một khối CSS nhỏ cho danh sách stream chết, dùng biến màu sẵn có)
- Test: `tests/ui/test_settings_hometv.py` (thêm)

**Interfaces:**
- Consumes: `GET /api/admin/hometv/health/status`, `health` trong danh sách nguồn, `POST .../sources/{id}/check`, `GET .../sources/{id}/health?status=dead&limit=50`.
- Behaviour (đọc `renderGroup`, `sourceRow`, `metaText`, `paintActions`, `schedule` trong file làm khuôn):
  - Hàng trạng thái trên danh sách nguồn: "Kiểm tra stream: bật (máy chủ ở VN)" / "Kiểm tra stream: tắt (máy chủ ngoài Việt Nam)" / "Kiểm tra stream: tắt (chưa xác định vị trí máy chủ)" (khi `enabled=false` và `country=null`); điều khiển được bằng remote TV như `info(...)`.
  - `metaText`: nguồn đã duyệt và `health.checked > 0` thì thêm "{alive}/{checked} sống"; `health.checking` thì thêm "Đang kiểm tra…". `schedule(...)` cũng chạy lại khi có nguồn `health.checking`.
  - Thao tác **Kiểm tra ngay** sau **Làm mới ngay**, chỉ khi nguồn đã duyệt, đang bật và `status.enabled`; sau khi bấm hiện "Đang kiểm tra…" và tải lại cho tới khi `checking` tắt (cơ chế như `queued`, hết hạn sau `QUEUED_MAX_MS`).
  - Thao tác **Xem stream chết** mở danh sách ngay trong dòng (tối đa 50 mục: "{channel_name} · {error} · chết {fail_streak} lần", bằng `textContent`); rỗng thì "Không có stream chết"; lỗi thì thông báo ngắn.

- [ ] **Step 1: Viết test lỗi** (mẫu `hometv_page`, `admin_page`, `server_json` trong file; dựng dữ liệu bằng `hometv.db` và `health_store.record`; giả `geo.current` bằng monkeypatch): `test_status_row_on_and_off`, `test_meta_shows_alive_over_checked`, `test_meta_shows_checking_and_polls_until_done`, `test_check_now_visible_only_for_approved_enabled_and_when_enabled`, `test_check_now_posts_and_shows_checking`, `test_dead_list_shows_reason_and_streak`, `test_dead_list_empty_message`, `test_dead_list_escapes_html_in_channel_name`.
- [ ] **Step 2: Chạy** `.venv/bin/python -m pytest tests/ui/test_settings_hometv.py -q -p no:cacheprovider`. Mong đợi: FAIL ở các test mới.
- [ ] **Step 3: Sửa `settings-hometv.js`** theo Behaviour trên; không đổi hành vi các thao tác cũ.
- [ ] **Step 4: Chạy** `.venv/bin/python -m pytest tests/ui/test_settings_hometv.py tests/ui/test_settings_admin.py tests/test_static_versioning.py -q -p no:cacheprovider`. Mong đợi: PASS.
- [ ] **Step 5: Commit** `feat(hometv): trạng thái và thao tác kiểm tra stream trong Cài đặt`.

### Task 10: Kiểm tra toàn bộ và ghi memory

- [ ] **Step 1: Chạy toàn bộ** `timeout 560 .venv/bin/python -m pytest -q -p no:cacheprovider > <scratchpad>/full.txt 2>&1` rồi `node --test tests/js/*.test.mjs`. Mong đợi: PASS hết; báo số liệu thật, nếu có test lỗi thì ghi rõ test nào.
- [ ] **Step 2: Cập nhật memory** `hometv-project`: gói 4 xong, chỉ chạy khi máy chủ ở Việt Nam (`HOMETV_HEALTH`), và còn việc hoãn (SSRF guard, giới hạn tài nguyên bộ kiểm) trong `hometv-ui-hardening-backlog`.
- [ ] **Step 3: Commit** nếu có thay đổi trong repo.
