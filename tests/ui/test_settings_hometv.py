"""Trang Cài đặt, nhóm HomeTV: quản lý nguồn IPTV (admin)."""
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
    """Admin mở Cài đặt; nhóm HomeTV tải danh sách nguồn sau khi trang dựng xong."""
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
    """Các thao tác đang hiện của một nguồn: [data-act, chữ]."""
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
    """Vòng lặp nền tải xong một nguồn (TestClient không chạy lifespan nên ghi thẳng vào DB)."""
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
    assert source_ids(page) == DEFAULT_IDS   # theo ưu tiên
    assert "Chờ duyệt" in page.locator(src("iptv-org-vn")).inner_text()
    assert page.locator(f"{src('iptv-org-vn')} .set-source-name").inner_text() == "IPTV Org Vietnam"
    assert meta(page, "iptv-org-vn") == "Chờ duyệt · 0 kênh"   # chưa tải lần nào: không ghi giờ
    keys = page.evaluate(f"[...document.querySelectorAll('{GROUP} .set-row')].map(r => r.dataset.key || r.dataset.id)")
    assert keys == DEFAULT_IDS + ["hometv.add"]
    assert page.locator('[data-key="hometv.add"]').inner_text().strip() == "+ Thêm nguồn"
    assert page.locator('[data-key="hometv.iptv"]').count() == 0
    assert page.locator(f"{GROUP} .set-source-actions").count() == 0   # thao tác chỉ mở khi chạm
    assert page.console_errors == []


def test_add_source(pw, open_app, public_dns):
    page = hometv_page(open_app)
    tap(page, '[data-key="hometv.add"] .set-action')
    page.wait_for_selector("#hometvName")
    assert page.evaluate("document.activeElement.id") == "hometvName"
    add = page.locator('[data-key="hometv.add"] button[type="submit"]')
    assert add.inner_text() == "Thêm"
    # 422: lỗi hiện dưới đúng ô
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
    # sửa URL rồi thêm lại
    page.fill("#hometvUrl", "https://iptv.example/list.m3u")
    tap(page, '[data-key="hometv.add"] button[type="submit"]')
    wait_request(page, f"POST {PATH}", count=2)
    page.wait_for_selector(src("nguon-thu"))
    assert meta(page, "nguon-thu") == "Chờ duyệt · 0 kênh"
    assert page.locator(f"{src('nguon-thu')} .set-source-name").inner_text() == "Nguồn thử"
    assert page.locator("#hometvName").count() == 0   # form thu lại thành nút
    assert page.locator('[data-key="hometv.add"]').inner_text().strip() == "+ Thêm nguồn"
    assert "nguon-thu" in [s["id"] for s in server_json(page, PATH)]
    assert page.console_errors == []


def test_approve_and_disable(pw, open_app):
    page = hometv_page(open_app)
    sid = "iptv-org-vn"
    open_source(page, sid)
    assert acts(page, sid) == [["approve", "Duyệt"], ["reject", "Từ chối"], ["disable", "Tắt"],
                               ["refresh", "Làm mới ngay"], ["edit", "Sửa"], ["delete", "Xoá"]]
    # màu đỏ chỉ cho Xoá
    reds = page.evaluate("[...document.querySelectorAll('.set-source .set-danger')].map(b => b.dataset.act)")
    assert reds == ["delete"]
    assert page.eval_on_selector(f'{src(sid)} [data-act="delete"]', "b => getComputedStyle(b).color") == ACCENT
    do(page, sid, "approve")
    wait_request(page, f"PATCH {PATH}/{sid}")
    wait_meta(page, sid, "Đã duyệt")
    assert server_source(page, sid)["rights_status"] == "approved"
    assert acts(page, sid)[0] == ["unapprove", "Bỏ duyệt"]
    do(page, sid, "unapprove")
    wait_request(page, f"PATCH {PATH}/{sid}", count=2)
    wait_meta(page, sid, "Chờ duyệt")
    assert server_source(page, sid)["rights_status"] == "pending_review"
    assert acts(page, sid)[0] == ["approve", "Duyệt"]
    # Tắt: dòng mờ, ghi "Đã tắt"
    do(page, sid, "disable")
    page.wait_for_function("(s) => document.querySelector(`.set-source[data-id=\"${s}\"]`).textContent.includes('Đã tắt')", arg=sid)
    assert float(page.eval_on_selector(f"{src(sid)} .set-main", "e => getComputedStyle(e).opacity")) < 1
    assert server_source(page, sid)["enabled"] is False
    assert ["enable", "Bật"] in acts(page, sid)
    assert "refresh" not in [a for a, _ in acts(page, sid)]   # nguồn tắt không được tải
    # thao tác chỉ đổi dòng, không làm mờ các nút
    assert page.eval_on_selector(f'{src(sid)} [data-act="enable"]', "b => getComputedStyle(b).opacity") == "1"
    # Bật lại
    do(page, sid, "enable")
    page.wait_for_function("(s) => !document.querySelector(`.set-source[data-id=\"${s}\"]`).textContent.includes('Đã tắt')", arg=sid)
    assert server_source(page, sid)["enabled"] is True
    assert page.eval_on_selector(f"{src(sid)} .set-main", "e => getComputedStyle(e).opacity") == "1"
    assert page.console_errors == []


