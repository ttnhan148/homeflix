# HomeTV — Thu thập nguồn IPTV — Design Spec

> **Ngày:** 2026-10-31
> **Trạng thái:** Chờ review spec
> **Thuộc dự án:** HomeTV (gói 2/5). Gói 1 (Cài đặt và quyền admin) đã xong: `docs/superpowers/specs/2026-10-31-settings-admin-design.md`

## Mục tiêu

Admin quản lý danh sách nguồn playlist IPTV ngay trong trang Cài đặt. Server tự tải các playlist theo chu kỳ, phân tích chúng, chuẩn hóa tên kênh và gộp các stream của cùng một kênh từ nhiều nguồn vào một sổ kênh. Gói 3 (giao diện HomeTV), gói 4 (kiểm tra và chấm điểm stream) và gói 5 (EPG, xuất file) dựng tiếp trên sổ kênh này.

Gói này chạy được trên server hiện tại: playlist nằm trên GitHub và jsDelivr, không bị chặn theo vùng.

## Quyết định đã chốt

| Chủ đề | Quyết định |
|---|---|
| Vị trí code | Package `hometv/` trong repo này, chạy chung tiến trình với HomeFlix, route gắn qua `APIRouter` |
| Lưu trữ | File SQLite riêng `hometv.db` trong `DATA_DIR`, tách khỏi `homeflix.db` |
| Lịch chạy | Vòng lặp asyncio như các worker đang có; không thêm APScheduler |
| Giao diện admin | Chỉ quản lý nguồn. Sửa và gộp kênh bằng tay để gói sau |
| Duyệt quyền phát | Theo từng nguồn: Chờ duyệt, Đã duyệt, Từ chối. Nguồn mới luôn là Chờ duyệt |
| API công khai | Chưa có ở gói này. Gói 3 thêm, và chỉ trả kênh thuộc nguồn Đã duyệt và đang bật |
| DRM, xác thực | Chỉ ghi nhận dấu hiệu (`drm_hint`), không bao giờ dùng key hay vượt xác thực |

## Package `hometv/`

Mỗi file một việc, không phụ thuộc FastAPI trừ `api.py`:

- `db.py`: mở `hometv.db` (WAL, `PRAGMA foreign_keys=ON`), tạo bảng, migrate theo `schema_version`, khóa ghi.
- `netguard.py`: kiểm tra URL và địa chỉ đích (chặn SSRF).
- `fetcher.py`: tải một playlist có giới hạn kích thước, thời gian, số lần chuyển hướng.
- `m3u.py`: phân tích Extended M3U thành danh sách mục.
- `normalize.py`: chuẩn hóa tên, nhận diện đài, phân nhóm, nhãn chất lượng.
- `registry.py`: nhập một bản playlist vào sổ kênh trong một transaction.
- `sources.py`: thêm, sửa, xoá nguồn; nguồn mặc định; quy tắc giữ bản tốt; lịch thử lại.
- `scheduler.py`: vòng lặp `hometv_refresher()`, khóa lượt tải, dọn dẹp.
- `api.py`: `APIRouter` các route admin.

`app.py` chỉ thêm: gọi `hometv.db.init()` lúc khởi động, chạy `hometv_refresher()` trong `lifespan`, và `app.include_router(...)`.

## Dữ liệu (`hometv.db`)

