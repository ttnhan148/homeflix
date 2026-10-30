# Phiên theo thiết bị, giao diện iOS 26 và cast từ iPhone lên TV — Design Spec

> **Ngày:** 2026-10-29
> **Trạng thái:** Chờ review spec
> **Prototype đã duyệt:** https://sharehtml.zhenjia.dev/s/seQBVUB3y9 (kèm các chỉnh sửa ở mục "Thay đổi so với prototype")

## Mục tiêu

Người trong nhà dùng HomeFlix trên iPhone và TV/máy chiếu mà không cần đăng nhập. Mỗi thiết bị nhớ danh sách lưu và tiến độ xem của riêng nó. Giao diện dùng chung ngôn ngữ iOS 26 (Liquid Glass). iPhone duyệt phim và bấm Phát lên TV cùng mạng, theo cơ chế giống cast của YouTube.

## Quyết định đã chốt

| Chủ đề | Quyết định |
|---|---|
| Định danh thiết bị | Cookie ID thiết bị do server cấp. IP chỉ dùng để gom thiết bị cùng nhà, không dùng để định danh. |
| Tủ phim | Giữ nút Lưu. Danh sách lưu và tiến độ xem riêng từng thiết bị. Không còn tủ phim dùng chung. |
| TV | Dùng như bình thường, không có màn hình chờ, không có nút "phát tiếp" từ thiết bị khác. |
| Cast | Chỉ iPhone có nút cast. TV nhận lệnh, iPhone thành điều khiển. |
| Điều khiển từ iPhone | Phát/dừng, tua ±10 giây, đổi tập, bật/tắt tự qua tập và bỏ intro, ngắt kết nối. Không có D-pad. |
| Trang chủ | Phim hot chiếu rạp làm hero (poster phủ nền), thanh lọc dính ở giữa trang. |
| Bộ lọc | Loại phim + Thể loại + Quốc gia + Năm. Mặc định "Tất cả" (dùng `/api/home/latest`). |
| Đã bỏ | Hẹn giờ tắt, tủ phim chung, banner phát tiếp trên TV, xem tiếp từ thiết bị khác. |

## Chia gói

Mỗi gói có plan và vòng test riêng. Làm theo thứ tự vì gói sau dựa vào gói trước.

1. **Nền tảng thiết bị** (backend): ID thiết bị, nhóm "nhà", lưu và tiến độ theo thiết bị, vị trí xem theo giây.
2. **Giao diện iOS 26** (iPhone và TV): hero, thanh lọc, vùng an toàn, quy tắc 3 chạm.
3. **Cast**: thiết bị cùng nhà, lệnh điều khiển, bảng điều khiển trên iPhone.
4. **Tự động hóa trình phát**: tự qua tập, bỏ qua mở đầu.

## Gói 1 — Nền tảng thiết bị

### Định danh

- Middleware đọc cookie `hf_device` (UUIDv4). Nếu thiếu, server tạo mới và gửi `Set-Cookie` với `HttpOnly; SameSite=Lax; Path=/; Max-Age=63072000` (2 năm), thêm `Secure` khi request đi qua HTTPS.
- Chỉ cấp cookie ở `GET /` và các route `/api/saved*`, `/api/devices*`, `/api/cast*`. Không cấp ở `/proxy/*`, `/media/*`, `/static/*`.
- Cookie do server đặt nên không bị giới hạn 7 ngày của Safari áp cho cookie ghi bằng JavaScript. App thêm vào màn hình chính của iOS có kho cookie riêng, nên tính là thiết bị riêng. Người dùng phân biệt bằng tên thiết bị.
- Mất cookie (xóa dữ liệu Safari) tạo thiết bị mới. Dữ liệu cũ của thiết bị đó bị bỏ lại và bị dọn sau 180 ngày không hoạt động.

### Nhóm "nhà" (household)

