# Danh Sách Tập Phim Bộ Đồng Nhất (Unified Episode List) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thay 2 render path danh sách tập tách biệt (player drawer `.ep-btn` + trang đã lưu `.saved-ep-btn`) bằng MỘT component `renderEpisodeList()` dùng chung 3 nơi (trang chi tiết + drawer + trang đã lưu), hiển thị đầy đủ trạng thái xem/tải, và xử lý scale >100/1000 tập bằng chunk 100 + pager + jump.

**Architecture:** 1 hàm render duy nhất `renderEpisodeList(containerEl, opts)` trong `templates/index.html` (createElement — tránh lỗi escape chuỗi), nhận `episodes/watchStates/movieSlug/onPlay`; chunk state lưu trên element (`containerEl._epChunk`); giữ nguyên backend + cơ chế `.ep-dl-indicator`/`updateDownloadIndicatorsUI()` (poll 5s) + drawer toggle.

**Tech Stack:** Vanilla JS/CSS trong `templates/index.html` (3578 dòng), không build step, không framework, không test framework — kiểm thử bằng CDP headless Chrome.

**Tham chiếu spec:** `docs/superpowers/specs/2026-07-31-unified-episode-list-design.md`.

## Global Constraints

- Chỉ sửa `templates/index.html`. **Không đụng** `app.py`, `.ep-dl-indicator` CSS cơ bản, cơ chế `/api/saved/progress`, `/api/download/*`, polling `fetchDownloadStatuses`.
- Mọi UI text, comment bằng **tiếng Việt**.
- Watch state lấy từ `savedMoviesList` (`episode_states` keyed by `link_m3u8`); phim chưa lưu → `watchStates = {}` (không watch icon, vẫn tải được).
- Component render tối đa `EP_CHUNK = 100` button/lần; pager + ô "Nhảy tới tập" chỉ hiện khi tổng > 100.
- `episodeNumber(name)` parse số đầu tiên từ tên (VD "Tập 3" → 3); tập không có số (Trailer/Full) → chunk 1, không jump được.
- Nút tập luôn `event.stopPropagation()` trước khi gọi `onPlay` (trang đã lưu có `card.onclick` mở detail — không được bắn).
- Giữ nguyên: `.device-tv` focus ring, `.device-mobile` 2 cột, tắt backdrop-filter mobile, `.episodes-drawer` toggle/collapse.

---

### Task 1: Component `renderEpisodeList` + CSS mới

**Files:**
- Modify: `templates/index.html` — thêm CSS block mới sau khối `.episodes-drawer` CSS (sau dòng ~360); thêm JS section mới sau `buildEpisodesGridInDrawer` (sau dòng 2127).

**Interfaces:**
- Produces (dùng bởi Task 2/3/4):
  - `const EP_CHUNK = 100`
  - `function episodeNumber(name)` → number | NaN
  - `function renderEpisodeList(containerEl, opts)` với `opts = { episodes, movieSlug, watchStates, onPlay(ep, globalIndex), autoFocusWatching?, showTitle?, showMeta? }`; đọc/ghi `containerEl._epChunk`; gọi `requestDownloadEpisode` khi bấm indicator; gán `btn.__epUrl = ep.link_m3u8` (cho highlight active).
  - Helper nội bộ `el(tag, className, text)`.

- [ ] **Step 1: Thêm CSS block** (chèn ngay sau dòng đóng của `.episodes-drawer .episodes-grid::-webkit-scrollbar-thumb`, ~dòng 361)