- `schema_version(version INTEGER)`.
- `sources`: `id TEXT PRIMARY KEY` (slug sinh từ tên, ví dụ `iptv-org-vn`), `name`, `url`, `priority INTEGER` (1–100, số nhỏ là ưu tiên cao), `enabled INTEGER`, `rights_status TEXT` (`pending_review` | `approved` | `rejected`), `refresh_interval INTEGER` (giây), `timeout INTEGER` (giây, mặc định 30), `max_size_mb INTEGER` (mặc định 20), `last_fetch_at`, `last_success_at`, `last_error TEXT`, `content_hash TEXT`, `channel_count INTEGER`, `stream_count INTEGER`, `retry_count INTEGER`, `next_fetch_at REAL`, `created_at`, `updated_at`.
- `source_versions`: `id`, `source_id` (FK, xoá theo nguồn), `content_hash`, `fetched_at`, `entry_count`, `body BLOB` (gzip). Giữ 5 bản mới nhất mỗi nguồn; bản mới nhất là bản tốt gần nhất.
- `channels`: `id INTEGER PRIMARY KEY`, `tvg_id TEXT` (đã viết thường, có index), `normalized_name TEXT` (có index), `broadcaster TEXT`, `display_name TEXT`, `category TEXT`, `category_source TEXT` (`auto` | `manual`), `logo_url TEXT`, `enabled INTEGER`, `created_at`, `updated_at`.
- `streams`: `id INTEGER PRIMARY KEY`, `channel_id` (FK), `source_id` (FK, xoá theo nguồn), `url TEXT`, `url_hash TEXT` (SHA-256, có index, duy nhất theo cặp `source_id`, `url_hash`), `protocol TEXT` (`http` | `https`), `http_headers TEXT` (JSON: `user_agent`, `referrer`), `display_name TEXT` (tên gốc ở nguồn), `quality_hint TEXT`, `drm_hint INTEGER`, `first_seen_at`, `last_seen_at`, `active INTEGER`. Gói 4 thêm cột sức khỏe.
- `jobs`: `id`, `job_type`, `source_id`, `status` (`running` | `ok` | `error`), `started_at`, `completed_at`, `duration_ms`, `records_processed`, `skipped_lines`, `error_message`.

## Nguồn mặc định

Chỉ thêm khi bảng `sources` đang trống (lần khởi động đầu). Admin xoá nguồn nào thì nguồn đó không tự quay lại. Tất cả: Chờ duyệt, đang bật, chu kỳ 6 giờ.

| id | Tên | URL | Ưu tiên |
|---|---|---|---|
| `doubo-vn` | Doubo Vietnam | `https://cdn.jsdelivr.net/gh/streamline-iptv/iptv-playlists@dist/countries/vn.m3u` | 10 |
| `iptv-org-vn` | IPTV Org Vietnam | `https://iptv-org.github.io/iptv/countries/vn.m3u` | 20 |
| `dangdvd-iptv` | dangdvd IPTV | `https://raw.githubusercontent.com/dangdvd/dangdvd.github.io/main/IPTV.m3u` | 30 |
| `khanh71-http` | khanh71 HTTP | `https://raw.githubusercontent.com/khanh71/All-In-One-IPTV/main/http-iptv.m3u` | 50 |

`vuquangngoc/iptv_vietnam` không có playlist nên không thêm. `khanh71-iptv` (`iptv.m3u8`, ưu tiên 40) bị bỏ: 78/78 kênh là `udp://` nên luôn báo "Playlist hỏng".

## Tải playlist

- Chỉ nhận `http` và `https`, URL tối đa 2048 ký tự.
- Chặn SSRF (`netguard.py`): phân giải DNS ngay trước mỗi lần kết nối, kể cả ở mỗi lần chuyển hướng. Từ chối nếu bất kỳ địa chỉ nào là private, loopback, link-local, multicast, reserved, unspecified hoặc CGNAT (`100.64.0.0/10`). Quy tắc này phủ cả dải mạng Docker. Lúc thêm hoặc sửa nguồn cũng kiểm tra một lần. Khoảng hở rất ngắn giữa lúc phân giải và lúc kết nối (DNS rebinding) được chấp nhận vì chỉ admin thêm được URL.
- Giới hạn: timeout theo nguồn (mặc định 30 giây), tối đa 5 lần chuyển hướng, dừng đọc khi vượt `max_size_mb`.
- So `content_hash` (SHA-256) với bản tốt gần nhất. Trùng thì chỉ cập nhật `last_fetch_at` và `last_success_at`, không nhập lại.
- **Không bao giờ thay bản tốt bằng bản hỏng.** Bản mới bị bỏ qua (giữ bản tốt, ghi `last_error`) khi: không có dòng `#EXTM3U`, không có mục nào, hoặc số mục giảm quá 50% so với bản tốt gần nhất.
- Tải lỗi: kênh và stream đã nhập giữ nguyên. Bản mới hợp lệ mà thiếu một stream cũ: stream đó `active=0`, không xoá.

## Phân tích M3U (`m3u.py`)

