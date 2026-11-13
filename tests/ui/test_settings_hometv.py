"""Settings page, HomeTV group: managing IPTV sources (admin)."""
import json
import time

import hometv
from tests.ui.conftest import tap
from tests.ui.test_settings import wait_request
from tests.ui.test_settings_admin import admin_page, server_json

GROUP = '.set-group[data-group="hometv"]'
PATH = "/api/admin/hometv/sources"
DEFAULT_IDS = ["doubo-vn", "iptv-org-vn", "dangdvd-iptv", "khanh71-http"]
ACCENT = "rgb(229, 9, 20)"
DANGER = "rgb(255, 107, 107)"


def hometv_page(open_app, **kw):
    """Admin opens Settings; the HomeTV group loads the source list after the page finishes rendering."""
    page = admin_page(open_app, **kw)
    page.wait_for_selector('[data-key="hometv.add"]')
    return page


def src(sid):
    return f'.set-source[data-id="{sid}"]'


def meta(page, sid):
    return page.locator(f"{src(sid)} .set-source-meta").inner_text()


def wait_meta(page, sid, text, present=True, timeout=4000):
    page.wait_for_function("([s, t, on]) => { const m = document.querySelector(`.set-source[data-id=\"${s}\"] .set-source-meta`);"
                           " return !!m && m.textContent.includes(t) === on; }", arg=[sid, text, present], timeout=timeout)


def source_ids(page):
    return page.evaluate(f"[...document.querySelectorAll('{GROUP} .set-source')].map(r => r.dataset.id)")


def acts(page, sid):
    """Actions currently shown for a source: [data-act, text]."""
    return page.evaluate("(s) => [...document.querySelectorAll(`.set-source[data-id=\"${s}\"] [data-act]`)]"
                         ".filter(b => b.getClientRects().length).map(b => [b.dataset.act, b.textContent])", sid)


def open_source(page, sid):
    tap(page, f"{src(sid)} > .set-action")
    page.wait_for_selector(f"{src(sid)} .set-source-actions")


def do(page, sid, name):
    tap(page, f'{src(sid)} [data-act="{name}"]')


def server_source(page, sid):
    return next(s for s in server_json(page, PATH) if s["id"] == sid)


def finish_fetch(sid, channels):
    """The background loop finishes fetching a source (TestClient doesn't run lifespan, so write straight to the DB)."""
    now = time.time()
    with hometv.db.tx() as c:
        c.execute("UPDATE sources SET last_fetch_at=?, last_success_at=?, channel_count=? WHERE id=?",
                  (now, now, channels, sid))


def set_source(sid, **cols):
    with hometv.db.tx() as c:
        c.execute(f"UPDATE sources SET {', '.join(k + '=?' for k in cols)} WHERE id=?", (*cols.values(), sid))


def patch_bodies(page):
    bodies = []
    page.on("request", lambda r: bodies.append(json.loads(r.post_data))
            if r.method == "PATCH" and PATH in r.url else None)
    return bodies


def test_hometv_lists_default_sources(pw, open_app):
    page = hometv_page(open_app)
    assert page.locator(f"{GROUP} .set-source").count() == 4
    assert source_ids(page) == DEFAULT_IDS   # by priority
    assert "duyệt" not in page.locator(GROUP).inner_text()   # default sources need no approval
    assert page.locator(f"{src('iptv-org-vn')} .set-source-name").inner_text() == "IPTV Org Vietnam"
    assert meta(page, "iptv-org-vn") == "0 kênh"   # never fetched: no time shown
    keys = page.evaluate(f"[...document.querySelectorAll('{GROUP} .set-row')].map(r => r.dataset.key || r.dataset.id)")
    assert keys == DEFAULT_IDS + ["hometv.add"]
    assert page.locator('[data-key="hometv.add"]').inner_text().strip() == "+ Thêm nguồn"
    assert page.locator('[data-key="hometv.iptv"]').count() == 0
    assert page.locator(f"{GROUP} .set-source-actions").count() == 0   # actions only open on tap
    assert page.console_errors == []


