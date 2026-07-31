# Thêm Tab Phim Bộ & TV Shows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thêm 2 tab mới **Phim Bộ** (`phim-bo`) và **TV Shows** (`tv-shows`) vào thanh tab trang chủ, cạnh Phim Chiếu Rạp và Phim Lẻ — tổng 4 tab. Mỗi tab có nguồn API riêng, bộ lọc (Thể loại/Quốc gia/Năm) riêng, infinite scroll riêng.

**Architecture:** Mở rộng pattern "homepage filters" đã có: backend thêm 2 endpoint bọc helper generic `_fetch_danh_sach` (đã sẵn) + 2 dòng cache warmer; frontend thêm 2 tab button, 2 key state, refactor `homeKey(tab)` lookup map thay cho ternary `activeGridTab === 'phim-chieu-rap' ? ... : 'phimLe'` (7 chỗ) trước khi mở rộng sang 4 tab.

**Tech Stack:** FastAPI + SQLite (cache-aside), Vanilla JS trong `templates/index.html`, API phimapi.com. Không test framework — kiểm thử bằng `curl` + CDP headless Chrome.

**Tham chiếu spec:** `docs/superpowers/specs/2026-07-31-add-phim-bo-tv-shows-tabs-design.md`.

## Global Constraints

- Backend là 1 file `app.py`, frontend là 1 file `templates/index.html` — **không tách module, không thêm dependency, không build step**.
- Mọi UI text, comment, docs bằng **tiếng Việt**.
- **Không sửa** `cached_fetch`, `_fetch_danh_sach`, `_danh_sach_cache_key`, CSS filter bar, API surface cũ.
- `homeKey(tab)` **không** xử lý `latest` — hero vẫn dùng `homeState.latest` qua special-case `section === 'latest' ? 'latest' : homeKey(section)` trong `fetchHomeSection`.
- Tab mặc định vẫn `phim-chieu-rap`. Bộ lọc: 1 giá trị/tiêu chí, AND, `""` = không lọc.
- Cache key mới: `home:phim-bo:{page}:{category}:{country}:{year}` và `home:tv-shows:{page}:{category}:{country}:{year}` (TTL 3600).
- Chạy local để test: `uvicorn app:app --reload --host 0.0.0.0 --port 6969` (hoặc server đang chạy sẵn).

---

### Task 1: Backend — 2 endpoint Phim Bộ & TV Shows + cache warmer

**Files:**
- Modify: `app.py` — thêm 2 endpoint sau `home_phim_chieu_rap` (khoảng dòng 1185, trước `_fetch_filter_list` dòng 1187); sửa `home_cache_warmer` (khoảng dòng 1247).

**Interfaces:**
- Consumes: `_danh_sach_cache_key(section, page, category, country, year)`, `_fetch_danh_sach(type_slug, page, category, country, year)`, `_warm_danh_sach(section, page)`, `cached_fetch` — tất cả đã tồn tại, không sửa.
- Produces (dùng bởi Task 2):
  - `GET /api/home/phim-bo` → `{"items": [...], "pagination": {...}}` (+ `error` nếu fail).
  - `GET /api/home/tv-shows` → cùng shape.
  - Cache warmer làm nóng `home:phim-bo:1:::` và `home:tv-shows:1:::`.

- [ ] **Step 1: Thêm 2 endpoint** (chèn ngay sau khối `home_phim_chieu_rap` — dòng cuối của hàm đó là `return {"items": [], "pagination": {}, "error": str(e)}` ~dòng 1185, trước `async def _fetch_filter_list`)

