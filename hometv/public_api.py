"""Route công khai của HomeTV: danh sách kênh, stream, yêu thích, gần đây, và đường phát qua proxy."""
import asyncio
import time
from typing import Callable

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from hometv import catalog, play
from hometv.fetcher import FetchError

PLAYLIST_TYPE = "application/vnd.apple.mpegurl"
PASS_HEADERS = ("content-type", "content-length", "content-range", "accept-ranges")


async def _db(fn, *args):
    return await asyncio.to_thread(fn, *args)


def _not_found(msg: str = "Không tìm thấy kênh") -> JSONResponse:
    return JSONResponse({"detail": msg}, status_code=404)


def make_public_router(get_client: Callable[[], httpx.AsyncClient]) -> APIRouter:
    router = APIRouter(prefix="/api/hometv")

    @router.get("/channels")
    async def channels():
        return await _db(catalog.list_channels, time.time())

    @router.get("/channels/{channel_id}/streams")
    async def streams(channel_id: int):
        rows = await _db(catalog.channel_streams, channel_id)
        if not rows:
            return _not_found("Kênh không còn khả dụng")
        return [{
            "id": r["id"],
            "play_url": play.seg_url(r["id"], r["url"]) if play.is_direct_url(r["url"]) else play.index_url(r["id"]),
            "quality_hint": r["quality_hint"],
        } for r in rows]

    @router.put("/favorites/{channel_id}", status_code=204)
    async def favorite_on(channel_id: int):
        ok = await _db(catalog.set_favorite, channel_id, True, time.time())
        return Response(status_code=204) if ok else _not_found()

    @router.delete("/favorites/{channel_id}", status_code=204)
    async def favorite_off(channel_id: int):
        await _db(catalog.set_favorite, channel_id, False, time.time())
        return Response(status_code=204)

    @router.post("/recent/{channel_id}", status_code=204)
    async def recent(channel_id: int):
        ok = await _db(catalog.mark_recent, channel_id, time.time())
        return Response(status_code=204) if ok else _not_found()

    async def _play(request: Request, stream_id: int, url: str | None):
        """Phát một URL của stream: playlist thì viết lại, còn lại stream thẳng về client (không ghi đĩa)."""
        stream = await _db(catalog.valid_stream, stream_id)
        if stream is None:
            return _not_found("Kênh không còn khả dụng")
        is_index = url is None
        url = url or stream["url"]
        headers = play.upstream_headers(stream["http_headers"], request.headers.get("range"))
        try:
            resp, final = await play.open_upstream(get_client(), url, headers)
        except FetchError as e:
            return PlainTextResponse(e.message, status_code=502, headers={"Cache-Control": "no-store"})
        ctype = resp.headers.get("content-type", "")
        by_hint = "mpegurl" in ctype.lower() or play.is_playlist_url(final)
        first = b""
        chunks = resp.aiter_raw().__aiter__()
        playlist = by_hint
        if not by_hint and not play.is_direct_url(final):
            # CDN trả playlist với Content-Type lạ hoặc URL không có đuôi: nhận ra qua byte đầu
            first = await anext(chunks, b"")
            playlist = play.looks_like_playlist(first)
        if playlist:
            try:
                body = await resp.aread() if by_hint else first + b"".join([c async for c in chunks])
                text = play.rewrite_playlist(body, final, stream_id)
            except (play.PlaylistError, httpx.HTTPError):
                return PlainTextResponse("Playlist hỏng", status_code=502, headers={"Cache-Control": "no-store"})
            finally:
                await resp.aclose()
            return Response(text, media_type=PLAYLIST_TYPE, headers={"Cache-Control": "no-store"})
        if is_index:  # index.m3u8 của stream mà nội dung không phải playlist: để client thử nguồn khác
            await resp.aclose()
            return PlainTextResponse("Không phải playlist HLS", status_code=502, headers={"Cache-Control": "no-store"})

        async def body_stream():
            if first:
                yield first
            async for c in chunks:
                yield c
        out = {h: resp.headers[h] for h in PASS_HEADERS if h in resp.headers}
        return StreamingResponse(body_stream(), status_code=resp.status_code, headers=out,
                                 background=BackgroundTask(resp.aclose))

    @router.get("/play/{stream_id}/index.m3u8")
    async def play_index(request: Request, stream_id: int):
        return await _play(request, stream_id, None)

    @router.get("/play/{stream_id}/seg")
    async def play_seg(request: Request, stream_id: int, u: str):
        return await _play(request, stream_id, u)

    return router
