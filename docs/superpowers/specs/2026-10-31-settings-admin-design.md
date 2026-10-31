# Trang Cài đặt và quyền admin — Design Spec

> **Ngày:** 2026-10-31
> **Trạng thái:** Chờ review spec
> **Thuộc dự án:** HomeTV (gói 1/5)

## Mục tiêu

Gom mọi cài đặt về một trang. Ai cũng chỉnh được cài đặt của máy mình. Cài đặt hệ thống, quản lý thiết bị và (về sau) nguồn IPTV chỉ admin mới chỉnh được. Đây là nền cho các gói HomeTV sau: nguồn IPTV, EPG và ngưỡng chấm điểm sẽ được thêm vào trang này.

## Dự án HomeTV: chia gói

Mỗi gói có spec, plan và vòng test riêng, làm theo thứ tự.

1. **Cài đặt và quyền admin** (spec này).
2. **Thu thập IPTV**: quản lý nguồn, tải và phân tích M3U, chuẩn hóa kênh, sổ kênh.
3. **HomeTV bản đầu**: icon chuyển HomeFlix ⇄ HomeTV cạnh logo, lưới kênh, trình phát có nguồn dự phòng.
4. **Kiểm tra và chấm điểm stream**: HTTP, HLS, segment, ffprobe, trạng thái sức khỏe, chọn nguồn chính và dự phòng.
5. **EPG và xuất file**: XMLTV, các file M3U cố định, metadata.

Ràng buộc chung cho cả dự án, đã chốt:

- Gói 4 cần server đặt ở Việt Nam. Server hiện tại ở Anh (85.211.182.136): khi thử 20 host IPTV, 12 host timeout, 1 host trả `403`. Chủ dự án sẽ chuyển HomeFlix sang một VPS ở Việt Nam. Gói 1–3 không phụ thuộc vào việc này.
- Không vượt DRM hay xác thực dưới bất kỳ hình thức nào. Stream có DRM hoặc cần đăng nhập chỉ được ghi trạng thái `DRM_PROTECTED` / `AUTH_REQUIRED` rồi loại khỏi playlist.
- Chỉ xuất stream thuộc nguồn đã được admin duyệt (`rights_status`). Mặc định nguồn ở trạng thái `pending_review`.

## Quyết định đã chốt

| Chủ đề | Quyết định |
|---|---|
| Phạm vi trang | Thiết bị này · Quản trị · Thiết bị trong nhà · Hệ thống · HomeTV (khung) |
| Lưu cấu hình | Khai báo trong `settings.py`, giá trị lưu ở bảng `app_settings`, có hiệu lực ngay |
| Mật khẩu admin | Biến môi trường `HOMEFLIX_ADMIN_PASSWORD`, không lưu trong DB |
| Lên admin | Nhập mật khẩu một lần trên mỗi máy, hoặc xin duyệt bằng mã 6 số từ một máy đã là admin |
| Lối vào | Icon bánh răng trên header, thay icon thanh trượt hiện tại |
| Cài đặt của máy | Giữ nguyên chỗ lưu hiện tại, chỉ gom về một trang |
| Lưu thay đổi | Tự lưu, không có nút Lưu |

## Backend

### Module mới

- `settings.py`: khai báo, đọc, ghi và kiểm tra cài đặt hệ thống. Không phụ thuộc FastAPI.
- `admin.py`: mật khẩu, khóa khi nhập sai, yêu cầu xin quyền admin. Không phụ thuộc FastAPI.
- `app.py`: chỉ thêm route, gọi vào hai module trên, và thay các hằng số viết cứng bằng `settings.get(...)`.

### Khai báo cài đặt (`settings.py`)

Mỗi cài đặt là một `Setting(key, label, type, default, min, max, step, unit, group)`. `type` gồm `int` và `bool`. Bản này chỉ có nhóm `system`:

| key | Nhãn | Mặc định | min–max | Bước | Đơn vị | Thay cho |
|---|---|---|---|---|---|---|
| `cache.max_gb` | Dung lượng tối đa | 10 | 1–500 | 1 | GB | `MAX_CACHE_SIZE` |
| `cache.max_age_hours` | Giữ cache | 6 | 1–168 | 1 | giờ | `MAX_CACHE_AGE` |
| `prefetch.concurrency` | Luồng tải trước | 4 | 1–16 | 1 | | `PREFETCH_CONCURRENCY` |
| `home.refresh_minutes` | Làm mới trang chủ | 10 | 5–120 | 5 | phút | `WARM_CACHE_INTERVAL` |

- Giá trị lưu trong `app_settings` với key dạng `setting:<key>`, value là JSON.
- `settings.get(key)` đọc từ bộ nhớ đệm trong tiến trình; bộ đệm được làm mới sau mỗi lần ghi. Key chưa có trong DB thì trả giá trị mặc định.
- Giá trị trong DB không đọc được, sai kiểu hoặc ngoài min–max: trả giá trị mặc định và ghi log cảnh báo một lần. Không bao giờ ném lỗi.
- `settings.update(dict)`: kiểm tra mọi key trước. Chỉ cần một key sai là không ghi gì. Ghi tất cả trong một transaction.

Cách mỗi cài đặt có hiệu lực:

- `cache.max_gb`, `cache.max_age_hours`: `prune_cache()` đọc lại ở mỗi lượt dọn (hằng giờ). Giảm dung lượng tối đa không xoá cache ngay. `/api/cache/status` cũng dùng `cache.max_gb`.
- `prefetch.concurrency`: khi đổi, tạo semaphore mới cho các lượt tải trước tiếp theo. Lượt đang chạy giữ semaphore cũ.
- `home.refresh_minutes`: trình làm mới trang chủ đọc lại trước mỗi lần chờ.

### Quyền admin

Dữ liệu:

- Bảng `devices` thêm cột `is_admin INTEGER NOT NULL DEFAULT 0`, thêm bằng `ALTER TABLE` trong `init_db()` chỉ khi cột chưa có.
- Bảng mới `admin_requests(id TEXT PRIMARY KEY, device_id TEXT NOT NULL, code TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL)`. `status` gồm `pending`, `approved`, `denied`. Yêu cầu hết hạn sau 10 phút.

Mật khẩu:

- Lấy từ `HOMEFLIX_ADMIN_PASSWORD`. Không đặt (hoặc rỗng) thì mọi tính năng admin tắt: `GET /api/settings` trả `admin_enabled: false`, mọi route `/api/admin/*` trả `404`, log in cảnh báo lúc khởi động.
- So mật khẩu bằng `hmac.compare_digest` trên SHA-256 của hai chuỗi. Không bao giờ log mật khẩu hay chuỗi người dùng nhập.
- Nhập sai 5 lần trong 15 phút thì khóa 15 phút, đếm riêng theo thiết bị và theo IP. Bộ đếm nằm trong bộ nhớ (app chạy một worker), mất khi khởi động lại.
- Đúng mật khẩu thì đặt `is_admin = 1` cho thiết bị đang gọi. Không tạo cookie mới: máy vẫn được nhận diện bằng cookie `hf_device` sẵn có.

Xin duyệt (cho TV):

1. TV gọi `POST /api/admin/requests`. Server tạo mã 6 số ngẫu nhiên (`secrets`), thay yêu cầu đang chờ cũ của chính thiết bị đó nếu có. Toàn hệ thống tối đa 20 yêu cầu chờ; quá thì trả `429`.
2. TV hiện mã và đếm ngược, gọi `GET /api/admin/requests/mine` mỗi 3 giây cho tới khi kết quả là `approved`, `denied` hoặc `expired`.
3. Máy admin thấy các yêu cầu đang chờ ở đầu nhóm "Thiết bị trong nhà" (tải lại mỗi 5 giây khi trang Cài đặt đang mở), kèm tên thiết bị và mã. Bấm Cho phép thì đặt `is_admin = 1` cho máy xin; bấm Từ chối thì đánh dấu `denied`.

Thu quyền:

- Admin thu được quyền admin của máy khác, và tự đăng xuất máy mình (`POST /api/admin/logout`).
- Server không cho thu quyền máy admin cuối cùng (`409`), trừ trường hợp chính máy đó tự đăng xuất.
- Có nút "Thu quyền mọi máy khác", dùng sau khi đổi mật khẩu.
- Đổi mật khẩu: sửa `.env` rồi deploy lại. Các máy đã là admin vẫn giữ quyền.

Bảo mật request:

- Thêm `/api/settings` và `/api/admin` vào `DEVICE_COOKIE_PATHS`.
- Chống CSRF: cookie `SameSite=Lax` nên trình duyệt không gửi cookie kèm POST, PATCH hay DELETE từ site khác. Thêm vào đó, mọi route ghi bắt buộc `Content-Type: application/json`, sai thì trả `415`.
- API không bao giờ trả id thiết bị thật, vì id đó chính là cookie đăng nhập. Mọi chỗ trả hoặc nhận id thiết bị đều dùng id công khai `_cast_public_id()`.
- `POST /api/cache/clear` (hiện không cần đăng nhập, giao diện chưa gọi) chuyển thành `POST /api/admin/cache/clear`. Route cũ bị bỏ.

### API

| Method | Route | Quyền | Việc |
|---|---|---|---|
| GET | `/api/settings` | mọi máy | `{admin_enabled, is_admin, groups:[{id, label, items:[{key, label, type, value, default, min, max, step, unit}]}]}`. Máy không phải admin nhận `groups: []` |
| PATCH | `/api/settings` | admin | Nhận `{key: value, …}`. Sai thì `422 {"errors": {key: "lý do"}}` và không lưu gì. Đúng thì trả lại như GET |
| POST | `/api/admin/login` | mọi máy | `{password}`. `204` nếu đúng, `401` nếu sai, `429` kèm `retry_after` nếu đang bị khóa |
| POST | `/api/admin/logout` | admin | Bỏ quyền của chính máy này |
| POST | `/api/admin/revoke-others` | admin | Thu quyền admin của mọi máy trừ máy đang gọi |
| POST | `/api/admin/requests` | mọi máy | Tạo yêu cầu, trả `{code, expires_at}` |
| GET | `/api/admin/requests/mine` | mọi máy | `{status}`: `pending`, `approved`, `denied`, `expired` hoặc `none` |
| GET | `/api/admin/requests` | admin | Danh sách yêu cầu đang chờ: `[{id, device:{id, name, kind}, code, expires_at}]` |
| POST | `/api/admin/requests/{id}/approve` | admin | Chỉ với yêu cầu đang `pending` và chưa hết hạn, không thì `409` |
| POST | `/api/admin/requests/{id}/deny` | admin | Như trên |
| GET | `/api/admin/devices` | admin | `[{id, name, kind, household, last_seen, is_admin, is_self}]`. `id` là id công khai; `household` là 8 ký tự đầu của mã nhà, chỉ dùng để gom nhóm |
| PATCH | `/api/admin/devices/{id}` | admin | `{name?, is_admin?}`. Thu quyền máy admin cuối cùng thì trả `409` |
| DELETE | `/api/admin/devices/{id}` | admin | Xoá thiết bị cùng Tủ phim và tiến độ xem của nó. Không xoá được chính máy đang gọi (`409`) |
| POST | `/api/admin/cache/clear` | admin | Như `/api/cache/clear` hiện tại |

Route admin trả `403` khi máy chưa là admin, trả `404` khi `admin_enabled` là false.

### Deploy

- `deploy/deploy.sh` build từ `git archive`, nên file `.env` không có trong bản build. Sửa: nếu có `$REPO/deploy/.env` thì copy sang `$SRC/deploy/.env` trước khi `docker compose build`.
- `deploy/docker-compose.yml` thêm `env_file: [{path: .env, required: false}]`.
- `.env` đã nằm trong `.gitignore`.

## Giao diện

### Lối vào và điều hướng

