# HomeTV Bố cục giống HomeFlix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** HomeTV có bố cục và trang play giống HomeFlix (tab Home / Tìm kiếm / Tủ kênh, hàng kênh xem gần đây, thanh lọc, danh sách phát tối đa 10 dòng), thêm lọc Alive và chấm trạng thái kênh, bằng cách tách code dùng chung từ HomeFlix thay vì viết lại.

**Architecture:** Server thêm `status` cho kênh và đổi `/api/hometv/channels` thành `{health, channels}`. Trong `templates/index.html` tách các hàm và macro Jinja dùng chung (thẻ poster, thẻ Xem tiếp, menu lọc, danh sách tập, khung player); HomeFlix gọi lại chúng với hành vi mặc định không đổi, rồi `static/hometv.js` gọi cùng các hàm đó.

**Tech Stack:** FastAPI + SQLite (`hometv/`), Jinja `templates/index.html`, JS thuần, pytest + Playwright (`tests/ui`), `node --test`.

**Spec:** `docs/superpowers/specs/2026-10-31-hometv-layout-design.md`

## Global Constraints

- Chạy test bằng `.venv/bin/python -m pytest` (python hệ thống thiếu thư viện); JS: `node --test tests/js/*.test.mjs` (glob, không phải thư mục).
- **Hồi quy HomeFlix** (gọi là `REG`): `.venv/bin/python -m pytest tests/ui -q -x` phải xanh sau mỗi task sửa `templates/index.html` hoặc `static/hf.css`. Nếu chạy lâu, chạy nền ra file trong scratchpad.
- Nhãn và thông báo tiếng Việt, đúng chữ trong spec: `Home`, `Tìm kiếm`, `Tủ kênh`, `Kênh xem gần đây`, `Alive`, `Tất cả`, `Nhóm`, `Xóa lọc`, `Không có kênh nào khớp.`, `Tủ kênh trống`.
- Giữ nguyên các id HomeTV mà test hiện có dùng: `#hometvToggle`, `#hometv-root`, `#tvGrid`, `.tv-card[data-id]`, `.tv-star`, `#tvEmpty`, `#tvRetry`, `#tvPlayer`, `#tvVideo`, `#tvPlayerTitle`, `#tvPlayerClose` (nút quay lại), `#tvToast`, `#tvPlayerError`, `#tvPlayerMsg`, `#tvPlayerRetry`, `#tvSearch`. Test nào phải đổi vì bố cục mới thì đổi trong task sở hữu.
- HomeTV **không** dùng lớp `player-active` (`static/cast.js` và `exitPlayer` đọc nó để biết đang phát phim). HomeTV dùng `tv-player-active`.
- Hàm dùng chung tách ra giữ hành vi mặc định của HomeFlix. Mọi tham số thêm vào là tuỳ chọn.
- Test gọn theo `lean-tests-single-user`; không thêm bảo mật/gia cố (backlog `docs/superpowers/backlog-hometv.md`).
- Commit tiếng Việt, cuối thông điệp: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Không đọc `deploy/.env`. Không push, không merge khi chưa được yêu cầu.

## Review Focus

- Kênh đang phát không nằm trong danh sách đang lọc (mở từ hàng Xem gần đây khi lọc Alive): danh sách phát lấy toàn bộ kênh thay vì rỗng; Lên/Xuống vẫn đổi kênh.
- Người dùng đang bật lọc Alive rồi máy chủ báo `health: false` (tải lại, bộ kiểm tắt): lọc reset về Tất cả, không hiện lưới rỗng.
- Yêu thích hoặc xem gần đây đổi khi đang ở tab khác: Tủ kênh và hàng Xem gần đây vẽ lại mỗi lần vào tab.
- `switchTab` chạy khi trang vừa tải, trước khi `hometv.js` khởi tạo: móc ánh xạ phải an toàn khi `window.HFTV` chưa có.
- Bấm nhanh nhiều dòng trong danh sách phát: chỉ một lần mở kênh cuối (gộp 400 ms), không treo nhiều player.

---

### Task 1: Trạng thái kênh trên server và API `{health, channels}`

**Files:**
- Modify: `hometv/catalog.py` (`list_channels`), `hometv/public_api.py` (route `/channels`), `static/hometv.js` (`load`: đọc `.channels`, lưu `state.health`)
- Test: `tests/hometv/test_hometv_catalog.py`, `tests/hometv/test_hometv_public_api.py`, các test UI hiện có mock `/api/hometv/channels` (`tests/ui/test_hometv_ui.py::test_error_state_and_retry` chỉ trả 500, không đổi)