- Đọc `#EXTINF`: thời lượng, các thuộc tính `tvg-id`, `tvg-name`, `tvg-logo`, `group-title` (cho phép thuộc tính trong nháy kép, nháy đơn hoặc không có nháy), tên hiển thị sau dấu phẩy cuối cùng.
- `#EXTVLCOPT:http-user-agent=` và `#EXTVLCOPT:http-referrer=` gắn vào mục đứng sau, lưu vào `http_headers`.
- `#KODIPROP` có `license_key` hoặc `license_type`: đặt `drm_hint=1`, không lưu key.
- Dòng URL đầu tiên sau `#EXTINF` là URL của mục. Dòng bắt đầu bằng `#` mà không nhận ra thì bỏ qua. Dòng hỏng thì bỏ qua và đếm vào `skipped_lines`.
- Chấp nhận BOM UTF-8, xuống dòng CRLF; giải mã UTF-8 với `errors="replace"`.

## Chuẩn hóa và gộp kênh (`normalize.py`, `registry.py`)

Chuẩn hóa tên để so khớp (tên gốc vẫn giữ để hiển thị):

1. Unicode NFKC, viết thường, bỏ dấu tiếng Việt (`đ` thành `d`).
2. Bỏ hậu tố chất lượng ở cuối hoặc trong ngoặc: `hd`, `fhd`, `sd`, `uhd`, `4k`, `1080p`, `1080i`, `720p`, `576p`, `480p`, `backup`.
3. Bỏ dấu câu, gộp khoảng trắng, nối chữ với số liền sau: `vtv 1` thành `vtv1`.

Ví dụ: `VTV1 HD`, `VTV1 FHD`, `VTV 1` thành `vtv1`; `HTV1 (720p)` thành `htv1`; `HTV Thể Thao HD` thành `htv the thao`.

Nhận diện đài (`broadcaster`) theo thứ tự: (1) tiền tố của tên đã chuẩn hóa khớp bảng đài quốc gia `vtv`, `htv`, `vtc`, `thvl`, `vov`, `antv`, `qpvn`, `vnews`, `sctv`, `vtvcab`, `hplus`, `vinh long`; (2) tên chứa một trong 63 tỉnh/thành (danh sách không dấu đặt trong `normalize.py`) thì đài là tên tỉnh đó; (3) `group-title` đã bỏ emoji khớp bảng đài quốc gia (ví dụ `🇻🇳| SCTV`). Không khớp gì thì `broadcaster` rỗng.

Gộp mục vào kênh theo thứ tự (không bao giờ gộp chỉ vì trùng tên):

1. `tvg-id` trùng (viết thường, bỏ khoảng trắng). Bỏ qua id giả: rỗng, `no_epg`, `none`, `null`, `undefined`.
2. `normalized_name` trùng **và** cùng `broadcaster` (khác rỗng).
3. Không khớp gì: tạo kênh mới. Mục không xác định được đài thì chỉ khớp với kênh có cùng `tvg-id`, hoặc với kênh cùng tên do **chính nguồn đó** tạo ra lần trước.

Kênh đã có thì cập nhật `logo_url` (nếu đang trống) và `display_name` theo nguồn ưu tiên cao nhất đang có stream. `category_source=manual` thì không bao giờ ghi đè `category`.

Phân nhóm tự động (`category_source=auto`): bỏ emoji và tiền tố `xx| ` khỏi `group-title`, rồi so từ khóa không dấu theo thứ tự:

| Từ khóa | Nhóm |
|---|---|
| `dia phuong`, hoặc `broadcaster` là một tỉnh/thành | Local TV |
| `vtv`, `htv`, `vtc`, `quoc gia`, đài thuộc bảng đài quốc gia | National TV |
| `tin tuc`, `news`, `vnews` | News |
| `the thao`, `sport`, `bong da`, `football` | Sports |
| `hoat hinh`, `thieu nhi`, `kids`, `cartoon` | Kids |
| `phim`, `movie`, `cinema` | Movies |
| `am nhac`, `music`, `radio` | Music |
| `giai tri`, `entertainment`, `kham pha`, `discovery` | Entertainment |
| `quoc te`, `international`, tên nước trong danh sách tên nước thường gặp của `normalize.py` (`nga`, `my`, `han quoc`, `nhat ban`, `trung quoc`, `phap`, `anh`, `duc`, `uc`, `thai lan`, `russia`, `usa`, `korea`, `japan`, `china`, `france`, `uk`) | International |
| `general` | General |
| còn lại | Unknown |