def test_refresh_polls_until_done(pw, open_app):
    page = hometv_page(open_app, clock=True)
    sid = "doubo-vn"
    entry = f"GET {PATH}"
    n = page.requests.count(entry)
    page.clock.fast_forward(6100)
    page.wait_for_timeout(300)
    assert page.requests.count(entry) == n   # không có nguồn đang tải: không tự tải lại
    open_source(page, sid)
    # bấm Làm mới ngay: server chỉ xếp lượt (vòng lặp nền nhận trong ≤ 60 giây), chưa có job nào chạy
    do(page, sid, "refresh")
    wait_request(page, f"POST {PATH}/{sid}/refresh")
    wait_meta(page, sid, "Đang tải…")
    assert meta(page, sid) == "Chờ duyệt · 0 kênh · Đang tải…"
    wait_request(page, entry, count=n + 1)   # tải lại sau khi bấm
    n = page.requests.count(entry)
    page.clock.fast_forward(3100)
    wait_request(page, entry, count=n + 1)   # chưa tải xong: 3 giây tải lại một lần
    assert meta(page, sid) == "Chờ duyệt · 0 kênh · Đang tải…"
    finish_fetch(sid, channels=12)
    page.clock.fast_forward(3100)
    wait_meta(page, sid, "Đang tải…", present=False)
    assert meta(page, sid) == "Chờ duyệt · 12 kênh · vừa xong"
    # thao tác vẫn mở, focus còn trên nút Làm mới ngay
    assert page.evaluate("document.activeElement.dataset.act") == "refresh"
    n = page.requests.count(entry)
    page.clock.fast_forward(10000)
    page.wait_for_timeout(300)
    assert page.requests.count(entry) == n   # hết nguồn đang tải: thôi tải lại
    # vòng lặp nền không nhận lượt: sau 90 giây thôi chờ, về dòng phụ thường và thôi tải lại
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
    assert page.evaluate("window.innerWidth") == 430   # URL dài trong khối Sửa không nới trang (điện thoại thu nhỏ)
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
    # tên sửa ngay trong dòng
    tap(page, f'{src(sid)} [data-key="hometv.name"] .set-text')
    page.fill(f'{src(sid)} [data-key="hometv.name"] input', "  IPTV Org VN  ")
    page.press(f'{src(sid)} [data-key="hometv.name"] input', "Enter")
    wait_request(page, f"PATCH {PATH}/{sid}", count=3)
    page.wait_for_function("(s) => document.querySelector(`.set-source[data-id=\"${s}\"] .set-source-name`)"
                           ".textContent === 'IPTV Org VN'", arg=sid)
    assert bodies[-1] == {"name": "IPTV Org VN"}
    assert page.locator(f'{src(sid)} [data-key="hometv.name"] .set-text-value').inner_text() == "IPTV Org VN"
    # URL sai: 422 hiện dưới dòng URL, giữ URL cũ
    tap(page, f'{src(sid)} [data-key="hometv.url"] .set-text')
    page.fill(f'{src(sid)} [data-key="hometv.url"] input', "ftp://x.example/a.m3u")
    page.press(f'{src(sid)} [data-key="hometv.url"] input', "Enter")
    page.wait_for_selector(f'{src(sid)} [data-key="hometv.url"] .set-error')
    assert page.locator(f'{src(sid)} [data-key="hometv.url"] .set-error').inner_text() == "URL không hợp lệ"
    assert page.locator(f'{src(sid)} [data-key="hometv.url"] .set-text-value').inner_text() == \
        "https://iptv-org.github.io/iptv/countries/vn.m3u"
    assert server_source(page, sid)["url"] == "https://iptv-org.github.io/iptv/countries/vn.m3u"
    # Sửa lần nữa: thu khối sửa lại
    do(page, sid, "edit")
    assert page.locator(f'{src(sid)} [data-key="hometv.priority"]').count() == 0
    assert page.console_errors == []