- `household = sha256(salt + ip_chuẩn_hóa)[:16]`, với `salt` lưu trong DB, sinh một lần.
- Lấy IP: dùng `request.client.host`. Chỉ khi nó nằm trong biến môi trường `TRUSTED_PROXIES` mới đọc hop đầu của `X-Forwarded-For`. Mặc định không tin header này.
- Chuẩn hóa: IPv6 cắt về /64. Địa chỉ riêng (RFC1918, link-local) quy về hằng `lan`, vì khi server đặt trong mạng nhà thì mọi máy có IP LAN khác nhau nhưng cùng nhà.
- Hạn chế đã biết:
  - Mạng di động dùng CGNAT: người lạ có thể chung IP công khai. Xem mục Bảo mật.
  - iPhone dùng IPv6 còn TV dùng IPv4 thì khác nhà. Chưa xử lý ở phiên bản này.

### Dữ liệu

- `devices(id PK, name, kind, household, user_agent, last_seen, created_at)`. `kind` suy ra từ User-Agent và client gửi lên: `phone`, `tv`, `desktop`. Tên mặc định "iPhone", "TV", "Máy tính". Người dùng đổi tên được.
- `device_movies(device_id, slug, name, poster_url, year, episode_current, total_episodes, last_watched_episode, last_watched_url, position_sec, duration_sec, episodes, episode_states, saved, updated_at, PRIMARY KEY(device_id, slug))`.
  - `saved = 1`: phim nằm trong danh sách lưu.
  - Có `last_watched_*` hoặc `position_sec`: phim nằm trong "Tiếp tục xem".
- `saved_movies` (bảng chung cũ): không còn được đọc. Thiết bị đầu tiên được tạo sau khi nâng cấp nhận toàn bộ dòng cũ làm danh sách lưu của nó, để không mất dữ liệu. Bảng được giữ nguyên, không xóa.
- `episode_downloads` giữ nguyên, vẫn dùng chung vì file MP4 là tài nguyên của server.

### API

Các route hiện có giữ đường dẫn, đổi phạm vi theo thiết bị trong cookie:

| Route | Thay đổi |
|---|---|
| `GET /api/saved` | Trả phim của thiết bị hiện tại (`saved=1` hoặc có tiến độ). |
| `POST /api/saved` | Lưu phim cho thiết bị hiện tại. |
| `POST /api/saved/progress` | Thêm `position` và `duration` (giây). Client gửi mỗi 10 giây khi đang phát, khi tạm dừng và khi rời trang (`sendBeacon`). |
| `DELETE /api/saved/{slug}` | Bỏ lưu cho thiết bị hiện tại. |
| `GET /api/device` | Thông tin thiết bị hiện tại. |
| `PATCH /api/device` | Đổi tên. |

Các route này trả `Cache-Control: private, no-store` và `Vary: Cookie`. Middleware `api_cache_headers` hiện có đặt `public` cho `/api/home/*`, `/api/search`, `/api/movie/*`. Những route đó không phụ thuộc thiết bị nên giữ nguyên.

## Gói 2 — Giao diện iOS 26

### Ngôn ngữ thiết kế

- Chất liệu kính: nền mờ có `backdrop-filter`, viền sáng mảnh, bóng mềm. Tab bar nổi dạng viên thuốc, nút tìm kiếm tách riêng. Chip và nút bo tròn hoàn toàn. Phông hệ thống (`-apple-system`, SF Pro).
- Dùng chung token màu và độ mờ giữa iPhone và TV. TV dùng cỡ chữ từ 20px, viền tiêu điểm trắng 4px.
- Lùi về nền mờ đặc khi trình duyệt không hỗ trợ `backdrop-filter`.

### Trang chủ iPhone

