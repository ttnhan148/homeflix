# Project Index: M3U8 Turbo Proxy Player (HomeFlix)

Dự án **M3U8 Turbo Proxy Player** là một ứng dụng phát video và proxy các luồng HLS (M3U8) tích hợp cơ chế cache phân đoạn `.ts` thông minh theo phiên làm việc (session), giúp tối ưu hóa băng thông truyền tải và chống nghẽn tải trùng (Single Flight pattern).

---

## 📂 Cấu trúc Thư mục & Tệp tin

```text
HomeFlix/
├── app.py                # Điểm khởi chạy FastAPI & chứa logic proxy, cache, API dọn dẹp
├── requirements.txt      # Các thư viện Python phụ thuộc
├── install.sh            # Script tự động cài đặt và cấu hình Systemd trên Linux
├── update.sh             # Script tự động cập nhật phiên bản mới trên Linux
├── .gitignore            # Cấu hình bỏ qua các file tạm, cache và môi trường ảo
├── templates/
│   └── index.html        # Giao diện xem phim (HTML5 Video + HLS.js, giao diện Dark Theme)
└── static/
    ├── logo.png          # Logo ứng dụng & Apple Touch Icon
    └── manifest.json     # Cấu hình PWA (Progressive Web App)
```

---

## ⚙️ Các Thành phần Chính

### 1. Backend (`app.py`)
Sử dụng **FastAPI** không đồng bộ (asyncio) để xử lý các yêu cầu:
- **Proxy luồng M3U8 (`/proxy/m3u8`)**: Tải danh sách phát M3U8, phân tích cú pháp (Master hoặc Media Playlist) và viết lại (rewrite) tất cả các đường dẫn nội bộ (sub-playlists, segments `.ts`, key giải mã) hướng về phía Proxy của chúng ta.
- **Proxy đoạn TS (`/proxy/ts`)**:
  - Quản lý cache cục bộ tại thư mục `/cache/{session_id}/`.
  - **Single Flight Lock**: Khi nhiều yêu cầu cùng tải chung một segment `.ts`, hệ thống chỉ thực hiện 1 luồng tải từ nguồn chính và phân phối (Stream) tới các luồng khác đang đợi.
  - **Pass-through Stream**: Vừa tải từ nguồn vừa stream trực tiếp dữ liệu cho Client mà không cần đợi tải xong hoàn chỉnh.
- **Quản lý & Dọn dẹp Cache**:
  - Tự động chạy tác vụ ngầm định kỳ mỗi giờ để dọn dẹp cache quá hạn (> 6 giờ).
  - Giới hạn tổng dung lượng cache tối đa 10GB (nếu vượt quá sẽ xóa các tệp cũ nhất trước).
- **APIs**:
  - `/api/cache/status`: Trả về dung lượng cache hiện tại (GB, %).
  - `/api/cache/clear`: Xóa sạch toàn bộ cache ngay lập tức.
  - `/api/search`: Thực hiện tìm kiếm phim từ PhimAPI bằng từ khóa.
  - `/api/movie/{slug}`: Lấy thông tin chi tiết phim bao gồm danh sách tập phim và các link m3u8.
  - `/api/saved` (`GET`, `POST`): Quản lý danh sách tủ phim đã lưu trên server (lưu trữ tập tin `saved_movies.json`).
  - `/api/saved/{slug}` (`DELETE`): Xóa phim ra khỏi tủ phim lưu trên server.
  - `/api/saved/progress` (`POST`): Cập nhật tiến độ tập phim đang xem gần nhất để tự động phát tiếp khi quay lại (resume playback).

### 2. Frontend (`templates/index.html`)
Giao diện người dùng hiện đại, lấy cảm hứng từ phong cách điện ảnh (Netflix Red), tối ưu cho trải nghiệm xem phim:
- **Hệ thống Menu Tabs**:
  - **Nhập URL**: Trình phát video chính và khung dán link m3u8 thủ công (tự động giãn nở thông minh).
  - **Tìm Kiếm**: Tìm kiếm phim online từ kho dữ liệu phong phú, hiển thị chi tiết nội dung phim, chất lượng, thời lượng, và nút **PHÁT NGAY** ngắn gọn.
  - **Đã Lưu**: Quản lý lưu trữ các bộ phim ưa thích của người dùng trên máy chủ. Hiển thị trực tiếp danh sách tất cả tập phim kèm mã màu trực quan thể hiện trạng thái xem: Đang xem (Đỏ/Vàng), Đã xem (Xanh lá), và Chưa xem (Xám). Nhấn "XEM TIẾP" hoặc click trực tiếp vào bất kỳ tập nào sẽ tự động bắt đầu từ tiến trình xem gần nhất.
