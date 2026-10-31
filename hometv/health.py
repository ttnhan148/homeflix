"""Đo sức khỏe một stream: tải playlist và đoạn đầu bằng Python, ffprobe đọc file tạm."""
from dataclasses import dataclass


@dataclass
class HealthResult:
    ok: bool
    error: str | None = None
    latency_ms: int | None = None
    throughput_kbps: int | None = None
    width: int | None = None
    height: int | None = None
    vcodec: str | None = None
    bitrate_kbps: int | None = None
