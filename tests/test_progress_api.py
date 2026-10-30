import json, os, pytest

U1, U2 = "http://x/1.m3u8", "http://x/2.m3u8"
EPS = [{"name": "Tập 1", "link_m3u8": U1}, {"name": "Tập 2", "link_m3u8": U2}]

def saved(c, slug="phim-a"):
    c.post("/api/saved", json={"slug": slug, "name": "Phim A", "poster_url": "/p", "year": "2026", "episodes": EPS})

def prog(c, url, pos=None, dur=None, slug="phim-a", ep=None, **extra):
    body = {"slug": slug, "last_watched_episode": ep or ("Tập 1" if url == U1 else "Tập 2"), "last_watched_url": url, **extra}
    if pos is not None: body["position"] = pos
    if dur is not None: body["duration"] = dur
    return c.post("/api/saved/progress", json=body).json()

def current(c, slug="phim-a"): return next(m for m in c.get("/api/saved").json() if m["slug"] == slug)

def test_progress_stores_position_and_states(make_client):
    c = make_client(); saved(c)
    prog(c, U1, 30, 2700); prog(c, U2, 5, 2700)
    m = current(c)
    assert m["episode_states"] == {U1: "watched", U2: "watching"}
    assert m["last_watched_url"] == U2 and m["position"] == 5 and m["duration"] == 2700

def test_same_episode_heartbeat_only_updates_position(app_module, make_client):
    c = make_client(); saved(c)
    prog(c, U1, 1, 2700); prog(c, U2, 1, 2700)
    d = os.path.join(app_module.DOWNLOAD_DIR, "phim-a"); os.makedirs(d)
    open(os.path.join(d, "Tap-2.mp4"), "wb").write(b"x")
    app_module._db_update_download_status("phim-a", "Tập 2", "completed")
    for pos in (10, 20, 30):
        assert prog(c, U2, pos, 2700)["status"] == "success"
    st = app_module._db_get_download_status("phim-a", "Tập 2")
    assert st["delete_at"] is None
    m = current(c)
    assert m["episode_states"] == {U1: "watched", U2: "watching"} and m["position"] == 30

def test_switching_episode_schedules_deletion_of_finished_one(app_module, make_client):
    c = make_client(); saved(c); prog(c, U1, 1, 2700)
    d = os.path.join(app_module.DOWNLOAD_DIR, "phim-a"); os.makedirs(d)
    open(os.path.join(d, "Tap-1.mp4"), "wb").write(b"x")
    app_module._db_update_download_status("phim-a", "Tập 1", "completed")
    prog(c, U2, 0, 2700)
    assert app_module._db_get_download_status("phim-a", "Tập 1")["delete_at"] is not None

def test_no_deletion_scheduled_when_other_device_holds_movie(app_module, make_client):
    a, b = make_client(), make_client(); saved(a); saved(b); prog(a, U1, 1, 2700)
    d = os.path.join(app_module.DOWNLOAD_DIR, "phim-a"); os.makedirs(d)
    open(os.path.join(d, "Tap-1.mp4"), "wb").write(b"x")
    app_module._db_update_download_status("phim-a", "Tập 1", "completed")
    prog(a, U2, 0, 2700)
    assert app_module._db_get_download_status("phim-a", "Tập 1")["delete_at"] is None

def test_progress_is_per_device(make_client):
    a, b = make_client(), make_client(); saved(a); saved(b)
    prog(a, U2, 99, 2700)
    assert current(b)["last_watched_url"] in ("", None) and current(b)["position"] is None

@pytest.mark.parametrize("bad", ["abc", None, float("nan"), float("inf"), [1], {"a": 1}])
def test_garbage_position_rejected_without_writing(make_client, bad):
    c = make_client(); saved(c); prog(c, U1, 40, 2700)
    # httpx từ chối NaN/inf trong json=, nên gửi JSON thô như một client không chuẩn có thể làm
    raw = json.dumps({"slug": "phim-a", "last_watched_episode": "Tập 1", "last_watched_url": U1,
                      "position": bad, "duration": 2700})
    r = c.post("/api/saved/progress", content=raw, headers={"content-type": "application/json"})
    assert r.status_code == 200
    if bad is None:
        assert r.json()["status"] == "success"
    else:
        assert r.json() == {"status": "error", "message": "Invalid position"}
    assert current(c)["position"] == 40

def test_negative_and_oversized_position_are_clamped(make_client):
    c = make_client(); saved(c)
    prog(c, U1, -5, 2700); assert current(c)["position"] == 0
    prog(c, U1, 99999, 2700); assert current(c)["position"] == 2700

def test_unsaved_movie_with_name_creates_history_row(make_client):
    c = make_client()
    r = prog(c, U1, 12, 2700, name="Phim B", poster_url="/b", episodes=EPS)
    assert r["status"] == "success"
    row = current(c); assert row["saved"] is False and row["position"] == 12

def test_unsaved_movie_without_name_keeps_old_error(make_client):
    assert prog(make_client(), U1, 1, 2700) == {"status": "error", "message": "Movie not in saved list"}
