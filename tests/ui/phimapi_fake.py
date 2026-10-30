import httpx

PER_PAGE = 12
TOTAL_PAGES = 2
LONG_SERIES = "phim-nhieu-tap"


class _Resp:
    def __init__(self, payload): self._p = payload
    def raise_for_status(self): pass
    def json(self): return self._p


def _item(slug, name=None):
    return {"slug": slug, "name": name or slug, "origin_name": slug, "poster_url": f"{slug}.jpg",
            "thumb_url": f"{slug}-thumb.jpg", "year": 2026, "quality": "HD", "lang": "Vietsub",
            "episode_current": "Full", "tmdb": {}}


def _page(prefix, page, count=PER_PAGE):
    items = []
    for i in range(count):
        n = (page - 1) * PER_PAGE + i + 1
        slug = "phim-chung" if (page == 1 and i == 2 and prefix in ("phim-le", "phim-bo")) else f"{prefix}-{n}"
        items.append(_item(slug))
    pagination = {"currentPage": page, "totalPages": TOTAL_PAGES, "totalItems": PER_PAGE * TOTAL_PAGES}
    return {"data": {"items": items, "params": {"pagination": pagination}}}


class FakePhimapi:
    """Thay httpx.AsyncClient của app: trả dữ liệu phimapi cố định, không gọi mạng."""

    def __init__(self, fail=(), content=None, names=None, counts=None):
        self.counts = counts or {}
        self.fail = tuple(fail)
        self.content = content or {}
        self.names = names or {}

    async def get(self, url, params=None, **kw):
        if any(f in url for f in self.fail):
            raise httpx.HTTPError(f"fake failure for {url}")
        params = params or {}
        if "?" in url:
            from urllib.parse import parse_qs, urlparse
            q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
            params = {**q, **params}
        page = int(params.get("page", 1))
        if "/v1/api/home" in url:
            return _Resp(_page("latest", page, self.counts.get("latest", PER_PAGE)))
        if "/v1/api/danh-sach/" in url:
            kind = url.rsplit("/", 1)[1]
            return _Resp(self._with_names(_page(kind, page, self.counts.get(kind, PER_PAGE))))
        if url.endswith("/v1/api/the-loai"):
            return _Resp({"data": {"items": [{"name": "Hành Động", "slug": "hanh-dong"}, {"name": "Tình Cảm", "slug": "tinh-cam"}]}})
        if url.endswith("/v1/api/quoc-gia"):
            return _Resp({"data": {"items": [{"name": "Hàn Quốc", "slug": "han-quoc"}, {"name": "Mỹ", "slug": "my"}]}})
        if "/phim/" in url:
            return _Resp(self._detail(url.rsplit("/", 1)[1]))
        raise AssertionError(f"FakePhimapi: unexpected URL {url}")

    def _with_names(self, payload):
        for it in payload["data"]["items"]:
            if it["slug"] in self.names:
                it["name"] = self.names[it["slug"]]
        return payload

    def _detail(self, slug):
        count = 80 if slug == LONG_SERIES else 3
        movie = {"name": self.names.get(slug, slug), "slug": slug, "origin_name": slug, "poster_url": f"{slug}.jpg",
                 "year": 2026, "episode_current": "Full", "time": "90 phút", "quality": "HD", "lang": "Vietsub",
                 "content": self.content.get(slug, f"Mô tả {slug}"),
                 "category": [{"name": "Hành Động", "slug": "hanh-dong"}, {"name": "Phiêu Lưu", "slug": "phieu-luu"}],
                 "country": [{"name": "Mỹ", "slug": "my"}]}
        eps = [{"name": f"Tập {i}", "link_m3u8": f"http://media.test/{slug}/{i}.m3u8"} for i in range(1, count + 1)]
        return {"status": True, "movie": movie, "episodes": [{"server_data": eps}]}