`quality_hint` lấy từ hậu tố đã bỏ ở bước chuẩn hóa (`HD`, `FHD`, `1080p` …). Đây chỉ là nhãn tham khảo, không bao giờ dùng làm độ phân giải thật; độ phân giải thật do gói 4 đo.

Mỗi lượt nhập một nguồn là **một transaction**: lỗi thì hoàn tác cả lượt, sổ kênh không bao giờ bị cập nhật dở. Kênh không còn stream nào (sau khi xoá nguồn) thì xoá.

## Lịch chạy (`scheduler.py`)

- `hometv_refresher()` chạy trong `lifespan`. Cứ 60 giây chọn các nguồn đang bật có `next_fetch_at <= now`.
- Tối đa 2 lượt tải song song; mỗi host 1 lượt một lúc.
- Khóa: lượt tải ghi `jobs.status='running'` cho nguồn đó. Đã có lượt `running` mới hơn 10 phút thì bỏ qua; cũ hơn thì coi là chết và lấy lại.
- Tải thành công: `retry_count=0`, `next_fetch_at = now + refresh_interval`.
- Tải lỗi: thử lại sau 60, 300, rồi 900 giây (theo `retry_count`), cộng thêm ngẫu nhiên 0–20%. Hết 3 lần thì `retry_count=0` và quay về `now + refresh_interval`.
- "Làm mới ngay" và nguồn vừa thêm: `next_fetch_at = now`.
- Dọn mỗi ngày: giữ 5 `source_versions` mỗi nguồn, xoá `jobs` cũ hơn 30 ngày.

## API (`hometv/api.py`)

Mọi route dùng `require_admin` của app (`404` khi admin tắt, `403` khi không phải admin) và quy tắc JSON-only (`415`) đã áp cho `/api/admin`.

| Method | Route | Việc |
|---|---|---|
| GET | `/api/admin/hometv/sources` | `[{id, name, url, enabled, priority, rights_status, refresh_hours, last_fetch_at, last_success_at, last_error, channel_count, stream_count, fetching}]`, sắp theo `priority` rồi `name` |
| POST | `/api/admin/hometv/sources` | `{name, url}` → nguồn mới (`201`) |
| PATCH | `/api/admin/hometv/sources/{id}` | `{name?, url?, enabled?, priority?, refresh_hours?, rights_status?}` → nguồn sau khi sửa |
| POST | `/api/admin/hometv/sources/{id}/refresh` | `202`; đặt `next_fetch_at = now` |
| DELETE | `/api/admin/hometv/sources/{id}` | `204`; xoá nguồn, bản đã lưu, stream; xoá kênh mồ côi |

Kiểm tra đầu vào (`422 {"errors": {field: "lý do"}}`): tên 1–60 ký tự; URL hợp lệ theo `netguard`; `priority` 1–100; `refresh_hours` 1–168; `rights_status` thuộc ba giá trị. Đổi URL thì xoá `content_hash` và đặt `next_fetch_at = now`. Id lạ: `404`.

## Giao diện (nhóm "HomeTV" trong Cài đặt)

Thay dòng `hometv.iptv` ("Nguồn IPTV · có ở bản sau") bằng danh sách nguồn. Dùng lại các hàm dựng dòng, `every()`, `close()`, lưới điều hướng TV và kiểu dòng thiết bị trong `static/settings.js`.

- Mỗi nguồn một dòng `.set-source[data-id]`: tên; dòng phụ = trạng thái duyệt ("Chờ duyệt", "Đã duyệt", "Từ chối") · "{n} kênh" · thời gian cập nhật (`lastSeenText`). Có `last_error` thì dòng phụ là "Lỗi: {lỗi} (giữ bản cũ)" màu `--danger`. Nguồn tắt thì mờ và ghi "Đã tắt". Đang tải thì ghi "Đang tải…".
- Chạm mở các thao tác, xếp dọc như dòng thiết bị: **Duyệt** hoặc **Bỏ duyệt**, **Từ chối**, **Bật** hoặc **Tắt**, **Làm mới ngay**, **Sửa** (tên và URL sửa ngay trong dòng; ưu tiên và chu kỳ dùng nút (−)(+)), **Xoá** (màu `--accent`, `confirm("Xoá nguồn {tên}? Các kênh chỉ có ở nguồn này sẽ mất.")`).
- Dòng cuối "+ Thêm nguồn" mở form ngay trong dòng: Tên, URL, nút Thêm. Lỗi `422` hiện dưới ô tương ứng.
- Có nguồn `fetching` thì tải lại danh sách mỗi 3 giây cho tới khi không còn nguồn nào đang tải.
- `403` thì dựng lại trang như các nhóm admin khác.

