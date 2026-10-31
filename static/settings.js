// Trang Cài đặt: dựng các nhóm từ dữ liệu, gọi API, tự lưu (không có nút Lưu).
// Cài đặt phát của máy vẫn nằm ở localStorage qua HFPlayerAuto; panel #playerPrefs và trang này cùng nghe 'hf-prefs'.
(function (root) {
  const U = root.HFSettingsUtils;
  const SAVE_DELAY_MS = 400; // bước số: lưu sau lần bấm cuối
  const INTRO_ITEM = { key: 'player.introSeconds', label: 'Mở đầu kết thúc', min: 10, max: 300, step: 5, unit: 'giây' };
  const SVG = 'http://www.w3.org/2000/svg';

  let returnTo = 'search-tab';
  let returnScroll = 0;
  let renderSeq = 0;

  const $ = (id) => document.getElementById(id);

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function icon(d, cls) {
    const svg = document.createElementNS(SVG, 'svg');
    svg.setAttribute('width', '18');
    svg.setAttribute('height', '18');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '2');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('aria-hidden', 'true');
    if (cls) svg.setAttribute('class', cls);
    const path = document.createElementNS(SVG, 'path');
    path.setAttribute('d', d);
    svg.appendChild(path);
    return svg;
  }

  const chevron = () => icon('M9 5l7 7-7 7', 'set-chevron');

  // Ghi luôn gửi JSON có thân (route /api/settings, /api/admin trả 415 nếu thiếu). Lỗi HTTP ném Error có .status
  async function api(method, path, body) {
    const opts = { method };
    if (method !== 'GET') {
      opts.headers = { 'Content-Type': 'application/json' };
      opts.body = JSON.stringify(body === undefined ? {} : body);
    }
    const res = await fetch(path, opts);
    let data = null;
    try { data = await res.json(); } catch (e) { /* thân rỗng */ }
    if (!res.ok) {
      const err = new Error('HTTP ' + res.status);
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  // --- Dòng và nhóm (Task 10 dùng lại) ---
  function renderGroup(id, title, rows) {
    const section = el('section', 'set-group');
    section.dataset.group = id;
    section.appendChild(el('h3', 'set-group-title', title));
    const list = el('div', 'set-list');
    rows.filter(Boolean).forEach((r) => list.appendChild(r));
    section.appendChild(list);
    return section;
  }

  function baseRow(key, label) {
    const row = el('div', 'set-row');
    row.dataset.key = key;
    if (label) row.appendChild(el('span', 'set-label', label));
    return row;
  }

  function clearError(row) {
    const old = row.querySelector('.set-error');
    if (old) old.remove();
  }

  function showError(row, text) {
    clearError(row);
    row.appendChild(el('div', 'set-error', text));
  }

  function setSaving(row, on) {
    row.classList.toggle('is-saving', on);
    if (on) row.setAttribute('aria-busy', 'true');
    else row.removeAttribute('aria-busy');
  }

  // Lưu lỗi: 403 nghĩa là vừa mất quyền admin, dựng lại cả trang; còn lại báo ngay dưới dòng
  function saveFailed(row, err) {
    if (err && err.status === 403) { render(); return; }
    showError(row, 'Không lưu được');
  }

  // Công tắc lưu ngay khi bấm; lỗi thì trả về như cũ. row.hfSet(v) cập nhật từ bên ngoài (vd. sự kiện hf-prefs)
  function switchRow(key, label, value, onChange) {
    const row = baseRow(key, label);
    const sw = el('button', 'set-switch');
    sw.type = 'button';
    sw.setAttribute('role', 'switch');
    sw.setAttribute('aria-label', label);
    let current = !!value;
    let busy = false;
    const paint = () => sw.setAttribute('aria-checked', current ? 'true' : 'false');
    paint();
    sw.addEventListener('click', async () => {
      if (busy) return;
      const prev = current;
      current = !current;
      paint();
      clearError(row);
      busy = true;
      setSaving(row, true);
      try {
        await onChange(current);
      } catch (err) {
        current = prev;
        paint();
        saveFailed(row, err);
      } finally {
        busy = false;
        setSaving(row, false);
      }
    });
    // chạm vào chữ của dòng cũng bật/tắt
    row.addEventListener('click', (e) => { if (!e.target.closest('button, input')) sw.click(); });
    row.hfSet = (v) => { if (!busy) { current = !!v; paint(); } };
    row.appendChild(sw);
    return row;
  }

  function stepButton(dir) {
    const b = el('button', 'set-step');
    b.type = 'button';
    b.dataset.dir = String(dir);
    b.setAttribute('aria-label', dir < 0 ? 'Giảm' : 'Tăng');
    b.appendChild(icon(dir < 0 ? 'M5 12h14' : 'M12 5v14M5 12h14'));
    return b;
  }

  // Bước số (−)(+): hiện giá trị mới ngay, lưu một lần sau SAVE_DELAY_MS kể từ lần bấm cuối;
  // lỗi thì trả về giá trị đã lưu và hiện .set-error
  function stepperRow(item, onSave) {
    const row = baseRow(item.key, item.label);
    const box = el('div', 'set-stepper');
    const minus = stepButton(-1);
    const plus = stepButton(1);
    const value = el('span', 'set-value');
    value.setAttribute('aria-live', 'polite');
    box.append(minus, value, plus);
    row.appendChild(box);

    let saved = item.value;
    let shown = item.value;
    let timer = null;
    let inFlight = false;

    function paint() {
      value.textContent = U.formatValue(Object.assign({}, item, { value: shown }));
      minus.setAttribute('aria-disabled', shown <= item.min ? 'true' : 'false');
      plus.setAttribute('aria-disabled', shown >= item.max ? 'true' : 'false');
    }

    async function save() {
      timer = null;
      if (shown === saved) return;
      const next = shown;
      inFlight = true;
      setSaving(row, true);
      try {
        await onSave(next);
        saved = next;
      } catch (err) {
        clearTimeout(timer);
        timer = null;
        shown = saved;
        paint();
        saveFailed(row, err);
      } finally {
        inFlight = false;
        setSaving(row, false);
      }
    }

    function step(dir) {
      clearError(row);
      shown = U.stepValue(item, shown, dir);
      paint();
      clearTimeout(timer);
      timer = setTimeout(save, SAVE_DELAY_MS);
    }

    minus.addEventListener('click', () => step(-1));
    plus.addEventListener('click', () => step(1));
    row.hfSet = (v) => { if (timer === null && !inFlight) { saved = shown = v; paint(); } };
    paint();
    return row;
  }

  // Dòng thao tác: cả dòng là một nút (Task 10 gắn hành vi)
  function actionRow(key, label, onClick, danger) {
    const row = baseRow(key);
    row.classList.add('set-row-action');
    const btn = el('button', 'set-action' + (danger ? ' set-danger' : ''));
    btn.type = 'button';
    btn.appendChild(el('span', 'set-label', label));
    if (!danger) btn.appendChild(chevron());
    btn.addEventListener('click', () => { if (onClick) onClick(row, btn); });
    row.appendChild(btn);
    return row;
  }

  // Tên máy: chạm để sửa ngay trong dòng; Enter hoặc rời ô thì lưu, Esc thì thôi
  function nameRow(name) {
    const row = baseRow('device.name', 'Tên');
    const btn = el('button', 'set-text');
    btn.type = 'button';
    const text = el('span', 'set-text-value');
    btn.append(text, chevron());
    row.appendChild(btn);
    let current = name || '';
    const paint = () => { text.textContent = current || '—'; };
    paint();

    async function commit(next) {
      if (next === current) return;
      if (next.length < 1 || next.length > 40) { showError(row, 'Tên cần 1–40 ký tự'); return; }
      const prev = current;
      current = next;
      paint();
      setSaving(row, true);
      try {
        const d = await api('PATCH', '/api/device', { name: next });
        if (d && typeof d.name === 'string') { current = d.name; paint(); }
      } catch (err) {
        current = prev;
        paint();
        saveFailed(row, err);
      } finally {
        setSaving(row, false);
      }
    }

    btn.addEventListener('click', () => {
      clearError(row);
      const input = el('input', 'set-input');
      input.type = 'text';
      input.maxLength = 40;
      input.value = current;
      input.autocomplete = 'off';
      input.enterKeyHint = 'done';
      input.setAttribute('aria-label', 'Tên thiết bị');
      btn.replaceWith(input);
      input.focus();
      input.select();
      let done = false;
      const finish = (save, refocus) => {
        if (done) return;
        done = true;
        const next = input.value.trim();
        input.replaceWith(btn);
        if (refocus) btn.focus();
        if (save) commit(next);
      };
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); finish(true, true); }
        else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); finish(false, true); }
      });
      input.addEventListener('blur', () => finish(true, false));
    });
    return row;
  }

  // --- Các nhóm ---
  function deviceGroup(device, cast) {
    const P = root.HFPlayerAuto;
    const prefs = P.getPrefs();
    return renderGroup('device', 'Thiết bị này', [
      nameRow(device ? device.name : ''),
      switchRow('cast.allow_control', 'Cho phép điều khiển từ xa', cast ? cast.allow_control : true,
        (v) => api('PATCH', '/api/cast/settings', { allow_control: v })),
      switchRow('player.autoNext', 'Tự qua tập', prefs.autoNext, (v) => { P.setPrefs({ autoNext: v }); }),
      switchRow('player.autoIntro', 'Tự bỏ qua mở đầu', prefs.autoIntro, (v) => { P.setPrefs({ autoIntro: v }); }),
      stepperRow(Object.assign({ value: prefs.introSeconds }, INTRO_ITEM), (v) => { P.setPrefs({ introSeconds: v }); }),
    ]);
  }

  // Khung nhóm Quản trị; Task 10 gắn đăng nhập và xin duyệt
  function adminGroup() {
    return renderGroup('admin', 'Quản trị', [
      actionRow('admin.login', 'Đăng nhập admin'),
      actionRow('admin.request', 'Xin duyệt từ máy admin'),
    ]);
  }

  async function render() {
    const seq = ++renderSeq;
    const [device, cast, conf] = await Promise.all(
      [api('GET', '/api/device'), api('GET', '/api/cast/settings'), api('GET', '/api/settings')]
        .map((p) => p.catch(() => null)));   // /api/settings lỗi: vẫn có nhóm "Thiết bị này"
    if (seq !== renderSeq) return;   // đã có lượt dựng mới hơn
    const groups = [deviceGroup(device, cast)];
    if (conf && conf.admin_enabled) groups.push(adminGroup());
    const body = $('settingsBody');
    const hadFocus = body.contains(document.activeElement);
    body.replaceChildren(...groups);
    // TV: đặt focus vào dòng đầu để remote dùng được ngay
    if (!hadFocus && document.body.classList.contains('device-tv') && $('settings-tab').classList.contains('active')) {
      const first = body.querySelector('.set-row button');
      if (first && (document.activeElement === document.body || document.activeElement === $('settingsBtn'))) first.focus();
    }
  }

  // Gọi từ switchTab('settings-tab'); `from` là tab đang mở trước đó
  function open(from) {
    if (from && from !== 'settings-tab') {
      returnTo = from;
      returnScroll = root.scrollY || 0;
    }
    root.scrollTo(0, 0);
    return render();
  }

  function back() {
    const y = returnScroll;
    root.switchTab(returnTo);
    root.scrollTo(0, y);
  }

  function update(key, v) {
    const row = document.querySelector('#settingsBody .set-row[data-key="' + key + '"]');
    if (row && row.hfSet) row.hfSet(v);
  }

  // Panel #playerPrefs hoặc lệnh cast đổi cài đặt phát: trang Cài đặt hiện đúng
  document.addEventListener('hf-prefs', (e) => {
    const p = e.detail || root.HFPlayerAuto.getPrefs();
    update('player.autoNext', p.autoNext);
    update('player.autoIntro', p.autoIntro);
    update('player.introSeconds', p.introSeconds);
  });

  const backBtn = $('settingsBack');
  if (backBtn) backBtn.addEventListener('click', back);

  root.HFSettings = { open };
})(typeof globalThis !== 'undefined' ? globalThis : this);