- **Hero:** poster phim hot chiếu rạp phủ kín nền màn hình, kéo lên dưới thanh trạng thái. Phủ lớp chuyển sắc tối từ dưới lên. Chữ nằm đè lên poster: nhãn "Đang chiếu rạp", tên phim, năm · thể loại · quốc gia, mô tả 2–3 dòng, nút **Phát** và **Lưu**. Vuốt ngang để đổi giữa 5 phim hot. Poster lấy qua `/proxy/image` (đã có cache đĩa). Mô tả lấy từ `/api/movie/{slug}` (đã có cache), tải trước cho phim đang hiển thị và phim kế bên.
- **Thanh lọc:** dính ngay dưới hero khi cuộn. Hàng trên là loại phim (Tất cả, Chiếu rạp, Phim lẻ, Phim bộ, TV Shows). Hàng dưới là Thể loại, Quốc gia, Năm, mỗi chip mở một tờ chọn dạng viên thuốc, chạm giá trị là lọc ngay. Chip đang lọc đổi màu. Nút "Xóa lọc" hiện khi có lọc. Dùng lại `/api/home/categories`, `/api/home/countries` và tham số lọc của spec `2026-07-31-homepage-filters-design.md`.
- **Lưới kết quả:** 3 cột, có số phim khớp.
- **Tab:** Khám phá, Tủ phim, và nút Tìm kiếm tách riêng.

### Trang chủ TV

Hero poster phủ ngang, thanh lọc dạng chip điều hướng bằng D-pad, hàng kết quả cuộn ngang. Chip lọc mở menu kính, chọn là lọc ngay, Esc đóng. Bố cục như prototype.

### Vùng an toàn (iPhone 15 Pro Max trở lên)

Thiết bị mục tiêu: 15 Pro Max và 16 Pro Max trở lên, viewport 430×932 và 440×956 điểm, có Dynamic Island. `templates/index.html` đã có `viewport-fit=cover`. Thiết kế dựa vào `env(safe-area-inset-*)`, không đặt cứng số điểm ảnh.

- **Dọc:** inset trên khoảng 59–62pt (Dynamic Island và giờ), inset dưới 34pt (thanh Home). Nội dung và nút đặt dưới inset trên. Tab bar nổi đặt trên inset dưới cộng 8pt. Hero được phép kéo tràn dưới thanh trạng thái, nhưng không đặt chữ hay nút trong dải Dynamic Island.
- **Ngang (xem toàn màn hình):** inset trái/phải khoảng 59pt ở phía Dynamic Island, inset dưới 21pt. Thanh tiến độ, nút phát/dừng, Bỏ qua intro và hộp đếm ngược tập kế đều nằm trong vùng an toàn.
- **Chạm:** vùng chạm tối thiểu 44×44pt. Không đặt thao tác vuốt ở mép dưới cùng, nơi iOS dùng cho cử chỉ về Home.
- **Kiểm thử:** prototype đổi khung sang 430×932 có Dynamic Island và hiển thị dải inset để kiểm tra bố cục. Kiểm thử thật trên thiết bị hoặc Simulator ở cả hai kích thước.

### Quy tắc 3 chạm

Từ lúc mở app, xem toàn màn hình không quá 3 chạm, không tính chạm bộ lọc:

- Hero: **Phát** = 1 chạm. Tự tiếp tục từ vị trí đã lưu.
- Thẻ phim trên trang chủ: thẻ → **Phát** = 2 chạm.
- Phim bộ chọn tập cụ thể: thẻ → **Phát** hoặc tập = 2–3 chạm.
- Nút **Phát** nằm cố định ở cùng vị trí trên trang chi tiết, trong tầm ngón cái.
- Bấm Phát vào phát toàn màn hình ngay. Lệnh vào toàn màn hình phải chạy trong cùng sự kiện chạm vì iOS yêu cầu thao tác người dùng. Mã hiện tại đã có `video.webkitEnterFullscreen()`, tái sử dụng.
- Khi đã kết nối TV, **Phát** gửi thẳng lên TV, số chạm không đổi. App nhớ TV đã kết nối lần trước và tự nối lại nếu TV vẫn trực tuyến.

## Gói 3 — Cast

### Nguyên tắc

TV và iPhone đều là client của server. Không có kết nối trực tiếp giữa hai thiết bị. Server chuyển lệnh trong cùng nhà. Cả hai thiết bị đã dùng chung cookie ID, nên không cần đăng nhập.

### Thiết bị cùng nhà

