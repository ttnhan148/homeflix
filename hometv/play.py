"""Phát kênh qua proxy của HomeFlix: viết lại playlist về route /api/hometv/play, mở upstream.

Bản gọn: URL upstream nằm thẳng trong query `u` (chưa ký, chưa chặn SSRF, chưa giới hạn kích thước)."""
from urllib.parse import quote, urlsplit

import httpx
import m3u8

from hometv.fetcher import FetchError

DIRECT_EXT = (".ts", ".mp4", ".mkv", ".flv", ".webm", ".aac", ".mp3")
BASE = "/api/hometv/play"


class PlaylistError(ValueError):
    pass


def is_direct_url(url: str) -> bool:
    """Stream không phải HLS (theo đuôi path): phát thẳng, không viết lại."""
    return urlsplit(url).path.lower().endswith(DIRECT_EXT)


def index_url(stream_id: int) -> str:
    return f"{BASE}/{stream_id}/index.m3u8"


def seg_url(stream_id: int, url: str) -> str:
    return f"{BASE}/{stream_id}/seg?u={quote(url, safe='')}"


def rewrite_playlist(body: bytes, base_url: str, stream_id: int) -> str:
    """Viết lại mọi URI trong playlist (master, media, key, map) thành route seg; URI tương đối giải theo base_url."""
    text = body.decode("utf-8", errors="replace").lstrip("﻿ \t\r\n")
    if not text.startswith("#EXTM3U"):
        raise PlaylistError("Không phải playlist HLS")
    pl = m3u8.loads(text, uri=base_url)

    def fix(item) -> None:
        if item is not None and item.uri:
            item.uri = seg_url(stream_id, item.absolute_uri)

    if pl.is_variant:
        for item in [*pl.playlists, *pl.iframe_playlists, *pl.media]:
            fix(item)
    else:
        for item in [*pl.segments, *pl.keys, *(sg.init_section for sg in pl.segments)]:
            fix(item)
    return pl.dumps()


def upstream_headers(http_headers: dict, range_header: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if http_headers.get("user_agent"):
        out["User-Agent"] = str(http_headers["user_agent"])
    if http_headers.get("referrer"):
        out["Referer"] = str(http_headers["referrer"])
    if range_header:
        out["Range"] = range_header
    return out


async def open_upstream(client: httpx.AsyncClient, url: str, headers: dict[str, str], *,
                        max_redirects: int = 5) -> tuple[httpx.Response, str]:
    """Mở stream từ upstream, tự theo chuyển hướng; trả (response đang mở, URL cuối). Caller đóng response."""
    current = url
    try:
        for hop in range(max_redirects + 1):
            resp = await client.send(client.build_request("GET", current, headers=headers), stream=True)
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("location", "").strip()
                await resp.aclose()
                if not location or hop == max_redirects:
                    raise FetchError("Quá nhiều lần chuyển hướng" if location else f"HTTP {resp.status_code}")
                current = str(httpx.URL(current).join(location))
                continue
            if resp.status_code >= 400:
                await resp.aclose()
                raise FetchError(f"HTTP {resp.status_code}")
            return resp, current
    except (httpx.InvalidURL, ValueError):
        raise FetchError("URL không hợp lệ")
    except httpx.TimeoutException:
        raise FetchError("Hết thời gian chờ")
    except httpx.RequestError:
        raise FetchError("Không kết nối được")
    raise FetchError("Quá nhiều lần chuyển hướng")  # không tới được
