FROM python:3.11-slim

# Install ffmpeg and packages needed for video processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Create data & cache directories
RUN mkdir -p /app/cache /app/downloads

# Expose default port 6969
EXPOSE 6969

# Start HomeFlix
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "6969"]
