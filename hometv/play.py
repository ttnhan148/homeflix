"""Play channels through the HomeFlix proxy: rewrite playlists to the /api/hometv/play route, open upstream.

Lite version: the upstream URL sits directly in query `u` (unsigned, no SSRF blocking, no size limit yet)."""
from urllib.parse import quote, urlsplit

import httpx
import m3u8

from hometv.fetcher import REDIRECT_CODES, FetchError

DIRECT_EXT = (".ts", ".mp4", ".mkv", ".flv", ".webm", ".aac", ".mp3")
BASE = "/api/hometv/play"


class PlaylistError(ValueError):
    pass


def is_direct_url(url: str) -> bool:
    """Non-HLS stream (by path extension): play directly, no rewrite."""
    return urlsplit(url).path.lower().endswith(DIRECT_EXT)


def is_playlist_url(url: str) -> bool:
    return urlsplit(url).path.lower().endswith((".m3u8", ".m3u"))


def playlist_text(body: bytes) -> str | None:
    """Decoded playlist text (BOM/leading whitespace stripped), or None if it is not an M3U playlist."""
    text = body.decode("utf-8", errors="replace").lstrip("\ufeff \t\r\n")
    return text if text.startswith("#EXTM3U") else None


def looks_like_playlist(chunk: bytes) -> bool:
    return playlist_text(chunk) is not None


def index_url(stream_id: int) -> str:
    return f"{BASE}/{stream_id}/index.m3u8"


def seg_url(stream_id: int, url: str) -> str:
    return f"{BASE}/{stream_id}/seg?u={quote(url, safe='')}"


def rewrite_playlist(body: bytes, base_url: str, stream_id: int) -> str:
    """Rewrite every URI in the playlist (master, media, key, map) to the seg route; relative URIs resolve against base_url."""
    text = playlist_text(body)
    if text is None:
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
    """Open a stream from upstream, following redirects; return (open response, final URL). Caller closes the response."""
    current = url
    headers = {**headers, "Accept-Encoding": "identity"}  # video segments must not be compressed; aiter_raw keeps bytes intact
    try:
        for hop in range(max_redirects + 1):
            resp = await client.send(client.build_request("GET", current, headers=headers), stream=True)
            if resp.status_code in REDIRECT_CODES:
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
    raise FetchError("Quá nhiều lần chuyển hướng")  # unreachable
