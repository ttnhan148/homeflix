FROM python:3.11-slim

# Cài đặt ffmpeg và các gói cần thiết cho video processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements và cài đặt python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy mã nguồn ứng dụng
COPY . .

# Tạo các thư mục lưu dữ liệu & cache
RUN mkdir -p /app/cache /app/downloads

# Expose port mặc định 6969
EXPOSE 6969

# Khởi chạy HomeFlix
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "6969"]