## Xử lý lỗi

| Tình huống | Cách xử lý |
|---|---|
| Timeout, lỗi mạng, HTTP ≥ 400 | Giữ bản tốt; `last_error` = "Hết thời gian chờ", "Không kết nối được", "HTTP {mã}"; thử lại theo lịch |
| Quá `max_size_mb` | Ngắt tải; "Quá {n} MB"; giữ bản tốt |
| Không có `#EXTM3U` hoặc không có mục nào | "Playlist hỏng"; giữ bản tốt |
| Số mục giảm quá 50% | "Mất quá nửa số kênh"; giữ bản tốt |
| URL bị chặn (lúc tải) | "Địa chỉ bị chặn"; không thử lại tới khi URL đổi |
| URL bị chặn (lúc thêm/sửa) | `422` |
| Dòng lạ trong file | Bỏ qua, đếm `skipped_lines` |
| Lỗi khi nhập vào sổ kênh | Hoàn tác transaction; "Lỗi nhập dữ liệu"; giữ bản tốt; log chi tiết |
| `hometv.db` hỏng hoặc không mở được | Log lỗi, vòng lặp HomeTV không chạy, HomeFlix vẫn chạy bình thường |

## Kiểm thử

Viết test trước (TDD). Không gọi mạng thật: upstream giả bằng `httpx.MockTransport`; DNS giả bằng cách thay hàm phân giải của `netguard`.

- `tests/hometv/test_m3u.py`: thuộc tính có và không có nháy; tên có dấu phẩy; `#EXTVLCOPT`; `#KODIPROP` → `drm_hint`; BOM, CRLF; dòng hỏng được đếm. Dùng các file mẫu nhỏ cắt từ dữ liệu thật trong `tests/fixtures/hometv/`.
- `tests/hometv/test_normalize.py`: `VTV1 HD`/`VTV1 FHD`/`VTV 1` → `vtv1`; `HTV1 (720p)` → `htv1`; bỏ dấu; nhận diện đài; phân nhóm khi `group-title` có emoji; `quality_hint`.
- `tests/hometv/test_registry.py`: gộp theo `tvg-id`; gộp theo tên + đài; không gộp khi không rõ đài; bỏ qua `no_epg`; stream biến mất → `active=0`; `category_source=manual` không bị ghi đè; lỗi giữa lượt nhập thì hoàn tác.
- `tests/hometv/test_netguard.py`: IP private, loopback, link-local, CGNAT, IPv6 nội bộ; chuyển hướng vào IP nội bộ; scheme khác `http(s)`.
- `tests/hometv/test_fetch.py`: các quy tắc giữ bản tốt; giới hạn kích thước; hash trùng thì không nhập lại; lịch thử lại (60/300/900 giây, jitter trong 0–20%).
- `tests/hometv/test_scheduler.py`: chọn đúng nguồn đến hạn; khóa chặn lượt trùng; khóa chết sau 10 phút được lấy lại; tối đa 2 lượt và 1 lượt mỗi host; dọn dẹp.
- `tests/hometv/test_api.py`: `404` khi admin tắt, `403` khi không phải admin, `415`; kiểm tra đầu vào; thêm, sửa, xoá (xoá kênh mồ côi); làm mới trả `202`; nguồn mặc định chỉ thêm một lần.
- `tests/ui/test_settings_hometv.py`: nhóm HomeTV hiện nguồn mặc định; thêm nguồn; duyệt; bật/tắt; làm mới thì danh sách tự tải lại tới khi xong; xoá có xác nhận; lỗi hiện màu `--danger`; mọi dòng đi tới được bằng remote TV.

## Ngoài phạm vi

- Kiểm tra sức khỏe, đo chất lượng và chấm điểm stream (gói 4).
- API công khai và giao diện xem kênh, icon chuyển HomeFlix ⇄ HomeTV (gói 3).
- EPG, xuất M3U/XMLTV và metadata (gói 5).
- Sửa tên, đổi nhóm, bật/tắt và gộp kênh bằng tay.
- Phát qua relay cho stream `http` trên site `https` (gói 3/4).
