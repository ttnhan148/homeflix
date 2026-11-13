// Settings page: builds groups from data, calls the API, auto-saves (no Save button).
// This device's playback settings still live in localStorage via HFPlayerAuto; the #playerPrefs panel and this page both listen for 'hf-prefs'.
(function (root) {
  const U = root.HFSettingsUtils;
  const SAVE_DELAY_MS = 400; // number stepper: save after the last press
  const INTRO_ITEM = { key: 'player.introSeconds', label: 'Mở đầu kết thúc', min: 10, max: 300, step: 5, unit: 'giây' };
  const SVG = 'http://www.w3.org/2000/svg';
  const REQUESTS_POLL_MS = 5000; // admin device: reload pending requests
  const MINE_POLL_MS = 3000;     // device awaiting approval: poll for the result
  const TICK_MS = 1000;          // countdown
  const NAME_ERROR = 'Tên cần 1–40 ký tự';
  const KIND_LABELS = { tv: 'TV', phone: 'Điện thoại', desktop: 'Máy tính' };
  const FOCUSABLE = 'button, input, [tabindex]';

  let returnTo = 'search-tab';
  let returnScroll = 0;
  let renderSeq = 0;
  let isOpen = false;      // page is open: only then rebuild and run timers
  let clockSkew = 0;       // seconds: server time minus device time (TVs often have clock drift)
  let openDevice = null;   // public id of the device whose actions are open, kept across reloads
  const timers = new Set();

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

  // Writes always send a JSON body (/api/settings, /api/admin routes return 415 without one). HTTP errors throw an Error with .status
  async function api(method, path, body) {
    const opts = { method };
    if (method !== 'GET') {
      opts.headers = { 'Content-Type': 'application/json' };
      opts.body = JSON.stringify(body === undefined ? {} : body);
    }
    const res = await fetch(path, opts);
    const date = Date.parse(res.headers.get('Date') || '');
    if (!isNaN(date)) {
      const skew = (date - Date.now()) / 1000;
      clockSkew = Math.abs(skew) > 2 ? skew : 0;   // Date is only accurate to the second: ignore small drift
    }
    let data = null;
    try { data = await res.json(); } catch (e) { /* empty body */ }
    if (!res.ok) {
      const err = new Error('HTTP ' + res.status);
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  const soft = (p) => p.catch(() => null);
  const serverNow = () => Date.now() / 1000 + clockSkew;

  // Page timers: close() and every rebuild stop them all
  function every(ms, fn) {
    const id = setInterval(fn, ms);
    timers.add(id);
    return id;
  }

  function stopTimer(id) {
    clearInterval(id);
    timers.delete(id);
  }

  function stopTimers() {
    timers.forEach((id) => clearInterval(id));
    timers.clear();
  }

  // --- Rows and groups (reused by Task 10) ---
  function groupShell(id, title) {
    const section = el('section', 'set-group');
    section.dataset.group = id;
    section.appendChild(el('h3', 'set-group-title', title));
    return section;
  }

  function listOf(rows) {
    const list = el('div', 'set-list');
    rows.filter(Boolean).forEach((r) => list.appendChild(r));
    return list;
  }

  function renderGroup(id, title, rows) {
    const section = groupShell(id, title);
    section.appendChild(listOf(rows));
    return section;
  }

  function baseRow(key, label) {
    const row = el('div', 'set-row');
    row.dataset.key = key;
    if (label) row.appendChild(el('span', 'set-label', label));
    return row;
  }

  // The row's own error and subtext, not touching child rows (the IPTV source Edit block lives inside the source row)
  function clearError(row) {
    const old = row.querySelector(':scope > .set-error');
    if (old) old.remove();
  }

  function showError(row, text) {
    clearError(row);
    row.appendChild(el('div', 'set-error', text));
  }

  // Subtext line under the row (--muted color), e.g. "Đã xoá cache"
  function clearNote(row) {
    const old = row.querySelector(':scope > .set-note');
    if (old) old.remove();
  }

  function showNote(row, text) {
    clearNote(row);
    row.appendChild(el('div', 'set-note', text));
  }

  function setSaving(row, on) {
    row.classList.toggle('is-saving', on);
    if (on) row.setAttribute('aria-busy', 'true');
    else row.removeAttribute('aria-busy');
  }

  // Save error: 403 means admin rights were just lost, rebuild the whole page; otherwise show it right under the row.
  // `text` is a string or a function (err) => string
  function saveFailed(row, err, text) {
    if (err && err.status === 403) { render(); return; }
    const msg = typeof text === 'function' ? text(err) : text;
    showError(row, msg || 'Không lưu được');
  }

  // A write action on a row: skipped while running, shows saving state, errors shown under the row
  async function act(row, work, failText) {
    if (row.classList.contains('is-saving')) return;
    clearError(row);
    clearNote(row);
    setSaving(row, true);
    try {
      await work();
    } catch (err) {
      saveFailed(row, err, failText);
    } finally {
      setSaving(row, false);
    }
  }

  // Toggle saves immediately on press; reverts on error. row.hfSet(v) updates it externally (e.g. hf-prefs event)
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
    // tapping the row's text also toggles it (except error rows)
    row.addEventListener('click', (e) => { if (!e.target.closest('button, input, .set-error')) sw.click(); });
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

  // Number stepper (−)(+): shows the new value immediately, saves once SAVE_DELAY_MS after the last press;
  // on error reverts to the saved value and shows .set-error
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

  // Action row: the whole row is a button (behavior attached by Task 10)
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

  const validName = (s) => s.length >= 1 && s.length <= 40;

  // Inline text edit: `anchor` gives way to an input. Enter or blur calls onDone(text with leading/
  // trailing whitespace trimmed), Esc cancels. `focusBack()` picks where focus returns (defaults to anchor)
  function editInline(anchor, value, label, onDone, focusBack, maxLength = 40) {
    const input = el('input', 'set-input');
    input.type = 'text';
    input.maxLength = maxLength;
    input.value = value;
    input.autocomplete = 'off';
    input.enterKeyHint = 'done';
    input.setAttribute('aria-label', label);
    anchor.replaceWith(input);
    input.focus();
    input.select();
    let done = false;
    const finish = (save, refocus) => {
      if (done) return;
      done = true;
      const next = input.value.trim();
      input.replaceWith(anchor);
      if (refocus) {
        const target = focusBack ? focusBack() : anchor;
        if (target) target.focus();
      }
      if (save) onDone(next);
    };
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        if (e.isComposing || e.keyCode === 229) return;   // Enter confirming an IME word, not "save" (Safari: keyCode 229)
        finish(true, true);
      }
      else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); finish(false, true); }
    });
    input.addEventListener('blur', () => finish(true, false));
  }

  // Device name: tap to edit inline; Enter or blur saves, Esc cancels
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
      if (!validName(next)) { showError(row, NAME_ERROR); return; }
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
      editInline(btn, current, 'Tên thiết bị', commit);
    });
    return row;
  }

  // --- Groups ---
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

  // --- Admin: device is not admin yet ---
  function loginErrorText(err) {
    if (err && err.status === 401) return 'Sai mật khẩu';
    if (err && err.status === 429) {
      const s = err.data && Number(err.data.retry_after);
      return s > 0 ? 'Thử lại sau ' + Math.ceil(s / 60) + ' phút' : 'Thử lại sau';
    }
    return 'Không đăng nhập được';
  }

  // "Đăng nhập admin": tap opens an inline password field, Enter submits, Esc closes
  function loginRow() {
    const row = actionRow('admin.login', 'Đăng nhập admin', () => openForm());
    const btn = row.querySelector('.set-action');
    let busy = false;

    function openForm() {
      clearError(row);
      const form = el('form', 'set-form');
      const input = el('input', 'set-input');
      input.id = 'adminPassword';
      input.type = 'password';
      input.autocomplete = 'current-password';
      input.enterKeyHint = 'go';
      input.placeholder = 'Mật khẩu';
      input.setAttribute('aria-label', 'Mật khẩu admin');
      const go = el('button', 'set-pill is-primary', 'Vào');
      go.type = 'submit';
      form.append(input, go);
      btn.replaceWith(form);
      input.focus();

      let closed = false;
      const closeForm = (refocus) => {
        if (closed) return;
        closed = true;   // removing the focused input fires focusout midway: don't close twice
        form.replaceWith(btn);
        if (refocus) btn.focus();
      };
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        if (busy || !input.value) return;   // TV: a stray OK on an empty field must not use up one of the 5 lockout attempts
        busy = true;
        clearError(row);
        setSaving(row, true);
        try {
          await api('POST', '/api/admin/login', { password: input.value });
          render();   // this device is now admin
        } catch (err) {
          if (err && err.status === 404) { render(); return; }   // admin was just disabled
          showError(row, loginErrorText(err));
          input.select();
        } finally {
          busy = false;
          setSaving(row, false);
        }
      });
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeForm(true); }
      });
      // blur before typing anything: collapse back into the button
      form.addEventListener('focusout', (e) => {
        if (!busy && !input.value && !form.contains(e.relatedTarget)) closeForm(false);
      });
    }
    return row;
  }

  // "Xin duyệt từ máy admin": shows a 6-digit code and countdown, polls the result every MINE_POLL_MS.
  // The button stays in place while waiting so focus on TV isn't lost
  function requestRow(mine) {
    const row = actionRow('admin.request', 'Xin duyệt từ máy admin', () => { if (!waiting) ask(); });
    const btn = row.querySelector('.set-action');
    let waiting = false;
    let poll = null;
    let busy = false;

    function idle(message) {
      waiting = false;
      if (poll) stopTimer(poll);
      poll = null;
      btn.replaceChildren(el('span', 'set-label', 'Xin duyệt từ máy admin'), chevron());
      clearNote(row);
      if (message) showError(row, message);
      else clearError(row);
    }

    function wait(code, expiresAt) {
      clearError(row);
      const shown = row.querySelector('#adminCode');
      if (!shown || shown.textContent !== code) {
        const codeEl = el('span', 'set-code', code);
        codeEl.id = 'adminCode';
        btn.replaceChildren(el('span', 'set-label', 'Mã duyệt'), codeEl, el('span', 'set-countdown'));
        showNote(row, 'Trên máy admin, mở Cài đặt và bấm Cho phép');
      }
      const left = btn.querySelector('.set-countdown');
      left.dataset.expires = String(expiresAt);
      left.textContent = U.countdownText(expiresAt, serverNow());
      waiting = true;
      if (!poll && isOpen) poll = every(MINE_POLL_MS, check);
    }

    async function ask() {
      if (busy) return;
      busy = true;
      clearError(row);
      setSaving(row, true);
      try {
        const r = await api('POST', '/api/admin/requests', {});
        if (row.isConnected) wait(r.code, r.expires_at);
      } catch (err) {
        if (err && err.status === 404) render();
        else showError(row, err && err.status === 429 ? 'Đang có quá nhiều yêu cầu, thử lại sau' : 'Không gửi được');
      } finally {
        busy = false;
        setSaving(row, false);
      }
    }

    // Only trust is_admin from GET /api/settings: "approved" rebuilds the page, doesn't assume admin
    async function check() {
      if (busy) return;
      busy = true;
      try {
        const r = await api('GET', '/api/admin/requests/mine');
        if (!waiting || !row.isConnected) return;
        if (r.status === 'pending') wait(r.code, r.expires_at);
        else if (r.status === 'approved') { idle(); render(); }
        else if (r.status === 'denied') idle('Bị từ chối');
        else if (r.status === 'expired') idle('Hết hạn, thử lại');
        else idle();
      } catch (err) {
        if (err && err.status === 404) render();   // admin was just disabled; on network error retry next round
      } finally {
        busy = false;
      }
    }

    if (mine && mine.status === 'pending') wait(mine.code, mine.expires_at);   // page reopened while waiting
    return row;
  }

  function guestAdminGroup(mine) {
    return renderGroup('admin', 'Quản trị', [loginRow(), requestRow(mine)]);
  }

  // --- Admin: device is already admin ---
  // "Đổi mật khẩu admin": inline form (current, new, again); errors show under the right field
  function passwordRow() {
    const row = actionRow('admin.password', 'Đổi mật khẩu admin', () => openForm());
    const btn = row.querySelector('.set-action');
    let busy = false;

    function field(id, label, autocomplete) {
      const box = el('div', 'hometv-field');
      const input = el('input', 'set-input');
      input.id = id;
      input.type = 'password';
      input.autocomplete = autocomplete;
      input.placeholder = label;
      input.maxLength = 128;
      input.setAttribute('aria-label', label);
      box.appendChild(input);
      return { box, input };
    }

    function openForm() {
      clearError(row);
      clearNote(row);
      const form = el('form', 'set-form hometv-form');
      form.noValidate = true;
      const current = field('adminPwCurrent', 'Mật khẩu hiện tại', 'current-password');
      const next = field('adminPwNew', 'Mật khẩu mới (từ 8 ký tự)', 'new-password');
      const again = field('adminPwAgain', 'Nhập lại mật khẩu mới', 'new-password');
      again.input.enterKeyHint = 'go';
      const save = el('button', 'set-pill is-primary', 'Đổi');
      save.type = 'submit';
      form.append(current.box, next.box, again.box, save);
      btn.replaceWith(form);
      current.input.focus();
      const fields = { current, new: next, again };

      const fieldError = (key, text) => {
        fields[key].box.appendChild(el('div', 'set-error', text));
        fields[key].input.focus();
      };
      let closed = false;
      const closeForm = (refocus) => {
        if (closed) return;
        closed = true;   // removing the focused input fires focusout midway: don't close twice
        form.replaceWith(btn);
        if (refocus) btn.focus();
      };

      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        if (busy) return;
        clearError(row);
        form.querySelectorAll('.set-error').forEach((n) => n.remove());
        const empty = Object.keys(fields).find((k) => !fields[k].input.value);
        if (empty) { fields[empty].input.focus(); return; }   // Enter in an early field: go fill the rest first
        if (next.input.value !== again.input.value) { fieldError('again', 'Mật khẩu nhập lại không khớp'); return; }
        busy = true;
        setSaving(row, true);
        try {
          await api('POST', '/api/admin/password', { current: current.input.value, new: next.input.value });
          closeForm(true);
          showNote(row, 'Đã đổi mật khẩu');
        } catch (err) {
          if (err && (err.status === 403 || err.status === 404 || err.status === 409)) { render(); return; }
          const errors = (err && err.data && err.data.errors) || {};
          if (typeof errors.current === 'string') fieldError('current', errors.current);
          else if (typeof errors.new === 'string') fieldError('new', errors.new);
          else showError(row, err && err.status === 429 ? loginErrorText(err) : 'Không đổi được mật khẩu');
        } finally {
          busy = false;
          setSaving(row, false);
        }
      });
      form.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeForm(true); }
      });
      // leaving the form before typing anything: collapse back into the button
      form.addEventListener('focusout', (e) => {
        const typed = Object.values(fields).some((f) => f.input.value);
        if (!busy && !typed && !form.contains(e.relatedTarget)) closeForm(false);
      });
    }
    return row;
  }

  function adminGroup(conf) {
    return renderGroup('admin', 'Quản trị', [
      ...(conf && conf.password_env ? [] : [passwordRow()]),   // env var sets the password: nothing to change here
      actionRow('admin.logout', 'Đăng xuất admin', (row) => act(row, async () => {
        await api('POST', '/api/admin/logout', {});
        render();
      }, 'Không đăng xuất được')),
      actionRow('admin.revoke_others', 'Thu quyền mọi máy khác', (row) => {
        if (!root.confirm('Thu quyền admin của mọi máy khác?')) return;
        act(row, async () => {
          const r = await api('POST', '/api/admin/revoke-others', {});
          showNote(row, 'Đã thu quyền ' + ((r && r.revoked) || 0) + ' máy');
          await refreshDevices();
        }, 'Không thu quyền được');
      }, true),
    ]);
  }

  // --- Devices in the home ---
  function pill(text, cls) {
    const b = el('button', 'set-pill' + (cls ? ' ' + cls : ''), text);
    b.type = 'button';
    return b;
  }

  function countdown(expiresAt) {
    const span = el('span', 'set-countdown', U.countdownText(expiresAt, serverNow()));
    span.dataset.expires = String(expiresAt);
    return span;
  }

  // Pending request: device name, code and time remaining, Deny and Allow buttons
  function requestItem(it) {
    const row = el('div', 'set-row set-request');
    row.dataset.id = it.id;
    const main = el('div', 'set-main');
    main.appendChild(el('span', 'set-device-name', (it.device && it.device.name) || 'Thiết bị'));
    const meta = el('span', 'set-device-meta', 'Mã ' + it.code + ' · ');
    meta.appendChild(countdown(it.expires_at));
    main.appendChild(meta);
    const deny = pill('Từ chối', 'set-deny');
    const approve = pill('Cho phép', 'is-primary set-approve');
    deny.addEventListener('click', () => decide(row, it.id, false));
    approve.addEventListener('click', () => decide(row, it.id, true));
    row.append(main, deny, approve);
    return row;
  }

  function decide(row, id, approve) {
    return act(row, async () => {
      try {
        await api('POST', '/api/admin/requests/' + encodeURIComponent(id) + (approve ? '/approve' : '/deny'), {});
      } catch (err) {
        if (!err || err.status !== 409) throw err;   // 409: already expired or handled by another admin device
      }
      await Promise.all([refreshRequests(), approve ? refreshDevices() : null]);
    }, 'Không gửi được');
  }

  // Update the request list by id: existing rows stay (focus kept), new rows are appended
  function fillRequests(list, items) {
    const ids = items.map((it) => it.id);
    const old = new Map([...list.children].map((r) => [r.dataset.id, r]));
    let lostFocus = false;
    old.forEach((r, id) => {
      if (ids.includes(id)) return;
      if (r.contains(document.activeElement)) lostFocus = true;
      r.remove();
    });
    items.forEach((it, i) => {
      const r = old.get(it.id) || requestItem(it);
      if (list.children[i] !== r) list.insertBefore(r, list.children[i] || null);
    });
    list.hidden = items.length === 0;
    if (lostFocus) {
      const next = $('settingsBody').querySelector('.set-request button, .set-device .set-action');
      if (next) next.focus({ preventScroll: true });
    }
  }

  async function refreshRequests() {
    const list = document.querySelector('#settingsBody .set-requests');
    if (!list) return;
    try {
      const items = await api('GET', '/api/admin/requests');
      if (list.isConnected && Array.isArray(items)) fillRequests(list, items);
    } catch (err) {
      if (err && (err.status === 403 || err.status === 404)) render();   // rights just lost or admin disabled
    }
  }

  let polling = false;
  async function pollRequests() {
    if (polling) return;
    polling = true;
    try { await refreshRequests(); } finally { polling = false; }
  }

  const devicePath = (id) => '/api/admin/devices/' + encodeURIComponent(id);

  function deviceMeta(d) {
    const parts = [KIND_LABELS[d.kind] || 'Thiết bị'];
    if (d.is_self) parts.push('máy này');
    else if (d.last_seen) parts.push(U.lastSeenText(d.last_seen, serverNow()));   // never seen: show nothing
    return parts.join(' · ');
  }

  // A device: tap to open actions (rename, grant/revoke admin, delete). Name is user-set: textContent only
  function deviceRow(d) {
    const row = el('div', 'set-row set-row-action set-device');
    row.dataset.id = d.id;
    const head = el('button', 'set-action');
    head.type = 'button';
    const main = el('span', 'set-main');
    const line = el('span', 'set-name-line');
    line.appendChild(el('span', 'set-device-name', d.name));
    if (d.is_admin) line.appendChild(el('span', 'set-tag', 'admin'));
    main.append(line, el('span', 'set-device-meta', deviceMeta(d)));
    head.append(main, chevron());
    row.appendChild(head);

    const expand = (on) => {
      const panel = row.querySelector('.set-device-actions');
      if (panel) panel.remove();
      head.setAttribute('aria-expanded', on ? 'true' : 'false');
      if (on) row.appendChild(deviceActions(row, d));
    };
    row.hfExpand = expand;
    head.addEventListener('click', () => {
      const on = openDevice !== d.id;
      document.querySelectorAll('#settingsBody .set-device').forEach((r) => { if (r !== row && r.hfExpand) r.hfExpand(false); });
      openDevice = on ? d.id : null;
      expand(on);
    });
    expand(openDevice === d.id);
    return row;
  }

  // Actions for a device: vertical list, one action per row (fits any width)
  function subAction(act, text, danger) {
    const b = el('button', 'set-subaction' + (danger ? ' set-danger' : ''), text);
    b.type = 'button';
    b.dataset.act = act;
    return b;
  }

  function deviceActions(row, d) {
    const box = el('div', 'set-device-actions');
    const items = el('div', 'set-subactions');
    const rename = subAction('rename', 'Đổi tên');
    rename.addEventListener('click', () => {
      clearError(row);
      editInline(items, d.name, 'Tên mới', (next) => renameDevice(row, d, next),
        () => items.querySelector('[data-act="rename"]'));
    });
    items.appendChild(rename);
    if (!d.is_self) {   // own device: log out is in the Admin group, can't delete itself
      const admin = subAction('admin', d.is_admin ? 'Thu quyền admin' : 'Cấp quyền admin', d.is_admin);
      admin.addEventListener('click', () => act(row, async () => {
        await api('PATCH', devicePath(d.id), { is_admin: !d.is_admin });
        await refreshDevices();
      }, (err) => (err && err.status === 409 ? 'Cần giữ ít nhất một máy admin' : 'Không lưu được')));
      const del = subAction('delete', 'Xoá thiết bị', true);
      del.addEventListener('click', () => {
        if (!root.confirm('Xoá ' + d.name + '? Tủ phim và tiến độ xem của máy này sẽ mất.')) return;
        act(row, async () => {
          try {
            await api('DELETE', devicePath(d.id), {});
          } catch (err) {
            if (!err || err.status !== 404) throw err;   // already deleted on another device
          }
          if (openDevice === d.id) openDevice = null;
          await refreshDevices();
        }, 'Không xoá được');
      });
      items.append(admin, del);
    }
    box.appendChild(items);
    return box;
  }

  function renameDevice(row, d, next) {
    if (next === d.name) return;
    if (!validName(next)) { showError(row, NAME_ERROR); return; }
    act(row, async () => {
      await api('PATCH', devicePath(d.id), { name: next });
      if (d.is_self) render();   // the name under "Thiết bị này" changes too
      else await refreshDevices();
    }, (err) => (err && err.status === 422 ? NAME_ERROR : 'Không lưu được'));
  }

  // Group by home (own home first, own device at top); only label "Nhà này"/"Nhà khác" when there's more than one home
  function fillDevices(box, devices) {
    if (!Array.isArray(devices)) {
      const row = baseRow('devices.error', 'Không tải được danh sách');
      row.tabIndex = 0;   // read-only rows must still be reachable by remote control
      box.replaceChildren(listOf([row]));
      return;
    }
    const households = U.groupDevices(devices);
    households.forEach((g) => g.devices.sort((a, b) => Number(!!b.is_self) - Number(!!a.is_self)));
    const many = households.length > 1;
    box.replaceChildren(...households.map((g) => {
      const wrap = el('div', 'set-household');
      if (many) wrap.appendChild(el('p', 'set-sub', g.devices.some((d) => d.is_self) ? 'Nhà này' : 'Nhà khác'));
      wrap.appendChild(listOf(g.devices.map(deviceRow)));
      return wrap;
    }));
  }

  async function refreshDevices() {
    const box = document.querySelector('#settingsBody .set-households');
    if (!box) return;
    let devices;
    try {
      devices = await api('GET', '/api/admin/devices');
    } catch (err) {
      if (err && (err.status === 403 || err.status === 404)) render();
      return;
    }
    if (!box.isConnected) return;
    const spot = focusSpot();
    fillDevices(box, devices);
    if (spot) restoreFocus(spot, box.querySelector('.set-device .set-action'));
  }

  function devicesGroup(requests, devices) {
    const section = groupShell('devices', 'Thiết bị trong nhà');
    const list = el('div', 'set-list set-requests');
    fillRequests(list, Array.isArray(requests) ? requests : []);
    const box = el('div', 'set-households');
    fillDevices(box, devices);
    section.append(list, box);
    return section;
  }

  // --- System: built from groups of GET /api/settings ---
  function cacheRow(cache) {
    const row = baseRow('cache.usage', 'Cache đang dùng');
    row.tabIndex = 0;   // read-only but must be reachable by remote control
    const text = el('span', 'set-meter-text');
    const bar = el('div', 'set-meter');
    const fill = el('div', 'set-meter-fill');
    bar.appendChild(fill);
    bar.setAttribute('role', 'progressbar');
    bar.setAttribute('aria-label', 'Cache đang dùng');
    bar.setAttribute('aria-valuemin', '0');
    bar.setAttribute('aria-valuemax', '100');
    row.append(text, bar);
    row.hfCache = (c) => {
      const ok = c && typeof c.size_gb === 'number' && typeof c.max_gb === 'number';
      const size = ok ? c.size_gb.toLocaleString('vi-VN', { maximumFractionDigits: 1 }) : '';
      text.textContent = ok ? size + ' / ' + c.max_gb + ' GB' : '—';
      const pct = ok ? Math.min(100, Math.max(0, Number(c.percent) || 0)) : 0;
      fill.style.width = pct + '%';
      bar.setAttribute('aria-valuenow', String(Math.round(pct)));
    };
    row.hfCache(cache);
    return row;
  }

  async function refreshCache() {
    const row = document.querySelector('#settingsBody [data-key="cache.usage"]');
    const c = await soft(api('GET', '/api/cache/status'));
    if (row && row.isConnected && c) row.hfCache(c);
  }

  function settingRow(item) {
    const save = async (v) => {
      await api('PATCH', '/api/settings', { [item.key]: v });
      if (item.key === 'cache.max_gb') refreshCache();   // used percentage updates accordingly
    };
    if (item.type === 'bool') return switchRow(item.key, item.label, item.value, save);
    if (item.type === 'int') return stepperRow(item, save);
    return null;   // unknown type: skip
  }

  function clearCache(row) {
    if (!root.confirm('Xoá toàn bộ cache?')) return;
    act(row, async () => {
      const r = await api('POST', '/api/admin/cache/clear', {});
      if (!r || r.status !== 'success') throw new Error('cache clear failed');
      showNote(row, 'Đã xoá cache');
      await refreshCache();
    }, 'Không xoá được');
  }

  function systemGroup(conf, cache) {
    const groups = conf.groups || [];
    const g = groups.find((x) => x.id === 'system') || groups[0] || { label: 'Hệ thống', items: [] };
    return renderGroup('system', g.label || 'Hệ thống', [
      cacheRow(cache),
      ...(g.items || []).map(settingRow),
      actionRow('cache.clear', 'Xoá cache', clearCache, true),
    ]);
  }

  // --- HomeTV: IPTV sources (static/settings-hometv.js) share the row builders above ---
  const ui = {
    el, chevron, api, every, stopTimer, render, groupShell, baseRow, clearError, showError, setSaving,
    saveFailed, act, stepperRow, actionRow, editInline, subAction,
    lastSeenText: (t) => U.lastSeenText(t, serverNow()),
    isOpen: () => isOpen,
  };

  function hometvGroup() {
    return root.HFSettingsHometv ? root.HFSettingsHometv.renderGroup(ui) : null;
  }

  // --- Page build, focus preservation ---
  // Current focus: the row (data-key or data-id) and the element's index within the row
  function focusSpot() {
    const a = document.activeElement;
    if (!a || !$('settingsBody').contains(a)) return null;
    const row = a.closest('[data-key], [data-id]');
    if (!row) return null;
    const attr = row.dataset.key !== undefined ? 'key' : 'id';
    return { attr, value: row.dataset[attr], index: [...row.querySelectorAll(FOCUSABLE)].indexOf(a) };
  }

  function restoreFocus(spot, fallback) {
    const row = $('settingsBody').querySelector('[data-' + spot.attr + '="' + CSS.escape(spot.value) + '"]');
    const items = row ? [...row.querySelectorAll(FOCUSABLE)] : [];
    const target = items[Math.min(Math.max(spot.index, 0), items.length - 1)] || fallback;
    if (target) target.focus({ preventScroll: true });
  }

  async function render() {
    if (!isOpen) return;
    const seq = ++renderSeq;
    const [device, cast, conf] = await Promise.all(
      [api('GET', '/api/device'), api('GET', '/api/cast/settings'), api('GET', '/api/settings')]
        .map(soft));   // /api/settings failed: still show the "Thiết bị này" group
    if (seq !== renderSeq) return;   // a newer build has started
    const enabled = !!(conf && conf.admin_enabled);
    const isAdmin = enabled && !!conf.is_admin;
    let extra = [];
    if (isAdmin) {
      extra = await Promise.all([api('GET', '/api/admin/requests'), api('GET', '/api/admin/devices'),
        api('GET', '/api/cache/status')].map(soft));
    } else if (enabled) {
      extra = [await soft(api('GET', '/api/admin/requests/mine'))];
    }
    if (seq !== renderSeq) return;
    stopTimers();   // new groups start their own timers
    const groups = [deviceGroup(device, cast)];
    if (isAdmin) groups.push(adminGroup(conf), devicesGroup(extra[0], extra[1]), systemGroup(conf, extra[2]), hometvGroup());
    else if (enabled) groups.push(guestAdminGroup(extra[0]));
    const shown = groups.filter(Boolean);   // settings-hometv.js failed to load: drop the HomeTV group
    const body = $('settingsBody');
    const spot = focusSpot();
    body.replaceChildren(...shown);
    if (spot) {
      restoreFocus(spot, $('settingsBtn'));   // row is gone (e.g. admin rights just lost): back to the gear
    } else if (document.body.classList.contains('device-tv') && $('settings-tab').classList.contains('active')) {
      // TV: focus the first row so the remote works right away
      const first = body.querySelector('.set-row button');
      if (first && (document.activeElement === document.body || document.activeElement === $('settingsBtn'))) first.focus();
    }
    if (enabled) every(TICK_MS, tick);
    if (isAdmin) every(REQUESTS_POLL_MS, pollRequests);
  }

  function tick() {
    const now = serverNow();
    document.querySelectorAll('#settingsBody [data-expires]').forEach((n) => {
      n.textContent = U.countdownText(Number(n.dataset.expires), now);
    });
  }

  // Called from switchTab('settings-tab'); `from` is the previously open tab
  function open(from) {
    if (from && from !== 'settings-tab') {
      returnTo = from;
      returnScroll = root.scrollY || 0;
    }
    isOpen = true;
    root.scrollTo(0, 0);
    return render();
  }

  // Called from switchTab when leaving settings-tab: stop all timers, abandon any in-flight build
  function close() {
    isOpen = false;
    renderSeq++;
    stopTimers();
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

  // --- Remote control (TV): arrow keys move through a grid of rows x elements ---
  const NAV_FOCUSABLE = 'button, input, [tabindex="0"]';
  const visible = (n) => !n.disabled && n.getClientRects().length > 0;

  // Vertically stacked blocks inside a row: open device or source actions, add-source form
  const STACKED = '.set-device-actions, .set-source-actions, .hometv-form';

  // Grid: each .set-row with focusable elements is a row (the row itself counts if it has tabindex),
  // columns are those elements in DOM order; an element belongs to the nearest enclosing row (child rows in
  // a source's Edit block are separate rows). Every element directly inside a stacked block (STACKED) forms
  // its own row, so Down steps through each action
  function navGrid() {
    const grid = [];
    let last = null;
    $('settingsBody').querySelectorAll(NAV_FOCUSABLE).forEach((n) => {
      if (!visible(n)) return;
      const row = n.closest('.set-row');
      if (!row) return;
      const stack = n.closest(STACKED);
      const line = stack && row.contains(stack) ? n : row;
      if (line === last) grid[grid.length - 1].push(n);
      else grid.push([n]);
      last = line;
    });
    return grid;
  }

  function onNavKey(e) {
    if (!['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key)) return;
    if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    if (!document.body.classList.contains('device-tv')) return;
    const tab = $('settings-tab');
    const target = e.target;
    if (!tab || !target || !tab.contains(target) || !tab.classList.contains('active')) return;
    // in a text input, left/right belong to the caret; only Up/Down leave the field
    if (target.tagName === 'INPUT' && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) return;
    const grid = navGrid();
    let row = -1;
    let col = -1;
    grid.forEach((cells, r) => { const c = cells.indexOf(target); if (c >= 0) { row = r; col = c; } });
    if (row < 0) return;
    const [r, c] = U.gridMove(grid.map((cells) => cells.length), row, col, e.key);
    const next = grid[r] && grid[r][c];
    if (!next || next === target) return;
    e.preventDefault();
    next.focus({ preventScroll: true });
    next.scrollIntoView({ block: 'nearest' });
  }
  document.addEventListener('keydown', onNavKey);

  // #playerPrefs panel or a cast command changed playback settings: keep the Settings page in sync
  document.addEventListener('hf-prefs', (e) => {
    const p = e.detail || root.HFPlayerAuto.getPrefs();
    update('player.autoNext', p.autoNext);
    update('player.autoIntro', p.autoIntro);
    update('player.introSeconds', p.introSeconds);
  });

  const backBtn = $('settingsBack');
  if (backBtn) backBtn.addEventListener('click', back);

  root.HFSettings = { open, close };
})(typeof globalThis !== 'undefined' ? globalThis : this);
