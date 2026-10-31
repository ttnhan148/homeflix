"""Máy chủ có đang ở Việt Nam không: CDN truyền hình VN chặn IP ngoài nước, nên bộ kiểm stream
chỉ bật khi IP công khai của máy chủ ở VN (tắt tay/bật tay bằng HOMETV_HEALTH)."""
import dataclasses
import re
import time
from dataclasses import dataclass

import httpx

REFRESH_EVERY = 3600
STALE = 86400
TIMEOUT = 5.0
_CODE = re.compile(r"^[A-Z]{2}$")


@dataclass(frozen=True)
class HealthMode:
    mode: str
    enabled: bool
    country: str | None
    checked_at: float


_INITIAL = HealthMode("auto", False, None, 0.0)
_state: HealthMode = _INITIAL


def reset() -> None:
    global _state
    _state = _INITIAL


def parse_mode(value: str | None) -> str:
    v = (value or "").strip().lower()
    return v if v in ("auto", "on", "off") else "auto"


def _code(text: str) -> str | None:
    c = text.strip().upper()
    return c if _CODE.match(c) else None


async def detect_country(client: httpx.AsyncClient) -> str | None:
    """Mã quốc gia 2 chữ hoa của IP công khai; None nếu không nơi nào trả lời hợp lệ."""
    for url, as_json in (("https://ipinfo.io/country", False), ("https://api.country.is", True)):
        try:
            r = await client.get(url, timeout=TIMEOUT)
            if r.status_code != 200:
                continue
            code = _code(str(r.json().get("country", ""))) if as_json else _code(r.text)
        except (httpx.HTTPError, ValueError, AttributeError):
            continue
        if code:
            return code
    return None


async def refresh_mode(client: httpx.AsyncClient, mode: str, now: float) -> HealthMode:
    global _state
    mode = parse_mode(mode)
    country = await detect_country(client)
    prev = _state
    if mode == "on":
        _state = HealthMode(mode, True, country or prev.country, now)
    elif mode == "off":
        _state = HealthMode(mode, False, country or prev.country, now)
    elif country is not None:
        _state = HealthMode(mode, country == "VN", country, now)
    else:  # dò lỗi: giữ kết quả cũ (current() tự tắt khi quá STALE)
        _state = HealthMode(mode, prev.enabled if prev.mode == "auto" else False,
                            prev.country if prev.mode == "auto" else None, prev.checked_at)
    return _state


def current(now: float | None = None) -> HealthMode:
    now = time.time() if now is None else now
    s = _state
    if s.mode == "auto" and s.enabled and now - s.checked_at > STALE:
        return dataclasses.replace(s, enabled=False)
    return s
