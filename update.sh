#!/bin/bash

# Kiểm tra quyền root
if [ "$EUID" -ne 0 ]; then 
  echo "Vui lòng chạy script với quyền root (sudo ./update.sh)"
  exit 1
fi

INSTALL_DIR="/var/www/homeflix"
OLD_INSTALL_DIR="/var/www/m3u8player"
SERVICE_NAME="homeflix"
OLD_SERVICE_NAME="m3u8player"

echo "======================================="
echo "      CẬP NHẬT HOMEFLIX PROXY PLAYER   "
echo "======================================="


# 2. Dừng và gỡ bỏ service m3u8player cũ nếu đang chạy
if systemctl is-active --quiet "$OLD_SERVICE_NAME"; then
    echo "Đang dừng dịch vụ $OLD_SERVICE_NAME cũ..."
    systemctl stop "$OLD_SERVICE_NAME"
fi
if systemctl is-enabled --quiet "$OLD_SERVICE_NAME"; then
    echo "Đang vô hiệu hóa dịch vụ $OLD_SERVICE_NAME cũ..."
    systemctl disable "$OLD_SERVICE_NAME"
fi
if [ -f "/etc/systemd/system/${OLD_SERVICE_NAME}.service" ]; then
    echo "Xóa tệp cấu hình dịch vụ $OLD_SERVICE_NAME..."
    rm -f "/etc/systemd/system/${OLD_SERVICE_NAME}.service"
fi

# 3. Tạo thư mục cài đặt mới nếu chưa tồn tại
if [ ! -d "$INSTALL_DIR" ]; then
    echo "Tạo thư mục cài đặt mới tại $INSTALL_DIR..."
    mkdir -p "$INSTALL_DIR"
    mkdir -p "$INSTALL_DIR/templates"
    mkdir -p "$INSTALL_DIR/cache"
fi

# 4. Tạo môi trường ảo venv nếu chưa có (khi nâng cấp từ phiên bản cũ không đầy đủ)
if [ ! -d "$INSTALL_DIR/venv" ]; then
    echo "Tạo môi trường ảo venv..."
    python3 -m venv "$INSTALL_DIR/venv"
fi

# 5. Sao chép các tệp mới đè lên thư mục cài đặt
echo "[1/4] Đang cập nhật mã nguồn mới..."
cp app.py "$INSTALL_DIR/"
cp -r templates/index.html "$INSTALL_DIR/templates/"
cp requirements.txt "$INSTALL_DIR/"

# 6. Đảm bảo cấu hình systemd cho homeflix.service tồn tại
cat <<EOF > /etc/systemd/system/${SERVICE_NAME}.service
[Unit]
Description=HomeFlix Proxy Player FastAPI Service
After=network.target

[Service]
User=root
Group=root
WorkingDirectory=${INSTALL_DIR}
Environment="PATH=${INSTALL_DIR}/venv/bin"
ExecStart=${INSTALL_DIR}/venv/bin/uvicorn app:app --host 0.0.0.0 --port 6969
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable ${SERVICE_NAME}

# 3. Phân quyền lại cho thư mục cài đặt
echo "[2/4] Thiết lập lại phân quyền..."
chown -R root:root "$INSTALL_DIR"
chmod -R 755 "$INSTALL_DIR"
chmod -R 777 "$INSTALL_DIR/cache"

# 4. Cập nhật các dependencies từ requirements.txt mới
echo "[3/4] Cập nhật thư viện Python (pip)..."
if [ -f "$INSTALL_DIR/venv/bin/pip" ]; then
    "$INSTALL_DIR/venv/bin/pip" install --upgrade pip
    "$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"
else
    echo "Cảnh báo: Không tìm thấy môi trường ảo venv tại $INSTALL_DIR/venv. Bỏ qua bước pip install."
fi

# 5. Khởi động lại dịch vụ
echo "[4/4] Khởi động lại dịch vụ $SERVICE_NAME..."
systemctl daemon-reload
systemctl restart $SERVICE_NAME

# Hoàn tất và hiển thị trạng thái
echo "======================================="
echo " CẬP NHẬT THÀNH CÔNG!                  "
echo "======================================="
echo "Trạng thái dịch vụ hiện tại:"
systemctl status $SERVICE_NAME --no-pager -n 5
echo "======================================="
