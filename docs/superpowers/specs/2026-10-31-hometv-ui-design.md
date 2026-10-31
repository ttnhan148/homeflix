# HomeTV — Giao diện xem kênh — Design Spec

> **Ngày:** 2026-10-31
> **Trạng thái:** Chờ review spec
> **Thuộc dự án:** HomeTV (gói 3/5). Gói 1: `docs/superpowers/specs/2026-10-31-settings-admin-design.md`. Gói 2: `docs/superpowers/specs/2026-10-31-hometv-iptv-ingest-design.md`

## Mục tiêu

Người xem chuyển từ HomeFlix sang HomeTV bằng một icon cạnh logo, duyệt kênh theo nhóm hoặc tìm theo tên, và xem kênh ngay trong trang. Mỗi kênh có thể có nhiều stream từ nhiều nguồn; player tự thử lần lượt cho tới khi phát được. Giao diện dùng lại ngôn ngữ thiết kế của lưới phim HomeFlix và điều khiển được bằng remote TV.

Server dev nằm ở UK nên nhiều CDN Việt Nam từ chối kết nối (xem `cdn-geoblock-vn-only`). Image sẽ build và chạy trên VPS Việt Nam; gói này không tìm cách vượt geo-block, chỉ phải hoạt động đúng khi đường mạng thông.

## Quyết định đã chốt

| Chủ đề | Quyết định |
|---|---|
| Cách phát | Mọi stream đi qua proxy của HomeFlix, kể cả `https`. Không phát trực tiếp từ trình duyệt |
| Vị trí UI | Thêm mode HomeTV vào trang `index.html` hiện có, code JS mới ở `static/hometv.js` |
| Dữ liệu người dùng | Yêu thích và kênh xem gần đây lưu trong `hometv.db`, theo cả hộ gia đình (giống `/api/saved`) |
| Quyền xem | Chỉ kênh có stream `active`, thuộc nguồn `approved` và đang bật |
| DRM, xác thực | Bỏ stream `drm_hint=1`. Không bao giờ dùng key hay vượt xác thực |
| Phạm vi UI | Lọc theo nhóm, tìm kiếm, yêu thích, xem gần đây, đổi kênh bằng remote TV |

## Proxy phát trực tiếp (route mới)

`/proxy/m3u8` và `/proxy/ts` hiện có **không dùng được** cho HomeTV: nhận URL tuỳ ý từ client (không chặn SSRF), không gắn header theo stream, và `/proxy/ts` ghi từng đoạn xuống đĩa cho mỗi session, sẽ làm đầy đĩa với truyền hình trực tiếp chạy liên tục. Gói này thêm cặp route riêng, không đổi hành vi của cặp cũ:

- `GET /api/hometv/play/{stream_id}/index.m3u8`: tải playlist của stream (qua `netguard` ở mỗi lần kết nối và chuyển hướng), viết lại mọi URI bên trong thành `/api/hometv/play/{stream_id}/seg?u=<token>`. Master playlist, media playlist, key và map đều được viết lại.
- `GET /api/hometv/play/{stream_id}/seg?u=<token>`: stream thẳng từ upstream về client, **không ghi đĩa**. `<token>` là URL tuyệt đối do server ký (HMAC với khoá của app) và chỉ hợp lệ cho đúng `stream_id` đó; client không tạo được token cho URL tuỳ ý.
- Server tải upstream với `http_headers` của stream (`user_agent`, `referrer`). Client không thấy header này.
- `stream_id` phải tồn tại, `active=1`, `drm_hint=0`, nguồn `approved` và đang bật; ngược lại `404`. Kiểm tra mỗi lần, không cache.
- Giới hạn: timeout kết nối 10 giây, đoạn tối đa 20 MB, tối đa 5 lần chuyển hướng, mỗi phản hồi playlist tối đa 2 MB.
- Chỉ nhận `Content-Type` của HLS (`application/vnd.apple.mpegurl`, `audio/mpegurl`, `video/MP2T`, `application/octet-stream`, `video/*`). Khác thì `502`.
- Stream không phải HLS (ví dụ `.ts` hay `.mp4` trực tiếp) đi qua `seg` với token của chính URL đó; chỉ stream HLS được viết lại.

## API công khai (`hometv/public_api.py`)

Gắn bằng `APIRouter` giống `hometv/api.py`. Không cần quyền admin. Chạy cùng cơ chế thiết bị/hộ gia đình mà HomeFlix đang dùng cho `/api/saved`.

| Method | Route | Việc |
|---|---|---|
| GET | `/api/hometv/channels` | `[{id, name, category, logo, favorite, recent_at}]`; mỗi kênh phải có ít nhất một stream hợp lệ |
| GET | `/api/hometv/channels/{id}/streams` | `[{id, play_url, quality_hint}]` theo thứ tự thử; `404` nếu không còn stream hợp lệ |
| PUT | `/api/hometv/favorites/{id}` | `204`; idempotent |
| DELETE | `/api/hometv/favorites/{id}` | `204`; idempotent |
| POST | `/api/hometv/recent/{id}` | `204`; ghi lượt xem, giữ 20 kênh mới nhất |

