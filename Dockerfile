FROM python:3.11-slim

# Install ffmpeg and build deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    build-essential \
    && apt-get clean

WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app
RUN useradd -m -u 1000 appuser || true 
RUN chown -R appuser:appuser /app

ENV PORT=6969
EXPOSE 6969
USER appuser
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "6969", "--workers", "1"]