```python
@app.get("/api/home/phim-bo")
async def home_phim_bo(page: int = 1, category: str = "", country: str = "", year: str = ""):
    """Phim Bộ + bộ lọc thể loại/quốc gia/năm (cache 1 giờ theo tổ hợp lọc)"""
    try:
        return await cached_fetch(
            _danh_sach_cache_key("phim-bo", page, category, country, year), 3600,
            lambda: _fetch_danh_sach("phim-bo", page, category, country, year))
    except Exception as e:
        logger.error(f"Error fetching home/phim-bo: {e}")
        return {"items": [], "pagination": {}, "error": str(e)}


@app.get("/api/home/tv-shows")
async def home_tv_shows(page: int = 1, category: str = "", country: str = "", year: str = ""):
    """TV Shows + bộ lọc thể loại/quốc gia/năm (cache 1 giờ theo tổ hợp lọc)"""
    try:
        return await cached_fetch(
            _danh_sach_cache_key("tv-shows", page, category, country, year), 3600,
            lambda: _fetch_danh_sach("tv-shows", page, category, country, year))
    except Exception as e:
        logger.error(f"Error fetching home/tv-shows: {e}")
        return {"items": [], "pagination": {}, "error": str(e)}
```

- [ ] **Step 2: Cập nhật cache warmer**

Trong `home_cache_warmer` (khoảng dòng 1247-1256), thêm 2 dòng sau `await _warm_danh_sach("phim-chieu-rap", 1)` và sửa comment "cả 3 section" thành "cả 5 section":

```python
    while True:
        logger.info("[CacheWarmer] Bắt đầu làm mới cache homepage...")
        # Page 1 cho cả 5 section (có thể mở rộng thêm page sau)
        await _warm_section("latest", 1, 1800, "https://phimapi.com/v1/api/home?page={page}")
        await _warm_danh_sach("phim-le", 1)
        await _warm_danh_sach("phim-chieu-rap", 1)
        await _warm_danh_sach("phim-bo", 1)
        await _warm_danh_sach("tv-shows", 1)
        logger.info(f"[CacheWarmer] Đợi {WARM_CACHE_INTERVAL}s cho lần refresh tiếp theo...")
        await asyncio.sleep(WARM_CACHE_INTERVAL)
```

- [ ] **Step 3: Kiểm thử bằng curl**

```bash
# 1. Không lọc — mỗi endpoint trả 24 items
curl -s "http://localhost:6969/api/home/phim-bo?page=1" | python3 -c "import json,sys; d=json.load(sys.stdin); print('phim-bo items:', len(d.get('items', [])), 'total:', d.get('pagination', {}).get('totalItems'))"
# Expected: phim-bo items: 24
curl -s "http://localhost:6969/api/home/tv-shows?page=1" | python3 -c "import json,sys; d=json.load(sys.stdin); print('tv-shows items:', len(d.get('items', [])), 'total:', d.get('pagination', {}).get('totalItems'))"
# Expected: tv-shows items: 24

# 2. Lọc kết hợp
curl -s "http://localhost:6969/api/home/phim-bo?page=1&category=hanh-dong&year=2024" | python3 -c "import json,sys; d=json.load(sys.stdin); print('phim-bo filter items:', len(d.get('items', [])), 'total:', d.get('pagination', {}).get('totalItems'))"
# Expected: items > 0 và totalItems < total không lọc (tổ hợp hợp lệ, không crash)

# 3. Cache HIT lần gọi 2
curl -s "http://localhost:6969/api/home/phim-bo?page=1" > /dev/null
curl -s "http://localhost:6969/api/home/phim-bo?page=1" > /dev/null
# Xem log server: "[Cache] HIT key=home:phim-bo:1:::"

# 4. Regression 2 endpoint cũ vẫn hoạt động
curl -s "http://localhost:6969/api/home/phim-le?page=1" | python3 -c "import json,sys; d=json.load(sys.stdin); print('phim-le items:', len(d.get('items', [])))"
curl -s "http://localhost:6969/api/home/phim-chieu-rap?page=1" | python3 -c "import json,sys; d=json.load(sys.stdin); print('phim-chieu-rap items:', len(d.get('items', [])))"
# Expected: 24 / 24
```