```css

        /* --- EPISODE LIST (dùng chung detail + drawer + saved) --- */
        .ep-list {
            display: flex;
            flex-direction: column;
            gap: 0.6rem;
        }
        .ep-list-header {
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            gap: 0.75rem;
            flex-wrap: wrap;
        }
        .ep-list-title {
            font-size: 1.05rem;
            font-weight: 600;
            color: var(--text-main);
        }
        .ep-list-meta {
            font-size: 0.78rem;
            color: var(--text-dim);
        }
        .ep-list-pager {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            flex-wrap: wrap;
        }
        .ep-pager-btn {
            min-width: 34px;
            height: 34px;
            border-radius: var(--radius-md);
            border: 1px solid var(--glass-border);
            background: rgba(18, 18, 20, 0.7);
            color: var(--text-main);
            font-size: 1rem;
            cursor: pointer;
            transition: var(--transition);
        }
        .ep-pager-btn:hover:not(:disabled) {
            border-color: var(--glass-border-strong);
            background: rgba(255, 255, 255, 0.06);
            transform: translateY(-1px);
        }
        .ep-pager-btn:active:not(:disabled) {
            transform: scale(0.96);
        }
        .ep-pager-btn:disabled {
            opacity: 0.35;
            cursor: default;
        }
        .ep-pager-label {
            font-size: 0.82rem;
            color: var(--text-dim);
            white-space: nowrap;
        }
        .ep-jump-input {
            width: 150px;
            height: 34px;
            padding: 0 0.6rem;
            border-radius: var(--radius-md);
            border: 1px solid var(--glass-border);
            background: rgba(18, 18, 20, 0.7);
            color: var(--text-main);
            font-size: 0.8rem;
        }
        .ep-jump-input::placeholder {
            color: var(--text-dim);
        }
        .ep-list-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
            gap: 0.5rem;
            max-height: 340px;
            overflow-y: auto;
            padding-right: 0.35rem;
        }
        .ep-list-grid::-webkit-scrollbar {
            width: 6px;
        }
        .ep-list-grid::-webkit-scrollbar-thumb {
            background: rgba(255,255,255,0.08);
            border-radius: 3px;
        }
        .ep-item {
            display: inline-flex;
            align-items: center;
            gap: 0.45rem;
            position: relative;
            background: rgba(18, 18, 20, 0.7);
            border: 1px solid var(--glass-border);
            padding: 0.6rem 0.75rem;
            color: var(--text-dim);
            font-size: 0.82rem;
            font-weight: 500;
            text-align: left;
            cursor: pointer;
            border-radius: var(--radius-md);
            transition: var(--transition);
            white-space: nowrap;
            overflow: hidden;
        }
        .ep-item:hover {
            background: rgba(255, 255, 255, 0.04);
            color: var(--text-main);
            border-color: var(--glass-border-strong);
            transform: translateY(-1px);
        }
        .ep-item:active {
            transform: scale(0.97);
        }
        .ep-item .ep-name {
            flex: 1;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .ep-item .ep-watch-state {
            display: inline-flex;
            align-items: center;
            flex-shrink: 0;
        }
        .ep-item.is-watched {
            color: #2cd054;
            border-color: rgba(44, 208, 84, 0.4);
        }
        .ep-item.is-watched:hover {
            background: rgba(44, 208, 84, 0.08);
            color: #2cd054;
        }
        .ep-item.is-watching {
            color: var(--accent-color);
            border-color: rgba(229, 9, 20, 0.45);
            animation: ep-pulse-accent 2.2s ease-in-out infinite;
        }
        .ep-item.is-watching:hover {
            background: rgba(229, 9, 20, 0.1);
            color: var(--accent-color);
        }
        .ep-item.active {
            background: rgba(229, 9, 20, 0.12);
            color: var(--accent-color);
            border-color: rgba(229, 9, 20, 0.35);
        }
        @keyframes ep-pulse-accent {
            0% { box-shadow: 0 0 0 0 rgba(229, 9, 20, 0.35); }
            70% { box-shadow: 0 0 0 5px rgba(229, 9, 20, 0); }
            100% { box-shadow: 0 0 0 0 rgba(229, 9, 20, 0); }
        }
```

- [ ] **Step 2: Thêm JS component** (chèn ngay sau hàm `buildEpisodesGridInDrawer`, ~dòng 2128)

