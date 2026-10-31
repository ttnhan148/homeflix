# HomeTV — Backlog sau gói 4 (ghi ngày 2026-10-31)

Gói 3 (giao diện) và gói 4 (kiểm tra stream) đã merge vào `main` và push lên `origin` (`aa83eac`). Chủ dự án chọn bản nhanh cho một người dùng, bỏ qua review cuối của gói 4 và hoãn phần gia cố. Tài liệu này là danh sách việc còn lại; mục A là phần chủ dự án định test vào cuối tuần 2026-11-07/08.

Trạng thái lúc ghi: `pytest -q` 991 pass, 1 skipped (test `ffprobe` thật, máy dev không có binary); `node --test tests/js/*.test.mjs` 47 pass. Production (docker `homeflix`) đã chạy `aa83eac`, `hometv.db` đã lên schema v3.

## A. Test cuối tuần (cần máy thật)

Cần server ở Việt Nam hoặc trình duyệt TV; môi trường dev (IP ngoài VN) không thử được.

1. **Bộ kiểm stream trên máy chủ VN.** Chuyển image sang VPS Việt Nam, vào Cài đặt → HomeTV: hàng trạng thái phải ghi "bật (máy chủ ở VN)". Duyệt một nguồn, bấm **Kiểm tra ngay**, chờ: dòng phụ hiện "Đang kiểm tra…" rồi "{sống}/{đã kiểm} sống".
2. **Xem stream chết.** Số liệu và lý do ("HTTP 403", "Không đọc được video"…) có hợp lý với stream thật không; mở thử vài stream bị chấm chết bằng player để so.
3. **Thứ tự stream và ẩn kênh.** Sau ít nhất 3 lượt kiểm (6 giờ một lượt, hoặc bấm tay 3 lần), kênh mà mọi stream đều chết phải biến mất khỏi lưới; một lượt sống thì hiện lại. Mở một kênh nhiều stream: stream điểm cao phải được thử trước.
4. **`ffprobe` thật.** Chạy `pytest tests/hometv/test_hometv_health.py -k real` trên máy có `ffmpeg`/`ffprobe` (hoặc trong container). Đã tự kiểm tay trong container `homeflix` (clip 320x240) nhưng chưa chạy được test tự động.
5. **Biến `HOMETV_HEALTH`.** Thử `on`, `off`, `auto` và giá trị lạ; xem hàng trạng thái và việc bật/tắt vòng lặp. Thử cắt mạng tới `ipinfo.io` để chắc bộ kiểm tắt (không xác định vị trí thì tắt).
6. **Stream thật khác dạng.** HLS có master playlist, stream `.ts` trực tiếp, playlist trả `Content-Type` lạ hoặc URL không đuôi, LL-HLS.
7. **Giao diện HomeTV trên TV thật.** Remote: lưới, Enter mở kênh, `f` bật yêu thích, ↑/↓ đổi kênh, Esc đóng. Trình duyệt TV cũ không có `IntersectionObserver` (xem C).
8. **Cast khi đang ở mode HomeTV** (phone cast phim lên TV đang xem kênh): hộp thoại cast phải nằm trên player, phim phát thì rời mode HomeTV.
9. **Máy thật với nhiều nguồn:** nguồn 500+ kênh, hiệu năng lưới, thời gian một lượt kiểm toàn bộ (tối đa 4 `ffprobe` song song).

## B. Review còn thiếu

- Review toàn nhánh gói 4 bị bỏ qua theo ý chủ dự án. Các hướng đáng soi nếu làm lại: `hometv/health.py` (chọn variant, hạn tải, kill `ffprobe`, đóng response), `hometv/health_scheduler.py` (khóa host kết hợp semaphore, cờ `checking` khi huỷ), `hometv/geo.py` (trạng thái auto/on/off, quá hạn 24 giờ), SQL ẩn kênh và sắp thứ tự trong `hometv/catalog.py`, `ALTER TABLE` trên DB production, `queuedChecks` trong `static/settings-hometv.js`.
- Review gói 3 đã làm: 4 lỗi quan trọng đã sửa.

## C. Việc nhỏ của gói 3 (minor, hoãn)

1. Esc ngay sau ↓ khi đang đổi kênh để lại `pendingId` cũ (lần đổi kênh sau đi sai).
2. Bỏ yêu thích trên TV (chip "Yêu thích") làm mất focus.
3. Chip "Xem gần đây" không hiện sau khi xem, phải tải lại.
4. Stream `direct` (`.ts`, `.mp4`) chưa bị ngắt khi đóng player (cần `video.load()` sau `removeAttribute('src')`).
5. Trình duyệt không có `IntersectionObserver` chỉ thấy 60 kênh đầu.
6. LL-HLS: `EXT-X-PART`, `PRELOAD-HINT`, rendition report, `SESSION-KEY` chưa được viết lại URI.
7. 503 có thể kẹt người dùng trong mode HomeTV (hiện chưa xảy ra vì server không còn trả 503).

## D. Gia cố và bảo mật (đã hoãn, làm một lượt)

Chi tiết trong spec `2026-10-31-hometv-ui-design.md` và `2026-10-31-hometv-health-design.md`; bản gọn trong memory `hometv-ui-hardening-backlog`.

- `/api/hometv/play/*`: token ký (HMAC, hết hạn 2 giờ), chặn SSRF bằng `netguard` ở mọi bước, allowlist Content-Type, giới hạn 2 MB playlist và 20 MB đoạn, timeout 10 giây, ánh xạ lỗi `502`/`403`. Hiện route nhận URL thẳng trong `u=` nên ai có `stream_id` hợp lệ dùng được làm relay.
- Bộ kiểm stream: cùng SSRF guard và giới hạn tài nguyên cho đường tải (`play.open_upstream`); `ffprobe` đọc dữ liệu không tin cậy (chỉ bị giới hạn bằng timeout 15 giây và file tạm 2 MB).
- `415` JSON-only cho `PUT/DELETE/POST /api/hometv/favorites|recent`; `503` khi `hometv.db` đóng.
- `/proxy/image` không chặn SSRF và redirect 307 tới URL gốc khi lỗi; logo kênh HomeTV đi qua route này.
- `fetcher.fetch_playlist_url` (gói 4, task 3) hiện chưa được dùng; nối lại cùng lúc với SSRF guard hoặc xoá.
- Vận hành: biến `TRUSTED_PROXIES` ở production chưa đặt nên mọi client trông như IP nginx.

## E. Khoảng trống test

- Gói 4 chưa có: gọi `ipinfo.io`/`api.country.is` thật; nhánh `HOMETV_HEALTH=on|off` trong vòng lặp (chỉ có test `parse_mode` và `refresh_mode`); đổi mode giữa chừng; thiếu `ffprobe` ở production; điều hướng remote TV cho hàng trạng thái và danh sách stream chết; stream có `referrer` thật.
- Gói 3 chưa có: nguồn bị gỡ duyệt giữa lúc đang phát; playlist trỏ vào IP nội bộ; token giả/hết hạn (cùng nhóm hoãn ở D); Esc trong lúc debounce; nút Cài đặt, cast trong mode HomeTV mới có test sau review.
- Các khoảng trống cũ của gói 1 và 2: memory `deferred-test-backlog`.

## F. Gói 5 (chưa bắt đầu)

EPG (XMLTV), giờ phát, xuất M3U/metadata. Chưa có spec.
