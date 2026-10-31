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
    c.favorite = want;
    renderChips();
    renderGrid();
    try {
      await api(want ? 'PUT' : 'DELETE', '/api/hometv/favorites/' + c.id);
    } catch (e) {
      c.favorite = !want;
      renderChips();
      renderGrid();
    }
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
    if (remembered()) enter();
  }

  root.HFTV = Object.assign(root.HFTV || {}, { enter, leave, channels: () => state.channels });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})(typeof globalThis !== 'undefined' ? globalThis : this);