**Interfaces:**
- Produces: `catalog.list_channels(now, use_health=False)` mỗi phần tử thêm khóa `status: str | None`. `use_health` tắt → `None`; bật → `"alive"` nếu `MAX(h.ok) = 1` trên các stream hợp lệ của kênh, `"dead"` nếu `MAX(h.ok) = 0`, `"unchecked"` nếu `NULL`. `GET /api/hometv/channels` → `{"health": bool, "channels": [...]}` với `health = geo.current().enabled`.
- Consumes: `health_store.record(stream_id, HealthResult(ok=...), https, now)` để dựng dữ liệu test (xem `test_hometv_public_api.py:72`).

- [ ] **Step 1: Viết test lỗi.** `test_list_channels_status_alive_dead_unchecked` (3 kênh: một kênh có stream `ok=True`, một kênh stream `ok=False` với `fail_streak` thấp hơn `FAIL_HIDE`, một kênh chưa có dòng health; gọi `list_channels(now, True)`, kỳ vọng `["alive","dead","unchecked"]` theo id) và `test_list_channels_status_none_without_health` (`use_health=False` → mọi `status is None`). Sửa test `/channels` hiện có sang `body["channels"]` và thêm assert `body["health"] is False` khi bộ kiểm tắt, `True` khi bật (ép bằng cách giống test hiện có của `geo`).
- [ ] **Step 2: Chạy, thấy đỏ.** `.venv/bin/python -m pytest tests/hometv/test_hometv_catalog.py tests/hometv/test_hometv_public_api.py -q`
- [ ] **Step 3: Cài đặt.** Trong `list_channels`, khi `use_health` thêm cột `(SELECT MAX(h.ok) FROM {_FROM}{_HEALTH} WHERE s.channel_id = oc.id AND {_VALID}) AS ok_max` (cột này là `NULL` khi tắt); ánh xạ `status` từ `ok_max`. Dùng đúng alias `oc` cho bảng ngoài (đã từng có lỗi alias đè). `public_api.channels()` trả `{"health": enabled, "channels": rows}`. `static/hometv.js` `load()`: `const res = await api('GET', '/api/hometv/channels'); state.channels = res.channels; state.health = res.health;` và khởi tạo `state.health = false`.
- [ ] **Step 4: Chạy lại** hai file trên cộng `tests/ui/test_hometv_ui.py tests/ui/test_hometv_player.py -q`. Kỳ vọng xanh.
- [ ] **Step 5: Commit** `feat(hometv): trạng thái kênh và API trả health cùng danh sách kênh`.

---

### Task 2: Logic thuần cho lọc Alive và nhóm

**Files:**
- Modify: `static/hometv-utils.js`
- Test: `tests/js/hometv-utils.test.mjs`

**Interfaces:**
- Produces trong `HFHomeTV`: `filterChannels(channels, { chip, query, alive, group })` — thêm `alive: boolean` (chỉ giữ `status === 'alive'`) và `group: string` (chỉ giữ `groupOf(c) === group`; rỗng/`undefined` là tất cả); `chip` giữ nguyên để không vỡ test cũ. `groupOptions(channels): {value, label}[]` — các nhóm có mặt theo `CATEGORY_ORDER`. `recentChannels(channels, max): channel[]` — kênh có `recent_at != null`, mới nhất trước, cắt `max`. `favoriteChannels(channels): channel[]`. `statusLabel(status): string` — `alive` → `Đang sống`, `dead` → `Đã chết`, `unchecked` → `Chưa kiểm`, khác → `''`.

- [ ] **Step 1: Viết test lỗi** (mỗi hàm một test ngắn, giá trị cụ thể): `alive` loại `dead` và `unchecked`; `alive` + `group: 'News'` giao nhau; `groupOptions` chỉ trả nhóm có mặt và đúng thứ tự `CATEGORY_ORDER`, nhóm lạ gộp `Unknown` với nhãn `Khác`; `recentChannels(list, 3)` sắp theo `recent_at` giảm dần và dài tối đa 3; `statusLabel` ba giá trị và `null` → `''`.
- [ ] **Step 2: Chạy** `node --test tests/js/*.test.mjs`, thấy đỏ.
- [ ] **Step 3: Cài đặt** các hàm trên trong `static/hometv-utils.js`, export qua `api`.
- [ ] **Step 4: Chạy lại**, xanh (47 test cũ vẫn pass).
- [ ] **Step 5: Commit** `feat(hometv): hàm lọc Alive, nhóm, kênh gần đây và nhãn trạng thái`.