- Mọi thiết bị gửi `POST /api/devices/heartbeat` mỗi 15 giây, kèm `kind` và tên. Server cập nhật `last_seen`.
- `GET /api/devices/nearby`: trả thiết bị cùng `household` với người gọi, có `kind = tv` hoặc `desktop`, đã heartbeat trong 40 giây gần nhất. iPhone dùng danh sách này cho nút cast.

### Kênh lệnh

- Thiết bị nhận (TV) mở `GET /api/cast/events` bằng `EventSource` (SSE). Server giữ hàng đợi theo `device_id` trong bộ nhớ.
- Lệnh: `play {slug, episode_url, position}`, `pause`, `resume`, `seek {delta | position}`, `episode {delta}`, `options {autoNext, autoIntro}`, `stop`.
- Controller gửi `POST /api/cast/{target_id}/command`. Server kiểm tra controller và target cùng `household`, nếu không trả 403.
- TV báo trạng thái `POST /api/cast/state {slug, episode, position, duration, playing}` mỗi 2 giây khi đang phát. Controller nhận trạng thái qua `GET /api/cast/state/{target_id}` hoặc SSE riêng để vẽ thanh tiến độ và nút phát/dừng.
- Khi TV phát một phim, tiến độ ghi vào `device_movies` của TV, không ghi vào iPhone.
- SSE gửi dòng chú thích `: ping` mỗi 15 giây và header `X-Accel-Buffering: no` để reverse proxy không giữ đệm.
- iOS ngắt kết nối nền khi khóa màn hình. iPhone nối lại SSE và lấy trạng thái mới khi sự kiện `visibilitychange` báo trang hiện lại.

### Giao diện iPhone

- Nút cast ở góc trên trang Khám phá và trên trang chi tiết. Có chấm xanh khi đã kết nối.
- Tờ "Phát lên" liệt kê thiết bị cùng nhà, mỗi thiết bị có trạng thái.
- Khi có phiên cast: nút Phát đổi thành "Phát lên TV". Thanh nhỏ phía trên tab bar hiện phim đang phát trên TV, chạm mở bảng điều khiển.
- TV hiện nhãn "iPhone của … đang kết nối". Khi iPhone ngắt, nhãn biến mất và TV giữ nguyên trạng thái.

### Giới hạn triển khai

Server chạy một tiến trình uvicorn (`install.sh` và `Dockerfile` không đặt `--workers`). Hàng đợi lệnh và danh sách thiết bị trực tuyến nằm trong bộ nhớ. Triển khai nhiều worker sẽ làm hỏng cast, và spec này không hỗ trợ trường hợp đó.

### Quyết định khi lập kế hoạch Gói 3

- Heartbeat chỉ gửi `kind`, không gửi tên: tên đã ở server (mặc định theo User-Agent, đổi bằng `PATCH /api/device`), gửi lại sẽ ghi đè tên người dùng đã đổi.
- `play` có `episode_url` và `position` tùy chọn: TV tự quyết tập và vị trí từ tiến độ riêng của nó, vì tiến độ ghi ở TV.
- Nhãn trên TV dùng tên thiết bị của controller ("iPhone đang kết nối"), vì server không biết tên người.
- Quyền lưu bền trong bảng `cast_allowed`; công tắc của TV ở `app_settings`. Liên kết, hiện diện và trạng thái chỉ ở bộ nhớ; lệnh hợp lệ tự lập lại liên kết.
- Id thiết bị là cookie đăng nhập nên không bao giờ gửi cho thiết bị khác: mọi route và sự kiện cast dùng id công khai `HMAC(salt, id)[:20]`, server chỉ giải ngược trong phạm vi cùng nhà. Lệnh và yêu cầu kết nối chỉ tới tab mới nhất của TV; controller im lặng quá 40 giây bị gỡ khỏi TV; liên kết trong bộ nhớ được lập lại khi luồng sự kiện báo `hello` hoặc iPhone thức dậy.
- Khi lệnh phát lên TV thất bại, iPhone báo "TV không phản hồi" và đưa nút Phát về phát cục bộ nhưng không tự phát (phát bất đồng bộ làm mất cử chỉ người dùng trên iOS).