Nếu server không có `--reload`, restart sau khi sửa `app.py`.

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "feat(api): add phim-bo and tv-shows home endpoints with filters"
```

---

### Task 2: Frontend — 4 tab + state + homeKey() refactor

> **Ghi chú:** HTML tab và JS wiring gộp vào MỘT commit để tránh trạng thái hỏng tạm (tab mới hiện nội dung sai) giữa 2 commit.

**Files:**
- Modify: `templates/index.html` — `.grid-tabs-nav` (~dòng 1956-1958); `homeState` (~dòng 2298-2303); thêm `homeKey()` trước `fetchHomeSection` (~dòng 2305); `fetchHomeSection` key/endpoint mapping (~dòng 2306, 2318); thay 8 ternary call site: `applyFilter` (~2357), `renderGridCards` (~2420), `setupHomeInfiniteScroll` (~2510), `switchHomeTab` (~2534), `renderFilterOptions` (~3506), `setFilter` (~3517), `clearAllFilters` (~3524), `updateFilterUI` (~3531).

**Interfaces:**
- Consumes: endpoint `/api/home/phim-bo`, `/api/home/tv-shows` (Task 1); CSS `.grid-tab-btn`, `.filter-bar` (đã có).
- Produces (dùng bởi Task 3): `homeState.phimBo`, `homeState.tvShows`; `homeKey(tab)`; 4 tab hoạt động đầy đủ filter/infinite scroll.

- [ ] **Step 1: Thêm 2 tab button vào HTML** (sau button Phim Lẻ, trong `.grid-tabs-nav`)

```html
                            <button class="grid-tab-btn" data-tab="phim-bo" onclick="switchHomeTab('phim-bo')">Phim Bộ</button>
                            <button class="grid-tab-btn" data-tab="tv-shows" onclick="switchHomeTab('tv-shows')">TV Shows</button>
```

- [ ] **Step 2: Thêm 2 key vào `homeState`** (sau dòng `phimChieuRap: {...}`)

```js
            phimBo: { items: [], page: 1, loading: false, hasMore: true, fetchSeq: 0, filters: { category: '', country: '', year: '' } },
            tvShows: { items: [], page: 1, loading: false, hasMore: true, fetchSeq: 0, filters: { category: '', country: '', year: '' } },
```

- [ ] **Step 3: Thêm `homeKey()`** (chèn ngay trước `async function fetchHomeSection`)

```js
        function homeKey(tab) {
            const map = { 'phim-chieu-rap': 'phimChieuRap', 'phim-le': 'phimLe', 'phim-bo': 'phimBo', 'tv-shows': 'tvShows' };
            return map[tab] || 'phimChieuRap';
        }
```

- [ ] **Step 4: Sửa mapping trong `fetchHomeSection`**

Dòng key mapping hiện tại:
```js
            const key = section === 'latest' ? 'latest' : section === 'phim-le' ? 'phimLe' : 'phimChieuRap';
```
Thay bằng:
```js
            const key = section === 'latest' ? 'latest' : homeKey(section);
```

Dòng endpoint mapping hiện tại:
```js
                endpoint = section === 'phim-le' ? '/api/home/phim-le' : '/api/home/phim-chieu-rap';
```
Thay bằng:
```js
                endpoint = section === 'phim-le' ? '/api/home/phim-le' : section === 'phim-bo' ? '/api/home/phim-bo' : section === 'tv-shows' ? '/api/home/tv-shows' : '/api/home/phim-chieu-rap';