---

### Task 3: `renderEpisodeList` nhận tuỳ chọn dòng, đơn vị, móc `decorate`

**Files:**
- Modify: `templates/index.html` (`renderEpisodeList`, khoảng dòng 2334), `static/hf.css` (khối `ep-list`)
- Test: `tests/ui/test_hometv_episode_list.py` (mới)

**Interfaces:**
- Produces: `renderEpisodeList(containerEl, opts)` nhận thêm trong `opts`: `unit` (chuỗi, mặc định `'tập'`, dùng trong `${total} ${unit}`), `rows` (số; nếu có: thêm lớp `ep-list-rows` vào `.ep-list`, đặt `--ep-rows` bằng giá trị, không phân trang), `decorate(btn, ep)` (gọi sau khi dựng mỗi `.ep-item`), `nameOf(ep)` (mặc định `HFUtils.epShort(ep.name)`). `.ep-list-rows .ep-list-grid` là một cột, `max-height: calc(var(--ep-rows) * <chiều cao dòng>)`, `overflow-y: auto`. Trả về `containerEl` không đổi; không đổi kiểu trả.

- [ ] **Step 1: Viết test UI lỗi** `test_rows_option_caps_list_at_ten_rows_and_scrolls`: mở app, `page.evaluate` gọi `renderEpisodeList` vào một `div` tạm với 40 mục `{name: 'K'+i, link_m3u8: String(i)}` và `{rows: 10, unit: 'kênh', decorate: (b) => b.dataset.x = '1'}`; assert tiêu đề `40 kênh`, không có `.ep-list-pager`, `.ep-list-grid` có `scrollHeight > clientHeight`, `clientHeight` ≤ 10 × chiều cao một `.ep-item` + khe (đo bằng JS, dung sai 2 px), mọi `.ep-item` có `data-x`. Thêm `test_default_options_unchanged`: gọi không có `rows`, tiêu đề `40 tập`, `.ep-list-pager` không có (40 < `EP_CHUNK`) và lưới nhiều cột như cũ.
- [ ] **Step 2: Chạy** `.venv/bin/python -m pytest tests/ui/test_hometv_episode_list.py -q`, đỏ.
- [ ] **Step 3: Cài đặt** các tuỳ chọn; CSS `.ep-list-rows` một cột, bỏ `minmax(150px…)`, dòng cao bằng `var(--tap)` hoặc giá trị cố định đã đo; `.ep-item` trong chế độ này rộng hết cột.
- [ ] **Step 4: Chạy** file mới rồi `REG`. Xanh.
- [ ] **Step 5: Commit** `refactor(ui): danh sách tập nhận tuỳ chọn dòng, đơn vị và móc decorate`.

---

### Task 4: `buildPosterCard` và `buildContinueCard` nhận bộ xử lý

**Files:**
- Modify: `templates/index.html` (`buildMovieCard` ~3037, `buildContinueCard` ~4120+)
- Test: `tests/ui/test_hometv_shared_cards.py` (mới)

**Interfaces:**
- Produces: `buildPosterCard({ title, meta, imgSrc, onClick, focusable })` → phần tử `.movie-card` gồm `.poster-wrap > img.movie-poster` và `.movie-info > .movie-title + .movie-meta`; `img.onerror` đổi sang `POSTER_PLACEHOLDER`; `focusable` mặc định theo quy tắc `device-mobile` hiện tại. `buildMovieCard(movie)` gọi nó (DOM kết quả giống hệt trước). `buildContinueCard(m, className, h)` với `h` tuỳ chọn `{ poster(m), meta(m), pct(m), label(m), onPlay(m), swipe }`; thiếu `h` → dùng hành vi phim hiện tại (`m.poster_url`, `HFUtils.continueMeta`, `HFUtils.progressPct`, `Phát tiếp ${name}`, `playResumeSavedMovie`, `swipe: true`). `swipe: false` → không dựng `swipe-act`, không gọi `attachContinueSwipe`; `pct` trả 0 → ẩn `.continue-progress`.