def test_add_source(pw, open_app, public_dns):
    page = hometv_page(open_app)
    tap(page, '[data-key="hometv.add"] .set-action')
    page.wait_for_selector("#hometvName")
    assert page.evaluate("document.activeElement.id") == "hometvName"
    add = page.locator('[data-key="hometv.add"] button[type="submit"]')
    assert add.inner_text() == "Thêm"
    # 422: error shown under the right field
    page.fill("#hometvName", "Nguồn thử")
    page.fill("#hometvUrl", "ftp://iptv.example/list.m3u")
    tap(page, '[data-key="hometv.add"] button[type="submit"]')
    wait_request(page, f"POST {PATH}")
    url_error = '[data-key="hometv.add"] .hometv-field:has(#hometvUrl) .set-error'
    page.wait_for_selector(url_error)
    assert page.locator(url_error).inner_text() == "URL không hợp lệ"
    assert page.locator('[data-key="hometv.add"] .hometv-field:has(#hometvName) .set-error').count() == 0
    assert page.eval_on_selector(url_error, "e => getComputedStyle(e).color") == DANGER
    assert page.locator(f"{GROUP} .set-source").count() == 4
    # fix the URL and add again
    page.fill("#hometvUrl", "https://iptv.example/list.m3u")
    tap(page, '[data-key="hometv.add"] button[type="submit"]')
    wait_request(page, f"POST {PATH}", count=2)
    page.wait_for_selector(src("nguon-thu"))
    assert meta(page, "nguon-thu") == "0 kênh"   # new sources are approved right away
    assert server_source(page, "nguon-thu")["rights_status"] == "approved"
    assert page.locator(f"{src('nguon-thu')} .set-source-name").inner_text() == "Nguồn thử"
    assert page.locator("#hometvName").count() == 0   # form collapses back into a button
    assert page.locator('[data-key="hometv.add"]').inner_text().strip() == "+ Thêm nguồn"
    assert "nguon-thu" in [s["id"] for s in server_json(page, PATH)]
    assert page.console_errors == []


def test_actions_and_disable(pw, open_app):
    page = hometv_page(open_app)
    sid = "iptv-org-vn"
    open_source(page, sid)
    assert acts(page, sid) == [["disable", "Tắt"], ["refresh", "Làm mới ngay"], ["dead", "Xem stream chết"],
                               ["edit", "Sửa"], ["delete", "Xoá"]]   # no Approve/Reject
    # red only for Delete
    reds = page.evaluate("[...document.querySelectorAll('.set-source .set-danger')].map(b => b.dataset.act)")
    assert reds == ["delete"]
    assert page.eval_on_selector(f'{src(sid)} [data-act="delete"]', "b => getComputedStyle(b).color") == ACCENT
    # Disable: row dimmed, shows "Đã tắt"
    do(page, sid, "disable")
    page.wait_for_function("(s) => document.querySelector(`.set-source[data-id=\"${s}\"]`).textContent.includes('Đã tắt')", arg=sid)
    assert float(page.eval_on_selector(f"{src(sid)} .set-main", "e => getComputedStyle(e).opacity")) < 1
    assert server_source(page, sid)["enabled"] is False
    assert ["enable", "Bật"] in acts(page, sid)
    assert "refresh" not in [a for a, _ in acts(page, sid)]   # disabled sources are not fetched
    # actions only change the row, they don't dim the buttons
    assert page.eval_on_selector(f'{src(sid)} [data-act="enable"]', "b => getComputedStyle(b).opacity") == "1"
    # Re-enable
    do(page, sid, "enable")
    page.wait_for_function("(s) => !document.querySelector(`.set-source[data-id=\"${s}\"]`).textContent.includes('Đã tắt')", arg=sid)
    assert server_source(page, sid)["enabled"] is True
    assert page.eval_on_selector(f"{src(sid)} .set-main", "e => getComputedStyle(e).opacity") == "1"
    assert page.console_errors == []


def test_leftover_pending_source_can_be_approved(pw, open_app):
    sid = "iptv-org-vn"
    set_source(sid, rights_status="pending_review")
    page = hometv_page(open_app)
    assert meta(page, sid) == "Chờ duyệt · 0 kênh"
    open_source(page, sid)
    assert acts(page, sid)[0] == ["approve", "Duyệt"]
    do(page, sid, "approve")
    wait_request(page, f"PATCH {PATH}/{sid}")
    wait_meta(page, sid, "Chờ duyệt", present=False)
    assert server_source(page, sid)["rights_status"] == "approved"
    assert "approve" not in [a for a, _ in acts(page, sid)]


