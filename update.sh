#!/bin/bash

# Kiểm tra quyền root
if [ "$EUID" -ne 0 ]; then 
  echo "Vui lòng chạy script với quyền root (sudo ./update.sh)"
  exit 1
fi

INSTALL_DIR="/var/www/homeflix"
SERVICE_NAME="homeflix"

echo "======================================="
echo "      CẬP NHẬT HOMEFLIX PROXY PLAYER   "
echo "======================================="

# 1. Kiểm tra thư mục cài đặt gốc có tồn tại không
if [ ! -d "$INSTALL_DIR" ]; then
    echo "LỖI: Không tìm thấy thư mục cài đặt gốc tại $INSTALL_DIR."
    echo "Hãy chắc chắn rằng ứng dụng đã được cài đặt lần đầu bằng install.sh"
    exit 1
fi

# 2. Sao chép các tệp mới đè lên thư mục cài đặt
echo "[1/4] Đang cập nhật mã nguồn mới..."
cp app.py "$INSTALL_DIR/"
cp -r templates/index.html "$INSTALL_DIR/templates/"
cp requirements.txt "$INSTALL_DIR/"

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