- [ ] **Step 1: Viết test lỗi** `test_poster_card_matches_movie_card_dom` (so `outerHTML` của `buildMovieCard({slug:'a', name:'A', poster_url:'/p', year:2020})` với cấu trúc kỳ vọng: các class, `data-slug`), `test_poster_card_calls_onclick_and_falls_back_on_image_error`, `test_continue_card_custom_handlers_no_swipe_no_progress` (gọi với `{ poster: () => '/l.png', meta: () => 'Tin tức', pct: () => 0, label: () => 'Mở K', onPlay, swipe: false }`: không có `.swipe-act`, không có `.continue-progress`, `aria-label` `Mở K`, click gọi `onPlay` một lần).
- [ ] **Step 2: Chạy**, đỏ.
- [ ] **Step 3: Cài đặt** hai hàm theo Interfaces; `buildMovieCard` và đường mặc định của `buildContinueCard` không đổi hành vi.
- [ ] **Step 4: Chạy** file mới rồi `REG` (đặc biệt `test_home_continue.py`, `test_continue_swipe.py`, `test_search.py`). Xanh.
- [ ] **Step 5: Commit** `refactor(ui): tách thẻ poster và thẻ xem tiếp dùng chung`.

---

### Task 5: Menu lọc dùng chung và macro Jinja `filter_dropdown`, `continue_section`

**Files:**
- Modify: `templates/index.html` (đầu `<body>`: macro; khối Home; `toggleFilterDropdown`, `renderFilterOptions`, `closeAllFilterDropdowns`)
- Test: `tests/ui/test_hometv_shared_filters.py` (mới)

**Interfaces:**
- Produces: `renderFilterMenu(menu, options, current, onPick)` (`options: {label, value}[]`, nút `Tất cả` rỗng do người gọi thêm; hàm chỉ dựng các `button.filter-option`, class `active` cho `value === current`). `filterProviders`: object `{ [type]: () => ({ options, current, onPick }) }`; `toggleFilterDropdown(type)` dùng `filterProviders[type]` nếu có, nếu không dùng nhánh category/country/year hiện tại (nhánh này gọi `renderFilterMenu`). Macro Jinja `filter_dropdown(type, label)` sinh `.filter-dropdown > button.filter-btn#filterBtn-{type} + .filter-menu#filterMenu-{type}` và macro `continue_section(id, title, link_label, link_tab)` sinh `<section class="home-section" hidden>` + `.sec-head` + `.home-continue#{id}List`; `homeContinue` và ba dropdown hiện có đổi sang dùng macro, id đầu ra y hệt trước (`homeContinue`, `homeContinueTitle`, `homeContinueList`, `filterBtn-category`…).
- Ghi chú: `window.filterProviders` phải là biến toàn cục truy cập được từ `hometv.js` (khai báo `const filterProviders = {}` ở script chính là đủ, vì `hometv.js` tải sau).

- [ ] **Step 1: Viết test lỗi** `test_filter_menu_renders_all_plus_options_and_marks_current` (gọi `renderFilterMenu` vào `div` tạm, kiểm số nút, `active`, và click gọi `onPick` với `value`), `test_custom_provider_opens_shared_dropdown` (đăng ký `filterProviders.demo`, dựng DOM `#filterMenu-demo`, gọi `toggleFilterDropdown('demo')`, menu có `open` và các lựa chọn; bấm ra ngoài đóng), `test_macro_markup_ids_unchanged` (các id HomeFlix kể trên đều tồn tại).
- [ ] **Step 2: Chạy**, đỏ.
- [ ] **Step 3: Cài đặt**; `{% macro %}` đặt ngay sau `<body>` mở; macro dùng cho khối Home hiện có.
- [ ] **Step 4: Chạy** file mới rồi `REG` (đặc biệt `test_filters.py`, `test_home_continue.py`). Xanh.
- [ ] **Step 5: Commit** `refactor(ui): menu lọc và khối Home dùng chung qua hàm và macro`.

---

### Task 6: Macro `player_shell` cho khung player

**Files:**
- Modify: `templates/index.html` (`globalPlayerContainer` ~2077-2122; khối CSS `body.player-active` ~1853-1880), `static/hf.css` (dòng ~323, 2053, 2094)
- Test: `tests/ui/test_hometv_shared_player.py` (mới)

