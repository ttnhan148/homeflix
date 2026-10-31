// HomeTV: mode xem kênh truyền hình. Logic thuần ở hometv-utils.js; tại đây là DOM, mạng và player.
(function (root) {
  const U = root.HFHomeTV;
  const MODE_KEY = 'hf_hometv_mode';
  const BATCH = 60;
  const $ = (id) => document.getElementById(id);

  const state = { channels: [], chip: 'all', query: '', shown: 0, loaded: false };
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

  function visible() {
    return U.filterChannels(state.channels, { chip: state.chip, query: state.query });
  }

  function categoryLabel(c) {
    return U.CATEGORY_LABELS[U.CATEGORY_ORDER.includes(c.category) ? c.category : 'Unknown'];
  }

  function buildCard(c) {
    const card = document.createElement('div');
    card.className = 'movie-card tv-card';
    card.dataset.id = String(c.id);
    card.tabIndex = 0;
    card.setAttribute('role', 'button');

    const wrap = document.createElement('div');
    wrap.className = 'poster-wrap';
    if (c.logo) {
      const img = document.createElement('img');
      img.className = 'movie-poster';
      img.loading = 'lazy';
      img.alt = '';
      img.src = c.logo;
      img.addEventListener('error', () => { img.replaceWith(initialEl(c)); });
      wrap.appendChild(img);
    } else {
      wrap.appendChild(initialEl(c));
    }
    const star = document.createElement('button');
    star.type = 'button';
    star.className = 'tv-star';
    star.textContent = '★';
    star.setAttribute('aria-pressed', String(c.favorite));
    star.setAttribute('aria-label', 'Yêu thích');
    star.addEventListener('click', (e) => { e.stopPropagation(); toggleFavorite(c); });
    wrap.appendChild(star);

    const info = document.createElement('div');
    info.className = 'movie-info';
    const title = document.createElement('div');
    title.className = 'movie-title';
    title.textContent = c.name;
    const meta = document.createElement('div');
    meta.className = 'movie-meta';
    meta.textContent = categoryLabel(c);
    info.append(title, meta);
    card.append(wrap, info);
    card.addEventListener('click', () => open(c.id));
    card.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && e.target === card) { e.preventDefault(); open(c.id); }
    });
    return card;
  }

  function initialEl(c) {
    const span = document.createElement('span');
    span.className = 'tv-initial';
    span.textContent = U.initial(c.name);
    return span;
  }

  function open(id) {
    if (root.HFTV && typeof root.HFTV.openChannel === 'function') root.HFTV.openChannel(id);
  }

  function renderChips() {
    const bar = $('tvChips');
    const chips = [{ key: 'all', label: 'Tất cả' }, ...U.buildChips(state.channels)];
    if (!chips.some((c) => c.key === state.chip)) state.chip = 'all';
    bar.replaceChildren(...chips.map((c) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'filter-option' + (c.key === state.chip ? ' active' : '');
      b.dataset.chip = c.key;
      b.textContent = c.label;
      b.addEventListener('click', () => { state.chip = c.key; renderChips(); renderGrid(); });
      return b;
    }));
  }

  function renderGrid() {
    const list = visible();
    const grid = $('tvGrid');
    grid.replaceChildren();
    state.shown = 0;
    $('tvEmpty').hidden = list.length > 0 || !state.loaded || state.channels.length === 0;
    if (!$('tvEmpty').hidden) $('tvEmpty').textContent = 'Không có kênh nào khớp.';
    appendBatch(list);
  }

  function appendBatch(list) {
    const grid = $('tvGrid');
    const next = list.slice(state.shown, state.shown + BATCH);
    grid.append(...next.map(buildCard));
    state.shown += next.length;
    $('tvMore').hidden = state.shown >= list.length;
  }

  function loadMore() {
    const more = $('tvMore');
    if (!more.hidden) appendBatch(visible());
  }

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
    $('tvChips').replaceChildren();
    $('tvMore').hidden = true;
    try {
      state.channels = await api('GET', '/api/hometv/channels');
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
    renderChips();
    renderGrid();
  }

  async function toggleFavorite(c) {
    const want = !c.favorite;
    const redraw = () => {
      // Vẽ lại lưới làm mất focus: trả focus về thẻ vừa thao tác (nếu còn trong danh sách)
      const hadFocus = !!document.activeElement && !!document.activeElement.closest('#tvGrid');
      renderChips();
      renderGrid();
      const card = hadFocus && document.querySelector('#tvGrid .tv-card[data-id="' + c.id + '"]');
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
    const rows = [[$('tvSearch')], [...document.querySelectorAll('#tvChips [data-chip]')]];
    let top = null;
    for (const card of document.querySelectorAll('#tvGrid .tv-card')) {
      if (top === null || Math.abs(card.offsetTop - top) > 4) { rows.push([]); top = card.offsetTop; }
      rows[rows.length - 1].push(card);
    }
    return rows.filter((r) => r.length);
  }

  function onKey(e) {
    if (!document.body.classList.contains('mode-hometv') || e.altKey || e.ctrlKey || e.metaKey) return;
    if (!$('tvPlayer').hidden) {
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
    if (target === $('tvSearch') && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) return;
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

  function enter() {
    document.body.classList.add('mode-hometv');
    $('hometv-root').hidden = false;
    $('hometvToggle').setAttribute('aria-pressed', 'true');
    remember(true);
    load();
  }

  function leave() {
    document.body.classList.remove('mode-hometv');
    $('hometv-root').hidden = true;
    $('hometvToggle').setAttribute('aria-pressed', 'false');
    remember(false);
  }

  function init() {
    if (!$('hometvToggle') || !U) return;
    $('hometvToggle').addEventListener('click', () => (document.body.classList.contains('mode-hometv') ? leave() : enter()));
    $('tvSearch').addEventListener('input', (e) => { state.query = e.target.value; renderGrid(); });
    if ('IntersectionObserver' in root) {
      observer = new IntersectionObserver((entries) => { if (entries.some((en) => en.isIntersecting)) loadMore(); },
        { rootMargin: '400px' });
      observer.observe($('tvMore'));
    }
    $('tvPlayerClose').addEventListener('click', closePlayer);
    $('tvPlayerRetry').addEventListener('click', () => { if (play.streams.length) attempt(0); else openChannel(play.channelId); });
    document.addEventListener('keydown', onKey);
    if (remembered()) enter();
  }

  root.HFTV = Object.assign(root.HFTV || {}, { enter, leave, channels: () => state.channels, openChannel, closePlayer });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})(typeof globalThis !== 'undefined' ? globalThis : this);
