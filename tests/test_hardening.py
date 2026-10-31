from tests.cast_helpers import real_id
import os

U1, U2 = "http://x/1.m3u8", "http://x/2.m3u8"
EPS = [{"name": "Tập 1", "link_m3u8": U1}, {"name": "Tập 2", "link_m3u8": U2}]

def saved(c, slug="phim-a"):
    return c.post("/api/saved", json={"slug": slug, "name": "Phim A", "poster_url": "/p", "year": "2026", "episodes": EPS})

def report(c, url, pos=None, dur=None, slug="phim-a"):
    body = {"slug": slug, "last_watched_episode": "Tập 1" if url == U1 else "Tập 2", "last_watched_url": url}
    if pos is not None: body["position"] = pos
    if dur is not None: body["duration"] = dur
    return c.post("/api/saved/progress", json=body).json()

def current(c, slug="phim-a"): return next(m for m in c.get("/api/saved").json() if m["slug"] == slug)

# --- #1 path traversal through slug ---
def test_dot_slugs_rejected_and_never_delete_parent_dirs(app_module, tmp_path, make_client):
    c = make_client()
    sentinel = tmp_path / "keep.txt"; sentinel.write_text("x")
    for bad in ("..", "."):
        assert c.post("/api/saved", json={"slug": bad, "name": "x"}).json() == {"status": "error", "message": "Invalid slug"}
    assert c.delete("/api/saved/%2E%2E").json() == {"status": "error", "message": "Invalid slug"}
    assert c.delete("/api/saved/%2e").json() == {"status": "error", "message": "Invalid slug"}
    assert sentinel.exists() and os.path.exists(app_module.DB_FILE)

def test_delete_never_removes_outside_download_dir(app_module, tmp_path, make_client):
    # slug hợp lệ về chữ nhưng bị chèn thẳng vào DB: realpath phải chặn rmtree ra ngoài DOWNLOAD_DIR
    c = make_client(); dev = real_id(c)
    app_module._db_upsert_device_movie(dev, {"slug": "a/../..", "name": "x", "episodes": [], "episode_states": {}})
    outside = tmp_path / "keep.txt"; outside.write_text("x")
    r = app_module._safe_rmtree_under(app_module.DOWNLOAD_DIR, os.path.join(app_module.DOWNLOAD_DIR, "a/../.."))
    assert r is False and outside.exists()

# --- #5 non-string fields ---
def test_non_string_fields_give_error_not_500(make_client):
    c = make_client(); saved(c)
    assert c.post("/api/saved", json={"slug": ["s"], "name": "x"}).json()["status"] == "error"
    for body in ({"slug": ["s"], "last_watched_url": U1}, {"slug": "phim-a", "last_watched_url": ["x"]},
                 {"slug": "phim-a", "last_watched_url": U1, "last_watched_episode": {"a": 1}}):
        r = c.post("/api/saved/progress", json=body)
        assert r.status_code == 200 and r.json()["status"] == "error"

# --- #4 POST /api/saved must not take position/duration/saved from the client ---
def test_save_ignores_client_position_and_saved(make_client):
    c = make_client()
    r = c.post("/api/saved", json={"slug": "s", "name": "S", "position": "abc", "duration": -5, "saved": False})
    assert r.status_code == 200 and r.json()["status"] == "success"
    r = c.post("/api/saved", json={"slug": "s2", "name": "S", "position": {"a": 1}})
    assert r.status_code == 200
    row = current(c, "s")
    assert row["position"] is None and row["duration"] is None and row["saved"] is True

def test_save_does_not_overwrite_server_position_with_stale_copy(make_client):
    c = make_client(); saved(c); report(c, U1, 50, 2700)
    c.post("/api/saved", json={"slug": "phim-a", "name": "Phim A", "position": 1, "duration": 9})
    assert current(c)["position"] == 50

# --- #2 late report for the previous episode ---
def test_late_report_for_previous_episode_is_ignored(app_module, make_client):
    c = make_client(); saved(c)
    report(c, U1, 1, 2700)
    report(c, U2)                       # play(ep2): chuyển tập, chưa có vị trí
    d = os.path.join(app_module.DOWNLOAD_DIR, "phim-a"); os.makedirs(d)
    open(os.path.join(d, "Tap-2.mp4"), "wb").write(b"x")
    app_module._db_update_download_status("phim-a", "Tập 2", "completed")
    report(c, U1, 2700, 2700)           # heartbeat/pause của tập 1 đến muộn
    report(c, U2, 20, 2700)
    m = current(c)
    assert m["last_watched_url"] == U2 and m["position"] == 20
    assert m["episode_states"] == {U1: "watched", U2: "watching"}
    assert app_module._db_get_download_status("phim-a", "Tập 2")["delete_at"] is None

def test_explicit_play_of_episode_clears_its_scheduled_deletion(app_module, make_client):
    c = make_client(); saved(c); report(c, U1, 1, 2700)
    d = os.path.join(app_module.DOWNLOAD_DIR, "phim-a"); os.makedirs(d)
    open(os.path.join(d, "Tap-1.mp4"), "wb").write(b"x")
    app_module._db_update_download_status("phim-a", "Tập 1", "completed")
    report(c, U2)                        # Tập 1 xong -> lên lịch xóa
    assert app_module._db_get_download_status("phim-a", "Tập 1")["delete_at"] is not None
    report(c, U1)                        # người dùng xem lại tập 1
    assert app_module._db_get_download_status("phim-a", "Tập 1")["delete_at"] is None

# --- #6 legacy adoption ---
def _legacy(m, slug):
    m.db_execute("INSERT INTO saved_movies (slug,name,poster_url,year,episode_current,total_episodes,"
                 "last_watched_episode,last_watched_url,episodes,episode_states,updated_at) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 (slug, "Cũ", "/p", "2020", "Full", 1, "", "", "[]", "{}", "2026-01-01T00:00:00"))

def test_cookieless_root_fetch_does_not_take_legacy_library(app_module, make_client):
    _legacy(app_module, "cu-1")
    make_client(ip="198.51.100.1").get("/")           # bot xem trước link / health check
    real = make_client(ip="203.0.113.7")
    assert [m["slug"] for m in real.get("/api/saved").json()] == ["cu-1"]

def test_legacy_adopted_only_once_even_after_all_devices_pruned(app_module, make_client):
    m = app_module; _legacy(m, "cu-1")
    c = make_client(); c.get("/api/saved")
    c.delete("/api/saved/cu-1")
    m._db_prune_inactive_devices(now=10**12)
    assert make_client(ip="198.51.100.9").get("/api/saved").json() == []