```js

        // --- Unified Episode List (dùng chung detail + drawer + saved) ---
        const EP_CHUNK = 100;

        function episodeNumber(name) {
            if (!name) return NaN;
            const m = String(name).match(/\d+/);
            return m ? parseInt(m[0], 10) : NaN;
        }

        function el(tag, className, text) {
            const n = document.createElement(tag);
            if (className) n.className = className;
            if (text !== undefined) n.textContent = text;
            return n;
        }

        function renderEpisodeList(containerEl, opts) {
            if (!containerEl) return;
            const { episodes, movieSlug, watchStates, onPlay, autoFocusWatching, showTitle, showMeta } = opts;
            if (!episodes || episodes.length === 0) {
                containerEl.innerHTML = '';
                return;
            }
            const ws = watchStates || {};
            const total = episodes.length;
            const chunkCount = Math.max(1, Math.ceil(total / EP_CHUNK));

            let chunk = containerEl._epChunk;
            if (typeof chunk !== 'number' || chunk < 0 || chunk >= chunkCount) {
                chunk = 0;
                if (autoFocusWatching !== false) {
                    const wIdx = episodes.findIndex(ep => ws[ep.link_m3u8] === 'watching');
                    if (wIdx > -1) chunk = Math.floor(wIdx / EP_CHUNK);
                }
            }
            containerEl._epChunk = chunk;

            containerEl.innerHTML = '';

            const list = el('div', 'ep-list');

            const start = chunk * EP_CHUNK;
            const slice = episodes.slice(start, start + EP_CHUNK);

            const header = el('div', 'ep-list-header');
            if (showTitle !== false) {
                header.appendChild(el('span', 'ep-list-title', `Danh sách tập (${total})`));
            }
            if (showMeta !== false) {
                const metaBits = [];
                const watchedCount = Object.values(ws).filter(s => s === 'watched').length;
                if (watchedCount > 0) metaBits.push(`Đã xem ${watchedCount}`);
                let dlCount = 0;
                if (movieSlug && downloadStatuses[movieSlug]) {
                    dlCount = Object.values(downloadStatuses[movieSlug]).filter(s => s === 'completed').length;
                }
                if (dlCount > 0) metaBits.push(`Đã tải ${dlCount}`);
                if (metaBits.length) header.appendChild(el('span', 'ep-list-meta', metaBits.join(' · ')));
            }
            if (header.childElementCount > 0) list.appendChild(header);

            if (chunkCount > 1) {
                const pager = el('div', 'ep-list-pager');
                const prev = el('button', 'ep-pager-btn', '‹');
                prev.disabled = chunk === 0;
                prev.title = 'Trang trước';
                prev.onclick = () => {
                    containerEl._epChunk = chunk - 1;
                    renderEpisodeList(containerEl, opts);
                };
                pager.appendChild(prev);
                pager.appendChild(el('span', 'ep-pager-label', `Tập ${start + 1}–${Math.min(start + EP_CHUNK, total)} / ${total}`));
                const next = el('button', 'ep-pager-btn', '›');
                next.disabled = chunk >= chunkCount - 1;
                next.title = 'Trang sau';
                next.onclick = () => {
                    containerEl._epChunk = chunk + 1;
                    renderEpisodeList(containerEl, opts);
                };
                pager.appendChild(next);
                const jump = el('input', 'ep-jump-input');
                jump.type = 'number';
                jump.min = 1;
                jump.max = total;
                jump.placeholder = 'Nhảy tới tập';
                jump.onkeydown = (e) => {
                    if (e.key !== 'Enter') return;
                    const n = parseInt(jump.value, 10);
                    if (!isNaN(n)) {
                        const idx = Math.min(total - 1, Math.max(0, n - 1));
                        containerEl._epChunk = Math.floor(idx / EP_CHUNK);
                        renderEpisodeList(containerEl, opts);
                    }
                };
                pager.appendChild(jump);
                list.appendChild(pager);
            }

            const grid = el('div', 'ep-list-grid');
            slice.forEach((ep, i) => {
                const realIdx = start + i;
                const btn = el('button', 'ep-item');
                btn.__epUrl = ep.link_m3u8;

                const st = ws[ep.link_m3u8];
                if (st === 'watching') {
                    btn.classList.add('is-watching');
                    btn.title = 'Đang xem — tiếp tục';
                } else if (st === 'watched') {
                    btn.classList.add('is-watched');
                    btn.title = 'Đã xem';
                }

                if (st === 'watching' || st === 'watched') {
                    const wsIcon = el('span', 'ep-watch-state');
                    wsIcon.innerHTML = st === 'watched'
                        ? '<svg width="12" height="12" fill="currentColor" viewBox="0 0 24 24"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>'
                        : '<svg width="12" height="12" fill="currentColor" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>';
                    btn.appendChild(wsIcon);
                }

                btn.appendChild(el('span', 'ep-name', ep.name));

                if (movieSlug) {
                    const indicator = el('span', 'ep-dl-indicator not_started');
                    indicator.setAttribute('data-movie-slug', movieSlug);
                    indicator.setAttribute('data-ep-name', ep.name);
                    indicator.setAttribute('data-ep-url', ep.link_m3u8);
                    indicator.onclick = (e) => requestDownloadEpisode(e, movieSlug, ep.name, ep.link_m3u8);
                    btn.appendChild(indicator);
                }

                btn.onclick = (e) => {
                    e.stopPropagation();
                    if (onPlay) onPlay(ep, realIdx);
                };
                grid.appendChild(btn);
            });
            list.appendChild(grid);
            containerEl.appendChild(list);
        }
```

- [ ] **Step 3: Kiểm tra JS syntax**

Không có node — dùng python:
```bash
python3 - <<'EOF'
import re, sys
src = open('templates/index.html', encoding='utf-8').read()
m = re.search(r'<script>(.*)</script>', src, re.S)
if not m:
    print("KHONG TIM THAY SCRIPT"); sys.exit(1)
js = m.group(1)
# Đếm cặp ngoặc
for a, b in [('{','}'), ('(',')'), ('[',']')]:
    print(a, js.count(a), b, js.count(b), 'OK' if js.count(a) == js.count(b) else 'LECH')
print('renderEpisodeList def:', 'function renderEpisodeList(' in js)
print('EP_CHUNK:', 'const EP_CHUNK = 100;' in js)
EOF
```
Expected: các cặp ngoặc cân bằng, 2 dòng kiểm tra `True`.

