import base64
import re
import time

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from tests.ui.phimapi_fake import FakePhimapi

HOST = "http://homeflix.test"
IPHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
TV_UA = "Mozilla/5.0 (SmartTV; Linux; Tizen 7.0) AppleWebKit/537.36 (KHTML, like Gecko) Safari/537.36"
PNG_1X1 = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
SAFE_VARS = ("--safe-top", "--safe-right", "--safe-bottom", "--safe-left")


def pytest_collection_modifyitems(items):
    for item in items:
        if "tests/ui" in str(item.fspath).replace("\\", "/"):
            item.add_marker(pytest.mark.ui)


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def open_app(app_module, browser):
    """open_app(width, height, tv, safe=(top, right, bottom, left), fake, delay) -> Page chưa goto.

    Mọi request tới homeflix.test đi qua TestClient của app; phimapi thay bằng FakePhimapi."""
    contexts = []

    def _open(*, width=430, height=932, tv=False, safe=(0, 0, 0, 0), fake=None, delay=None):
        app_module.client = fake or FakePhimapi()
        bridge = TestClient(app_module.app, base_url=HOST)
        if tv:
            ctx = browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=TV_UA, has_touch=False)
        else:
            ctx = browser.new_context(viewport={"width": width, "height": height}, user_agent=IPHONE_UA,
                                      has_touch=True, is_mobile=True, device_scale_factor=2)
        contexts.append(ctx)
        page = ctx.new_page()
        page.set_default_timeout(4000)
        page.requests, page.console_errors, page.taps, page.is_touch = [], [], 0, not tv

        # hls.js (CDN) bị chặn trong test: thay bằng bản giả ghi nhận việc gắn nguồn, và chặn phát thật
        page.add_init_script("""
            window.Hls = class { static isSupported() { return true; } loadSource() {} on() {} destroy() {}
                attachMedia() { window.__hlsAttached = true; } };
            window.Hls.Events = { MANIFEST_PARSED: 'manifestParsed', ERROR: 'error' };
            window.Hls.ErrorTypes = {};
            HTMLMediaElement.prototype.play = function () { window.__plays = (window.__plays || 0) + 1; return Promise.resolve(); };
            HTMLMediaElement.prototype.pause = function () {};
            HTMLMediaElement.prototype.canPlayType = function () { return ''; }; // ép đường hls.js, Chromium mới có HLS gốc
        """)

        # EventSource giả: page.route không phát được luồng, nên test tự đẩy sự kiện vào (es_emit)
        page.add_init_script("""
            window.__es = [];
            window.EventSource = class FakeEventSource {
                constructor(url) { this.url = url; this.readyState = 1; this.listeners = {}; this.onerror = null;
                    this.t = performance.now(); window.__es.push(this); }
                addEventListener(name, fn) { (this.listeners[name] = this.listeners[name] || []).push(fn); }
                close() { this.readyState = 2; }
            };
            const lastOpen = () => [...window.__es].reverse().find(e => e.readyState !== 2);
            window.__esEmit = (name, data) => { const es = lastOpen(); if (es) (es.listeners[name] || []).forEach(f => f({data: JSON.stringify(data)})); };
            window.__esError = () => { const es = lastOpen(); if (es && es.onerror) es.onerror(new Event('error')); };
        """)
        page.es_emit = lambda name, data: page.evaluate("([n, d]) => window.__esEmit(n, d)", [name, data])
        page.es_error = lambda: page.evaluate("window.__esError()")
        page.es_urls = lambda: page.evaluate("window.__es.map(e => e.url)")
        page.es_open_count = lambda: page.evaluate("window.__es.filter(e => e.readyState !== 2).length")

        if any(safe):
            props = dict(zip(SAFE_VARS, (f"{v}px" for v in safe)))
            page.add_init_script(
                "(() => { const p = %r; const go = () => { if (!document.documentElement) return false;"
                " for (const k in p) document.documentElement.style.setProperty(k, p[k]); return true; };"
                " if (!go()) new MutationObserver((_, o) => { if (go()) o.disconnect(); }).observe(document, {childList: true}); })();"
                % props)

        def on_console(msg):
            if msg.type == "error" and "Failed to load resource" not in msg.text:
                page.console_errors.append(msg.text)
        page.on("console", on_console)
        page.on("pageerror", lambda e: page.console_errors.append(str(e)))

        def bridge_handler(route):
            req = route.request
            path = req.url[len(HOST):]
            page.requests.append(f"{req.method} {path}")
            for prefix, secs in (delay or {}).items():
                if path.startswith(prefix):
                    page.wait_for_timeout(int(secs * 1000))
            headers = {k: v for k, v in req.headers.items() if k in ("content-type", "user-agent")}
            r = bridge.request(req.method, path, headers=headers, content=req.post_data_buffer)
            route.fulfill(status=r.status_code, headers={k: v for k, v in r.headers.items() if k != "content-length"}, body=r.content)

        page.route(re.compile(r"^https?://(?!homeflix\.test)"), lambda route: route.abort())  # hermetic: không mạng ngoài
        page.route(f"{HOST}/**", bridge_handler)
        page.route(f"{HOST}/proxy/image**", lambda route: route.fulfill(status=200, content_type="image/png", body=PNG_1X1))
        return page

    yield _open
    for c in contexts:
        c.close()


def tap(page, selector):
    """Một lần chạm của người dùng (chuột trên TV)."""
    page.tap(selector) if page.is_touch else page.click(selector)
    page.taps += 1
