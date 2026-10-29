def test_prune_removes_old_devices_and_their_movies_only(app_module):
    m = app_module
    old = m._db_touch_device("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "h", "phone", "ua", 1000.0)["id"]
    new = m._db_touch_device("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "h", "phone", "ua", 1000.0 + 181*86400)["id"]
    mv = {"slug": "s", "name": "S", "episodes": [], "episode_states": {}}
    m._db_upsert_device_movie(old, mv); m._db_upsert_device_movie(new, mv)
    assert m._db_prune_inactive_devices(now=1000.0 + 181*86400 + 1) == 1
    assert m._db_list_device_movies(old) == [] and len(m._db_list_device_movies(new)) == 1
    assert m.db_query_one("SELECT COUNT(*) FROM devices")[0] == 1

def test_prune_keeps_device_exactly_at_limit(app_module):
    m = app_module
    d = m._db_touch_device("cccccccc-cccc-4ccc-8ccc-cccccccccccc", "h", "tv", "ua", 1000.0)["id"]
    assert m._db_prune_inactive_devices(now=1000.0 + 180*86400) == 0
    assert m.db_query_one("SELECT COUNT(*) FROM devices WHERE id=?", (d,))[0] == 1