- [ ] **Step 4: Test component bằng CDP headless Chrome (synthetic 1100 tập)**

Server phải chạy: `uvicorn app:app --reload --host 0.0.0.0 --port 6969` (hoặc đã chạy). Viết script `/tmp/unified-ep-cdp.py` (Python stdlib, pattern CDP headless như các lần trước). Nội dung chính qua `Runtime.evaluate`:

```js
// Inject môi trường giả + gọi component
window.__testOut = {};
(function () {
  const eps = [];
  for (let i = 1; i <= 1100; i++) eps.push({ name: 'Tập ' + i, link_m3u8: 'https://x/' + i + '.m3u8' });
  window.__watch = {};
  window.__watch[eps[499].link_m3u8] = 'watching';   // Tập 500 đang xem
  window.__watch[eps[0].link_m3u8] = 'watched';      // Tập 1 đã xem
  window.__testEps = eps;
  window.downloadStatuses = { 'phim-test': { 'Tập 2': 'completed' } };
  const holder = document.createElement('div');
  holder.id = 'testEpHolder';
  document.body.appendChild(holder);
  renderEpisodeList(holder, {
    episodes: eps, movieSlug: 'phim-test', watchStates: window.__watch,
    onPlay: (ep, idx) => { window.__testOut.played = { name: ep.name, idx }; }
  });
  window.__testOut.holder = holder;
})();
```

Check (in order, mỗi check print PASS/FAIL):
- C1: `holder.querySelectorAll('.ep-item').length === 100`
- C2: pager label `Tập 401–500 / 1100` (autoFocusWatching mở chunk chứa Tập 500 — index 499 → chunk 4 → start 400, hiển thị 401–500)
- C3: `holder.querySelector('.ep-item.is-watching .ep-name').textContent === 'Tập 500'`
- C4: bấm `›` → label `Tập 501–600 / 1100`
- C5: set `jump.value = '1050'; jump.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter'}))` → label `Tập 1001–1100 / 1100`
- C6: nhảy về chunk đầu (`jump.value = '1'; dispatchEvent Enter`) → bấm button `.ep-item.is-watched` → `__testOut.played` có `name === 'Tập 1'` và `idx === 0`; và click KHÔNG bubble (không lỗi)
- C7: indicator download tồn tại: `holder.querySelectorAll('.ep-dl-indicator').length === 100`; sau khi chạy `updateDownloadIndicatorsUI()` → indicator của "Tập 2" có class `completed`
- C8: click indicator không bắn `onPlay` (bấm indicator → `__testOut.played` không đổi)

- [ ] **Step 5: Commit**

```bash
git add templates/index.html
git commit -m "feat(ui): add unified episode list component with chunked rendering"
```

---

### Task 2: Trang chi tiết — section danh sách tập

**Files:**
- Modify: `templates/index.html` — `showMovieDetail` (~3122-3184); `play()` progress handler (~2957-2990); thêm hàm `refreshDetailEpisodeList()`; CSS `.detail-episodes`.

**Interfaces:**
- Consumes: `renderEpisodeList`, `EP_CHUNK` (Task 1); `checkIsSaved`, `savedMoviesList`, `currentDetailEpisodes`, `currentMovie`, `playEpisodesListDirect` (đã có).
- Produces: `refreshDetailEpisodeList()` (dùng lại ở Task 3 nếu cần); container `#detailEpisodeList`.

- [ ] **Step 1: Thêm CSS `.detail-episodes`** (chèn sau khối `.ep-list` CSS Task 1)

```css
        .detail-episodes {
            margin-top: 1rem;
            background: rgba(18, 18, 20, 0.7);
            border: 1px solid var(--glass-border-strong);
            border-radius: var(--radius-xl);
            padding: 1.25rem;
            backdrop-filter: blur(8px);
        }
```

- [ ] **Step 2: Chèn section vào `showMovieDetail` innerHTML**

Trong template `detailDiv.innerHTML = \`...\`` tại `showMovieDetail` (~3146-3176), chèn dòng sau `<div class="detail-overview">...</div>` (dòng 3165):

```html
                        <div class="detail-episodes" id="detailEpisodeList"></div>
```

- [ ] **Step 3: Render episode list sau khi set innerHTML**

Trong `showMovieDetail`, ngay sau `setTimeout(initTabbing, 100);` (dòng 3177), chèn:

