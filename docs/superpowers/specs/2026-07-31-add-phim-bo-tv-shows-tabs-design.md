# Homepage Tabs — Thêm Phim Bộ & TV Shows — Design Spec

> **Ngày:** 2026-07-31
> **Trạng thái:** Đã duyệt (quyết định: 2 tab riêng, hướng A — mở rộng pattern hiện có)

## Goal

Thêm 2 tab mới **Phim Bộ** (`phim-bo`) và **TV Shows** (`tv-shows`) vào thanh tab trang chủ, cạnh 2 tab hiện có (Phim Chiếu Rạp, Phim Lẻ) — tổng 4 tab. Mỗi tab có nguồn API riêng, bộ lọc (Thể loại/Quốc gia/Năm) riêng, infinite scroll riêng.

## Context & Ràng buộc

- Backend 1 file `app.py`, frontend 1 file `templates/index.html` — **không tách module, không thêm dependency, không build step**.
- Mọi UI text, comment, docs bằng **tiếng Việt**.
- Pattern hiện có (đã shipped, feature "homepage filters"): endpoint mỗi section bọc `_fetch_danh_sach(type_slug, page, category, country, year)` qua `cached_fetch`; cache key `home:{section}:{page}:{category}:{country}:{year}`; `_warm_danh_sach` cho cache warmer; frontend state `homeState.{key}` với shape `{items, page, loading, hasMore, fetchSeq, filters}`.
- **Không sửa** `cached_fetch`, `_fetch_danh_sach`, CSS filter bar, API surface cũ, `_danh_sach_cache_key`.
- API phimapi hỗ trợ cả 2 type `phim-bo` và `tv-shows` qua `/v1/api/danh-sach/{type}` (đã verify: 24 items mỗi loại, page 1).

## Backend — `app.py`

**Thêm 2 endpoint** sau `home_phim_chieu_rap` (trước comment `# --- Background cache warmer cho Homepage ---`), y hệt `home_phim_le`/`home_phim_chieu_rap`:

- `GET /api/home/phim-bo` — `async def home_phim_bo(page=1, category="", country="", year="")` → `cached_fetch(_danh_sach_cache_key("phim-bo", page, category, country, year), 3600, lambda: _fetch_danh_sach("phim-bo", page, category, country, year))`. Catch exception → `{"items": [], "pagination": {}, "error": str(e)}`.
- `GET /api/home/tv-shows` — tương tự với `"tv-shows"`.
- Docstring tiếng Việt.

**Cache warmer:** thêm 2 dòng trong `home_cache_warmer`, cạnh 2 dòng hiện có:
`await _warm_danh_sach("phim-bo", 1)` và `await _warm_danh_sach("tv-shows", 1)`.

## Frontend — `templates/index.html`

**HTML** — trong `.grid-tabs-nav`, thêm sau nút Phim Lẻ:

```html
<button class="grid-tab-btn" data-tab="phim-bo" onclick="switchHomeTab('phim-bo')">Phim Bộ</button>
<button class="grid-tab-btn" data-tab="tv-shows" onclick="switchHomeTab('tv-shows')">TV Shows</button>
```

**State** — `homeState` thêm 2 key:

```js
phimBo: { items: [], page: 1, loading: false, hasMore: true, fetchSeq: 0, filters: { category: '', country: '', year: '' } },
tvShows: { items: [], page: 1, loading: false, hasMore: true, fetchSeq: 0, filters: { category: '', country: '', year: '' } },
```

**Refactor `homeKey(tab)`** — lookup map thay cho ternary `activeGridTab === 'phim-chieu-rap' ? 'phimChieuRap' : 'phimLe'` (đang xuất hiện 5-6 chỗ: `renderGridCards`, `switchHomeTab`, `applyFilter`, `renderFilterOptions`, `setFilter`, `clearAllFilters`, `updateFilterUI`):

```js
function homeKey(tab) {
    const map = { 'phim-chieu-rap': 'phimChieuRap', 'phim-le': 'phimLe', 'phim-bo': 'phimBo', 'tv-shows': 'tvShows' };
    return map[tab] || 'phimChieuRap';
}
```
Lưu ý: `homeKey` **không** dùng cho `latest` — nó chỉ map 4 grid tab. Các call site dùng cho grid tab (không gồm `fetchHomeSection`'s latest branch) đều thay ternary bằng `homeKey(...)`.

**`fetchHomeSection`** — endpoint mapping mở rộng: `'phim-bo' → '/api/home/phim-bo'`, `'tv-shows' → '/api/home/tv-shows'`. Key mapping: **`latest` vẫn special-case trực tiếp** (hero dùng `homeState.latest`, không đi qua `homeKey`) — `section === 'latest' ? 'latest' : homeKey(section)`. `homeKey()` chỉ phục vụ 4 grid tab. Query filters/lọc params/`fetchSeq`/`fetchError` giữ nguyên logic hiện có.

**`switchHomeTab` / `renderGridCards` / filter JS** — thay ternary bằng `homeKey(tab)`. Không đổi logic khác.

**Không đổi:** tab mặc định vẫn `phim-chieu-rap`; nút `✕ Xóa lọc`, dropdown, empty state, race-protection hoạt động nguyên vẹn cho 4 tab.

## Data flow

Giống hệt flow hiện tại: `switchHomeTab('phim-bo')` → `homeKey` → `fetchHomeSection('phim-bo', 1)` → fetch `/api/home/phim-bo?page=1&category=...` → `renderGridCards` → `setupHomeInfiniteScroll`. Bộ lọc đặt trên tab nào chỉ ảnh hưởng state tab đó.

## Error handling

- Backend: try/except per endpoint → error envelope, logger.error.
- Frontend: giữ nguyên `fetchError` gating (empty-state chỉ hiện khi request thành công với 0 kết quả).

## Testing (không có test framework — curl + trình duyệt)

**Backend:**
- `GET /api/home/phim-bo?page=1` → 24 items.
- `GET /api/home/tv-shows?page=1` → 24 items.
- Lọc kết hợp (vd `phim-bo?category=hanh-dong&year=2024`) → kết quả hợp lý, cache HIT key `home:phim-bo:1:hanh-dong::2024` (lần 2).

**Frontend (CDP headless Chrome):**
- 4 tab render đủ, chuyển tab giữ state riêng.
- Filter trên tab Phim Bộ/TV Shows (chọn thể loại → grid refetch đúng).
- Empty state khi tổ hợp 0 kết quả.
- Infinite scroll giữ bộ lọc trên tab mới.
- Regression: 2 tab cũ + hero (latest) không đổi.

## Ngoài scope

Không gộp 2 nguồn vào 1 tab; không generic hóa backend thành route động; không thêm sort/multi-select; không đổi cache TTL.