def test_refresh_polls_until_done(pw, open_app):
    page = hometv_page(open_app, clock=True)
    sid = "doubo-vn"
    entry = f"GET {PATH}"
    n = page.requests.count(entry)
    page.clock.fast_forward(6100)
    page.wait_for_timeout(300)
    assert page.requests.count(entry) == n   # no source fetching: no auto-reload
    open_source(page, sid)
    # tap Refresh now: the server only queues a run (the background loop picks it up within ≤ 60 seconds), no job running yet
    do(page, sid, "refresh")
    wait_request(page, f"POST {PATH}/{sid}/refresh")
    wait_meta(page, sid, "Đang tải…")
    assert meta(page, sid) == "0 kênh · Đang tải…"
    wait_request(page, entry, count=n + 1)   # reloads after the tap
    n = page.requests.count(entry)
    page.clock.fast_forward(3100)
    wait_request(page, entry, count=n + 1)   # not done fetching: reload once every 3 seconds
    assert meta(page, sid) == "0 kênh · Đang tải…"
    finish_fetch(sid, channels=12)
    page.clock.fast_forward(3100)
    wait_meta(page, sid, "Đang tải…", present=False)
    assert meta(page, sid) == "12 kênh · vừa xong"
    # actions stay open, focus still on the Refresh now button
    assert page.evaluate("document.activeElement.dataset.act") == "refresh"
    n = page.requests.count(entry)
    page.clock.fast_forward(10000)
    page.wait_for_timeout(300)
    assert page.requests.count(entry) == n   # no sources fetching anymore: stop reloading
    # background loop doesn't pick up the run: after 90 seconds stop waiting, go back to the normal subtitle and stop reloading
    do(page, sid, "refresh")
    wait_meta(page, sid, "Đang tải…")
    page.clock.fast_forward(91000)
    wait_meta(page, sid, "Đang tải…", present=False)
    n = page.requests.count(entry)
    page.clock.fast_forward(10000)
    page.wait_for_timeout(300)
    assert page.requests.count(entry) == n
    assert page.console_errors == []


def test_error_shown_in_danger_color(pw, open_app):
    set_source("khanh71-http", last_error="HTTP 404", last_fetch_at=time.time())
    page = hometv_page(open_app)
    err = f"{src('khanh71-http')} .set-source-error"
    assert page.locator(err).inner_text() == "Lỗi: HTTP 404 (giữ bản cũ)"
    assert page.eval_on_selector(err, "e => getComputedStyle(e).color") == DANGER
    assert page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--danger')").strip() == "#ff6b6b"
    assert page.locator(f"{src('doubo-vn')} .set-source-error").count() == 0
    assert page.locator(err).count() == 1


def test_delete_confirms(pw, open_app):
    page = hometv_page(open_app)
    sid = "doubo-vn"
    messages = []

    def dismiss(d):
        messages.append(d.message)
        d.dismiss()
    page.once("dialog", dismiss)
    open_source(page, sid)
    do(page, sid, "delete")
    page.wait_for_timeout(300)
    assert messages == ["Xoá nguồn Doubo Vietnam? Các kênh chỉ có ở nguồn này sẽ mất."]
    assert not any(r.startswith("DELETE ") for r in page.requests)
    assert page.locator(src(sid)).count() == 1
    page.once("dialog", lambda d: d.accept())
    do(page, sid, "delete")
    wait_request(page, f"DELETE {PATH}/{sid}")
    page.wait_for_selector(src(sid), state="detached")
    assert sid not in [s["id"] for s in server_json(page, PATH)]
    assert source_ids(page) == DEFAULT_IDS[1:]
    assert page.console_errors == []


