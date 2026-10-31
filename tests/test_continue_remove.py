import os

from tests.test_progress_api import EPS, U1, U2, prog, saved
from tests.test_saved_api import _plant_files


def rows(c): return {m["slug"]: m for m in c.get("/api/saved").json()}

def history(c, slug="phim-a"):
    prog(c, U1, 30, 2700, slug=slug, name="Phim A", poster_url="/p", year="2026", episodes=EPS)

def remove(c, slug="phim-a"): return c.delete(f"/api/saved/{slug}/progress").json()


def test_saved_movie_leaves_continue_but_stays_saved(make_client):
    c = make_client(); saved(c); prog(c, U1, 30, 2700); prog(c, U2, 600, 2700)
    r = remove(c)
    assert r["status"] == "success"
    m = rows(c)["phim-a"]
    assert m["saved"] is True
    assert m["last_watched_url"] == "" and m["last_watched_episode"] == ""
    assert m["position"] is None and m["duration"] is None
    assert m["episode_states"] == {U1: "watched"}  # tập đã xem giữ dấu, tập đang dở bỏ
    assert r["movie"]["last_watched_url"] == ""


def test_history_only_movie_is_deleted(make_client):
    c = make_client(); history(c)
    r = remove(c)
    assert r == {"status": "success", "movie": None}
    assert "phim-a" not in rows(c)


def test_history_only_movie_cleans_files_only_when_no_other_device_holds_it(app_module, make_client):
    a, b = make_client(), make_client()
    history(a); history(b)
    dl, cache = _plant_files(app_module, "phim-a")
    remove(a)
    assert os.path.exists(dl) and os.path.exists(cache)
    remove(b)
    assert not os.path.exists(dl) and not os.path.exists(cache)


def test_saved_movie_keeps_its_files(app_module, make_client):
    c = make_client(); saved(c); prog(c, U1, 30, 2700)
    dl, cache = _plant_files(app_module, "phim-a")
    remove(c)
    assert os.path.exists(dl) and os.path.exists(cache)


def test_watching_again_after_removal_does_not_schedule_deleting_current_episode(app_module, make_client):
    c = make_client(); saved(c); prog(c, U1, 30, 2700)
    remove(c)
    d = os.path.join(app_module.DOWNLOAD_DIR, "phim-a"); os.makedirs(d)
    open(os.path.join(d, "Tap-1.mp4"), "wb").write(b"x")
    app_module._db_update_download_status("phim-a", "Tập 1", "completed")
    prog(c, U1, 40, 2700)
    assert app_module._db_get_download_status("phim-a", "Tập 1")["delete_at"] is None
    assert rows(c)["phim-a"]["last_watched_url"] == U1


def test_removal_is_per_device(make_client):
    a, b = make_client(), make_client()
    saved(a); prog(a, U1, 30, 2700); saved(b); prog(b, U1, 30, 2700)
    remove(a)
    assert rows(b)["phim-a"]["last_watched_url"] == U1


def test_unknown_and_invalid_slugs(make_client):
    c = make_client()
    assert remove(c, "none") == {"status": "error", "message": "Movie not found"}
    assert c.delete("/api/saved/%2E%2E/progress").json()["status"] == "error"
