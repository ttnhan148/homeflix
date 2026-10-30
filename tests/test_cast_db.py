import time


def test_allow_is_per_pair_and_idempotent(app_module):
    m = app_module
    assert not m._db_cast_is_allowed("tv", "phone")
    m._db_cast_allow("tv", "phone")
    m._db_cast_allow("tv", "phone")
    assert m._db_cast_is_allowed("tv", "phone")
    assert not m._db_cast_is_allowed("tv", "other")
    assert not m._db_cast_is_allowed("tv2", "phone")
    assert len(m.db_query("SELECT * FROM cast_allowed")) == 1


def test_allow_control_defaults_true_and_toggles(app_module):
    m = app_module
    assert m._db_cast_allow_control("tv") is True
    m._db_cast_set_allow_control("tv", False)
    assert m._db_cast_allow_control("tv") is False
    assert m._db_cast_allow_control("tv2") is True
    m._db_cast_set_allow_control("tv", True)
    assert m._db_cast_allow_control("tv") is True


def test_pruning_a_device_removes_its_cast_rows_and_setting(app_module):
    m = app_module
    now = time.time()
    for dev_id, seen in (("old-tv", now - 200 * 86400), ("old-phone", now - 200 * 86400), ("new-tv", now)):
        m._db_touch_device(dev_id, "h", "tv", "ua", seen, adopt_legacy=False)
    m._db_cast_allow("old-tv", "new-phone")      # old-tv là target
    m._db_cast_allow("new-tv", "old-phone")      # old-phone là controller
    m._db_cast_allow("new-tv", "new-phone")
    m._db_cast_set_allow_control("old-tv", False)
    m._db_cast_set_allow_control("new-tv", False)
    assert m._db_prune_inactive_devices(now) == 2
    assert not m._db_cast_is_allowed("old-tv", "new-phone")
    assert not m._db_cast_is_allowed("new-tv", "old-phone")
    assert m._db_cast_is_allowed("new-tv", "new-phone")
    assert m._db_cast_allow_control("old-tv") is True   # khóa đã bị xóa
    assert m._db_cast_allow_control("new-tv") is False


def test_each_test_gets_a_fresh_hub(app_module):
    from cast import CastHub
    assert isinstance(app_module.cast_hub, CastHub)
    app_module.cast_hub.heartbeat("x", "tv")
