"""Route admin cho sức khỏe stream; require_admin được truyền vào để tránh import vòng với app.py."""
import time
from typing import Callable

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from hometv import geo, health_store, sources
from hometv.api import ID_RE, _not_found, _run

DISABLED = "Kiểm tra stream đang tắt (máy chủ không ở Việt Nam)"
NOT_ELIGIBLE = "Nguồn chưa duyệt hoặc đang tắt"


def _invalid(field: str, text: str) -> JSONResponse:
    return JSONResponse({"errors": {field: text}}, status_code=422)


def make_health_router(require_admin: Callable) -> APIRouter:
    router = APIRouter(prefix="/api/admin/hometv", dependencies=[Depends(require_admin)])

    @router.get("/health/status")
    async def status():
        m = geo.current()
        return {"mode": m.mode, "enabled": m.enabled, "country": m.country}

    @router.post("/sources/{source_id}/check", status_code=202)
    async def check(source_id: str):
        if not geo.current().enabled:
            return JSONResponse({"detail": DISABLED}, status_code=409)
        if not ID_RE.match(source_id):
            return JSONResponse({"detail": NOT_ELIGIBLE}, status_code=404)
        ok = await _run(health_store.request_check, source_id, time.time())
        if isinstance(ok, JSONResponse):
            return ok
        return {"queued": True} if ok else JSONResponse({"detail": NOT_ELIGIBLE}, status_code=404)

    @router.get("/sources/{source_id}/health")
    async def dead_list(source_id: str, status: str = "dead", limit: str = "50"):
        if status not in ("dead", "all"):
            return _invalid("status", "Phải là dead hoặc all")
        if not limit.isdigit() or not 1 <= int(limit) <= 200:
            return _invalid("limit", "Phải từ 1 đến 200")
        if not ID_RE.match(source_id):
            return _not_found()
        src = await _run(sources.internal, source_id)
        if isinstance(src, JSONResponse):
            return src
        if src is None:
            return _not_found()
        return await _run(health_store.dead_streams, source_id, status, int(limit))

    return router