**Interfaces:**
- Produces: macro Jinja `player_shell(ids)` với `ids` là dict `{container, header, back, title, right, wrapper, video, error}` sinh: `.player-header` (nút `.btn-overlay-back` + tiêu đề `.player-ep-title` + vùng phải), `.player-wrapper > video[playsinline controls] + .player-error`, dùng đúng các thuộc tính hiện có (`aria-label`, style inline hiện có). Phần riêng của phim (AirPlay overlay, `skipIntroBtn`, `autoNextOverlay`, nút `playerPrefsBtn`) truyền qua `{% call player_shell(...) %}` vào trong `.player-wrapper` / vùng phải. `globalPlayerContainer` dùng macro với id hiện có (`playerHeader`, `playerEpTitle`, `playerHeaderRight`, `videoPlayer`, `playerError`). Các bộ chọn `body.player-active …` đang định dạng khung (`.container`, `.player-wrapper`, `video`, `#playerHeader`, `.status-shield`, `.float-tabbar` ở mobile) thêm bộ chọn tương đương `body.tv-player-active …`; quy tắc `#episodesDrawerEl` và `#statusMessage` giữ nguyên. Nút quay lại của macro nhận `onclick` từ tham số `ids.back_onclick` (phim: `exitPlayer()`).

- [ ] **Step 1: Viết test lỗi** `test_global_player_markup_unchanged_by_macro` (các id trên tồn tại, `#playerHeader` chứa nút back và `#playerEpTitle`, `.player-wrapper` chứa `#videoPlayer`, `#skipIntroBtn`, `#autoNextOverlay`, `#airplayOverlay`), `test_theatre_rules_apply_to_tv_player_active` (thêm tạm class `tv-player-active` vào `body` và một `.player-wrapper` trong `.container`, assert `getComputedStyle(.container).maxWidth` là `100%` như khi `player-active`).
- [ ] **Step 2: Chạy**, đỏ ở test thứ hai.
- [ ] **Step 3: Cài đặt** macro, thay markup, mở rộng bộ chọn CSS.
- [ ] **Step 4: Chạy** file mới rồi `REG` (đặc biệt `test_player_safe_area.py`, `test_player_auto.py`, `test_stream_fallback.py`, `test_tabbar.py`). Xanh.
- [ ] **Step 5: Commit** `refactor(ui): khung player dùng chung qua macro, CSS theatre cho tv-player-active`.

---

### Task 7: Khung tab HomeTV và tab Home (Xem gần đây, lọc Alive/Nhóm, lưới, chấm trạng thái)

**Files:**
- Modify: `templates/index.html` (nút tab thêm `data-tv-label`; thay `#hometv-root` bằng ba `.tab-content.tv-tab`: `#tv-home-tab`, `#tv-search-tab`, `#tv-saved-tab`; `switchTab`, `syncFloatTabs`, `openSearchOverlay`), `static/hometv.js` (viết lại phần lưới), `static/hf.css` (bỏ `body.mode-hometv .tabs-nav/.float-tabbar` ẩn; thêm `.tv-dot`; ẩn tab phim và `.tv-tab` theo mode), `tests/ui/test_hometv_ui.py`, `tests/ui/test_hometv_style.py`
- Test: `tests/ui/test_hometv_ui.py`

**Interfaces:**
- Consumes: `HFHomeTV.filterChannels/groupOptions/recentChannels/statusLabel` (Task 2), `buildPosterCard`, `buildContinueCard(m, cls, h)`, `renderFilterMenu`, `filterProviders`, macros (Tasks 4–5), `state.health` (Task 1).
- Produces trong `HFTV`: `active(): boolean` (= `body` có `mode-hometv`), `tab(tabId): string` (ánh xạ `search-tab→tv-home-tab`, `saved-tab→tv-saved-tab`, `player-tab→tv-search-tab`, còn lại giữ nguyên), `onTab(tabId)` (vẽ lại nội dung tab, gọi từ `switchTab`). `switchTab(tabId)` đầu hàm: `if (window.HFTV && HFTV.active && HFTV.active()) tabId = HFTV.tab(tabId);` rồi chạy như cũ; nút đang chọn: `tv-home-tab` → `#tabBtnSearch`, `tv-saved-tab` → `#tabBtnSaved`, `tv-search-tab` → `#tabBtnPlayer`; cuối hàm gọi `HFTV.onTab(tabId)` nếu có. `syncFloatTabs` thêm `tv-home-tab: fabTabExplore`, `tv-saved-tab: fabTabLibrary`, `tv-search-tab: fabTabSearch`. `openSearchOverlay()`: khi HomeTV đang bật thì `switchTab('tv-search-tab')` rồi focus `#tvSearch`, không mở overlay phim. `HFTV.enter/leave` đổi `textContent` các nút có `data-tv-label` (lưu nhãn gốc ở `data-hf-label`) và gọi `switchTab('search-tab')`.
- Cấu trúc tab Home: `continue_section('tvRecent', 'Kênh xem gần đây', 'Tủ kênh', 'saved-tab')` (tối đa 3 thẻ, thẻ đầu `is-lead`, handler: logo, meta nhóm, `pct` 0, `swipe: false`, `onPlay` mở kênh); hàng nút `.grid-tabs-nav.type-row` với `Tất cả` và `Alive` (`data-tvfilter`; `Alive` ẩn khi `!state.health`); `filter_dropdown('group', 'Nhóm')` đăng ký `filterProviders.group` (nhãn nút đổi khi chọn, `Xóa lọc` hiện khi có lọc, `#tvResultCount.result-count`); `#tvGrid.movies-grid` + `#tvEmpty` + `#tvMore` như hiện nay, thẻ qua `buildPosterCard` rồi gắn `.tv-star` và chấm `.tv-dot[data-status]` (ẩn khi `!state.health`, `aria-label` bằng `statusLabel`). Lọc Alive/Nhóm lưu trong `state.alive` (boolean) và `state.group` (string); `load()` đặt `state.alive = false` nếu `!state.health`.

