"""Route admin cho nguồn IPTV; require_admin được truyền vào để tránh import vòng với app.py."""
import asyncio
import re
import time
from typing import Callable

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from hometv import db, sources

ID_RE = re.compile(r"^[a-z0-9-]{1,80}$")
BAD_BODY = {"errors": {"body": "Dữ liệu không hợp lệ"}}


def _not_found() -> JSONResponse:
    return JSONResponse({"detail": "Không tìm thấy nguồn"}, status_code=404)


async def _body(request: Request):
    """Trả dict JSON hoặc None nếu không phải object."""
    try:
        data = await request.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


async def _run(fn, *args):
    """Chạy hàm DB ngoài event loop; DB chưa mở thì 503, lỗi nghiệp vụ đổi sang JSON."""
    try:
        db.conn()  # RuntimeError nếu hometv.db chưa mở
        return await asyncio.to_thread(fn, *args)
    except RuntimeError:
        return JSONResponse({"detail": "HomeTV chưa sẵn sàng"}, status_code=503)
    except sources.ValidationError as e:
        return JSONResponse({"errors": e.errors}, status_code=422)
    except KeyError:
        return _not_found()


def make_router(require_admin: Callable) -> APIRouter:
    router = APIRouter(prefix="/api/admin/hometv/sources", dependencies=[Depends(require_admin)])

    @router.get("")
    async def list_all():
        return await _run(sources.list_sources, time.time())

    @router.post("", status_code=201)
    async def create(request: Request):
        data = await _body(request)
        if data is None:
            return JSONResponse(BAD_BODY, status_code=422)
        errors = {k: "Bắt buộc" for k in ("name", "url") if not isinstance(data.get(k), str)}
        if errors:
            return JSONResponse({"errors": errors}, status_code=422)
        return await _run(sources.create, data["name"], data["url"], time.time())

    @router.patch("/{source_id}")
    async def patch(source_id: str, request: Request):
        if not ID_RE.match(source_id):
            return _not_found()
        data = await _body(request)
        if data is None:
            return JSONResponse(BAD_BODY, status_code=422)
        return await _run(sources.update, source_id, data, time.time())

    @router.post("/{source_id}/refresh", status_code=202)
    async def refresh(source_id: str):
        if not ID_RE.match(source_id):
            return _not_found()
        r = await _run(sources.request_refresh, source_id, time.time())
        if isinstance(r, JSONResponse):
            return r
        return {"queued": True} if r else _not_found()

    @router.delete("/{source_id}")
    async def remove(source_id: str):
        if not ID_RE.match(source_id):
            return _not_found()
        r = await _run(sources.delete, source_id)
        if isinstance(r, JSONResponse):
            return r
        return Response(status_code=204) if r else _not_found()

    return router
