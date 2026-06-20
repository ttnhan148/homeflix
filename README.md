# 🎬 HomeFlix: M3U8 Turbo Proxy Player

Dự án **M3U8 Turbo Proxy Player (HomeFlix)** là một ứng dụng phát video và proxy các luồng HLS (M3U8) tích hợp cơ chế cache phân đoạn `.ts` thông minh theo phiên làm việc (session) và cache playlist M3U8. Hệ thống giúp tối ưu hóa băng thông truyền tải, chống nghẽn tải trùng (Single Flight pattern) và vượt tường lửa/hạn chế anti-leech từ máy chủ nguồn.

---

## ⚙️ Tính Năng Nổi Bật

- **Proxy & Rewrite M3U8/TS**: Tải danh sách phát, phân tích cú pháp và viết lại tất cả đường dẫn nội bộ (sub-playlists, key giải mã, segment `.ts`) hướng về Proxy Server.
- **Single Flight Lock**: Khi nhiều yêu cầu cùng tải chung một segment `.ts`, hệ thống chỉ tải 1 luồng duy nhất từ nguồn chính và phân phối trực tiếp tới các luồng khác đang đợi.
- **Pass-through Stream**: Vừa tải từ nguồn vừa truyền dữ liệu trực tiếp cho Client mà không cần đợi tải xong hoàn chỉnh.
- **Playlist Cache (VOD)**: Cache danh sách phát M3U8 trong vòng 60 phút giúp nhiều thiết bị khác nhau mở cùng 1 bộ phim sử dụng chung 1 tập hợp Token đã xác thực thành công, tránh bị từ chối truy cập (anti-leech).
- **Tự động Dọn dẹp Cache**: Cơ chế dọn dẹp chạy ngầm định kỳ để duy trì tổng dung lượng cache dưới 10GB và xóa sạch các session cũ quá 6 giờ.
- **Giao diện Modern & Cinema (Outfit Font)**: Giao diện tối hiện đại, đồng bộ phông chữ Outfit (tương tự Netflix Sans), tối ưu hóa riêng cho Smart TV/Máy chiếu (10-foot UI với Remote D-Pad) và Điện thoại di động.

---

## 🚀 Hướng Dẫn Cài Đặt & Cập Nhật trên Linux (Ubuntu)

### 1️⃣ Cài đặt mới hoàn toàn (Fresh Install)

Chạy các lệnh sau bằng quyền root để tải và cấu hình toàn bộ hệ thống lên server:

```bash
# 1. Clone mã nguồn từ GitHub về thư mục ~/homeflix
git clone https://github.com/ttnhan148/homeflix.git ~/homeflix

# 2. Di chuyển vào thư mục dự án
cd ~/homeflix

# 3. Cấp quyền thực thi và chạy script cài đặt tự động
sudo chmod +x install.sh
sudo ./install.sh
```

*Tập lệnh `install.sh` sẽ tự động cài đặt Python, venv, khởi tạo thư mục chạy `/var/www/homeflix`, cấu hình dịch vụ Systemd `homeflix.service` và mở cổng tường lửa `6969`.*

---

### 2️⃣ Cập nhật phiên bản mới nhất (Update)

Khi bạn đẩy code mới lên GitHub và muốn cập nhật mã nguồn chạy trên máy chủ:

```bash
# 1. Di chuyển vào thư mục dự án trên server
cd ~/homeflix

# 2. Xóa bỏ các thay đổi tạm thời để tránh xung đột
git reset --hard

# 3. Kéo code mới nhất từ GitHub
git pull

# 4. Cấp quyền thực thi và chạy script cập nhật tự động
sudo chmod +x update.sh
sudo ./update.sh
```

---

## 🛠️ Lệnh Quản Lý Dịch Vụ

Sau khi cài đặt hoặc cập nhật thành công, bạn có thể quản lý ứng dụng thông qua Systemd:

- **Xem trạng thái hoạt động**:
  ```bash
  sudo systemctl status homeflix
  ```
- **Khởi động lại dịch vụ**:
  ```bash
  sudo systemctl restart homeflix
  ```
- **Theo dõi logs trực tiếp (Real-time logs)**:
  ```bash
  sudo journalctl -u homeflix -f
  ```
