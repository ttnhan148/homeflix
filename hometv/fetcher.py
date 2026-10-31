"""Tải một playlist: giới hạn dung lượng/thời gian/chuyển hướng, kiểm tra SSRF ở MỌI bước."""
import asyncio

import httpx

from hometv import netguard

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


async def _get(client: httpx.AsyncClient, url: str, max_bytes: int, max_redirects: int) -> bytes:
    limit = FetchError(f"Quá {max_bytes // (1024 * 1024)} MB")
    current = url
    for hop in range(max_redirects + 1):
        await _guard(current)
        async with client.stream("GET", current) as resp:
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
            body = bytearray()
            async for chunk in resp.aiter_bytes():
                body += chunk
                if len(body) > max_bytes:
                    raise limit
            return bytes(body)
    raise FetchError("Quá nhiều lần chuyển hướng")  # không tới được


async def fetch_playlist(client: httpx.AsyncClient, url: str, *, timeout: float,
                         max_bytes: int, max_redirects: int = 5) -> bytes:
    try:
        async with asyncio.timeout(timeout):  # tổng thời gian cả lượt (mọi bước + body)
            return await _get(client, url, max_bytes, max_redirects)
    except (TimeoutError, httpx.TimeoutException):
        raise FetchError("Hết thời gian chờ")
    except httpx.TransportError:
        raise FetchError("Không kết nối được")