- [ ] **Step 1: Viết lại/viết thêm test UI** trong `tests/ui/test_hometv_ui.py` (hàm `seed` thêm tham số `health=None` để chèn `health_store.record` theo id): `test_tabs_labels_and_order_in_both_bars` (header: `Home`, `Tủ kênh`, `Tìm kiếm`; thanh dưới: `Home`, `Tìm kiếm`, `Tủ kênh`; rời mode trả về `Home`, `Tủ phim`, `Nhập link`), `test_recent_row_shows_latest_three_lead_first`, `test_alive_button_filters_and_hides_when_health_off`, `test_group_dropdown_filters_and_clear`, `test_status_dot_per_card_and_hidden_without_health`, `test_alive_filter_resets_when_health_goes_off` (Review Focus 2). Cập nhật các test cũ: `test_chips_filter…` → nhóm qua dropdown, `test_star_toggles…` → sao vẫn đúng và không còn chip yêu thích, `test_toggle_enters_and_remembers_mode` kiểm `#tv-home-tab` hiện và `#search-tab` ẩn. Cập nhật `test_hometv_style.py` cho thẻ mới (cùng giá trị bo góc, tỉ lệ, nền).
- [ ] **Step 2: Chạy** `.venv/bin/python -m pytest tests/ui/test_hometv_ui.py tests/ui/test_hometv_style.py -q`, đỏ.
- [ ] **Step 3: Cài đặt** theo Interfaces. `switchTab` an toàn khi `window.HFTV` chưa có (Review Focus 4): thêm test `test_switch_tab_safe_before_hometv_init` (xoá `window.HFTV` rồi gọi `switchTab('saved-tab')` không ném lỗi).
- [ ] **Step 4: Chạy** hai file trên, `tests/ui/test_hometv_player.py` (có thể đỏ ở phần player/remote: để Task 9–10), rồi `REG` trừ các test HomeTV chưa sửa (`--deselect` cụ thể). Phần HomeFlix xanh.
- [ ] **Step 5: Commit** `feat(hometv): thanh tab, tab Home với lọc Alive/Nhóm và chấm trạng thái`.

---

### Task 8: Tab Tìm kiếm và Tủ kênh

**Files:**
- Modify: `templates/index.html` (nội dung `#tv-search-tab`, `#tv-saved-tab`), `static/hometv.js`
- Test: `tests/ui/test_hometv_ui.py`

**Interfaces:**
- Consumes: `HFTV.onTab`, `HFHomeTV.favoriteChannels/recentChannels/filterChannels`, `buildPosterCard`.
- Tab Tìm kiếm: markup `search-bar` của HomeFlix (`.search-input-wrap` + icon + `#tvSearch`), `#tvSearchGrid.movies-grid`, `#tvSearchEmpty.filter-empty` (`Không có kênh nào khớp.`); lọc bằng `filterChannels(list, { query, alive: state.alive })`; thẻ và mở kênh dùng cùng hàm dựng thẻ của tab Home (một hàm `buildChannelCard(c)` trong `hometv.js` dùng chung hai nơi).
- Tab Tủ kênh: hai `section.library-section` (`Yêu thích`, `Xem gần đây`) với `h3.library-title` và lưới thẻ; `#tvLibraryEmpty.library-empty` `Tủ kênh trống` khi cả hai rỗng; `hidden` từng mục khi rỗng. Vẽ lại mỗi lần `onTab('tv-saved-tab')` (Review Focus 3). Đổi yêu thích ở bất kỳ tab nào cập nhật `state.channels` và vẽ lại tab đang hiện.

