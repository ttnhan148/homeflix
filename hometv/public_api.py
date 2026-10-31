"""Route công khai của HomeTV: danh sách kênh, stream, yêu thích, gần đây, và đường phát qua proxy."""
import asyncio
import time
from typing import Callable

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from hometv import catalog, play


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

    return router
