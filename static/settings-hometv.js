// Nhóm "HomeTV" của trang Cài đặt: quản lý nguồn IPTV (chỉ admin).
// Dùng lại các hàm dựng dòng của static/settings.js qua `ui` (xem settings.js: const ui).
// Chữ từ server (tên, URL, lỗi) chỉ đi qua textContent.
(function (root) {
  const PATH = '/api/admin/hometv/sources';
  const POLL_MS = 3000;   // có nguồn đang tải: tải lại danh sách
  const QUEUED_MAX_MS = 90000;   // vòng lặp nền nhận lượt trong ≤ 60 giây; quá hạn này thì thôi chờ
  const RIGHTS = { pending_review: 'Chờ duyệt', approved: 'Đã duyệt', rejected: 'Từ chối' };
  const PRIORITY = { key: 'hometv.priority', label: 'Ưu tiên', min: 1, max: 100, step: 1 };
  const HOURS = { key: 'hometv.refresh_hours', label: 'Chu kỳ tải', min: 1, max: 168, step: 1, unit: 'giờ' };

  // Giữ qua các lượt dựng lại (như máy đang mở ở "Thiết bị trong nhà")
  let openId = null;   // nguồn đang mở thao tác
  let editId = null;   // nguồn đang mở khối Sửa
  // Nguồn vừa bấm "Làm mới ngay" hoặc vừa đổi URL: server chỉ đặt next_fetch_at = now, vòng lặp nền
  // nhận lượt sau (fetching chưa bật ngay). id -> { lastFetch: last_fetch_at lúc bấm, at: Date.now() }
  const queued = new Map();

  function queue(d) {
    queued.set(d.id, { lastFetch: d.last_fetch_at, at: Date.now() });
  }

  // Bỏ lượt chờ khi nguồn đã tải xong (không còn fetching, last_fetch_at đổi), đã bị xoá, hoặc quá QUEUED_MAX_MS
  function pruneQueued(items) {
    const byId = new Map(items.map((d) => [d.id, d]));
    const now = Date.now();
    queued.forEach((q, id) => {
      const d = byId.get(id);
      if (!d || now - q.at > QUEUED_MAX_MS || (!d.fetching && d.last_fetch_at !== q.lastFetch)) queued.delete(id);
    });
  }

  const sourcePath = (id) => PATH + '/' + encodeURIComponent(id);

  // 422 {"errors": {field: lý do}} -> lý do đầu tiên; 503 -> HomeTV chưa sẵn sàng
  function errorText(err) {
    if (err && err.status === 503) return 'HomeTV chưa sẵn sàng';
    const errors = err && err.data && err.data.errors;
    const first = errors && Object.values(errors).find((v) => typeof v === 'string');
    return first || 'Không lưu được';
  }

  function renderGroup(ui) {
    const el = ui.el;
    const section = ui.groupShell('hometv', 'HomeTV');
    const list = el('div', 'set-list');
    section.appendChild(list);
    const addRow = addSourceRow();
    let loaded = false;
    let loadSeq = 0;
    let loading = 0;
    let poll = null;

    function metaText(d) {
      const parts = [RIGHTS[d.rights_status] || RIGHTS.pending_review, (d.channel_count || 0) + ' kênh'];
      if (d.fetching || queued.has(d.id)) parts.push('Đang tải…');
      else if (d.last_success_at) parts.push(ui.lastSeenText(d.last_success_at));   // chưa tải được lần nào: không ghi giờ
      return parts.join(' · ');
    }

    // --- Tải danh sách ---
    async function reload() {
      const seq = ++loadSeq;
      loading++;
      let items = null;
      let failure = null;
      try {
        items = await ui.api('GET', PATH);
      } catch (err) {
        failure = err;
      } finally {
        loading--;
      }
      if (seq !== loadSeq || !section.isConnected) return;   // đã có lượt tải mới hơn hoặc trang đã dựng lại
      if (failure) {
        if (failure.status === 403 || failure.status === 404) { ui.render(); return; }   // mất quyền hoặc admin bị tắt
        if (failure.status === 503) { info('hometv.unavailable', 'HomeTV chưa sẵn sàng'); schedule(false); return; }
        if (!loaded) info('hometv.error', 'Không tải được danh sách');
        return;   // lỗi mạng lúc đang có danh sách: giữ nguyên, lượt sau thử lại
      }
      loaded = true;
      fill(Array.isArray(items) ? items : []);
    }

    // Có nguồn đang tải hoặc đang chờ tải thì tải lại mỗi POLL_MS; chỉ khi trang đang mở. close() dừng mọi bộ đếm
    function schedule(fetching) {
      if (fetching && poll === null && ui.isOpen()) {
        poll = ui.every(POLL_MS, () => { if (!loading) reload(); });
      } else if (!fetching && poll !== null) {
        ui.stopTimer(poll);
        poll = null;
      }
    }

    // Dòng thông tin thay cho danh sách (HomeTV chưa sẵn sàng / lỗi tải)
    function info(key, text) {
      const row = ui.baseRow(key, text);
      row.tabIndex = 0;   // chỉ-đọc nhưng điều khiển từ xa phải tới được
      list.replaceChildren(row);
    }

    // Cập nhật theo id: dòng cũ giữ nguyên (không mất focus, khối Sửa còn nguyên), dòng mới thêm vào
    function fill(items) {
      if (!addRow.isConnected) list.replaceChildren(addRow);
      const ids = items.map((d) => d.id);
      if (!ids.includes(openId)) openId = null;
      if (!ids.includes(editId)) editId = null;
      pruneQueued(items);
      const active = document.activeElement;
      let lostAt = -1;
      [...list.querySelectorAll(':scope > .set-source')].forEach((r, i) => {
        if (ids.includes(r.dataset.id)) return;
        if (r.contains(active)) lostAt = i;
        r.remove();
      });
      const old = new Map([...list.querySelectorAll(':scope > .set-source')].map((r) => [r.dataset.id, r]));
      items.forEach((d, i) => {
        const r = old.get(d.id);
        if (r) r.hfUpdate(d);
        const row = r || sourceRow(d);
        if (list.children[i] !== row) list.insertBefore(row, list.children[i] || addRow);
      });
      // đổi thứ tự (vd. vừa sửa ưu tiên) làm rơi focus: đặt lại; dòng đang focus bị xoá: sang dòng kế
      if (active && active.isConnected && document.activeElement !== active && list.contains(active)) {
        active.focus({ preventScroll: true });
      } else if (lostAt >= 0) {
        const next = list.children[Math.min(lostAt, list.children.length - 1)];
        const target = next && next.querySelector('button');
        if (target) target.focus({ preventScroll: true });
      }
      schedule(queued.size > 0 || items.some((d) => d.fetching));
    }

    function repaintAll() {
      list.querySelectorAll(':scope > .set-source').forEach((r) => r.hfUpdate());
    }

    // --- Một nguồn: chạm để mở thao tác, xếp dọc như dòng thiết bị ---
    function sourceRow(d) {
      const row = el('div', 'set-row set-row-action set-source');
      row.dataset.id = d.id;
      const head = el('button', 'set-action');
      head.type = 'button';
      const main = el('span', 'set-main');
      const line = el('span', 'set-name-line');
      const name = el('span', 'set-source-name');
      line.appendChild(name);
      const meta = el('span', 'set-source-meta');
      main.append(line, meta);
      head.append(main, ui.chevron());
      row.appendChild(head);

      let cur = d;
      let panel = null;    // .set-source-actions
      let items = null;    // .set-subactions trong panel
      let editor = null;   // khối Sửa
      const slots = {};    // nút theo chỗ: Duyệt/Bỏ duyệt cùng một nút để focus trên TV không rơi

      function paint() {
        name.textContent = cur.name;
        let tag = line.querySelector('.set-tag');
        if (!cur.enabled && !tag) line.appendChild(el('span', 'set-tag', 'Đã tắt'));
        else if (cur.enabled && tag) tag.remove();
        meta.textContent = metaText(cur);
        let error = main.querySelector('.set-source-error');
        if (cur.last_error) {
          if (!error) { error = el('span', 'set-source-error'); main.appendChild(error); }
          error.textContent = 'Lỗi: ' + cur.last_error + ' (giữ bản cũ)';
        } else if (error) {
          error.remove();
        }
        row.classList.toggle('is-off', !cur.enabled);
        head.setAttribute('aria-expanded', openId === cur.id ? 'true' : 'false');
        paintActions();
      }

      function slot(key, act, text, danger) {
        let b = slots[key];
        if (!b) {
          b = ui.subAction(act, text, danger);
          b.addEventListener('click', () => run(b.dataset.act));
          slots[key] = b;
        }
        b.dataset.act = act;
        b.textContent = text;
        return b;
      }

      function paintActions() {
        if (openId !== cur.id) {
          if (panel) panel.remove();
          panel = items = editor = null;
          return;
        }
        if (!panel) {
          panel = el('div', 'set-source-actions');
          items = el('div', 'set-subactions');
          panel.appendChild(items);
          head.after(panel);
        }
        const approved = cur.rights_status === 'approved';
        const editing = editId === cur.id;
        if (editing && !editor) editor = editBlock();
        if (!editing) editor = null;
        if (editor) editor.hfSet(cur);
        const want = [
          approved ? slot('rights', 'unapprove', 'Bỏ duyệt') : slot('rights', 'approve', 'Duyệt'),
          cur.rights_status !== 'rejected' && slot('reject', 'reject', 'Từ chối'),
          cur.enabled ? slot('power', 'disable', 'Tắt') : slot('power', 'enable', 'Bật'),
          cur.enabled && slot('refresh', 'refresh', 'Làm mới ngay'),   // nguồn tắt không được tải
          slot('edit', 'edit', 'Sửa'),
          editor,
          slot('delete', 'delete', 'Xoá', true),
        ].filter(Boolean);
        slots.edit.setAttribute('aria-expanded', editing ? 'true' : 'false');
        // đổi tại chỗ: nút đang focus không bị gỡ ra rồi gắn lại
        const active = document.activeElement;
        const at = [...items.children].findIndex((n) => n.contains(active));
        [...items.children].forEach((n) => { if (!want.includes(n)) n.remove(); });
        want.forEach((n, i) => { if (items.children[i] !== n) items.insertBefore(n, items.children[i] || null); });
        if (at >= 0 && !items.contains(document.activeElement)) {
          const next = items.children[Math.min(at, items.children.length - 1)];
          if (next) next.focus({ preventScroll: true });
        }
      }

      function apply(next) {
        if (next && next.id === cur.id) cur = next;
        paint();
      }

      // Ghi rồi tải lại danh sách (thứ tự, trạng thái đang tải). 404: nguồn vừa bị xoá ở máy khác
      function write(work) {
        return ui.act(row, async () => {
          try {
            await work();
          } catch (err) {
            if (!err || err.status !== 404) throw err;
          }
          await reload();
        }, errorText);
      }

      const patch = (body) => write(async () => apply(await ui.api('PATCH', sourcePath(cur.id), body)));

      function run(act) {
        if (act === 'approve') patch({ rights_status: 'approved' });
        else if (act === 'unapprove') patch({ rights_status: 'pending_review' });
        else if (act === 'reject') patch({ rights_status: 'rejected' });
        else if (act === 'enable') patch({ enabled: true });
        else if (act === 'disable') patch({ enabled: false });
        else if (act === 'refresh') {
          write(async () => {
            await ui.api('POST', sourcePath(cur.id) + '/refresh', {});
            queue(cur);   // hiện "Đang tải…" ngay, tải lại cho tới khi vòng lặp nền tải xong
            paint();
          });
        }
        else if (act === 'edit') { editId = editId === cur.id ? null : cur.id; paint(); }
        else if (act === 'delete') {
          if (!root.confirm('Xoá nguồn ' + cur.name + '? Các kênh chỉ có ở nguồn này sẽ mất.')) return;
          write(() => ui.api('DELETE', sourcePath(cur.id), {}));
        }
      }

      // Khối Sửa: tên và URL sửa ngay trong dòng; ưu tiên và chu kỳ dùng (−)(+)
      function editBlock() {
        const box = el('div', 'hometv-edit');
        // lưu một trường; lỗi do hàm dựng dòng tự báo dưới đúng dòng
        const save = async (body) => {
          const prevUrl = cur.url;
          let next;
          try {
            next = await ui.api('PATCH', sourcePath(cur.id), body);
          } catch (err) {
            if (err && err.status === 404) { await reload(); return; }
            throw err;
          }
          // đổi URL: server đặt next_fetch_at = now; nguồn tắt thì vòng lặp nền không tải
          if (next && next.url !== prevUrl && next.enabled) queue(next);
          apply(next);
          await reload();
        };
        const nameRow = textRow('hometv.name', 'Tên', cur.name, 60, (v) => save({ name: v }));
        const urlRow = textRow('hometv.url', 'URL', cur.url, 2048, (v) => save({ url: v }));
        const prio = ui.stepperRow(Object.assign({ value: cur.priority }, PRIORITY), (v) => save({ priority: v }));
        const hours = ui.stepperRow(Object.assign({ value: cur.refresh_hours }, HOURS), (v) => save({ refresh_hours: v }));
        box.append(nameRow, urlRow, prio, hours);
        box.hfSet = (s) => {
          nameRow.hfSet(s.name);
          urlRow.hfSet(s.url);
          prio.hfSet(s.priority);
          hours.hfSet(s.refresh_hours);
        };
        return box;
      }

      head.addEventListener('click', () => {
        openId = openId === cur.id ? null : cur.id;
        if (editId !== openId) editId = null;
        repaintAll();   // mở nguồn này thì đóng nguồn khác
      });
      row.hfUpdate = (next) => { if (next) cur = next; paint(); };
      paint();
      return row;
    }

    // Chữ sửa ngay trong dòng (tên, URL): chạm để sửa, Enter hoặc rời ô thì lưu, Esc thì thôi
    function textRow(key, label, value, maxLength, save) {
      const row = ui.baseRow(key, label);
      const btn = el('button', 'set-text');
      btn.type = 'button';
      const text = el('span', 'set-text-value');
      btn.append(text, ui.chevron());
      row.appendChild(btn);
      let current = value;
      let busy = false;
      const paint = () => { text.textContent = current; };
      paint();

      // hiện chữ mới ngay; lỗi thì trả chữ cũ và báo dưới dòng
      async function commit(next) {
        if (next === current) return;
        const prev = current;
        current = next;
        paint();
        busy = true;
        ui.setSaving(row, true);
        try {
          await save(next);
        } catch (err) {
          current = prev;
          paint();
          ui.saveFailed(row, err, errorText);   // vd. 422 "URL không hợp lệ"
        } finally {
          busy = false;
          ui.setSaving(row, false);
        }
      }

      btn.addEventListener('click', () => {
        ui.clearError(row);
        ui.editInline(btn, current, label, commit, null, maxLength);
      });
      // đang sửa (nút nhường chỗ cho ô nhập) hoặc đang lưu: không ghi đè
      row.hfSet = (v) => { if (!busy && btn.isConnected && typeof v === 'string') { current = v; paint(); } };
      return row;
    }

    // --- "+ Thêm nguồn": mở form ngay trong dòng (Tên, URL, Thêm); lỗi 422 hiện dưới đúng ô ---
    function addSourceRow() {
      const row = ui.actionRow('hometv.add', '+ Thêm nguồn', () => openForm());
      const btn = row.querySelector('.set-action');
      let busy = false;

      function field(id, label, type, maxLength, placeholder) {
        const box = el('div', 'hometv-field');
        const input = el('input', 'set-input');
        input.id = id;
        input.type = type;
        input.maxLength = maxLength;
        input.placeholder = placeholder;
        input.autocomplete = 'off';
        input.autocapitalize = 'off';
        input.spellcheck = false;
        input.setAttribute('aria-label', label);
        box.appendChild(input);
        return { box, input };
      }

      function openForm() {
        ui.clearError(row);
        const form = el('form', 'set-form hometv-form');
        form.noValidate = true;   // URL do server kiểm tra (netguard), báo lỗi dưới ô
        const nameF = field('hometvName', 'Tên nguồn', 'text', 60, 'Tên');
        const urlF = field('hometvUrl', 'URL playlist', 'url', 2048, 'https://…');
        nameF.input.enterKeyHint = 'next';
        urlF.input.enterKeyHint = 'go';
        const add = el('button', 'set-pill is-primary', 'Thêm');
        add.type = 'submit';
        form.append(nameF.box, urlF.box, add);
        btn.replaceWith(form);
        nameF.input.focus();
        const fields = { name: nameF, url: urlF };

        const clearFieldErrors = () => form.querySelectorAll('.set-error').forEach((n) => n.remove());
        let closed = false;
        const closeForm = (refocus) => {
          if (closed) return;
          closed = true;   // gỡ ô đang focus sẽ bắn focusout ngay giữa chừng: không đóng lần hai
          form.replaceWith(btn);
          if (refocus) btn.focus();
        };

        form.addEventListener('submit', async (e) => {
          e.preventDefault();
          if (busy) return;
          busy = true;
          ui.clearError(row);
          clearFieldErrors();
          ui.setSaving(row, true);
          try {
            await ui.api('POST', PATH, { name: nameF.input.value.trim(), url: urlF.input.value.trim() });
            closeForm(true);
            await reload();
          } catch (err) {
            if (err && (err.status === 403 || err.status === 404)) { ui.render(); return; }   // mất quyền, admin bị tắt
            const errors = (err && err.status === 422 && err.data && err.data.errors) || {};
            let shown = false;
            Object.keys(fields).forEach((k) => {
              if (typeof errors[k] !== 'string') return;
              fields[k].box.appendChild(el('div', 'set-error', errors[k]));
              shown = true;
            });
            if (!shown) ui.showError(row, errorText(err));
            const bad = Object.keys(fields).find((k) => typeof errors[k] === 'string');
            if (bad) fields[bad].input.focus();
          } finally {
            busy = false;
            ui.setSaving(row, false);
          }
        });
        form.addEventListener('keydown', (e) => {
          if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeForm(true); }
        });
        // Enter ở ô Tên sang ô URL (đúng phím "next" trên bàn phím điện thoại), Enter ở ô URL mới gửi
        nameF.input.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') { e.preventDefault(); urlF.input.focus(); }
        });
        // rời form khi chưa gõ gì: thu lại thành nút
        form.addEventListener('focusout', (e) => {
          if (!busy && !nameF.input.value && !urlF.input.value && !form.contains(e.relatedTarget)) closeForm(false);
        });
      }
      return row;
    }

    reload();
    return section;
  }

  root.HFSettingsHometv = { renderGroup };
})(typeof globalThis !== 'undefined' ? globalThis : this);