- [ ] **Step 1: Viết test lỗi** `test_search_tab_folds_diacritics_and_ignores_blank` (chuyển từ `test_search_folds…`: bấm tab `Tìm kiếm` trước khi `fill("#tvSearch")`), `test_search_respects_alive_filter`, `test_library_shows_favorites_and_recents_then_empty_text`, `test_library_redraws_after_favorite_toggled_in_home`.
- [ ] **Step 2: Chạy**, đỏ.
- [ ] **Step 3: Cài đặt.**
- [ ] **Step 4: Chạy** `tests/ui/test_hometv_ui.py -q`, xanh.
- [ ] **Step 5: Commit** `feat(hometv): tab Tìm kiếm và Tủ kênh`.

---

### Task 9: Trang play kiểu theatre và danh sách phát 10 dòng

**Files:**
- Modify: `templates/index.html` (thay khối `#tvPlayer` bằng `player_shell` + `#tvPlaylist`), `static/hometv.js` (player), `static/hf.css` (xoá khối `.tv-player*` cũ, thêm phần còn thiếu), `tests/ui/test_hometv_player.py`
- Test: `tests/ui/test_hometv_player.py`

**Interfaces:**
- Consumes: `player_shell` (Task 6), `renderEpisodeList` tuỳ chọn (Task 3), `HFHomeTV.stepChannel`.
- Markup: `#tvPlayer` là `div` trong `.container` (không còn `position: fixed`), tạo bằng `player_shell({container:'tvPlayer', header:'tvPlayerHeader', back:'tvPlayerClose', back_onclick:'', title:'tvPlayerTitle', right:'tvPlayerRight', wrapper:'tvPlayerWrap', video:'tvVideo', error:'tvPlayerError'})`; `#tvToast`, `#tvPlayerMsg`, `#tvPlayerRetry` nằm trong hộp lỗi/khung video như trước; dưới khung video có `#tvPlaylist`.
- Mở trang play: ẩn `#stickyHeader` và mọi `.tab-content` (giống `playEpisodesListDirect`), `body.classList.add('tv-player-active')`, hiện `#tvPlayer`. Đóng (`closePlayer`): ngược lại, hiện lại tab đang chọn bằng `switchTab` của tab hiện tại, bỏ `tv-player-active`, focus về thẻ kênh vừa xem.
- Danh sách phát: `renderPlaylist()` gọi `renderEpisodeList($('tvPlaylist'), { episodes: list.map(c => ({ name: c.name, link_m3u8: String(c.id) })), watchStates: { [String(play.channelId)]: 'watching' }, rows: 10, unit: 'kênh', showMeta: false, autoFocusWatching: false, decorate: (btn, ep) => <thêm logo nhỏ và .tv-dot>, onPlay: (ep) => switchTo(Number(ep.link_m3u8)) })`, rồi cuộn dòng `.is-watching` vào tầm nhìn bên trong bảng. `list` = `visible()` của tab đang mở; nếu kênh mở không nằm trong `list` thì `list = state.channels` (Review Focus 1). `switchTo(id)` dùng cơ chế gộp 400 ms của `stepPlayer` (đặt `play.pendingId` rồi hẹn giờ) — tách phần hẹn giờ ra hàm `queueOpen(id)` dùng chung cho Lên/Xuống và bấm dòng (Review Focus 5). Đổi kênh vẽ lại playlist (chỉ đổi lớp `is-watching` nếu đo thấy vẽ lại cả bảng bị giật).

