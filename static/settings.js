// Trang Cài đặt: dựng các nhóm từ dữ liệu, gọi API, tự lưu (không có nút Lưu).
// Cài đặt phát của máy vẫn nằm ở localStorage qua HFPlayerAuto; panel #playerPrefs và trang này cùng nghe 'hf-prefs'.
(function (root) {
  const U = root.HFSettingsUtils;
  const SAVE_DELAY_MS = 400; // bước số: lưu sau lần bấm cuối
  const INTRO_ITEM = { key: 'player.introSeconds', label: 'Mở đầu kết thúc', min: 10, max: 300, step: 5, unit: 'giây' };
  const SVG = 'http://www.w3.org/2000/svg';
  const REQUESTS_POLL_MS = 5000; // máy admin: tải lại yêu cầu chờ duyệt
  const MINE_POLL_MS = 3000;     // máy đang xin duyệt: hỏi kết quả
  const TICK_MS = 1000;          // đếm ngược
  const NAME_ERROR = 'Tên cần 1–40 ký tự';
  const KIND_LABELS = { tv: 'TV', phone: 'Điện thoại', desktop: 'Máy tính' };
  const FOCUSABLE = 'button, input, [tabindex]';

  let returnTo = 'search-tab';
  let returnScroll = 0;
  let renderSeq = 0;
  let isOpen = false;      // trang đang mở: chỉ khi đó mới dựng lại và chạy bộ đếm
  let clockSkew = 0;       // giây: giờ server trừ giờ máy (TV hay lệch giờ)
  let openDevice = null;   // id công khai của máy đang mở thao tác, giữ qua lượt tải lại
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

  // Ghi luôn gửi JSON có thân (route /api/settings, /api/admin trả 415 nếu thiếu). Lỗi HTTP ném Error có .status
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
      clockSkew = Math.abs(skew) > 2 ? skew : 0;   // Date chỉ chính xác tới giây: lệch nhỏ thì bỏ qua
    }
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

  const soft = (p) => p.catch(() => null);
  const serverNow = () => Date.now() / 1000 + clockSkew;

  // Bộ đếm của trang: close() và mỗi lượt dựng lại dừng hết
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

  // --- Dòng và nhóm (Task 10 dùng lại) ---
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

  function clearError(row) {
    const old = row.querySelector('.set-error');
    if (old) old.remove();
  }

  function showError(row, text) {
    clearError(row);
    row.appendChild(el('div', 'set-error', text));
  }

  // Dòng chữ phụ dưới dòng (màu --muted), vd. "Đã xoá cache"
  function clearNote(row) {
    const old = row.querySelector('.set-note');
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

  // Lưu lỗi: 403 nghĩa là vừa mất quyền admin, dựng lại cả trang; còn lại báo ngay dưới dòng.
  // `text` là chữ hoặc hàm (err) => chữ
  function saveFailed(row, err, text) {
    if (err && err.status === 403) { render(); return; }
    const msg = typeof text === 'function' ? text(err) : text;
    showError(row, msg || 'Không lưu được');
  }

  // Một thao tác ghi trên dòng: bỏ qua khi đang chạy, hiện trạng thái đang lưu, lỗi thì báo dưới dòng
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
    // chạm vào chữ của dòng cũng bật/tắt (trừ dòng lỗi)
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

  const validName = (s) => s.length >= 1 && s.length <= 40;

  // Sửa chữ ngay trong dòng: `anchor` nhường chỗ cho ô nhập. Enter hoặc rời ô thì onDone(chữ đã bỏ
  // khoảng trắng hai đầu), Esc thì thôi. `focusBack()` chọn chỗ đặt lại focus (mặc định là anchor)
  function editInline(anchor, value, label, onDone, focusBack) {
    const input = el('input', 'set-input');
    input.type = 'text';
    input.maxLength = 40;
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
      if (e.key === 'Enter') { e.preventDefault(); finish(true, true); }
      else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); finish(false, true); }
    });
    input.addEventListener('blur', () => finish(true, false));
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

  // --- Quản trị: máy chưa là admin ---
  function loginErrorText(err) {
    if (err && err.status === 401) return 'Sai mật khẩu';
    if (err && err.status === 429) {
      const s = err.data && Number(err.data.retry_after);
      return s > 0 ? 'Thử lại sau ' + Math.ceil(s / 60) + ' phút' : 'Thử lại sau';
    }
    return 'Không đăng nhập được';
  }

  // "Đăng nhập admin": chạm mở ô mật khẩu ngay trong dòng, Enter để gửi, Esc để đóng
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
        closed = true;   // gỡ ô đang focus sẽ bắn focusout ngay giữa chừng: không đóng lần hai
        form.replaceWith(btn);
        if (refocus) btn.focus();
      };
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        if (busy || !input.value) return;   // TV: OK nhầm lúc ô trống không được tốn một lượt trong 5 lượt khoá
        busy = true;
        clearError(row);
        setSaving(row, true);
        try {
          await api('POST', '/api/admin/login', { password: input.value });
          render();   // máy này giờ là admin
        } catch (err) {
          if (err && err.status === 404) { render(); return; }   // admin vừa bị tắt
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
      // rời ô khi chưa gõ gì: thu lại thành nút
      form.addEventListener('focusout', (e) => {
        if (!busy && !input.value && !form.contains(e.relatedTarget)) closeForm(false);
      });
    }
    return row;
  }

  // "Xin duyệt từ máy admin": hiện mã 6 số và đếm ngược, hỏi kết quả mỗi MINE_POLL_MS.
  // Nút giữ nguyên trong suốt lúc chờ nên focus trên TV không mất
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

    // Chỉ tin is_admin của GET /api/settings: "approved" thì dựng lại trang, không tự coi là admin
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
        if (err && err.status === 404) render();   // admin vừa bị tắt; lỗi mạng thì thử lại lượt sau
      } finally {
        busy = false;
      }
    }

    if (mine && mine.status === 'pending') wait(mine.code, mine.expires_at);   // mở lại trang khi đang chờ
    return row;
  }

  function guestAdminGroup(mine) {
    return renderGroup('admin', 'Quản trị', [loginRow(), requestRow(mine)]);
  }

  // --- Quản trị: máy đã là admin ---
  function adminGroup() {
    return renderGroup('admin', 'Quản trị', [
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

  // --- Thiết bị trong nhà ---
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

  // Yêu cầu chờ duyệt: tên máy, mã và thời gian còn lại, nút Từ chối và Cho phép
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
        if (!err || err.status !== 409) throw err;   // 409: đã hết hạn hoặc máy admin khác đã xử lý
      }
      await Promise.all([refreshRequests(), approve ? refreshDevices() : null]);
    }, 'Không gửi được');
  }

  // Cập nhật danh sách yêu cầu theo id: dòng cũ giữ nguyên (không mất focus), dòng mới thêm vào
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
      if (err && (err.status === 403 || err.status === 404)) render();   // vừa mất quyền hoặc admin bị tắt
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
    else if (d.last_seen) parts.push(U.lastSeenText(d.last_seen, serverNow()));   // chưa từng thấy: không ghi gì
    return parts.join(' · ');
  }

  // Một máy: chạm để mở thao tác (đổi tên, cấp/thu quyền admin, xoá). Tên do người dùng đặt: chỉ textContent
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

  // Thao tác với một máy: danh sách dọc, mỗi thao tác một dòng (vừa mọi bề ngang)
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
    if (!d.is_self) {   // máy mình: đăng xuất ở nhóm Quản trị, không tự xoá được
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
            if (!err || err.status !== 404) throw err;   // đã bị xoá ở máy khác
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
      if (d.is_self) render();   // tên ở "Thiết bị này" cũng đổi
      else await refreshDevices();
    }, (err) => (err && err.status === 422 ? NAME_ERROR : 'Không lưu được'));
  }

  // Gom theo nhà (nhà mình trước, máy mình đứng đầu); chỉ ghi "Nhà này"/"Nhà khác" khi có hơn một nhà
  function fillDevices(box, devices) {
    if (!Array.isArray(devices)) {
      const row = baseRow('devices.error', 'Không tải được danh sách');
      row.tabIndex = 0;   // dòng chỉ-đọc vẫn phải với tới được bằng điều khiển từ xa
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

  // --- Hệ thống: dựng từ groups của GET /api/settings ---
  function cacheRow(cache) {
    const row = baseRow('cache.usage', 'Cache đang dùng');
    row.tabIndex = 0;   // chỉ-đọc nhưng điều khiển từ xa phải tới được
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
      if (item.key === 'cache.max_gb') refreshCache();   // phần trăm đã dùng đổi theo
    };
    if (item.type === 'bool') return switchRow(item.key, item.label, item.value, save);
    if (item.type === 'int') return stepperRow(item, save);
    return null;   // kiểu chưa biết: bỏ qua
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

  // --- HomeTV: khung, nội dung thật ở gói sau ---
  function hometvGroup() {
    const row = baseRow('hometv.iptv');
    row.tabIndex = 0;   // chỉ-đọc nhưng điều khiển từ xa phải tới được
    const label = el('span', 'set-label', 'Nguồn IPTV');
    label.appendChild(el('span', 'set-muted', ' · có ở bản sau'));
    row.appendChild(label);
    return renderGroup('hometv', 'HomeTV', [row]);
  }

  // --- Dựng trang, giữ focus ---
  // Chỗ đang focus: dòng (data-key hoặc data-id) và thứ tự của phần tử trong dòng
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
        .map((p) => p.catch(() => null)));   // /api/settings lỗi: vẫn có nhóm "Thiết bị này"
    if (seq !== renderSeq) return;   // đã có lượt dựng mới hơn
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
    stopTimers();   // nhóm mới tự bật bộ đếm của mình
    const groups = [deviceGroup(device, cast)];
    if (isAdmin) groups.push(adminGroup(), devicesGroup(extra[0], extra[1]), systemGroup(conf, extra[2]), hometvGroup());
    else if (enabled) groups.push(guestAdminGroup(extra[0]));
    const body = $('settingsBody');
    const spot = focusSpot();
    body.replaceChildren(...groups);
    if (spot) {
      restoreFocus(spot, $('settingsBtn'));   // dòng không còn (vd. vừa mất quyền admin): về bánh răng
    } else if (document.body.classList.contains('device-tv') && $('settings-tab').classList.contains('active')) {
      // TV: đặt focus vào dòng đầu để remote dùng được ngay
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

  // Gọi từ switchTab('settings-tab'); `from` là tab đang mở trước đó
  function open(from) {
    if (from && from !== 'settings-tab') {
      returnTo = from;
      returnScroll = root.scrollY || 0;
    }
    isOpen = true;
    root.scrollTo(0, 0);
    return render();
  }

  // Gọi từ switchTab khi rời settings-tab: dừng mọi bộ đếm, bỏ lượt dựng đang chạy dở
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

  // --- Điều khiển từ xa (TV): phím mũi tên đi trong lưới dòng x phần tử ---
  const NAV_FOCUSABLE = 'button, input, [tabindex="0"]';
  const visible = (n) => !n.disabled && n.getClientRects().length > 0;

  // Lưới: mỗi .set-row có phần tử focus được là một dòng (chính dòng đó tính nếu có tabindex),
  // cột là các phần tử ấy theo thứ tự DOM. Quy tắc cho máy đang mở: mọi phần tử nằm trong
  // .set-device-actions (xếp dọc) tự thành một dòng riêng, nên Xuống đi qua từng thao tác
  function navGrid() {
    const grid = [];
    $('settingsBody').querySelectorAll('.set-row').forEach((row) => {
      const own = [];
      if (row.matches('[tabindex="0"]') && visible(row)) own.push(row);
      const cells = [...row.querySelectorAll(NAV_FOCUSABLE)].filter(visible);
      cells.forEach((n) => { if (!n.closest('.set-device-actions')) own.push(n); });
      if (own.length) grid.push(own);
      cells.forEach((n) => { if (n.closest('.set-device-actions')) grid.push([n]); });
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
    // trong ô nhập chữ, trái/phải thuộc về con trỏ; chỉ Lên/Xuống mới rời ô
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

  // Panel #playerPrefs hoặc lệnh cast đổi cài đặt phát: trang Cài đặt hiện đúng
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
