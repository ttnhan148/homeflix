# HomeTV — Kiểm tra sức khỏe và chấm điểm stream — Design Spec

> **Ngày:** 2026-10-31
> **Trạng thái:** Chờ review spec
> **Thuộc dự án:** HomeTV (gói 4/5). Gói 2: `docs/superpowers/specs/2026-10-31-hometv-iptv-ingest-design.md`. Gói 3: `docs/superpowers/specs/2026-10-31-hometv-ui-design.md`

## Mục tiêu

Server tự kiểm tra các stream của nguồn đã duyệt: còn sống không, độ phân giải, codec, bitrate, tốc độ tải. Mỗi stream có điểm; người xem luôn thấy stream tốt nhất trước, stream chết bị hạ xuống cuối, và kênh chết hẳn tự ẩn khỏi lưới. Admin thấy tình trạng từng nguồn trong Cài đặt và có nút "Kiểm tra ngay".

Các CDN truyền hình Việt Nam chặn IP ngoài nước (xem `cdn-geoblock-vn-only`). Server dev không ở Việt Nam, server prod ở Việt Nam. Bộ kiểm **chỉ chạy khi máy chủ đang ở Việt Nam**, nếu không mọi stream sẽ bị chấm chết nhầm.

## Quyết định đã chốt

| Chủ đề | Quyết định |
|---|---|
| Cách chạy | Vòng lặp nền định kỳ (6 giờ) cộng nút "Kiểm tra ngay" cho từng nguồn |
| Cách đo | Python tải playlist và đoạn đầu, `ffprobe` chỉ đọc file cục bộ vừa tải |
| Stream chết | Hạ xuống cuối danh sách. Kênh chỉ ẩn khi mọi stream đều chết 3 lần kiểm liên tiếp; một lần sống là hiện lại |
| Bật bộ kiểm | Tự phát hiện: chỉ bật khi IP công khai của máy chủ ở Việt Nam. Có thể ép bằng biến môi trường |
| Phạm vi | Chỉ stream `active`, không DRM, thuộc nguồn đã duyệt và đang bật |
| Hoãn | Chặn SSRF và giới hạn tài nguyên cho đường tải của bộ kiểm (cùng danh sách hoãn với gói 3, xem memory `hometv-ui-hardening-backlog`) |

## Phát hiện máy chủ ở Việt Nam (`hometv/geo.py`)

- `async def detect_country(client) -> str | None`: hỏi mã quốc gia của IP công khai. Thử lần lượt `https://ipinfo.io/country` (trả chữ thường/hoa kèm xuống dòng) rồi `https://api.country.is` (JSON `{"country": "VN"}`); mỗi nơi timeout 5 giây. Trả mã 2 chữ hoa; không nơi nào trả lời hợp lệ thì `None`.
- Biến môi trường `HOMETV_HEALTH`: `auto` (mặc định), `on` (luôn bật, bỏ qua phát hiện), `off` (luôn tắt). Giá trị lạ coi là `auto`.
- `async def refresh_mode(client, mode: str, now: float) -> HealthMode`: tính trạng thái hiện tại. `HealthMode` là dataclass `{mode: str, enabled: bool, country: str | None, checked_at: float}`. Với `auto`: `enabled = country == "VN"`; `None` (không xác định) thì `enabled = False` (an toàn). Với `on`/`off`: `enabled` theo biến, `country` vẫn được dò để hiển thị.
- Kết quả giữ trong biến mức module và đọc bằng `geo.current() -> HealthMode` (trước khi dò lần đầu: `enabled=False`, `country=None`). Dò lại mỗi giờ để chuyển server không cần khởi động lại; khi dò thất bại giữ nguyên kết quả cũ nhưng không quá 24 giờ, quá hạn thì `enabled=False`.
- Khi `enabled=False`: bộ kiểm không chạy, và dữ liệu sức khỏe cũ **không được dùng** để ẩn kênh hay đổi thứ tự (tránh kênh biến mất vì số liệu lỗi thời sau khi chuyển server).

## Dữ liệu (`hometv.db`, schema v3)