```js
                    if (episodesList.length > 0) {
                        const savedMovie = savedMoviesList.find(item => item.slug === m.slug);
                        const watchStates = (savedMovie && savedMovie.episode_states) ? savedMovie.episode_states : {};
                        renderEpisodeList(document.getElementById('detailEpisodeList'), {
                            episodes: episodesList,
                            movieSlug: m.slug,
                            watchStates,
                            onPlay: (ep) => {
                                currentMovie = { slug: m.slug, name: m.name, poster_url: m.poster_url };
                                playEpisodesListDirect(currentDetailEpisodes, ep.link_m3u8);
                            }
                        });
                    }
```

- [ ] **Step 4: Thêm `refreshDetailEpisodeList()`** (chèn ngay sau `showMovieDetail`, trước `playSelectedMovieDirect` ~dòng 3185)

```js
        function refreshDetailEpisodeList() {
            const listEl = document.getElementById('detailEpisodeList');
            if (!listEl || !currentDetailEpisodes || currentDetailEpisodes.length === 0) return;
            let watchStates = {};
            let movie = currentMovie;
            if (movie && movie.slug) {
                const saved = savedMoviesList.find(item => item.slug === movie.slug);
                if (saved && saved.episode_states) watchStates = saved.episode_states;
            }
            renderEpisodeList(listEl, {
                episodes: currentDetailEpisodes,
                movieSlug: movie ? movie.slug : null,
                watchStates,
                onPlay: (ep) => {
                    if (movie) currentMovie = { slug: movie.slug, name: movie.name, poster_url: movie.poster_url };
                    playEpisodesListDirect(currentDetailEpisodes, ep.link_m3u8);
                }
            });
        }
```

- [ ] **Step 5: Gọi refresh khi cập nhật tiến độ xem trong `play()`**

Trong `play()` (async), trong `.then(res => res.json()).then(data => {...})` của `POST /api/saved/progress` (khoảng 2957-2990) — sau khối cập nhật `savedMoviesList[sIdx].episode_states[url] = "watching";` — chèn:

```js
                        refreshDetailEpisodeList();
```

- [ ] **Step 6: Test CDP**

Viết script `/tmp/unified-ep-detail-cdp.py` (pattern cũ):
1. Tìm 1 phim bộ có tập (search → mở detail; hoặc dùng slug có sẵn từ `/api/home/phim-bo`). Chờ `.detail-episodes .ep-item` xuất hiện.
2. Check: `document.querySelectorAll('#detailEpisodeList .ep-item').length > 0`; `.ep-list-title` text = `Danh sách tập (N)`.
3. Bấm 1 `.ep-item` → chờ `#drawerEpGrid` tồn tại và `document.getElementById('epCount').textContent === String(N)`; `currentPlaybackIndex` đúng index.
4. Nếu phim bộ nào < 100 tập → check không có pager (`.ep-list-pager` absent).
5. Regression: bấm "Quay lại" → về search; bấm lại phim → detail render lại không lỗi.

- [ ] **Step 7: Commit**

```bash
git add templates/index.html
git commit -m "feat(ui): show unified episode list on movie detail page"
```

---

### Task 3: Player drawer — dùng component chung

**Files:**
- Modify: `templates/index.html` — `buildEpisodesDrawer` (~2069-2090), `buildEpisodesGridInDrawer` (~2099-2127), `play()` selector (~2951), CSS `.episodes-drawer .episodes-grid` (~346-353).

**Interfaces:**
- Consumes: `renderEpisodeList`, `EP_CHUNK` (Task 1); `currentMovie`, `savedMoviesList`, `play()`.
- Produces: `syncDrawerToPlaying(url)` (cập nhật chunk + active theo tập đang phát).

- [ ] **Step 1: Đổi class grid drawer**

Trong `buildEpisodesDrawer`, đổi dòng `grid.className = 'episodes-grid';` (2083) thành:

```js
    grid.className = 'episodes-drawer-grid';
```

- [ ] **Step 2: Thêm CSS `.ep-drawer-grid`** (chèn sau CSS `.episodes-drawer` block, cùng chỗ Task 1 Step 1)

```css
        .episodes-drawer .episodes-drawer-grid {
            display: flex;
            flex-direction: column;
            gap: 0.6rem;
            padding: 0.75rem 1rem;
        }
```

- [ ] **Step 3: Thay thân `buildEpisodesGridInDrawer`**

Thay toàn bộ hàm `buildEpisodesGridInDrawer` (2099-2127) bằng:

```js
        function buildEpisodesGridInDrawer(episodesArray, playIdx) {
            const grid = document.getElementById('drawerEpGrid');
            if (!grid) return;

            const countEl = document.getElementById('epCount');
            if (countEl) countEl.textContent = episodesArray.length;

            const safePlayIdx = Math.max(0, playIdx || 0);
            grid._epChunk = Math.floor(safePlayIdx / EP_CHUNK);

            let watchStates = {};
            if (currentMovie && currentMovie.slug) {
                const saved = savedMoviesList.find(item => item.slug === currentMovie.slug);
                if (saved && saved.episode_states) watchStates = saved.episode_states;
            }

            renderEpisodeList(grid, {
                episodes: episodesArray,
                movieSlug: currentMovie ? currentMovie.slug : null,
                watchStates,
                showTitle: false,
                onPlay: (ep, i) => play(ep.link_m3u8, ep.name, i)
            });

            const localIdx = safePlayIdx - grid._epChunk * EP_CHUNK;
            const activeBtn = grid.querySelectorAll('.ep-item')[localIdx];
            if (activeBtn) activeBtn.classList.add('active');
        }
```

- [ ] **Step 4: Thêm `syncDrawerToPlaying()`** (chèn ngay sau `buildEpisodesGridInDrawer`)

```js
        function syncDrawerToPlaying(url) {
            const grid = document.getElementById('drawerEpGrid');
            if (!grid) return;
            const epIdx = playbackEpisodes.findIndex(ep => ep.link_m3u8 === url);
            if (epIdx === -1) return;
            const targetChunk = Math.floor(epIdx / EP_CHUNK);
            if (grid._epChunk !== targetChunk) {
                grid._epChunk = targetChunk;
                buildEpisodesGridInDrawer(playbackEpisodes, epIdx);
            } else {
                grid.querySelectorAll('.ep-item').forEach(b => b.classList.toggle('active', b.__epUrl === url));
            }
        }
```

- [ ] **Step 5: Sửa `play()` — thay selector chết + gọi sync**

Trong `play()` (2948-2952), thay dòng:

```js
            const btns = document.querySelectorAll('#epGridContent .ep-btn, #drawerEpGrid .ep-btn');
            btns.forEach((b, i) => b.classList.toggle('active', i === idx));
```

bằng:

```js
            syncDrawerToPlaying(url);
```

(Đặt ngay sau `currentPlaybackIndex = idx;`.)

- [ ] **Step 6: Test CDP**

Script `/tmp/unified-ep-drawer-cdp.py`:
1. Mở detail 1 phim bộ → bấm 1 tập (hoặc Phát Ngay) → chờ `#drawerEpGrid .ep-item` xuất hiện.
2. Check: `#drawerEpGrid .ep-item` có `.ep-name`; `#drawerEpGrid .ep-list-header` KHÔNG có `.ep-list-title` (showTitle false); toggle drawer còn hoạt động.
3. Check active: đúng 1 `.ep-item.active` và `__epUrl` trùng tập đang phát.
4. Auto-advance: nếu phim >1 tập, `playNextEpisode()` → active chuyển đúng tập mới.
5. Nếu phim là phim đã lưu: sau khi phát, `.ep-item.is-watching` tồn tại trong drawer.
6. Phim bộ >100 tập (nếu tìm được qua `/api/home/phim-bo`): pager hiển thị; phát tập 150 → drawer tự mở chunk chứa tập 150.

- [ ] **Step 7: Commit**

```bash
git add templates/index.html
git commit -m "feat(ui): reuse unified episode list in player drawer"
```

---

### Task 4: Trang đã lưu — dùng component chung

**Files:**
- Modify: `templates/index.html` — `renderSavedMovies` episode loop (~3305-3327); CSS `.saved-movie-row .episodes-container` (~1638-1650).

**Interfaces:**
- Consumes: `renderEpisodeList` (Task 1); `m.episode_states`, `m.episodes`, `m.slug`, `playEpisodeFromSaved`, `syncEpisodesForMovie` (đã có).
- Produces: không có API mới.

- [ ] **Step 1: Sửa CSS `.episodes-container`**

Thay khối `.saved-movie-row .episodes-container` (hiện là `display:flex; flex-wrap:wrap;` + `max-height:130px`) bằng:

```css
        .saved-movie-row .episodes-container {
            display: block;
            margin-top: 0.5rem;
            width: 100%;
        }
```

- [ ] **Step 2: Thay vòng lặp episode trong `renderSavedMovies`**

Trong `renderSavedMovies` (3296-3356), thay khối `let epsHtml = "";` + `if (m.episodes...)` (3305-3327) bằng:

```js
                let epsContainer = null;
                if (m.episodes && m.episodes.length > 0) {
                    epsContainer = document.createElement('div');
                    epsContainer.className = 'episodes-container';
                } else {
                    epsContainer = document.createElement('div');
                    epsContainer.className = 'episodes-container';
                    epsContainer.innerHTML = `<span style="font-size: 0.85rem; color: var(--text-dim);">Chưa có danh sách tập phim. <a href="#" style="color: var(--accent-color); text-decoration: underline;" onclick="event.stopPropagation(); syncEpisodesForMovie(\`${m.slug}\`)">Đồng bộ ngay</a></span>`;
                }
```

