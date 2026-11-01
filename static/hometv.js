// HomeTV: mode xem kênh truyền hình. Logic thuần ở hometv-utils.js; tại đây là DOM, mạng và player.
(function (root) {
  const U = root.HFHomeTV;
  const MODE_KEY = 'hf_hometv_mode';
  const BATCH = 60;
  const $ = (id) => document.getElementById(id);

  const state = { channels: [], health: false, alive: false, group: '', query: '', shown: 0, loaded: false };
  const RECENT_MAX = 3;
  const SEARCH_MAX = 120;  // kết quả tìm kiếm không phân lô: cắt ở mức này
  let observer = null;

  function store() {
    try { return root.localStorage; } catch (e) { return null; }
  }
  function remember(on) {
    const s = store();
    try { if (s) s.setItem(MODE_KEY, on ? '1' : '0'); } catch (e) { /* storage bị chặn: không nhớ mode */ }
  }
  function remembered() {
    const s = store();
    try { return !!s && s.getItem(MODE_KEY) === '1'; } catch (e) { return false; }
  }

  async function api(method, path) {
    const r = await fetch(path, { method, headers: { 'Content-Type': 'application/json' } });
    if (!r.ok) throw Object.assign(new Error('HTTP ' + r.status), { status: r.status });
    return r.status === 204 ? null : r.json();
  }

  // Danh sách theo bộ lọc của tab Home (Alive, Nhóm); dùng cho lưới và để chuyển kênh trong player
  function visible() {
    return U.filterChannels(state.channels, { alive: state.alive, group: state.group });
  }

  function categoryLabel(c) {
    return U.CATEGORY_LABELS[U.CATEGORY_ORDER.includes(c.category) ? c.category : 'Unknown'];
  }

  function initialEl(c) {
    const span = document.createElement('span');
    span.className = 'tv-initial';
    span.textContent = U.initial(c.name);
    return span;
  }

  // Thẻ kênh dựng bằng buildPosterCard của HomeFlix, gắn thêm sao yêu thích và chấm trạng thái
  function buildChannelCard(c) {
    const card = root.buildPosterCard({ title: c.name, meta: categoryLabel(c), imgSrc: c.logo, onClick: () => open(c.id), focusable: true });
    card.classList.add('tv-card');
    card.dataset.id = String(c.id);
    card.setAttribute('role', 'button');
    const wrap = card.querySelector('.poster-wrap');
    const img = wrap.querySelector('img');
    if (c.logo) img.onerror = () => { img.replaceWith(initialEl(c)); };
    else img.replaceWith(initialEl(c));
    if (state.health && c.status) {
      const dot = document.createElement('span');
      dot.className = 'tv-dot';
      dot.dataset.status = c.status;
      dot.setAttribute('role', 'img');
      dot.setAttribute('aria-label', U.statusLabel(c.status));
      wrap.appendChild(dot);
    }
    const star = document.createElement('button');
    star.type = 'button';
    star.className = 'tv-star';
    star.textContent = '★';
    star.setAttribute('aria-pressed', String(c.favorite));
    star.setAttribute('aria-label', 'Yêu thích');
    star.addEventListener('click', (e) => { e.stopPropagation(); toggleFavorite(c); });
    wrap.appendChild(star);
    card.addEventListener('keydown', (e) => {
      // stopPropagation: trình xử lý Enter chung của trang không click thêm lần nữa
      if (e.key === 'Enter' && e.target === card) { e.preventDefault(); e.stopPropagation(); open(c.id); }
    });
    return card;
  }

  function open(id) {
    if (root.HFTV && typeof root.HFTV.openChannel === 'function') root.HFTV.openChannel(id);
  }

  // Hàng "Kênh xem gần đây": thẻ Xem tiếp của HomeFlix, không vuốt, không thanh tiến độ
  function renderRecent() {
    const list = $('tvRecentList');
    const rows = U.recentChannels(state.channels, RECENT_MAX);
    list.replaceChildren(...rows.map((c, i) => {
      const card = root.buildContinueCard({ slug: 'ch-' + c.id, name: c.name }, 'home-cont' + (i === 0 ? ' is-lead' : ''), {
        poster: () => c.logo, meta: () => categoryLabel(c), pct: () => 0, label: () => 'Mở ' + c.name,
        onPlay: () => open(c.id), swipe: false,
      });
      card.dataset.id = String(c.id);
      return card;
    }));
    $('tvRecent').hidden = rows.length === 0;
  }

  function renderFilters() {
    const alive = document.querySelector('[data-tvfilter="alive"]');
    alive.hidden = !state.health;
    document.querySelectorAll('[data-tvfilter]').forEach((b) => b.classList.toggle('active', (b.dataset.tvfilter === 'alive') === state.alive));
    const opt = U.groupOptions(state.channels).find((o) => o.value === state.group);
    if (!opt) state.group = '';
    const btn = $('filterBtn-group');
    btn.textContent = opt ? opt.label : 'Nhóm';
    btn.classList.toggle('active', !!opt);
    $('tvClearBtn').style.display = state.alive || state.group ? 'inline-block' : 'none';
  }

  function renderGrid() {
    const list = visible();
    const grid = $('tvGrid');
    grid.replaceChildren();
    state.shown = 0;
    $('tvEmpty').hidden = list.length > 0 || !state.loaded || state.channels.length === 0;
    if (!$('tvEmpty').hidden) $('tvEmpty').textContent = 'Không có kênh nào khớp.';
    $('tvResultCount').textContent = state.loaded ? list.length + ' kênh' : '';
    appendBatch(list);
  }

  function appendBatch(list) {
    const grid = $('tvGrid');
    const next = list.slice(state.shown, state.shown + BATCH);
    grid.append(...next.map(buildChannelCard));
    state.shown += next.length;
    $('tvMore').hidden = state.shown >= list.length;
  }

  function loadMore() {
    const more = $('tvMore');
    if (!more.hidden) appendBatch(visible());
  }

  function refilter() {
    renderFilters();
    renderGrid();
  }

  // Tab Tìm kiếm: chỉ hiện kết quả khi có từ khoá, giống ô tìm phim của HomeFlix; lọc Alive vẫn áp dụng
  function renderSearch() {
    const q = state.query.trim();
    const found = q ? U.filterChannels(state.channels, { query: q, alive: state.alive }) : [];
    $('tvSearchGrid').replaceChildren(...found.slice(0, SEARCH_MAX).map(buildChannelCard));
    $('tvSearchEmpty').hidden = !q || found.length > 0;
  }

  // Tab Tủ kênh: yêu thích và xem gần đây; vẽ lại mỗi lần vào tab
  function renderLibrary() {
    const favs = U.favoriteChannels(state.channels);
    const recents = U.recentChannels(state.channels, Infinity);
    $('tvFavGrid').replaceChildren(...favs.map(buildChannelCard));
    $('tvLibRecentGrid').replaceChildren(...recents.map(buildChannelCard));
    $('tvFavSection').hidden = favs.length === 0;
    $('tvLibRecentSection').hidden = recents.length === 0;
    $('tvLibraryEmpty').hidden = favs.length + recents.length > 0;
  }

  function activeTabId() {
    const el = document.querySelector('#hometv-root .tab-content.active');
    return el ? el.id : null;
  }

  function renderActive() {
    const id = activeTabId();
    if (id === 'tv-search-tab') renderSearch();
    else if (id === 'tv-saved-tab') renderLibrary();
    else renderGrid();
  }

  filterProviders.group = () => ({
    options: U.groupOptions(state.channels),
    current: state.group,
    onPick: (value) => { state.group = value; root.closeAllFilterDropdowns(); refilter(); },
  });

  function showMessage(text, retry) {
    const box = $('tvEmpty');
    box.hidden = false;
    box.replaceChildren(document.createTextNode(text));
    if (retry) {
      const b = document.createElement('button');
      b.type = 'button';
      b.id = 'tvRetry';
      b.className = 'filter-option';
      b.textContent = 'Thử lại';
      b.addEventListener('click', load);
      box.append(' ', b);
    }
  }

  async function load() {
    $('tvGrid').replaceChildren();
    $('tvMore').hidden = true;
    try {
      const res = await api('GET', '/api/hometv/channels');
      state.channels = res.channels;
      state.health = res.health;
    } catch (e) {
      state.loaded = false;
      if (e.status === 503) $('hometvToggle').hidden = true;
      showMessage('Không tải được danh sách kênh.', true);
      return;
    }
    state.loaded = true;
    if (state.channels.length === 0) {
      showMessage('Chưa có kênh nào. Admin: duyệt nguồn trong Cài đặt → HomeTV.', false);
      return;
    }
    $('tvEmpty').hidden = true;
    if (!state.health) state.alive = false;
    renderRecent();
    refilter();
  }

  async function toggleFavorite(c) {
    const want = !c.favorite;
    const redraw = () => {
      // Vẽ lại lưới làm mất focus: trả focus về thẻ vừa thao tác (nếu còn trong danh sách)
      const hadFocus = !!document.activeElement && !!document.activeElement.closest('.movies-grid');
      renderActive();
      const card = hadFocus && document.querySelector('#hometv-root .tab-content.active .tv-card[data-id="' + c.id + '"]');
      if (card) card.focus({ preventScroll: true });
    };
    c.favorite = want;
    redraw();
    try {
      await api(want ? 'PUT' : 'DELETE', '/api/hometv/favorites/' + c.id);
    } catch (e) {
      c.favorite = !want;
      redraw();
    }
  }


  // ---------- Player ----------
  const STALL_MS = 8000;
  const SWITCH_DEBOUNCE_MS = 400;
  const play = { token: 0, hls: null, timer: null, streams: [], index: 0, channelId: null, recentSent: false,
    pendingId: null, switchTimer: null };

  function channelById(id) {
    return state.channels.find((c) => c.id === id) || null;
  }

  function isTVDevice() {
    return document.body.classList.contains('device-tv');
  }

  function stopAttempt() {
    play.token += 1;
    if (play.timer) { clearTimeout(play.timer); play.timer = null; }
    if (play.hls) { play.hls.destroy(); play.hls = null; }
    const video = $('tvVideo');
    video.onerror = null;
    video.onloadeddata = null;
  }

  function toast(text) {
    const t = $('tvToast');
    t.hidden = !text;
    t.textContent = text || '';
  }

  function playerError(text) {
    toast('');
    $('tvPlayerMsg').textContent = text;
    $('tvPlayerError').hidden = false;
  }

  function attempt(i) {
    stopAttempt();
    const my = play.token;
    play.index = i;
    $('tvPlayerError').hidden = true;
    toast(i > 0 ? 'Đang thử nguồn ' + (i + 1) + '/' + play.streams.length : '');
    const video = $('tvVideo');
    const url = play.streams[i].play_url;
    const fail = () => { if (my === play.token) next(); };
    play.timer = setTimeout(fail, STALL_MS);
    video.onerror = fail;
    video.onloadeddata = () => {
      if (my !== play.token) return;
      clearTimeout(play.timer);
      play.timer = null;
      toast('');
      if (!play.recentSent) {
        play.recentSent = true;
        const played = channelById(play.channelId);
        if (played) played.recent_at = Date.now() / 1000;
        api('POST', '/api/hometv/recent/' + play.channelId).catch(() => {});
      }
    };
    if (/\/index\.m3u8$/.test(url) && typeof root.Hls !== 'undefined' && root.Hls.isSupported()) {
      const hls = new root.Hls(root.HFPlayerUtils.hlsConfig(isTVDevice(), true));
      play.hls = hls;
      hls.loadSource(url);
      hls.attachMedia(video);
      hls.on(root.Hls.Events.MANIFEST_PARSED, () => { video.play().catch(() => {}); });
      hls.on(root.Hls.Events.ERROR, (event, data) => { if (data && data.fatal) fail(); });
    } else {
      video.src = url;
      video.play().catch(() => {});
    }
  }

  function next() {
    if (play.index + 1 < play.streams.length) attempt(play.index + 1);
    else { stopAttempt(); playerError('Kênh tạm thời không phát được.'); }
  }

  async function openChannel(id) {
    const c = channelById(id);
    stopAttempt();
    play.channelId = id;
    play.recentSent = false;
    play.streams = [];
    $('tvPlayerTitle').textContent = c ? c.name : '';
    $('tvPlayer').hidden = false;
    $('tvPlayer').focus({ preventScroll: true });  // focus rời lưới phía sau, Enter không mở lại thẻ cũ
    $('tvPlayerError').hidden = true;
    toast('');
    const my = play.token;
    try {
      const streams = await api('GET', '/api/hometv/channels/' + id + '/streams');
      if (my !== play.token) return;
      play.streams = streams;
      attempt(0);
    } catch (e) {
      if (my !== play.token) return;
      playerError(e.status === 404 ? 'Kênh không còn khả dụng.' : 'Không tải được kênh.');
      if (e.status === 404) load();
    }
  }

  function closePlayer() {
    stopAttempt();
    if (play.switchTimer) { clearTimeout(play.switchTimer); play.switchTimer = null; }
    const video = $('tvVideo');
    video.pause();
    video.removeAttribute('src');
    $('tvPlayer').hidden = true;
    const card = document.querySelector('#tvGrid .tv-card[data-id="' + play.channelId + '"]');
    if (card) card.focus({ preventScroll: true });
  }

  // Đổi kênh bằng phím Lên/Xuống: gom các lần bấm nhanh thành một lần mở
  function stepPlayer(dir) {
    const list = visible();
    const from = play.pendingId != null ? play.pendingId : play.channelId;
    const to = U.stepChannel(list, from, dir);
    if (to == null) return;
    play.pendingId = to;
    const c = channelById(to);
    $('tvPlayerTitle').textContent = c ? c.name : '';
    if (play.switchTimer) clearTimeout(play.switchTimer);
    play.switchTimer = setTimeout(() => {
      play.switchTimer = null;
      const id = play.pendingId;
      play.pendingId = null;
      openChannel(id);
    }, SWITCH_DEBOUNCE_MS);
  }

  // ---------- Remote TV ----------
  function gridRows() {
    const rows = [
      [...document.querySelectorAll('#tvRecentList .home-cont')],
      [...document.querySelectorAll('[data-tvfilter]')].filter((b) => !b.hidden),
      [$('filterBtn-group'), $('tvClearBtn')].filter((b) => b && b.style.display !== 'none'),
    ];
    let top = null;
    for (const card of document.querySelectorAll('#tvGrid .tv-card')) {
      if (top === null || Math.abs(card.offsetTop - top) > 4) { rows.push([]); top = card.offsetTop; }
      rows[rows.length - 1].push(card);
    }
    return rows.filter((r) => r.length);
  }

  function onKey(e) {
    if (!active() || e.altKey || e.ctrlKey || e.metaKey) return;
    if (!$('tvPlayer').hidden) {
      if (e.key === 'Enter' && !(e.target.tagName === 'BUTTON' || e.target.tagName === 'VIDEO')) {
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      if (e.key === 'Escape') { e.preventDefault(); closePlayer(); }
      else if (isTVDevice() && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
        e.preventDefault();
        stepPlayer(e.key === 'ArrowDown' ? 1 : -1);
      }
      return;
    }
    if (!isTVDevice()) return;
    const target = e.target;
    if (e.key === 'f' && target && target.classList && target.classList.contains('tv-card')) {
      const c = channelById(Number(target.dataset.id));
      if (c) { e.preventDefault(); toggleFavorite(c); }
      return;
    }
    if (!['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key)) return;
    const rows = gridRows();
    let row = -1;
    let col = -1;
    rows.forEach((cells, r) => { const c = cells.indexOf(target); if (c >= 0) { row = r; col = c; } });
    if (row < 0) return;
    const [r, c] = root.HFSettingsUtils.gridMove(rows.map((cells) => cells.length), row, col, e.key);
    const dest = rows[r] && rows[r][c];
    if (!dest || dest === target) return;
    e.preventDefault();
    dest.focus({ preventScroll: true });
    dest.scrollIntoView({ block: 'nearest' });
  }

  // Nhãn nút tab đổi theo mode: nhãn HomeTV nằm ở data-tv-label, nhãn gốc cất ở data-hf-label
  function relabel(on) {
    document.querySelectorAll('[data-tv-label]').forEach((el) => {
      if (on) { el.dataset.hfLabel = el.textContent; el.textContent = el.dataset.tvLabel; }
      else if (el.dataset.hfLabel != null) { el.textContent = el.dataset.hfLabel; }
    });
  }

  const TAB_MAP = { 'search-tab': 'tv-home-tab', 'saved-tab': 'tv-saved-tab', 'player-tab': 'tv-search-tab' };

  function active() {
    return document.body.classList.contains('mode-hometv');
  }

  function tab(tabId) {
    return TAB_MAP[tabId] || tabId;
  }

  function onTab(tabId) {
    if (!state.loaded) return;
    if (tabId === 'tv-home-tab') renderRecent();
    else if (tabId === 'tv-search-tab') renderSearch();
    else if (tabId === 'tv-saved-tab') renderLibrary();
  }

  function enter() {
    document.body.classList.add('mode-hometv');
    $('hometv-root').hidden = false;
    $('hometvToggle').setAttribute('aria-pressed', 'true');
    remember(true);
    relabel(true);
    root.switchTab('tv-home-tab');
    load();
  }

  // keepTab: người gọi tự lo tab sắp hiện (Cài đặt, phát phim); mặc định về tab Home của HomeFlix
  function leave(keepTab) {
    if (!$('tvPlayer').hidden) closePlayer();
    document.body.classList.remove('mode-hometv');
    $('hometv-root').hidden = true;
    $('hometvToggle').setAttribute('aria-pressed', 'false');
    remember(false);
    relabel(false);
    if (!keepTab) root.switchTab('search-tab');
  }

  function init() {
    if (!$('hometvToggle') || !U) return;
    $('hometvToggle').addEventListener('click', () => (active() ? leave() : enter()));
    document.querySelectorAll('[data-tvfilter]').forEach((b) => b.addEventListener('click', () => {
      state.alive = b.dataset.tvfilter === 'alive';
      refilter();
    }));
    $('tvSearch').addEventListener('input', (e) => { state.query = e.target.value; renderSearch(); });
    $('tvClearBtn').addEventListener('click', () => { state.alive = false; state.group = ''; root.closeAllFilterDropdowns(); refilter(); });
    if ('IntersectionObserver' in root) {
      observer = new IntersectionObserver((entries) => { if (entries.some((en) => en.isIntersecting)) loadMore(); },
        { rootMargin: '400px' });
      observer.observe($('tvMore'));
    }
    $('tvPlayerClose').addEventListener('click', closePlayer);
    $('tvPlayerRetry').addEventListener('click', () => { if (play.streams.length) attempt(0); else openChannel(play.channelId); });
    document.addEventListener('keydown', onKey, true);  // capture: chạy trước trình xử lý Enter chung của trang
    $('settingsBtn').addEventListener('click', () => { if (active()) leave(true); });
    if (remembered()) enter();
  }

  root.HFTV = Object.assign(root.HFTV || {}, { enter, leave, active, tab, onTab, channels: () => state.channels, openChannel, closePlayer });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})(typeof globalThis !== 'undefined' ? globalThis : this);