- `name` là `display_name` tính lúc đọc từ `channels` theo nguồn ưu tiên cao nhất đang có stream hợp lệ.
- `logo` là `/proxy/image?url=...` (URL logo gốc không bao giờ trả thẳng). Logo rỗng thì để `null`.
- `play_url` là `/api/hometv/play/{stream_id}/index.m3u8`. URL gốc của stream, `http_headers` và `drm_hint` không nằm trong phản hồi.
- Thứ tự stream: ưu tiên nguồn cao hơn trước, rồi `url` `https` trước `http`. Gói 4 sẽ thêm điểm sức khỏe vào thứ tự này.
- Các route `PUT/DELETE/POST` cần `Content-Type: application/json` hoặc không có thân; đồng bộ với quy tắc JSON-only của app (`415`).
- `hometv.db` không mở được: `503`, HomeFlix vẫn chạy.
- Bảng mới trong `hometv.db` (migrate `schema_version`): `favorites(channel_id PK, created_at)` và `recents(channel_id PK, viewed_at)`; cả hai `FOREIGN KEY ... ON DELETE CASCADE` tới `channels`.
- `/api/hometv/*` phải có trong danh sách route cho phép của middleware cookie thiết bị (xem chú thích ở `app.py` về `/proxy`, `/media`, `/static`). Route `play` không cấp cookie, giống `/proxy`.

## Giao diện

### Chuyển mode

- Nút icon HomeFlix ⇄ HomeTV ngay sau `.brand` trong `appHeader`. Bấm thì `body` đổi class `mode-tv` (HomeTV) và lưu lựa chọn trong `localStorage`, bọc `try/catch` giống đoạn truy cập `localStorage` đã có (Safari chế độ riêng tư).
- Mode HomeTV ẩn `.tabs-nav` của phim và hiện `#hometv-root` do `static/hometv.js` dựng. Cast và Cài đặt giữ nguyên chỗ.
- Nút mode ẩn khi `/api/hometv/channels` trả `503`.
- `/channels` trả rỗng: hiện "Chưa có kênh nào." Nếu người xem là admin thì thêm "Duyệt nguồn trong Cài đặt → HomeTV."

### Lưới kênh

- Hàng trên cùng: ô tìm và các chip nhóm. Chip đầu tiên là "Yêu thích" và "Xem gần đây" (chỉ hiện khi có dữ liệu), rồi các nhóm có kênh theo thứ tự National TV, News, Sports, Kids, Movies, Music, Entertainment, International, Local TV, General, Unknown (nhãn tiếng Việt hiển thị là chuỗi cố định trong `hometv.js`).
- Tìm kiếm chạy ở client trên danh sách đã tải: bỏ dấu tiếng Việt (`đ` thành `d`), không phân biệt hoa thường, khớp chuỗi con.
- Render theo lô 60 thẻ, tải thêm khi cuộn tới cuối, dùng dòng "Đang tải thêm" đã có của lưới phim.
- Mọi chuỗi từ API (tên, nhóm) gán bằng `textContent`; logo gán qua thuộc tính `src`. Không dùng `innerHTML` với dữ liệu từ API.

### Ngôn ngữ thiết kế

Dùng lại CSS của lưới phim trong `static/hf.css`, không thêm màu mới:

- Biến màu `--ground`, `--surface`, `--text`, `--faint`, `--accent` và `--font`.
- Cấu trúc thẻ như `.movie-card`: không viền, không đổ bóng, nền trong suốt; hover chỉ `brightness(1.12)`. Khung logo là `.poster-wrap` bo 6 px, nền `--surface`, nhưng tỷ lệ `16 / 9` (khác phim là `2 / 3`), logo `object-fit: contain` có đệm.
- Tên kênh dùng `.movie-title` (14 px, hai dòng; TV 20 px, một dòng cắt bằng dấu …); dòng phụ dùng `.movie-meta` (tên nhóm).
- Lưới dùng `.movies-grid` (`minmax(150px, 1fr)`; TV `minmax(200px, 1fr)`); riêng mobile dùng 2 cột để logo đủ lớn.
- Trạng thái chờ dùng `.skeleton-card` (khối phẳng, `hf-pulse`, không shimmer), khối ảnh đổi sang `16 / 9`.
- Chip nhóm dùng kiểu `.filter-option`. Nút ★ là icon nhỏ ở góc khung logo, nền tối trong suốt, không viền.
- Focus trên TV dùng `body.device-tv :focus-visible` đã có (viền trắng 4 px).
- Phần CSS mới (ô `16 / 9`, nút ★, vị trí chip) là một khối nhỏ trong `hf.css`, đặt cạnh khối lưới phim.

### Player và stream dự phòng