def test_edit_priority_and_interval(pw, open_app):
    page = hometv_page(open_app)
    sid = "iptv-org-vn"
    bodies = patch_bodies(page)
    open_source(page, sid)
    do(page, sid, "edit")
    page.wait_for_selector(f'{src(sid)} [data-key="hometv.priority"]')
    assert page.get_attribute(f'{src(sid)} [data-act="edit"]', "aria-expanded") == "true"
    assert page.evaluate("window.innerWidth") == 430   # a long URL in the Edit block doesn't widen the page (narrow phone)
    assert page.locator(f'{src(sid)} [data-key="hometv.priority"] .set-value').inner_text() == "20"
    assert page.locator(f'{src(sid)} [data-key="hometv.refresh_hours"] .set-value').inner_text() == "6 giờ"
    assert page.locator(f'{src(sid)} [data-key="hometv.name"] .set-text-value').inner_text() == "IPTV Org Vietnam"
    tap(page, f'{src(sid)} [data-key="hometv.priority"] .set-step[data-dir="1"]')
    tap(page, f'{src(sid)} [data-key="hometv.refresh_hours"] .set-step[data-dir="1"]')
    wait_request(page, f"PATCH {PATH}/{sid}", count=2)
    page.wait_for_timeout(200)
    assert sorted(bodies, key=json.dumps) == [{"priority": 21}, {"refresh_hours": 7}]
    s = server_source(page, sid)
    assert (s["priority"], s["refresh_hours"]) == (21, 7)
    # name edited inline in the row
    tap(page, f'{src(sid)} [data-key="hometv.name"] .set-text')
    page.fill(f'{src(sid)} [data-key="hometv.name"] input', "  IPTV Org VN  ")
    page.press(f'{src(sid)} [data-key="hometv.name"] input', "Enter")
    wait_request(page, f"PATCH {PATH}/{sid}", count=3)
    page.wait_for_function("(s) => document.querySelector(`.set-source[data-id=\"${s}\"] .set-source-name`)"
                           ".textContent === 'IPTV Org VN'", arg=sid)
    assert bodies[-1] == {"name": "IPTV Org VN"}
    assert page.locator(f'{src(sid)} [data-key="hometv.name"] .set-text-value').inner_text() == "IPTV Org VN"
    # bad URL: 422 shown under the URL row, old URL kept
    tap(page, f'{src(sid)} [data-key="hometv.url"] .set-text')
    page.fill(f'{src(sid)} [data-key="hometv.url"] input', "ftp://x.example/a.m3u")
    page.press(f'{src(sid)} [data-key="hometv.url"] input', "Enter")
    page.wait_for_selector(f'{src(sid)} [data-key="hometv.url"] .set-error')
    assert page.locator(f'{src(sid)} [data-key="hometv.url"] .set-error').inner_text() == "URL không hợp lệ"
    assert page.locator(f'{src(sid)} [data-key="hometv.url"] .set-text-value').inner_text() == \
        "https://iptv-org.github.io/iptv/countries/vn.m3u"
    assert server_source(page, sid)["url"] == "https://iptv-org.github.io/iptv/countries/vn.m3u"
    # Edit again: collapses the edit block
    do(page, sid, "edit")
    assert page.locator(f'{src(sid)} [data-key="hometv.priority"]').count() == 0
    assert page.console_errors == []


# --- Stream health (package 4) ---
import pytest  # noqa: E402

from hometv import geo, health_store  # noqa: E402
from hometv.geo import HealthMode  # noqa: E402
from hometv.health import HealthResult  # noqa: E402
from tests.hometv.test_hometv_catalog import seed_channel, seed_stream  # noqa: E402

STATUS = ".hometv-health-status"


def geo_is(monkeypatch, enabled, country="VN"):
    monkeypatch.setattr(geo, "current", lambda now=None: HealthMode("auto", enabled, country, 1.0))


def approve(sid="doubo-vn"):
    set_source(sid, rights_status="approved")


def seed_health(sid="doubo-vn", dead_name="Kênh chết", streak=3, alive=1):
    c = hometv.db.conn()
    seed_channel(c, 1, dead_name)
    for i in range(1, alive + 1):
        seed_stream(c, 10 + i, 1, sid, "https://ok.example/%d.m3u8" % i)
        health_store.record(10 + i, HealthResult(ok=True, height=720), True, 1.0)
    seed_stream(c, 99, 1, sid, "https://dead.example/x.m3u8")
    for n in range(streak):
        health_store.record(99, HealthResult(ok=False, error="HTTP 403"), True, float(n))


@pytest.mark.parametrize("enabled,country,text", [
    (True, "VN", "Kiểm tra stream: bật (máy chủ ở VN)"),
    (False, "MY", "Kiểm tra stream: tắt (máy chủ ngoài Việt Nam)"),
    (False, None, "Kiểm tra stream: tắt (chưa xác định vị trí máy chủ)"),
])
def test_status_row(pw, open_app, monkeypatch, enabled, country, text):
    geo_is(monkeypatch, enabled, country)
    page = hometv_page(open_app)
    page.wait_for_selector(STATUS)
    assert page.locator(STATUS).inner_text() == text
    assert page.locator(f"{GROUP} .set-row").count() == 5   # the status row is not a .set-row (doesn't mix into the source list)