- Bảng `stream_health`: `stream_id INTEGER PRIMARY KEY REFERENCES streams(id) ON DELETE CASCADE`, `checked_at REAL`, `ok INTEGER`, `error TEXT`, `latency_ms INTEGER`, `throughput_kbps INTEGER`, `width INTEGER`, `height INTEGER`, `vcodec TEXT`, `bitrate_kbps INTEGER`, `fail_streak INTEGER NOT NULL DEFAULT 0`, `score INTEGER NOT NULL DEFAULT 50`. Một dòng mỗi stream, ghi đè mỗi lần kiểm.
- Cột mới `sources.next_check_at REAL` (NULL nghĩa là đến hạn ngay) và `sources.last_check_at REAL`.
- Migrate theo `schema_version`: v2 lên v3 chỉ thêm bảng và cột, không mất dữ liệu.

## Bộ kiểm (`hometv/health.py`)

`async def check_stream(client, url: str, headers: dict[str, str], *, probe) -> HealthResult` với `probe` là hàm chạy `ffprobe` được tiêm vào để test. `HealthResult`: `ok`, `error`, `latency_ms`, `throughput_kbps`, `width`, `height`, `vcodec`, `bitrate_kbps`.

1. Mở URL bằng `play.open_upstream` (header UA/referrer của stream, theo chuyển hướng), timeout 10 giây. `latency_ms` là thời gian tới byte đầu của playlist.
2. Stream HLS: nội dung phải bắt đầu bằng `#EXTM3U`. Master playlist thì chọn variant có `BANDWIDTH` thấp nhất mà vẫn có độ phân giải khai báo (nếu không có thì variant đầu tiên) rồi tải playlist con. Lấy đoạn media đầu tiên.
3. Tải tối đa 2 MB của đoạn đầu trong 10 giây. `throughput_kbps` là byte đã tải chia thời gian tải. Đoạn rỗng thì chết.
4. Ghi 2 MB đó ra file tạm (thư mục tạm của tiến trình, xoá ngay sau đó) và chạy `ffprobe -v error -nostdin -print_format json -show_streams <file>` bằng `asyncio.create_subprocess_exec`, timeout 15 giây, kill khi quá giờ.
5. Sống khi `ffprobe` thấy ít nhất một luồng video; lấy `width`, `height`, `codec_name`, `bit_rate` (nếu thiếu thì ước lượng từ byte và thời lượng đoạn). Không có luồng video là chết.
6. Stream `direct` (đuôi `.ts`, `.mp4`, `.mkv`, `.flv`, `.webm`, `.aac`, `.mp3`, theo `play.is_direct_url`): bỏ bước playlist, tải 1 MB đầu rồi `ffprobe`. Stream audio thuần (không có luồng video) vẫn tính là chết ở gói này.
7. Mọi lỗi (mạng, HTTP ≥ 400, hết giờ, `ffprobe` lỗi) cho `ok=False` và `error` là lý do ngắn bằng tiếng Việt: "Hết thời gian chờ", "HTTP 403", "Không phải playlist", "Không tải được đoạn", "Không đọc được video". Không bao giờ ném lỗi ra ngoài `check_stream`.

## Chấm điểm (`hometv/scoring.py`)

Hàm thuần `score(result, https: bool) -> int`, kết quả 0–100:

- Chết: 0.
- Sống: 50, cộng bậc độ phân giải theo `height` (≥1080 cộng 25, ≥720 cộng 18, ≥480 cộng 10, thấp hơn hoặc không rõ cộng 4), cộng tốc độ tải so với thời lượng đoạn (tải nhanh hơn thời gian thực ít nhất 2 lần cộng 15, ít nhất 1 lần cộng 8, chậm hơn cộng 0), cộng 10 nếu stream là `https`. Tối đa 100.
- Chưa kiểm: 50 (không phạt, không thưởng).

## Lưu kết quả và quy tắc ẩn kênh (`hometv/health_store.py`)

- `record(stream_id, result, now)`: ghi đè dòng `stream_health`; `ok` thì `fail_streak = 0`, ngược lại tăng 1; tính `score`.
- Hàm đọc trong `hometv/catalog.py` nhận thêm tham số `use_health: bool`:
  - `channel_streams`: sắp theo `score` giảm dần, hoà thì `priority` nguồn tăng dần, rồi `https` trước `http`, rồi `streams.id`. Stream chưa kiểm dùng điểm 50. Chỉ áp dụng khi `use_health=True`; nếu không giữ thứ tự cũ (gói 3).
  - `list_channels`: khi `use_health=True`, kênh bị ẩn nếu **mọi** stream hợp lệ của nó đều có dòng sức khỏe với `fail_streak >= 3`. Kênh có ít nhất một stream chưa kiểm hoặc `fail_streak < 3` vẫn hiện.
  - `valid_stream` (đường phát) **không** lọc theo sức khỏe: stream chết vẫn phát được nếu người xem thử.