- Chọn kênh thì mở player toàn màn hình, gọi `/channels/{id}/streams`, rồi thử lần lượt từng `play_url`.
- Tách phần tạo `Hls` trong `attachSource` (`templates/index.html`) thành hàm dùng chung đặt trong `static/player-utils.js` (`HFPlayerUtils`) để HomeTV gọi lại; không sao chép cấu hình. Hành vi player của phim không đổi.
- Một stream coi là hỏng khi: hls.js báo lỗi fatal, hoặc 8 giây chưa có frame đầu (`loadeddata`). Hỏng thì chuyển stream kế và hiện toast "Đang thử nguồn {i}/{n}".
- Hết stream: "Kênh tạm thời không phát được" và nút Thử lại (thử lại từ stream đầu).
- Phát thành công thì gọi `POST /recent/{id}`.
- `404` từ `/streams` hoặc `play`: "Kênh không còn khả dụng", tải lại danh sách kênh.

### Remote TV

- Lưới: mũi tên di chuyển giữa chip và thẻ kênh, Enter mở kênh, phím `f` (hoặc nút màu của remote) bật/tắt ★.
- Player: ↑/↓ đổi kênh theo thứ tự đang hiển thị (cùng nhóm hoặc bộ lọc), Esc về lưới. Đổi kênh có độ trễ 400 ms (debounce) để bấm nhanh không tạo hàng loạt request.
- Dùng lại logic điều hướng lưới trong `static/settings.js`. Nếu nó gắn chặt vào Cài đặt thì tách phần chung thành `static/tv-nav.js`, và `settings.js` gọi lại; hành vi Cài đặt không đổi.

## Xử lý lỗi

| Tình huống | Xử lý |
|---|---|
| `/channels` lỗi mạng hoặc 5xx | "Không tải được danh sách kênh" và nút Thử lại |
| `/channels` trả `503` | Ẩn nút mode HomeTV |
| Kênh hết stream hợp lệ (nguồn bị gỡ duyệt giữa chừng) | `404` ở `/streams`; báo "Kênh không còn khả dụng", tải lại danh sách |
| Stream hỏng hoặc quá 8 giây | Chuyển stream kế; hết thì "Kênh tạm thời không phát được" |
| `play` với `stream_id` không hợp lệ, `drm_hint=1`, nguồn chưa duyệt hoặc đã tắt | `404` |
| `seg` với token sai chữ ký hoặc sai `stream_id` | `403` |
| URL upstream bị `netguard` chặn | `502`; không thử lại |
| Upstream timeout, lỗi mạng, `Content-Type` lạ | `502`; log ngắn, không lộ URL gốc ra client |
| `localStorage` bị chặn | Không nhớ được mode, mặc định HomeFlix, không báo lỗi |

## Kiểm thử

Viết test trước (TDD). Gọn, một người dùng; không gọi mạng thật (upstream giả bằng `httpx.MockTransport`, DNS giả qua hàm phân giải của `netguard`).

- `tests/hometv/test_public_api.py`: chỉ trả kênh thuộc nguồn `approved` và đang bật; stream `drm_hint=1` bị bỏ; phản hồi không chứa URL gốc, `http_headers`, `drm_hint`; thứ tự stream; yêu thích và gần đây (thêm, xoá, idempotent, giữ 20); `503` khi DB hỏng; `404` khi kênh hết stream.
- `tests/hometv/test_play_proxy.py`: playlist được viết lại về route `seg` với token ký; `seg` stream thẳng và không ghi đĩa; `stream_id` lạ hoặc nguồn chưa duyệt `404`; token sai hoặc của stream khác `403`; gắn đúng UA và referrer; địa chỉ nội bộ bị chặn `502`; vượt giới hạn kích thước `502`.
- `tests/ui/test_hometv.py`: nút đổi mode và nhớ lựa chọn; lưới hiện kênh; chip nhóm; tìm không dấu; ★ lưu và hiện ở "Yêu thích"; stream đầu lỗi thì chuyển stream 2 và có toast; hết stream hiện thông báo và nút Thử lại; remote TV (mũi tên, Enter, `f`, ↑/↓ đổi kênh).
- `tests/ui/test_hometv_style.py`: thẻ kênh dùng đúng class `.movie-card`, `.poster-wrap`, `.movie-title`, `.movie-meta`; không có màu mới ngoài biến của `hf.css`.

## Ngoài phạm vi

- Kiểm tra sức khỏe, đo chất lượng và chấm điểm stream (gói 4).
- EPG, giờ phát, lịch, xuất M3U/XMLTV (gói 5).
- Phát kênh lên TV bằng cast (cast hiện chỉ biết phim).
- Sửa tên, đổi nhóm, bật/tắt và gộp kênh bằng tay.
- DRM và stream cần xác thực: bỏ qua, không bao giờ dùng key.
- Vượt geo-block: server chạy ở Việt Nam, không thêm cơ chế riêng.

## Việc vận hành (ngoài gói này)

- `TRUSTED_PROXIES` ở production vẫn chưa đặt nên mọi client trông như IP của nginx. Yêu thích và gần đây theo hộ gia đình nên không bị ảnh hưởng, nhưng nên đặt trước khi chạy trên VPS Việt Nam.
