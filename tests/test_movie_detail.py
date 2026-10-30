class _Resp:
    def __init__(self, payload): self._p = payload
    def raise_for_status(self): pass
    def json(self): return self._p

class _FakeClient:
    def __init__(self, payload): self.payload = payload
    async def get(self, url, **kw): return _Resp(self.payload)

def _phimapi(**movie_extra):
    movie = {"name": "Phim", "slug": "x", "origin_name": "X", "poster_url": "p.jpg", "year": 2026,
             "episode_current": "Full", "time": "90 phút", "quality": "HD", "lang": "Vietsub", "content": "c"}
    movie.update(movie_extra)
    return {"status": True, "movie": movie, "episodes": []}

def _detail(app_module, make_client, slug, **movie_extra):
    app_module.client = _FakeClient(_phimapi(**movie_extra))
    return make_client().get(f"/api/movie/{slug}").json()["movie"]

def test_movie_detail_includes_category_and_country_names(app_module, make_client):
    m = _detail(app_module, make_client, "phim-cat-ok",
                category=[{"name": "Hành Động", "slug": "hanh-dong"}, {"name": "Phiêu Lưu", "slug": "phieu-luu"}],
                country=[{"name": "Mỹ", "slug": "my"}])
    assert m["category"] == ["Hành Động", "Phiêu Lưu"] and m["country"] == ["Mỹ"]

def test_movie_detail_missing_or_malformed_category_country_is_empty_list(app_module, make_client):
    m = _detail(app_module, make_client, "phim-cat-missing")
    assert m["category"] == [] and m["country"] == []
    m = _detail(app_module, make_client, "phim-cat-bad", category="x",
                country=[{"slug": "a"}, {"name": 5}, {"name": "Hàn Quốc"}, "str"])
    assert m["category"] == [] and m["country"] == ["Hàn Quốc"]