- Route công khai truyền `use_health=geo.current().enabled`.

## Lịch chạy (`hometv/health_scheduler.py`)

- `hometv_health_checker()` chạy trong `lifespan` cạnh `hometv_refresher()`, chỉ khi `hometv.db` mở được. Cứ 60 giây: gọi `geo.refresh_mode` nếu đã quá 1 giờ; nếu `enabled` thì chọn nguồn đã duyệt, đang bật, có `next_check_at <= now` (hoặc NULL).
- Mỗi nguồn kiểm mọi stream `active`, `drm_hint=0` của nó. Tối đa 4 `ffprobe` chạy song song toàn bộ, mỗi host tối đa 1 lượt cùng lúc. Một lượt không chạy hai nguồn trùng nhau (khóa theo nguồn trong bộ nhớ).
- Xong một nguồn: `last_check_at = now`, `next_check_at = now + 6 giờ`.
- Nguồn vừa được duyệt (`rights_status` đổi sang `approved`) hoặc vừa nạp playlist mới thành công: `next_check_at = now` để kênh mới được chấm sớm. Nạp lại playlist **không** xoá dữ liệu sức khỏe của stream còn tồn tại.
- Hủy sạch khi tắt server: tác vụ `ffprobe` bị kill, file tạm bị xoá.
- Không tìm thấy binary `ffprobe`: log một lần, vòng lặp kiểm dừng; mọi stream giữ trạng thái chưa kiểm.

## API admin (`hometv/api.py`)

Dùng `require_admin` và quy tắc JSON-only (`415`) đã áp cho `/api/admin`.

| Method | Route | Việc |
|---|---|---|
| GET | `/api/admin/hometv/health/status` | `{mode, enabled, country}` từ `geo.current()` |
| GET | `/api/admin/hometv/sources` | thêm `health: {checked, alive, dead, unknown, checking}` vào mỗi nguồn; `checking` là cờ lượt kiểm đang chạy |
| POST | `/api/admin/hometv/sources/{id}/check` | `202`, đặt `next_check_at = now`. `409 {"detail": "Kiểm tra stream đang tắt (máy chủ không ở Việt Nam)"}` khi `enabled=False`. `404` nếu nguồn lạ, hoặc nguồn chưa duyệt/đang tắt (thông điệp "Nguồn chưa duyệt hoặc đang tắt") |
| GET | `/api/admin/hometv/sources/{id}/health?status=dead&limit=50` | `[{stream_id, channel_name, ok, error, fail_streak, width, height, score}]`; `status` là `dead` hoặc `all`, `limit` 1–200. Không trả URL stream |

`checked` là số stream của nguồn có dòng sức khỏe; `alive` là số có `ok=1`; `dead` là `ok=0`; `unknown` là stream hợp lệ chưa có dòng.

## Giao diện (nhóm "HomeTV" trong Cài đặt, `static/settings-hometv.js`)

- Hàng trạng thái trên danh sách nguồn: "Kiểm tra stream: bật (máy chủ ở VN)" hoặc "Kiểm tra stream: tắt (máy chủ ngoài Việt Nam)" từ `/health/status`; không xác định được quốc gia thì "tắt (chưa xác định vị trí máy chủ)".
- Dòng phụ của nguồn nối thêm "{alive}/{checked} sống"; có `checking` thì thêm "Đang kiểm tra…" và dùng lại bộ tải lại 3 giây hiện có cho tới khi tắt cờ.
- Thao tác mới **Kiểm tra ngay** sau **Làm mới ngay**, chỉ hiện với nguồn đã duyệt và đang bật, và ẩn (hoặc mờ) khi `enabled=False`.
- Thao tác mới **Xem stream chết** mở danh sách ngay trong dòng (tối đa 50 mục, mỗi mục "Tên kênh · lý do · chết {n} lần"). Rỗng thì "Không có stream chết".
- Chuỗi hiển thị gán bằng `textContent`; điều hướng bằng remote TV dùng lại lưới hiện có.

## Xử lý lỗi

