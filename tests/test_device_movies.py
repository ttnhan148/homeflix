A, B = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

def movie(slug="phim-a", **kw):
    base = {"slug": slug, "name": "Phim A", "poster_url": "/p.jpg", "year": "2026",
            "episodes": [{"name": "Tập 1", "link_m3u8": "http://x/1.m3u8"}], "episode_states": {}}
    base.update(kw); return base

def test_devices_do_not_see_each_others_movies(app_module):
    m = app_module
    m._db_upsert_device_movie(A, movie())
    assert [x["slug"] for x in m._db_list_device_movies(A)] == ["phim-a"]
    assert m._db_list_device_movies(B) == []
    assert m._db_get_device_movie(B, "phim-a") is None

def test_roundtrip_types(app_module):
    m = app_module
    m._db_upsert_device_movie(A, movie(position=61.5, duration=2700.0, last_watched_url="http://x/1.m3u8"))
    got = m._db_get_device_movie(A, "phim-a")
    assert got["saved"] is True and got["position"] == 61.5 and got["duration"] == 2700.0
    assert got["episodes"][0]["name"] == "Tập 1" and got["episode_states"] == {}

def test_upsert_without_position_keeps_old_position(app_module):
    m = app_module
    m._db_upsert_device_movie(A, movie(position=10.0, duration=100.0))
    m._db_upsert_device_movie(A, movie(name="Đổi tên"))
    got = m._db_get_device_movie(A, "phim-a")
    assert got["name"] == "Đổi tên" and got["position"] == 10.0

def test_list_includes_unsaved_movie_with_progress_only(app_module):
    m = app_module
    m._db_upsert_device_movie(A, movie("chi-xem", saved=False, position=5.0))
    m._db_upsert_device_movie(A, movie("khong-gi", saved=False))
    assert [x["slug"] for x in m._db_list_device_movies(A)] == ["chi-xem"]

def test_delete_and_other_devices_hold(app_module):
    m = app_module
    m._db_upsert_device_movie(A, movie()); m._db_upsert_device_movie(B, movie())
    assert m._db_other_devices_hold(A, "phim-a") is True
    assert m._db_delete_device_movie(A, "phim-a") is True
    assert m._db_other_devices_hold(B, "phim-a") is False
    assert m._db_delete_device_movie(A, "phim-a") is False

def test_all_device_movies_one_per_slug_latest(app_module):
    m = app_module
    m._db_upsert_device_movie(A, movie(name="Cũ")); m._db_upsert_device_movie(B, movie(name="Mới"))
    rows = m._db_all_device_movies()
    assert len(rows) == 1 and rows[0]["name"] == "Mới"

def _legacy(m, slug):
    m.db_execute("INSERT INTO saved_movies (slug,name,poster_url,year,episode_current,total_episodes,"
                 "last_watched_episode,last_watched_url,episodes,episode_states,updated_at) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 (slug, "Cũ "+slug, "/p", "2020", "Full", 1, "Tập 1", "u", '[{"name":"Tập 1","link_m3u8":"u"}]', '{"u":"watching"}', "2026-01-01T00:00:00"))

def test_first_device_adopts_legacy_movies_once(app_module, make_client):
    m = app_module
    _legacy(m, "cu-1"); _legacy(m, "cu-2")
    first = make_client(ip="203.0.113.7"); first.get("/api/device")
    got = {x["slug"]: x for x in m._db_list_device_movies(first.get("/api/device").json()["id"])}
    assert set(got) == {"cu-1", "cu-2"} and got["cu-1"]["saved"] is True
    assert got["cu-1"]["episode_states"] == {"u": "watching"} and got["cu-1"]["position"] is None
    second = make_client(ip="198.51.100.9")
    assert m._db_list_device_movies(second.get("/api/device").json()["id"]) == []
    assert m.db_query_one("SELECT COUNT(*) FROM saved_movies")[0] == 2

def test_no_adoption_when_no_legacy_rows(app_module, make_client):
    c = make_client(); c.get("/api/device")
    assert app_module.db_query_one("SELECT COUNT(*) FROM device_movies")[0] == 0