```

- [ ] **Step 5: Thay 8 ternary call site bằng `homeKey()`**

| Vị trí | Dòng hiện tại | Thay bằng |
|---|---|---|
| `applyFilter` | `const key = homeState.activeGridTab === 'phim-chieu-rap' ? 'phimChieuRap' : 'phimLe';` | `const key = homeKey(homeState.activeGridTab);` |
| `renderGridCards` | `const key = tab === 'phim-chieu-rap' ? 'phimChieuRap' : 'phimLe';` | `const key = homeKey(tab);` |
| `setupHomeInfiniteScroll` | `const key = tab === 'phim-chieu-rap' ? 'phimChieuRap' : 'phimLe';` | `const key = homeKey(tab);` |
| `switchHomeTab` | `const key = tab === 'phim-chieu-rap' ? 'phimChieuRap' : 'phimLe';` | `const key = homeKey(tab);` |
| `renderFilterOptions` | `const key = homeState.activeGridTab === 'phim-chieu-rap' ? 'phimChieuRap' : 'phimLe';` | `const key = homeKey(homeState.activeGridTab);` |
| `setFilter` | `const key = homeState.activeGridTab === 'phim-chieu-rap' ? 'phimChieuRap' : 'phimLe';` | `const key = homeKey(homeState.activeGridTab);` |
| `clearAllFilters` | `const key = homeState.activeGridTab === 'phim-chieu-rap' ? 'phimChieuRap' : 'phimLe';` | `const key = homeKey(homeState.activeGridTab);` |
| `updateFilterUI` | `const key = homeState.activeGridTab === 'phim-chieu-rap' ? 'phimChieuRap' : 'phimLe';` | `const key = homeKey(homeState.activeGridTab);` |

Chú ý: `renderGridCards`, `setupHomeInfiniteScroll` và `switchHomeTab` dùng biến `tab` (không phải `homeState.activeGridTab`) — không nhầm lẫn. Sau khi thay xong, chạy `grep -n "'phim-chieu-rap' ? 'phimChieuRap' : 'phimLe'" templates/index.html` phải trả **0 kết quả**. Kiểm tra tại 2510: `setupHomeInfiniteScroll` đóng trên biến `state` (state lấy 1 lần, grid observer dùng lại) — không cần sửa thêm.

- [ ] **Step 6: Kiểm tra JS syntax + render**

Không có node trong máy. Kiểm tra:
1. Trích `<script>` từ `templates/index.html`, đếm cặp `{}` `()` `[]` cân bằng, hoặc dùng esprima qua python nếu có.
2. `curl -s "http://localhost:6969/"` → 200, HTML chứa 4 button `data-tab` (`phim-chieu-rap`, `phim-le`, `phim-bo`, `tv-shows`).
3. Chạy CDP headless Chrome script ở Step 7.

- [ ] **Step 7: Kiểm thử tương tác bằng CDP headless Chrome**

Viết script `/tmp/homeflix-tabs-cdp.py` (Python stdlib + Chrome headless, remote-debugging-port) — tham khảo pattern driver CDP: mở `http://localhost:6969/`, chờ `.movie-card` xuất hiện, rồi:

```python
# Các phép kiểm (evaluate từng câu qua Runtime.evaluate, in kết quả):
# C1: 4 tab button
#     document.querySelectorAll('.grid-tab-btn').length  -> 4
# C2: click Phim Bộ -> grid load phim-bo
#     switchHomeTab('phim-bo'); sau ~2s:
#     homeState.activeGridTab  -> 'phim-bo'
#     homeState.phimBo.items.length  -> 24
#     document.querySelectorAll('#homeGridContent .movie-card').length  -> 24
# C3: click TV Shows -> grid load tv-shows
#     switchHomeTab('tv-shows'); sau ~2s:
#     homeState.tvShows.items.length  -> 24
# C4: filter trên tab Phim Bộ
#     switchHomeTab('phim-bo'); sau ~1s; setFilter('category','hanh-dong'); sau ~2s:
#     document.getElementById('filterBtn-category').textContent  -> 'Hành Động ▾'
#     homeState.phimBo.filters.category  -> 'hanh-dong'
#     homeState.phimBo.items.length  -> 24 (hoặc > 0)
# C5: clear filter -> về đầy đủ
#     document.getElementById('filterClearBtn').click(); sau ~2s:
#     homeState.phimBo.filters  -> {"category":"","country":"","year":""}
# C6: per-tab state — chuyển tab không làm mất filter của tab khác
#     setFilter('country','han-quoc'); switchHomeTab('tv-shows'); switchHomeTab('phim-bo'); sau ~1s:
#     homeState.phimBo.filters.country  -> 'han-quoc'
#     document.getElementById('filterBtn-country').textContent  -> 'Hàn Quốc ▾'
# C7: regression — 2 tab cũ còn nguyên
#     switchHomeTab('phim-chieu-rap'); sau ~1s:
#     homeState.phimChieuRap.items.length  -> 24
#     homeState.latest.items.length  -> 24
```