| Tình huống | Xử lý |
|---|---|
| Playlist lỗi mạng, hết giờ, HTTP ≥ 400, không có `#EXTM3U` | Stream chết, `error` ghi lý do ngắn |
| Đoạn đầu không tải được hoặc rỗng | Chết, "Không tải được đoạn" |
| `ffprobe` không có luồng video, lỗi, hoặc quá 15 giây | Chết, "Không đọc được video"; tiến trình bị kill khi hết giờ |
| Lỗi bất ngờ khi kiểm một stream | Bắt ở mức từng stream, ghi chết với lý do chung; các stream khác tiếp tục |
| Nguồn bị gỡ duyệt hoặc tắt giữa lúc kiểm | Bỏ qua các stream còn lại của nguồn, không ghi kết quả |
| Stream bị xóa khi nạp lại playlist | `stream_health` tự xóa theo `ON DELETE CASCADE` |
| Không dò được quốc gia | `enabled=False` (mode `auto`); không kiểm, không ẩn kênh |
| Máy chủ chuyển từ VN sang ngoài VN | `enabled=False` sau lần dò kế tiếp; dữ liệu cũ bị bỏ qua, kênh hiện lại |
| Tất cả stream bị chấm chết vì mạng nghẽn | Kênh ẩn sau 3 lần liên tiếp; một lần sống là hiện lại; admin thấy lý do ở "Xem stream chết" |
| `hometv.db` hỏng | Như gói 2: log, HomeTV tắt, HomeFlix vẫn chạy |

## Kiểm thử

Viết test trước (TDD). Gọn, một người dùng; không mạng thật, không chạy `ffprobe` thật ở test đơn vị (`probe` và `detect_country` được tiêm giả).

- `tests/hometv/test_hometv_geo.py`: `detect_country` đọc cả hai định dạng, rơi sang nguồn thứ hai khi nguồn đầu lỗi, `None` khi cả hai lỗi; `refresh_mode` cho `auto` với `VN`/`MY`/`None`, `on`, `off`, giá trị lạ; kết quả cũ quá 24 giờ thì `enabled=False`.
- `tests/hometv/test_hometv_scoring.py`: các bậc điểm, chết = 0, giới hạn 100.
- `tests/hometv/test_hometv_health.py`: `check_stream` với upstream giả: playlist hỏng, master chọn variant, đoạn rỗng, hết giờ, không có luồng video, stream `direct`, `ffprobe` giả trả JSON mẫu.
- `tests/hometv/test_hometv_health_store.py`: `fail_streak` tăng và về 0; ẩn kênh khi mọi stream `fail_streak >= 3`, hiện lại sau một lần sống; `use_health=False` thì bỏ qua dữ liệu; `/streams` sắp theo điểm, chưa kiểm xếp ở 50; xóa stream thì xóa dòng; migrate v2 lên v3.
- `tests/hometv/test_hometv_health_scheduler.py`: chọn đúng nguồn đến hạn, bỏ qua nguồn chưa duyệt/tắt, không chạy khi `enabled=False`, tối đa 4 song song, `next_check_at` đặt lại, duyệt nguồn thì đến hạn ngay, hủy sạch.
- `tests/hometv/test_hometv_api.py` (thêm): `health` trong danh sách nguồn; `POST .../check` `202`, `409` khi tắt, `404` nguồn lạ/chưa duyệt; `GET .../health` lọc `dead`, không lộ URL; `GET /health/status`.
- `tests/ui/test_settings_hometv.py` (thêm): hàng trạng thái, dòng phụ "x/y sống", "Đang kiểm tra…", nút Kiểm tra ngay chỉ hiện đúng chỗ, danh sách stream chết.
- Một test dùng `ffprobe` thật trên clip 1 giây sinh bằng `ffmpeg` để chắc phần phân tích JSON khớp binary trên image; bỏ qua nếu máy test thiếu `ffprobe`.

## Ngoài phạm vi

- EPG, xuất M3U/XMLTV (gói 5).
- Lịch sử kiểm, biểu đồ, cảnh báo, thông báo.
- Đo độ ổn định dài hạn (jitter, rớt gói) và sửa tay điểm.
- Chặn SSRF và giới hạn tài nguyên cho đường tải của bộ kiểm (hoãn, chung danh sách với gói 3).
- Tự thử lại ngay khi người xem gặp stream chết (client đã tự chuyển stream dự phòng ở gói 3).
- Stream chỉ có audio (radio): tính là chết ở gói này.