Và trong template `card.innerHTML = \`...\`` thay dòng `<div class="episodes-container">${epsHtml}</div>` (3336-3338) bằng:

```html
                            <div class="eps-host"></div>
```

Sau khi `grid.appendChild(card);` (3355) — **trước** dòng `updateDownloadIndicatorsUI();` — chèn:

```js
                const epsHost = card.querySelector('.eps-host');
                if (epsHost) {
                    if (m.episodes && m.episodes.length > 0) {
                        epsHost.replaceWith(epsContainer);
                        renderEpisodeList(epsContainer, {
                            episodes: m.episodes,
                            movieSlug: m.slug,
                            watchStates: m.episode_states || {},
                            onPlay: (ep, idx) => playEpisodeFromSaved(m.slug, idx)
                        });
                    } else {
                        epsHost.replaceWith(epsContainer);
                    }
                }
```

Lưu ý: `.eps-host` chỉ placeholder tạm trong card — sau `replaceWith` không còn tồn tại (mỗi card 1 cái, dùng class thay ID để tránh trùng ID trong vòng lặp).

- [ ] **Step 3: Test CDP**

Script `/tmp/unified-ep-saved-cdp.py`:
1. Vào tab "Phim Đã Lưu" (`switchTab('saved-tab')`) — chờ `.saved-movie-row .ep-item`.
2. Check: mỗi row có `.ep-item`; `.ep-list-title` = `Danh sách tập (N)`; các button tải `.ep-dl-indicator` có data-attrs đúng.
3. Check watch state: phim đã phát có `.ep-item.is-watching` / `.is-watched` đúng số lượng.
4. Bấm 1 `.ep-item` → `#drawerEpGrid` mở đúng tập (không kích hoạt `card.onclick` mở detail — verify `movieDetailContainer` không display block).
5. Bấm vào `.saved-movie-row` (ngoài nút tập) → vẫn mở detail như cũ.
6. Bấm "Xem tiếp" → resume đúng tập đang xem.
7. Row không có tập → hiện link "Đồng bộ ngay".

- [ ] **Step 4: Commit**

```bash
git add templates/index.html
git commit -m "feat(ui): reuse unified episode list on saved movies page"
```

---

### Task 5: Dọn CSS chết + E2E tổng hợp

**Files:**
- Modify: `templates/index.html` — xóa CSS `.episodes-section`, `.episodes-grid` (generic), `.ep-btn`, `.saved-ep-btn`, `pulse-border-yellow`, rule gộp `.ep-btn, .saved-ep-btn`, selector `.device-tv .ep-btn`, `.device-mobile .ep-btn`, `body.player-active .episodes-section`; thay bằng `.ep-item`/`.ep-drawer-grid`.

**Interfaces:**
- Không sản sinh API mới. Kết quả: không còn class `ep-btn`, `saved-ep-btn`, `episodes-section`, `episodes-grid` (generic) trong toàn file.

- [ ] **Step 1: Xóa các khối CSS chết** (đối chiếu đúng vị trí, dòng có thể đã lệch sau các task trước — dùng nội dung để tìm):

1. Khối `.episodes-drawer .episodes-grid { ... }` + scrollbar của nó (~346-360) — xóa (thay thế bằng `.episodes-drawer .episodes-drawer-grid` đã thêm ở Task 3 Step 2).
2. Khối `.device-mobile .episodes-drawer .episodes-grid` (~374-377) — chuyển selector thành `.device-mobile .episodes-drawer .ep-list-grid` (giữ style 2 cột cho grid bên trong component).
3. Khối `.episodes-section { ... }` (display:none, ~549-558) — xóa.
4. Khối `.episodes-grid { ... }` generic + scrollbar (~560-576) — xóa.
5. Khối `.ep-btn { ... }`, `.ep-btn:hover`, `.ep-btn.active` (~578-604) — xóa.
6. Toàn bộ `.saved-ep-btn.*` (~1650-1714) + `@keyframes pulse-border-yellow` — xóa.
7. Rule `.ep-btn, .saved-ep-btn { display:inline-flex... }` + `.ep-btn { min-width:140px }` (~1716-1725) — xóa.
8. `.saved-movie-row .episodes-container::-webkit-scrollbar` + thumb (~1649-1656) — xóa (sau Task 4 Step 1 `.episodes-container` là `display:block`, hết scroll).
9. `body.player-active .episodes-section` (tìm `player-active .episodes-section`, ~1847-1851) — sửa selector còn lại thành `body.player-active #statusMessage` (bỏ `.episodes-section` khỏi nhóm).

