import pytest

from cast import CastHub
from fastapi.testclient import TestClient

IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"

@pytest.fixture
def app_module(tmp_path, monkeypatch):
    import app as m
    monkeypatch.setattr(m, "DB_FILE", str(tmp_path / "t.db"))
    monkeypatch.setattr(m, "DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setattr(m, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(m, "_db_conn", None)
    (tmp_path / "downloads").mkdir()
    (tmp_path / "cache").mkdir()
    monkeypatch.setattr(m, "cast_hub", CastHub())
    m.init_db()
    yield m
    if m._db_conn is not None:
        m._db_conn.close()
        m._db_conn = None

@pytest.fixture
def make_client(app_module):
    def _make(ip="203.0.113.7", ua=IPHONE):
        return TestClient(app_module.app, client=(ip, 50000), headers={"user-agent": ua})
    return _make
