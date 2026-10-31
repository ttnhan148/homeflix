"""Tải một playlist: giới hạn dung lượng/thời gian/chuyển hướng, kiểm tra SSRF ở MỌI bước."""
import asyncio
import zlib

import httpx

from hometv import netguard

BROKEN = "Playlist hỏng"
REDIRECT_CODES = (301, 302, 303, 307, 308)


class FetchError(Exception):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.message = message
        self.retryable = retryable


def make_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    # trust_env=False: biến HTTP(S)_PROXY không được đẩy request qua proxy tự phân giải tên
    return httpx.AsyncClient(follow_redirects=False, trust_env=False,
                             headers={"user-agent": "HomeTV/1.0"}, transport=transport)


async def _guard(url: str) -> None:
    # Kiểm tra cú pháp + DNS trước khi gửi; DNS chạy trong thread để không chặn event loop
    try:
        host = netguard.check_url(url)
        await asyncio.to_thread(netguard.check_host, host)
    except netguard.BlockedURL as e:
        raise FetchError(e.reason, retryable=False)


def _decoder(encoding: str):
    """Trả hàm giải nén từng đoạn có giới hạn đầu ra, hoặc None nếu không mã hóa."""
    if encoding in ("", "identity"):
        return None
    if encoding not in ("gzip", "deflate"):
        raise FetchError(BROKEN)  # br, gzip lồng nhau, ...: không giải mã
    wbits = [16 + zlib.MAX_WBITS] if encoding == "gzip" else [zlib.MAX_WBITS, -zlib.MAX_WBITS]
    state = {"d": zlib.decompressobj(wbits[0]), "first": True}

    def feed(data: bytes, room: int) -> bytes:
        """Giải nén data, tối đa room byte đầu ra (ném lỗi nếu vượt)."""
        out = bytearray()
        while True:
            try:
                piece = state["d"].decompress(data, room - len(out) + 1)
            except zlib.error:
                if state["first"] and len(wbits) > 1:  # deflate thô không có header zlib
                    state["d"] = zlib.decompressobj(wbits[1])
                    state["first"] = False
                    continue
                raise FetchError(BROKEN)
            state["first"] = False
            out += piece
            if len(out) > room:
                return bytes(out)
            data = state["d"].unconsumed_tail
            if not data:
                return bytes(out)

    feed.done = lambda: state["d"].eof
    return feed


async def _get(client: httpx.AsyncClient, url: str, timeout: float, max_bytes: int,
               max_redirects: int) -> bytes:
    limit = FetchError(f"Quá {max_bytes // (1024 * 1024)} MB")
    current = url
    for hop in range(max_redirects + 1):
        await _guard(current)
        async with client.stream(
                "GET", current, headers={"accept-encoding": "identity"},
                follow_redirects=False, timeout=httpx.Timeout(timeout)) as resp:
            code = resp.status_code
            if code in REDIRECT_CODES:
                location = resp.headers.get("location", "").strip()
                if not location:
                    raise FetchError(f"HTTP {code}")
                if hop == max_redirects:
                    raise FetchError("Quá nhiều lần chuyển hướng")
                try:
                    current = str(httpx.URL(current).join(location))
                except (httpx.InvalidURL, ValueError):
                    raise FetchError(netguard.INVALID, retryable=False)
                continue
            if code >= 400:
                raise FetchError(f"HTTP {code}")
            try:
                if int(resp.headers.get("content-length", 0)) > max_bytes:
                    raise limit
            except ValueError:
                pass
            decode = _decoder(resp.headers.get("content-encoding", "").strip().lower())
            raw_total = 0
            body = bytearray()
            # Đọc byte thô (không để httpx tự giải nén): đếm cả byte thô lẫn byte đã giải nén
            async for chunk in resp.aiter_raw():
                raw_total += len(chunk)
                if raw_total > max_bytes:
                    raise limit
                body += chunk if decode is None else decode(chunk, max_bytes - len(body))
                if len(body) > max_bytes:
                    raise limit
            if decode is not None and not decode.done():
                raise FetchError(BROKEN)
            return bytes(body)
    raise FetchError("Quá nhiều lần chuyển hướng")  # không tới được


async def fetch_playlist(client: httpx.AsyncClient, url: str, *, timeout: float,
                         max_bytes: int, max_redirects: int = 5) -> bytes:
    try:
        async with asyncio.timeout(timeout):  # tổng thời gian cả lượt (mọi bước + body)
            return await _get(client, url, timeout, max_bytes, max_redirects)
    except (TimeoutError, httpx.TimeoutException):
        raise FetchError("Hết thời gian chờ")
    except (httpx.InvalidURL, UnicodeError):  # Location hỏng do httpx parse trong stream()
        raise FetchError(netguard.INVALID, retryable=False)
    except httpx.RequestError:
        raise FetchError("Không kết nối được")