## Gói 4 — Tự động hóa trình phát

- **Tự qua tập:** hết tập thì đếm ngược 10 giây rồi phát tập kế, có nút Hủy. Cài đặt theo thiết bị.
- **Bỏ qua mở đầu:** `phimapi.com` không cung cấp mốc intro. Spec này không giả vờ phát hiện được intro. Nút "Bỏ qua mở đầu" nhảy tới mốc cố định (mặc định 85 giây, đặt được trong cài đặt) và chỉ hiện trong 90 giây đầu của tập. Prototype dùng cùng quy ước.

## Bảo mật và quyền riêng tư

- Dữ liệu lưu và tiến độ gắn với cookie ID của từng thiết bị, không thể đọc từ thiết bị khác.
- Cast dựa trên IP chung. Trên mạng di động CGNAT hoặc Wi-Fi công cộng, người lạ cùng IP công khai có thể thấy TV trong danh sách "gần đây". Biện pháp:
  - Lần đầu một controller kết nối vào TV, TV hiện hộp xác nhận "iPhone … muốn kết nối. Cho phép?". Sau khi cho phép, TV nhớ controller đó theo ID. Chỉ lần đầu mới cần.
  - TV có công tắc "Cho phép điều khiển từ thiết bị khác" trong cài đặt.
- Lệnh cast chỉ được chuyển khi controller đã được TV cho phép và hai bên cùng nhà.
- IP chỉ lưu dưới dạng băm kèm `salt`.

## Xử lý lỗi

| Tình huống | Hành vi |
|---|---|
| Cookie bị chặn hoặc xóa | Server cấp ID mới. Giao diện hiển thị thiết bị trống, không báo lỗi. |
| TV mất kết nối giữa phiên cast | iPhone hiện "TV không phản hồi", nút Phát quay về phát trên iPhone. |
| SSE đứt | Client tự nối lại sau 2 giây, thời gian chờ tăng dần tới 30 giây. |
| Không có thiết bị cùng nhà | Tờ "Phát lên" hiện "Không thấy TV nào cùng mạng", kèm gợi ý mở HomeFlix trên TV. |
| `phimapi.com` lỗi | Dùng cache cũ như hiện tại (stale-fallback). Hero rơi về danh sách phim lẻ đã cache. |

## Kiểm thử

Theo CLAUDE.md, làm theo TDD. Dự án hiện chưa có test, nên gói 1 thêm `pytest` và `httpx` (`TestClient`) vào `requirements-dev.txt`.

- **Gói 1:** cấp cookie lần đầu, giữ cookie lần sau; hai cookie khác nhau không thấy dữ liệu của nhau; chuẩn hóa IPv6 và IP riêng thành `lan`; chỉ tin `X-Forwarded-For` từ proxy trong danh sách; nhập dữ liệu cũ vào thiết bị đầu tiên.
- **Gói 3:** 403 khi khác nhà hoặc chưa được TV cho phép; lệnh tới đúng TV; heartbeat quá 40 giây thì TV biến mất khỏi danh sách.
- **Gói 2:** chụp ảnh bằng Playwright ở 430×932, 440×956 và 1920×1080; kiểm tra thủ công trên iPhone thật cho toàn màn hình, vùng an toàn và cast.

## Thay đổi so với prototype

- Khung iPhone đổi thành 430×932 có Dynamic Island và dải inset (trên 59pt, dưới 34pt).
- Hero trang chủ dùng poster phủ nền, chữ mô tả đè lên.
- Thêm xác nhận phía TV cho lần kết nối đầu tiên.
- Prototype dùng gradient thay poster thật. Poster thật lấy từ phimapi.com.

## Ngoài phạm vi

- Đăng nhập, mật khẩu, tài khoản người dùng.
- Ghép thiết bị bằng mã 4 số (chỉ cân nhắc nếu IPv6/IPv4 hoặc CGNAT gây phiền).
- D-pad điều khiển TV từ iPhone.
- Tìm kiếm trên TV bằng iPhone.
- Hẹn giờ tắt.
- Chạy nhiều worker uvicorn.
