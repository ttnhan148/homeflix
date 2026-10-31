"""Chấm điểm stream 0-100 từ kết quả kiểm tra (hàm thuần)."""
from hometv.health import HealthResult

UNKNOWN = 50  # chưa kiểm: không thưởng, không phạt
_RES_TIERS = ((1080, 25), (720, 18), (480, 10))
_RES_DEFAULT = 4


def score(result: HealthResult, https: bool) -> int:
    if not result.ok:
        return 0
    total = UNKNOWN
    h = result.height or 0
    total += next((pts for floor, pts in _RES_TIERS if h >= floor), _RES_DEFAULT)
    if result.throughput_kbps and result.bitrate_kbps:
        ratio = result.throughput_kbps / result.bitrate_kbps
        total += 15 if ratio >= 2 else 8 if ratio >= 1 else 0
    if https:
        total += 10
    return min(100, total)