def test_meta_shows_alive_over_checked(pw, open_app, monkeypatch):
    geo_is(monkeypatch, True)
    approve()
    seed_health(alive=2)
    page = hometv_page(open_app)
    wait_meta(page, "doubo-vn", "2/3 sống")
    assert meta(page, "doubo-vn") == "0 kênh · 2/3 sống"
    assert "sống" not in meta(page, "iptv-org-vn")


def test_meta_shows_checking_and_polls_until_done(pw, open_app, monkeypatch):
    geo_is(monkeypatch, True)
    approve()
    seed_health()
    health_store.checking.add("doubo-vn")
    try:
        page = hometv_page(open_app, clock=True)
        wait_meta(page, "doubo-vn", "Đang kiểm tra…")
        health_store.checking.discard("doubo-vn")
        page.clock.fast_forward(3100)
        wait_meta(page, "doubo-vn", "Đang kiểm tra…", present=False)
    finally:
        health_store.checking.discard("doubo-vn")


def test_check_now_visibility(pw, open_app, monkeypatch):
    geo_is(monkeypatch, True)
    approve()
    set_source("iptv-org-vn", rights_status="pending_review")   # leftover non-approved source
    set_source("dangdvd-iptv", enabled=0)
    page = hometv_page(open_app)
    for sid, shown in (("doubo-vn", True), ("iptv-org-vn", False), ("dangdvd-iptv", False)):
        open_source(page, sid)
        assert (["check", "Kiểm tra ngay"] in acts(page, sid)) is shown, sid
    geo_is(monkeypatch, False, "MY")
    page = hometv_page(open_app)
    open_source(page, "doubo-vn")
    assert ["check", "Kiểm tra ngay"] not in acts(page, "doubo-vn")


def test_check_now_posts_and_shows_checking(pw, open_app, monkeypatch):
    geo_is(monkeypatch, True)
    approve()
    page = hometv_page(open_app, clock=True)
    open_source(page, "doubo-vn")
    do(page, "doubo-vn", "check")
    wait_request(page, f"POST {PATH}/doubo-vn/check")
    wait_meta(page, "doubo-vn", "Đang kiểm tra…")
    health_store.checking.add("doubo-vn")   # background loop picks up the run
    try:
        page.clock.fast_forward(3100)
        page.wait_for_timeout(200)
        assert "Đang kiểm tra…" in meta(page, "doubo-vn")
    finally:
        health_store.checking.discard("doubo-vn")
    # once seen on then off, it clears immediately; if the run is faster than the reload cycle, it clears after QUEUED_MAX_MS
    page.clock.fast_forward(3100)
    page.clock.fast_forward(91000)
    wait_meta(page, "doubo-vn", "Đang kiểm tra…", present=False)


def test_dead_list_shows_reason_and_streak(pw, open_app, monkeypatch):
    geo_is(monkeypatch, True)
    approve()
    seed_health(dead_name="VTV9", streak=3)
    page = hometv_page(open_app)
    open_source(page, "doubo-vn")
    do(page, "doubo-vn", "dead")
    page.wait_for_selector(f"{src('doubo-vn')} .hometv-dead-item")
    assert page.locator(f"{src('doubo-vn')} .hometv-dead-item").all_inner_texts() == ["VTV9 · HTTP 403 · chết 3 lần"]


def test_dead_list_empty_message(pw, open_app, monkeypatch):
    geo_is(monkeypatch, True)
    approve()
    page = hometv_page(open_app)
    open_source(page, "doubo-vn")
    do(page, "doubo-vn", "dead")
    page.wait_for_selector(f"{src('doubo-vn')} .hometv-dead-empty")
    assert page.locator(f"{src('doubo-vn')} .hometv-dead-empty").inner_text() == "Không có stream chết"


def test_dead_list_escapes_html_in_channel_name(pw, open_app, monkeypatch):
    geo_is(monkeypatch, True)
    approve()
    seed_health(dead_name="<img src=x onerror=window.__pwned=1>")
    page = hometv_page(open_app)
    open_source(page, "doubo-vn")
    do(page, "doubo-vn", "dead")
    page.wait_for_selector(f"{src('doubo-vn')} .hometv-dead-item")
    assert page.locator(f"{src('doubo-vn')} .hometv-dead-item img").count() == 0
    assert page.evaluate("window.__pwned") is None