- Icon bánh răng thay `#castSettingsBtn` trên header, mở tab mới `settings-tab` qua `switchTab('settings-tab')`.
- Bỏ popup `#castSettings`; công tắc "Cho phép điều khiển từ xa" chuyển vào trang Cài đặt.
- Giữ panel `#playerPrefs` trong trình phát. Panel này và trang Cài đặt cùng đọc, ghi qua `HFPlayerAuto`; đổi ở một bên thì bên kia hiển thị đúng ở lần mở sau.
- Trang có nút ‹ ở đầu để quay về tab trước. Trên điện thoại, bấm bất kỳ nút nào ở thanh tab cũng thoát khỏi Cài đặt. Khi đang ở Cài đặt, thanh tab không tô sáng nút nào.

### Bố cục

- Danh sách theo nhóm: tiêu đề nhóm dùng kiểu `.sec-head`. Mỗi nhóm là một khối `var(--surface)` bo góc 20px, các dòng cách nhau bằng đường kẻ `var(--line)`, mỗi dòng cao tối thiểu `var(--tap)`.
- Công tắc nền trắng khi bật. Chữ phụ màu `var(--muted)`. Màu đỏ `var(--accent)` chỉ dùng cho thao tác nguy hiểm (Xoá cache, Xoá thiết bị, Thu quyền).
- Máy tính và TV: trang rộng tối đa 640px, căn giữa. TV dùng cỡ chữ lớn theo các quy tắc `body.device-tv` đang có.
- Nội dung trang dựng từ dữ liệu: nhóm "Hệ thống" sinh ra từ `groups` của `GET /api/settings`, không viết HTML riêng cho từng cài đặt.

Các nhóm, theo thứ tự:

1. **Thiết bị này**: Tên (chạm để sửa ngay trong dòng, 1–40 ký tự), Cho phép điều khiển từ xa, Tự qua tập, Tự bỏ qua mở đầu, Mở đầu kết thúc ở giây (10–300).
2. **Quản trị**:
   - Chưa là admin: "Đăng nhập admin" (mở ô mật khẩu ngay trong dòng) và "Xin duyệt từ máy admin" (hiện mã 6 số và đếm ngược 10 phút).
   - Đã là admin: "Đăng xuất admin" và "Thu quyền mọi máy khác".
   - `admin_enabled` là false: ẩn cả nhóm.
3. **Thiết bị trong nhà** (admin): yêu cầu đang chờ ở đầu, kèm nút Từ chối và Cho phép. Bên dưới là thiết bị gom theo nhà, mỗi dòng có tên, loại, lần dùng gần nhất và nhãn "admin". Chạm vào một thiết bị để mở các thao tác: đổi tên, cấp hoặc thu quyền admin, xoá (hỏi xác nhận, ghi rõ sẽ mất Tủ phim của máy đó).
4. **Hệ thống** (admin): thanh dung lượng cache đang dùng trên tối đa (đọc từ `/api/cache/status`, route này giữ công khai như cũ), 4 cài đặt số ở bảng trên, nút Xoá cache (hỏi xác nhận).
5. **HomeTV** (admin): một dòng "Nguồn IPTV · có ở bản sau".

### Tương tác

- Công tắc lưu ngay khi bấm. Cài đặt số dùng nút (−) và (+) (không dùng ô nhập), lưu sau 400ms kể từ lần bấm cuối. Trong lúc lưu, dòng đó hiện trạng thái đang lưu.
- Lưu lỗi (mạng, `422`, `403`): trả giá trị về như trước và hiện một dòng lỗi ngắn ngay dưới dòng đó. Gặp `403` thì tải lại trang Cài đặt (quyền admin vừa bị thu).
- TV: mọi dòng, công tắc và nút đều chọn được bằng remote. (−) và (+) là hai nút riêng, chọn bằng phím mũi tên như các nút khác.
- Không có chữ chạy, không có emoji, nhãn ngắn.

### Code giao diện