- **Trình phát Video**: Tự động phát hiện trình phát gốc (Safari/iOS) hoặc sử dụng **HLS.js** để phát mượt mà trên tất cả các trình duyệt hiện đại.
- **Trình phân tích Tập phim**: Hỗ trợ dán danh sách link (dạng `Tập 01|url.m3u8` hoặc chỉ cần dán chuỗi chứa URL) và tự động tạo danh sách tập phim nhanh chóng.
- **Theo dõi Cache**: Hiển thị thanh tiến trình dung lượng cache thời gian thực và nút dọn dẹp bộ nhớ đệm tiện lợi.
- **Khả năng PWA**: Sẵn sàng để "Thêm vào màn hình chính" trên iOS/Android nhờ tệp `manifest.json`.
- **Tối ưu hóa Máy chiếu & Smart TV (10-foot UI)**:
  - **Kích thước hiển thị lớn**: Tăng kích thước phông chữ, khoảng cách đệm và kích thước nút bấm để dễ đọc khi ngồi xa.
  - **Hỗ trợ Điều khiển từ xa (Remote D-Pad)**: Tích hợp `tabindex` động và xử lý bắt phím `Enter` để dễ dàng duyệt chọn các thẻ phim, nút bấm thông qua các phím điều hướng của điều khiển máy chiếu.
  - **Mượt mà và Ổn định**: Giảm thiểu các hiệu ứng phức tạp gây trễ (lag) đối với CPU máy chiếu yếu.
  - **Tối ưu Buffer HLS.js**: Điều chỉnh thông số bộ đệm HLS.js nhỏ gọn hơn nhằm chạy mượt mà ngay cả khi kết nối mạng chậm và RAM giới hạn của thiết bị máy chiếu.
- **Tự động Nhận diện Thiết bị (Dynamic Device Detection)**:
  - Sử dụng JavaScript để phân tích chuỗi User-Agent nhằm phân loại thiết bị: `device-tv` (Smart TV/Máy chiếu), `device-mobile` (iPhone/iPad/Android) hoặc `device-desktop` (Máy tính).
  - Tự động thay đổi cách bố trí menu, font chữ, kích cỡ hình ảnh và cấu hình HLS.js cho tương thích tối đa với từng thiết bị cụ thể.
  - Loại bỏ các hiệu ứng focus thừa trên thiết bị di động (để tối ưu hóa chạm vuốt) và bật viền vàng Neon bắt mắt trên TV/Máy chiếu (để tối ưu hóa dùng Remote D-Pad).
- **Chế độ Rạp Chiếu Phim (Theatre Mode - Màn hình phát duy nhất)**:
  - Khi bắt đầu phát video (từ link thủ công, tìm kiếm hoặc tủ phim), ứng dụng sẽ tự động ẩn đi Header, Thanh điều hướng (tabs-nav) và toàn bộ các tab nội dung.
  - Hiển thị duy nhất một màn hình phát phim trực quan gồm: Nút "Thoát Trình Phát", Video Player, Trạng thái phát và Danh sách các tập phim bên dưới.
  - Khi nhấn "Thoát Trình Phát", video sẽ được tạm dừng, giải phóng bộ nhớ và khôi phục lại trạng thái giao diện trước đó một cách mượt mà.

---

## 🚀 Hướng dẫn Cài đặt & Chạy ứng dụng

### Chạy cục bộ (Local Development)
1. Cài đặt các thư viện phụ thuộc:
   ```bash
   pip install -r requirements.txt
   ```
2. Chạy ứng dụng thông qua Uvicorn:
   ```bash
   uvicorn app:app --reload --host 0.0.0.0 --port 6969
   ```
3. Truy cập địa chỉ `http://localhost:6969` trên trình duyệt.

### Triển khai trên Linux Server (Sản xuất)
Chạy lệnh cài đặt tự động với quyền root:
```bash
sudo chmod +x install.sh
sudo ./install.sh
```
Script sẽ tự động:
- Cài đặt Python 3, pip, venv.
- Cấu hình thư mục dịch vụ tại `/var/www/homeflix`.
- Đăng ký và khởi chạy dịch vụ với **Systemd** (`homeflix.service`).
- Tự động cấu hình mở cổng tường lửa `6969`.

### 🔄 Hướng dẫn Cập nhật Phiên bản (trên Ubuntu/Linux)
Khi bạn cập nhật code mới cho ứng dụng đang chạy trên Ubuntu, hãy chạy tập lệnh cập nhật tự động có sẵn để đơn giản hóa quá trình:

1. **Chuẩn bị mã nguồn mới**: Tải hoặc copy các tệp mới (`app.py`, `templates/index.html`, `requirements.txt`, `update.sh`) vào thư mục tạm thời trên server.
2. **Cấp quyền và chạy script cập nhật**:
   ```bash
   sudo chmod +x update.sh
   sudo ./update.sh
   ```
   Tập lệnh `update.sh` sẽ tự động:
   - Dừng dịch vụ `m3u8player` cũ (nếu có).
   - Sao chép các tệp mã nguồn mới vào thư mục `/var/www/homeflix`.
   - Cài đặt/cập nhật các dependencies mới vào môi trường ảo `venv`.
   - Cấu hình và khởi chạy lại dịch vụ với systemd dưới tên dịch vụ mới là `homeflix.service`.

3. **Kiểm tra logs và trạng thái dịch vụ**:
   ```bash
   # Kiểm tra trạng thái dịch vụ
   sudo systemctl status homeflix
   # Xem log trực tiếp của ứng dụng
   sudo journalctl -u homeflix -f
   ```