- [ ] **Step 2: Đổi selector còn tham chiếu `.ep-btn`/`.saved-ep-btn`/`.episodes-section`:**

| Vị trí cũ | Thay bằng |
|---|---|
| `.device-tv .ep-btn { ... }` (~992-996) | `.device-tv .ep-item { ... }` (giữ nguyên style) |
| `.device-tv .ep-btn:focus,` trong list focus (~1036) | `.device-tv .ep-item:focus,` |
| `.device-mobile .ep-btn { ... }` (~1090-1093) | `.device-mobile .ep-item { ... }` |
| `.device-mobile .ep-btn,` trong list backdrop (~1138) | `.device-mobile .ep-item,` |
| `.device-mobile .saved-ep-btn,` (~1139) | xóa dòng (không còn class) |
| `.device-mobile .episodes-section,` (~1147) | xóa dòng |

- [ ] **Step 3: Xác minh không còn sót**

```bash
grep -n "ep-btn\|saved-ep-btn\|episodes-section\|episodes-grid" templates/index.html
```
Expected: **0 kết quả**. (Nếu còn sót ở comment/JS string — dọn tiếp. `episodes-drawer-grid` và `episodes-drawer` được phép tồn tại.)

- [ ] **Step 4: E2E tổng hợp CDP** (`/tmp/unified-ep-e2e-cdp.py`, tái dùng các check Task 1-4):

1. **Detail**: mở phim bộ → `.ep-list` hiện; bấm tập → player đúng tập; bấm "Quay lại" → về được.
2. **Drawer**: `.ep-item` đúng; toggle đóng/mở; `syncDrawerToPlaying` cập nhật active khi auto-advance.
3. **Saved**: watch state (`is-watched`/`is-watching`) + download indicator; bấm tập không mở detail; row click vẫn mở detail; "Xem tiếp" resume.
4. **Chunk/jump (synthetic 1100)**: lặp lại C1-C5 Task 1 — pager + jump + watching-chunk.
5. **Download**: bấm indicator 1 tập → `pending` → (chờ poll) → `downloading`/`completed`; không bắn play.
6. **Regression**: `/api/home/*` + `/api/movie/{slug}` còn 200; phim lẻ (không có tập) detail không lỗi; TV focus (device-tv) `.ep-item` có focus ring; mobile (device-mobile) `.ep-item` font nhỏ hơn + 2 cột.

- [ ] **Step 5: Commit**

```bash
git add templates/index.html
git commit -m "chore(ui): remove dead episode-list CSS and finalize unified component"
```

---

## Self-Review

**Spec coverage:**
- 1 component dùng chung 3 nơi → Task 1 (component) + Task 2 (detail) + Task 3 (drawer) + Task 4 (saved).
- Trạng thái đã xem/đang xem/đã tải/đang tải → `.ep-item.is-watched`/`.is-watching` (icon trái) + `.ep-dl-indicator` (phải, giữ cơ chế cũ).
- Scale >100/1000 → `EP_CHUNK=100`, pager `‹ Tập x–y / N ›`, ô nhập jump, autoFocusWatching.
- Header tóm tắt `Đã xem X · Đã tải Y` → `.ep-list-meta`.
- Xóa `.ep-btn`/`.saved-ep-btn` → Task 5 (grep 0). CSS `.episodes-section` chết → Task 5.
- Không đụng backend → không task nào sửa `app.py`.
- Phim chưa lưu không watch state → component nhận `watchStates = {}` (Task 2 Step 3, Task 4 watchStates `m.episode_states || {}`).
- Giữ TV/mobile/focus → Task 5 Step 2 chuyển selector, Task 4 giữ `.device-mobile` 2 cột cho grid cũ → cập nhật sang `.ep-item`.

**Placeholder scan:** Không có TBD/TODO; mọi bước đều có code/command cụ thể; CDP scripts được mô tả bằng check cụ thể (không phụ thuộc file /tmp có sẵn).

**Type consistency:** `renderEpisodeList(containerEl, opts)` với opts `{episodes, movieSlug, watchStates, onPlay(ep, globalIndex), autoFocusWatching?, showTitle?, showMeta?}` dùng nhất quán ở 3 call site (Task 2 `m.slug`/`m.episode_states`; Task 3 `currentMovie.slug`/`savedMoviesList` lookup; Task 4 `m.slug`/`m.episode_states`). `btn.__epUrl` + `containerEl._epChunk` nhất quán Task 1→3. `syncDrawerToPlaying` chỉ dùng trong Task 3 (play()). `refreshDetailEpisodeList` chỉ Task 2.
