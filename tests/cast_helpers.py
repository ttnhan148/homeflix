import asyncio


async def asgi_stream(app, path, *, headers=None, cookies=None, until=lambda chunks: True, timeout=2.0):
    """GET một luồng (SSE) thẳng qua ASGI, không mở cổng mạng.

    Thu các phần thân tới khi `until(chunks)` đúng rồi gửi http.disconnect.
    Trả (status, headers_thường, chunks, finished); finished False nếu app không kết thúc sau khi ngắt."""
    raw_headers = [(b"host", b"homeflix.test"), (b"user-agent", b"pytest")]
    for k, v in (headers or {}).items():
        raw_headers.append((k.lower().encode(), v.encode()))
    if cookies:
        raw_headers.append((b"cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()).encode()))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "path": path,
             "raw_path": path.encode(), "query_string": b"", "root_path": "", "scheme": "http",
             "headers": raw_headers, "client": ("203.0.113.7", 50000), "server": ("homeflix.test", 80)}
    disconnect = asyncio.Event()
    sent_body = False
    chunks, info = [], {}

    async def receive():
        nonlocal sent_body
        if not sent_body:
            sent_body = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            info["status"] = message["status"]
            info["headers"] = {k.decode().lower(): v.decode() for k, v in message["headers"]}
        elif message["type"] == "http.response.body" and message.get("body"):
            chunks.append(message["body"])
            if until(chunks):
                disconnect.set()

    task = asyncio.create_task(app(scope, receive, send))
    finished = True
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout)
    except asyncio.TimeoutError:
        disconnect.set()
        try:
            await asyncio.wait_for(asyncio.shield(task), 1.0)
        except asyncio.TimeoutError:
            finished = False
            task.cancel()
    return info.get("status"), info.get("headers", {}), chunks, finished
