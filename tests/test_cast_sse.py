import asyncio
import threading
import time

from tests.cast_helpers import asgi_stream


def setup_device(make_client):
    c = make_client()
    info = c.get("/api/device").json()
    return info["id"], {"hf_device": c.cookies.get("hf_device")}


def run(coro):
    return asyncio.run(coro)


def text(chunks):
    return b"".join(chunks).decode()


def test_stream_headers_and_hello_first(app_module, make_client):
    dev, cookies = setup_device(make_client)
    status, headers, chunks, finished = run(asgi_stream(app_module.app, "/api/cast/events", cookies=cookies,
                                                        until=lambda c: b"hello" in b"".join(c)))
    assert status == 200 and finished
    assert headers["content-type"].startswith("text/event-stream")
    assert headers["x-accel-buffering"] == "no"
    assert "no-store" in headers["cache-control"]
    first = text(chunks)
    assert first.startswith("event: hello") and app_module._cast_public_id(dev) in first and dev not in first


def test_hello_lists_current_controllers_with_names(app_module, make_client):
    tv_id, tv_cookies = setup_device(make_client)
    phone = make_client()
    phone_id = phone.get("/api/device").json()["id"]
    app_module.cast_hub.link(phone_id, tv_id)
    _, _, chunks, _ = run(asgi_stream(app_module.app, "/api/cast/events", cookies=tv_cookies, until=lambda c: True))
    body = text(chunks)
    assert f'"id": "{app_module._cast_public_id(phone_id)}"' in body and '"name": "iPhone"' in body
    assert phone_id not in body


def test_event_published_to_device_arrives_on_stream(app_module, make_client):
    dev, cookies = setup_device(make_client)
    threading.Timer(0.2, lambda: app_module.cast_hub.publish(dev, "command", {"type": "pause"})).start()
    _, _, chunks, finished = run(asgi_stream(app_module.app, "/api/cast/events", cookies=cookies,
                                             until=lambda c: b"event: command" in b"".join(c), timeout=3))
    assert finished and 'event: command\ndata: {"type": "pause"}' in text(chunks)


def test_ping_comment_is_sent_when_idle(app_module, make_client, monkeypatch):
    monkeypatch.setattr(app_module, "CAST_PING_SECONDS", 0.05)
    _, cookies = setup_device(make_client)
    _, _, chunks, finished = run(asgi_stream(app_module.app, "/api/cast/events", cookies=cookies,
                                             until=lambda c: b": ping" in b"".join(c), timeout=3))
    assert finished and ": ping\n\n" in text(chunks)


def test_not_gzipped_even_if_client_accepts_gzip(app_module, make_client):
    _, cookies = setup_device(make_client)
    _, headers, chunks, _ = run(asgi_stream(app_module.app, "/api/cast/events", cookies=cookies,
                                            headers={"accept-encoding": "gzip"}, until=lambda c: True))
    assert "content-encoding" not in headers and text(chunks).startswith("event: hello")


def test_disconnect_unsubscribes(app_module, make_client):
    dev, cookies = setup_device(make_client)
    run(asgi_stream(app_module.app, "/api/cast/events", cookies=cookies, until=lambda c: True))
    assert not app_module.cast_hub.has_subscribers(dev)


def test_connections_are_capped_and_evicted_stream_ends(app_module, make_client):
    dev, cookies = setup_device(make_client)

    async def scenario():
        stop = lambda c: b"event: bye" in b"".join(c)
        tasks = []
        for _ in range(5):
            tasks.append(asyncio.create_task(asgi_stream(app_module.app, "/api/cast/events", cookies=cookies, until=stop, timeout=4)))
            await asyncio.sleep(0.1)       # kết nối lần lượt để thứ tự đăng ký xác định
        assert len(app_module.cast_hub._subs[dev]) == 4
        assert tasks[0].done()            # luồng cũ nhất bị đóng và kết thúc
        app_module.cast_hub.publish(dev, "bye", {})
        results = await asyncio.gather(*tasks)
        return results

    results = run(scenario())
    assert all(r[3] for r in results)     # không luồng nào treo
    assert not app_module.cast_hub.has_subscribers(dev)
