import os

EP = [{"name": "Tập 1", "link_m3u8": "http://x/1.m3u8"}]
def body(slug="phim-a"): return {"slug": slug, "name": "Phim A", "poster_url": "/p", "year": "2026", "episodes": EP}

def test_save_and_list_are_isolated_per_device(make_client):
    a, b = make_client(), make_client()
    r = a.post("/api/saved", json=body())
    assert r.json()["status"] == "success" and r.json()["movie"]["total_episodes"] == 1
    got = a.get("/api/saved").json()
    assert [m["slug"] for m in got] == ["phim-a"] and got[0]["saved"] is True
    assert b.get("/api/saved").json() == []

def test_missing_slug_keeps_old_error_shape(make_client):
    assert make_client().post("/api/saved", json={"name": "x"}).json() == {"status": "error", "message": "Missing slug"}

def test_saved_responses_are_private(make_client):
    r = make_client().get("/api/saved")
    assert r.headers["cache-control"] == "private, no-store"

def test_delete_unknown_movie_keeps_old_error_shape(make_client):
    assert make_client().delete("/api/saved/none").json() == {"status": "error", "message": "Movie not found"}

def _plant_files(app_module, slug):
    d = os.path.join(app_module.DOWNLOAD_DIR, slug); os.makedirs(d)
    open(os.path.join(d, "Tap-1.mp4"), "wb").write(b"x")
    sid = __import__("hashlib").md5(b"http://x/1.m3u8").hexdigest()
    s = os.path.join(app_module.CACHE_DIR, sid); os.makedirs(s)
    app_module._db_update_download_status(slug, "Tập 1", "completed")
    return d, s

def test_delete_keeps_shared_files_while_another_device_holds_movie(app_module, make_client):
    a, b = make_client(), make_client()
    a.post("/api/saved", json=body()); b.post("/api/saved", json=body())
    dl, cache = _plant_files(app_module, "phim-a")
    assert a.delete("/api/saved/phim-a").json()["status"] == "success"
    assert os.path.exists(dl) and os.path.exists(cache)
    assert app_module._db_get_download_status("phim-a", "Tập 1")["status"] == "completed"
    assert b.delete("/api/saved/phim-a").json()["status"] == "success"
    assert not os.path.exists(dl) and not os.path.exists(cache)
    assert app_module._db_get_download_status("phim-a", "Tập 1")["status"] == "not_started"