Mỗi bước chờ đủ thời gian (dùng `Runtime.evaluate` với `awaitPromise` + `setInterval` poll như pattern cũ). In kết quả từng check. Tất cả phải pass trước khi commit.

- [ ] **Step 8: Commit**

```bash
git add templates/index.html
git commit -m "feat(ui): add Phim Bộ and TV Shows tabs with per-tab filter state"
```

---

### Task 3: Kiểm thử tổng hợp & hoàn tất

**Files:**
- Không sửa code (chỉ kiểm thử). Nếu phát hiện bug, sửa và commit kèm.

- [ ] **Step 1: Backend — toàn bộ 4 section**

```bash
for q in phim-le phim-chieu-rap phim-bo tv-shows; do
  curl -s "http://localhost:6969/api/home/$q?page=1" | python3 -c "import json,sys; d=json.load(sys.stdin); print('$q no-filter items:', len(d.get('items', [])))"
  curl -s "http://localhost:6969/api/home/$q?page=1&category=tinh-cam&country=viet-nam&year=2023" | python3 -c "import json,sys; d=json.load(sys.stdin); print('$q filter items:', len(d.get('items', [])), 'total:', d.get('pagination', {}).get('totalItems'))"
done
# Expected: mỗi section no-filter 24; filter trả kết quả hợp lý hoặc 0 (không crash)
```

- [ ] **Step 2: Frontend — quy trình đầy đủ**

Chạy lại script CDP ở Task 2 Step 7 (hoặc mở `http://localhost:6969/` thủ công):
1. 4 tab render, chuyển tab nhanh liên tục không lỗi.
2. Lọc kết hợp trên Phim Bộ + TV Shows (chọn thể loại, quốc gia, năm).
3. Tổ hợp 0 kết quả → hiện "Không tìm thấy phim phù hợp với bộ lọc."
4. Infinite scroll: trên tab TV Shows kéo xuống đáy → load thêm trang, giữ bộ lọc.
5. Reset (✕ Xóa lọc) hoạt động trên từng tab.
6. Regression: hero (latest) + 2 tab cũ không đổi; phát 1 phim từ kết quả lọc tab Phim Bộ → detail mở được.

- [ ] **Step 3: Kiểm tra không hồi quy**

- `/api/home/latest` vẫn cache `home:latest:{page}`.
- `/api/home/categories` + `/api/home/countries` không đổi.
- Cache warmer log có `OK section=phim-bo` / `OK section=tv-shows` sau chu kỳ chạy.

- [ ] **Step 4: Commit nếu có sửa phát sinh**

```bash
git add -A
git commit -m "fix(ui): resolve issues found in end-to-end 4-tab testing"
```

---

## Self-Review

**Spec coverage:**
- 2 endpoint mới + cache key + warmer → Task 1.
- 4 tab HTML + 2 key state + `homeKey()` refactor + mapping `fetchHomeSection` → Task 2 (Step 1-5).
- Kiểm thử backend (curl) + frontend (CDP) → Task 2 Step 6-7, Task 3.
- `homeKey` loại trừ `latest` → Global Constraints + Task 2 Step 4 (giữ `section === 'latest' ? 'latest' : ...`).
- Regression 2 tab cũ + hero → Task 2 Step 7 C7, Task 3 Step 2-3.

**Placeholder scan:** Không có TBD/TODO; mọi bước có code/command cụ thể. CDP script được mô tả bằng phép kiểm cụ thể (không dựa vào file /tmp có sẵn).

**Type consistency:** `homeKey(tab)` trả `phimChieuRap|phimLe|phimBo|tvShows`; `homeState.phimBo`/`homeState.tvShows` cùng shape với 2 key cũ (có `filters`, `fetchSeq`); `fetchHomeSection` giữ special-case `latest`. 8 call site được thay bằng `homeKey(tab)` hoặc `homeKey(homeState.activeGridTab)` đúng theo biến có sẵn tại mỗi hàm (`renderGridCards`/`setupHomeInfiniteScroll`/`switchHomeTab` dùng `tab`, các hàm còn lại dùng `homeState.activeGridTab`).