- `static/settings-utils.js`: hàm thuần, chạy được trong trình duyệt lẫn Node (cùng kiểu `home-utils.js`): định dạng giá trị và đơn vị, tăng/giảm theo bước có kẹp min–max, gom thiết bị theo nhà, định dạng "lần dùng gần nhất", đếm ngược.
- `static/settings.js`: dựng trang, gọi API, tự lưu.
- `templates/index.html`: thêm khung `#settings-tab`, đổi icon header, bỏ `#castSettings`, nạp hai file trên qua `asset()`.
- `static/hf.css`: kiểu cho danh sách nhóm, công tắc, nút (−)(+).

## Xử lý lỗi

| Tình huống | Cách xử lý |
|---|---|
| Giá trị trong DB hỏng hoặc ngoài giới hạn | Dùng mặc định, log cảnh báo một lần, app vẫn chạy |
| `PATCH` có key lạ hoặc sai kiểu | `422` kèm lý do theo key, không lưu gì |
| Đổi `prefetch.concurrency` khi đang tải | Lượt sau dùng semaphore mới |
| Giảm `cache.max_gb` | Lượt dọn kế tiếp mới cắt bớt |
| Yêu cầu admin hết hạn hoặc bị từ chối | TV hiện "Hết hạn, thử lại" hoặc "Bị từ chối" |
| Quá 20 yêu cầu chờ | `429` |
| Nhập sai mật khẩu quá 5 lần | `429` kèm `retry_after`, giao diện hiện thời gian còn phải chờ |
| Mất mạng khi lưu | Trả giá trị về như trước, báo lỗi tại dòng đó |

## Kiểm thử

Viết test trước (TDD), theo kiểu test đang có trong repo.

- `tests/test_settings_api.py`:
  - Đọc mặc định; lưu rồi đọc lại; kẹp min–max; `PATCH` có một key sai thì không key nào được lưu.
  - Giá trị hỏng trong DB trả mặc định.
  - Máy không phải admin nhận `groups: []` và bị `403` khi `PATCH`.
  - `prune_cache()` dùng `cache.max_gb` và `cache.max_age_hours` mới; trình làm mới trang chủ dùng `home.refresh_minutes` mới; `prefetch.concurrency` tạo semaphore mới.
- `tests/test_admin_api.py`:
  - Đăng nhập đúng và sai; khóa sau 5 lần sai, theo cả thiết bị lẫn IP.
  - Không đặt `HOMEFLIX_ADMIN_PASSWORD` thì mọi route admin trả `404`.
  - Xin duyệt: tạo, duyệt, từ chối, hết hạn, thay yêu cầu cũ, giới hạn 20.
  - Không thu quyền được máy admin cuối cùng; không xoá được chính mình.
  - API thiết bị không bao giờ chứa id thật trong response.
  - Route ghi thiếu `Content-Type: application/json` bị `415`.
  - `/api/cache/clear` cũ không còn.
- `tests/js/settings-utils.test.mjs`: các hàm thuần ở trên.
- `tests/ui/test_settings.py` (Playwright):
  - Icon bánh răng mở trang Cài đặt; máy thường chỉ thấy "Thiết bị này" và "Quản trị".
  - Đăng nhập xong thì hiện các nhóm admin.
  - Công tắc và (−)(+) lưu, còn nguyên sau khi tải lại trang; (−)(+) chỉ gửi một request sau 400ms.
  - TV xin duyệt, điện thoại cho phép, TV thành admin.
  - Điều hướng trên TV bằng phím mũi tên.
  - `#playerPrefs` và trang Cài đặt cùng trạng thái.
  - Lưu lỗi thì giá trị quay lại như cũ.
- Chụp màn hình trang Cài đặt ở 430×932, 440×956 và 1920×1080 (TV), xem lại bằng mắt.

## Ngoài phạm vi

- Nội dung thật cho nhóm HomeTV (gói 2–5).
- Icon chuyển HomeFlix ⇄ HomeTV (gói 3).
- Chuyển server về Việt Nam.
- Đưa cài đặt phát từ `localStorage` lên server.
- Đổi mật khẩu admin từ giao diện.