- [ ] **Step 1: Viết/cập nhật test.** Cập nhật `test_opens_player_and_attaches_first_stream` (`#tvPlayer` hiện và `body.tv-player-active`), `test_close_destroys_hls_and_clears_timer` (nút `#tvPlayerClose`, tab quay lại), các test dùng `Escape`. Test mới: `test_playlist_shows_max_ten_rows_and_scrolls` (25 kênh: `#tvPlaylist .ep-list-grid` cao ≤ 10 dòng, cuộn được, tiêu đề `25 kênh`), `test_playlist_highlights_playing_channel_in_view` (mở kênh 20, dòng `.is-watching` nằm trong vùng nhìn thấy của bảng), `test_click_playlist_row_switches_channel_debounced` (bấm ba dòng nhanh → đúng một lần `/streams` mới, `__hlsSource` của dòng cuối), `test_playlist_uses_all_channels_when_current_not_in_filter` (lọc Alive, mở kênh `unchecked` từ hàng Xem gần đây → playlist không rỗng, có dòng đang phát), `test_player_hides_header_and_tabs_and_restores_on_close`.
- [ ] **Step 2: Chạy** `.venv/bin/python -m pytest tests/ui/test_hometv_player.py -q`, đỏ.
- [ ] **Step 3: Cài đặt**; xoá CSS `.tv-player`, `.tv-player video`, `.tv-player-title`, `.tv-player-close`, quy tắc `body.device-tv .tv-player-title`; giữ `.tv-toast`/`.tv-player-error` chuyển sang định vị trong `.player-wrapper`.
- [ ] **Step 4: Chạy** file trên; `test_cast_playback_leaves_hometv_mode` vẫn xanh (rời mode khi phim phát).
- [ ] **Step 5: Commit** `feat(hometv): trang play kiểu theatre và danh sách phát tối đa 10 dòng`.

---

### Task 10: Remote TV, dọn dẹp, hồi quy toàn bộ

**Files:**
- Modify: `static/hometv.js` (`gridRows`, `onKey`), `static/hf.css` (dọn CSS không còn dùng: `.tv-bar`, `.tv-search`, `.tv-chips`), `tests/ui/test_hometv_player.py`, `docs/superpowers/backlog-hometv.md`
- Test: `tests/ui/test_hometv_player.py`

**Interfaces:**
- `gridRows()` trả các hàng điều hướng theo thứ tự trên màn hình trong tab đang hiện: (Home) hàng thẻ Xem gần đây, hàng nút `Tất cả/Alive`, hàng nút dropdown `Nhóm` + `Xóa lọc`, rồi các hàng thẻ lưới; (Tìm kiếm) `#tvSearch`, rồi lưới; (Tủ kênh) các hàng thẻ. Trong trang play: Lên/Xuống đổi kênh (như hiện nay), Esc đóng, Enter trong player không mở lại thẻ cũ. Khi focus nằm trong `#tvPlaylist` thì Lên/Xuống để trình duyệt cuộn/di chuyển giữa các dòng (không gọi `stepPlayer`). Phím `f` trên thẻ vẫn bật yêu thích.

- [ ] **Step 1: Cập nhật/viết test.** Sửa `test_tv_remote_grid_navigation_enter_and_star` (bỏ chờ chip yêu thích; kiểm DB và thẻ có `aria-pressed="true"`), thêm `test_tv_remote_moves_from_recent_row_to_filter_row_to_grid`, `test_tv_up_down_in_playlist_moves_between_rows_without_switching`. Các test `test_tv_up_down_changes_channel…`, `test_enter_inside_player…`, `test_one_enter_on_grid_card…` phải còn xanh.
- [ ] **Step 2: Chạy**, đỏ ở test mới.
- [ ] **Step 3: Cài đặt**, rồi xoá CSS và id mồ côi (`#tvChips`, `.tv-bar`…); `grep -n "tvChips\|tv-bar\|tv-chips" -r static templates tests` phải rỗng.
- [ ] **Step 4: Chạy toàn bộ:** `.venv/bin/python -m pytest -q` (nền, ~5 phút, ra file trong scratchpad) và `node --test tests/js/*.test.mjs`. Kỳ vọng: toàn bộ xanh (có thể 1 skipped `ffprobe`).
- [ ] **Step 5: Ghi backlog.** Thêm vào `docs/superpowers/backlog-hometv.md` mục "Bố cục HomeTV (bổ sung)": các việc chủ dự án test cuối tuần cho bố cục mới (xem trên TV thật bằng remote, bảng 10 dòng với danh sách kênh dài, hàng Xem gần đây trên điện thoại, chấm trạng thái sau khi bộ kiểm chạy trên VPS Việt Nam, lọc Alive khi có dữ liệu thật).
- [ ] **Step 6: Commit** `feat(hometv): điều khiển remote theo bố cục mới, dọn CSS, ghi backlog`.
