// "HomeTV" group of the Settings page: manages IPTV sources (admin only).
// Reuses the row builders from static/settings.js via `ui` (see settings.js: const ui).
// Text from the server (name, URL, errors) only goes through textContent.
(function (root) {
  const PATH = '/api/admin/hometv/sources';
  const STATUS_PATH = '/api/admin/hometv/health/status';
  const POLL_MS = 3000;   // a source is loading: reload the list
  const QUEUED_MAX_MS = 90000;   // background loop picks it up within ≤ 60 seconds; past this limit stop waiting
  const RIGHTS = { pending_review: 'Chờ duyệt', approved: 'Đã duyệt', rejected: 'Từ chối' };
  const PRIORITY = { key: 'hometv.priority', label: 'Ưu tiên', min: 1, max: 100, step: 1 };
  const HOURS = { key: 'hometv.refresh_hours', label: 'Chu kỳ tải', min: 1, max: 168, step: 1, unit: 'giờ' };

  // Kept across rebuilds (like the open device in "Thiết bị trong nhà")
  let openId = null;   // source whose actions are open
  let editId = null;   // source whose Edit block is open
  // Source just hit "Làm mới ngay" or had its URL changed: server only sets next_fetch_at = now, the background loop
  // picks it up next round (fetching isn't on yet). id -> { lastFetch: last_fetch_at at press time, at: Date.now() }
  const queued = new Map();
  // Source just hit "Kiểm tra ngay": server only sets next_check_at = now, the background loop picks it up next round (≤ 60 seconds).
  // id -> { at, seen }: seen = health.checking was seen on; stop waiting once it turns on then off, or after QUEUED_MAX_MS
  const queuedChecks = new Map();

  function pruneChecks(items) {
    const byId = new Map(items.map((d) => [d.id, d]));
    const now = Date.now();
    queuedChecks.forEach((q, id) => {
      const d = byId.get(id);
      const checking = !!(d && d.health && d.health.checking);
      if (checking) q.seen = true;
      if (!d || (q.seen && !checking) || (!q.seen && now - q.at > QUEUED_MAX_MS)) queuedChecks.delete(id);
    });
  }

  function queue(d) {
    queued.set(d.id, { lastFetch: d.last_fetch_at, at: Date.now() });
  }

  // Drop the wait once the source finished loading (no longer fetching, last_fetch_at changed), was deleted, or after QUEUED_MAX_MS
  function pruneQueued(items) {
    const byId = new Map(items.map((d) => [d.id, d]));
    const now = Date.now();
    queued.forEach((q, id) => {
      const d = byId.get(id);
      if (!d || now - q.at > QUEUED_MAX_MS || (!d.fetching && d.last_fetch_at !== q.lastFetch)) queued.delete(id);
    });
  }

  const sourcePath = (id) => PATH + '/' + encodeURIComponent(id);

  // 422 {"errors": {field: reason}} -> first reason; 503 -> HomeTV not ready
  function errorText(err) {
    if (err && err.status === 503) return 'HomeTV chưa sẵn sàng';
    if (err && err.status === 409 && err.data && typeof err.data.detail === 'string') return err.data.detail;
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
    let health = { enabled: false, country: null };   // status of the server's stream checker
    const statusRow = el('div', 'set-note hometv-health-status');
    statusRow.tabIndex = 0;   // read-only but must be reachable by remote control
    section.insertBefore(statusRow, list);

    function paintStatus() {
      statusRow.textContent = 'Kiểm tra stream: ' + (health.enabled ? 'bật (máy chủ ở VN)'
        : health.country ? 'tắt (máy chủ ngoài Việt Nam)' : 'tắt (chưa xác định vị trí máy chủ)');
    }
    paintStatus();
    let loaded = false;
    let loadSeq = 0;
    let loading = 0;
    let poll = null;

    function metaText(d) {
      // Sources are approved by default; only a leftover non-approved state is shown
      const parts = [(d.channel_count || 0) + ' kênh'];
      if (d.rights_status !== 'approved') parts.unshift(RIGHTS[d.rights_status] || RIGHTS.pending_review);
      const h = d.health;
      if (d.rights_status === 'approved' && h && h.checked > 0) parts.push(h.alive + '/' + h.checked + ' sống');
      if (d.fetching || queued.has(d.id)) parts.push('Đang tải…');
      else if (d.last_success_at) parts.push(ui.lastSeenText(d.last_success_at));   // never loaded: show no time
      if ((h && h.checking) || queuedChecks.has(d.id)) parts.push('Đang kiểm tra…');
      return parts.join(' · ');
    }

    // --- Load list ---
    async function reload() {
      const seq = ++loadSeq;
      loading++;
      let items = null;
      let failure = null;
      const statusReq = ui.api('GET', STATUS_PATH).catch(() => null);   // status error doesn't break the list
      try {
        items = await ui.api('GET', PATH);
      } catch (err) {
        failure = err;
      } finally {
        loading--;
      }
      if (seq !== loadSeq || !section.isConnected) return;   // a newer load started or the page was rebuilt
      if (failure) {
        if (failure.status === 403 || failure.status === 404) { ui.render(); return; }   // rights lost or admin disabled
        if (failure.status === 503) { info('hometv.unavailable', 'HomeTV chưa sẵn sàng'); schedule(false); return; }
        if (!loaded) info('hometv.error', 'Không tải được danh sách');
        return;   // network error while a list is shown: keep it, retry next round
      }
      loaded = true;
      const st = await statusReq;
      if (seq !== loadSeq || !section.isConnected) return;
      if (st && typeof st.enabled === 'boolean') { health = st; paintStatus(); }
      fill(Array.isArray(items) ? items : []);
    }

    // While a source is loading or queued, reload every POLL_MS; only while the page is open. close() stops all timers
    function schedule(fetching) {
      if (fetching && poll === null && ui.isOpen()) {
        poll = ui.every(POLL_MS, () => { if (!loading) reload(); });
      } else if (!fetching && poll !== null) {
        ui.stopTimer(poll);
        poll = null;
      }
    }

    // Info row in place of the list (HomeTV not ready / load error)
    function info(key, text) {
      const row = ui.baseRow(key, text);
      row.tabIndex = 0;   // read-only but must be reachable by remote control
      list.replaceChildren(row);
    }

    // Update by id: existing rows stay (focus kept, Edit block intact), new rows are appended
    function fill(items) {
      if (!addRow.isConnected) list.replaceChildren(addRow);
      const ids = items.map((d) => d.id);
      if (!ids.includes(openId)) openId = null;
      if (!ids.includes(editId)) editId = null;
      pruneQueued(items);
      pruneChecks(items);
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
      // reordering (e.g. priority just edited) drops focus: restore it; focused row deleted: move to the next row
      if (active && active.isConnected && document.activeElement !== active && list.contains(active)) {
        active.focus({ preventScroll: true });
      } else if (lostAt >= 0) {
        const next = list.children[Math.min(lostAt, list.children.length - 1)];
        const target = next && next.querySelector('button');
        if (target) target.focus({ preventScroll: true });
      }
      schedule(queued.size > 0 || queuedChecks.size > 0 || items.some((d) => d.fetching || (d.health && d.health.checking)));
    }

    function repaintAll() {
      list.querySelectorAll(':scope > .set-source').forEach((r) => r.hfUpdate());
    }

    // --- A source: tap to open actions, stacked vertically like device rows ---
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
      let editor = null;   // Edit block
      let deadBox = null;  // dead stream list
      const slots = {};    // button by position: Enable/Disable share one button so focus on TV doesn't drop

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
          panel = items = editor = deadBox = null;
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
          !approved && slot('rights', 'approve', 'Duyệt'),   // only for a leftover non-approved source
          cur.enabled ? slot('power', 'disable', 'Tắt') : slot('power', 'enable', 'Bật'),
          cur.enabled && slot('refresh', 'refresh', 'Làm mới ngay'),   // disabled sources aren't loaded
          approved && cur.enabled && health.enabled && slot('check', 'check', 'Kiểm tra ngay'),
          approved && slot('dead', 'dead', 'Xem stream chết'),
          deadBox,
          slot('edit', 'edit', 'Sửa'),
          editor,
          slot('delete', 'delete', 'Xoá', true),
        ].filter(Boolean);
        slots.edit.setAttribute('aria-expanded', editing ? 'true' : 'false');
        // change in place: the focused button isn't removed and reattached
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

      // Write then reload the list (order, loading state). 404: source was just deleted on another device
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

      // Inline dead stream list (up to 50); text only via textContent
      async function toggleDead() {
        if (deadBox) { deadBox.remove(); deadBox = null; paintActions(); return; }
        deadBox = el('div', 'set-note hometv-dead');
        deadBox.appendChild(el('div', 'hometv-dead-loading', 'Đang tải…'));
        paintActions();
        const mine = deadBox;
        let list = null;
        try {
          list = await ui.api('GET', sourcePath(cur.id) + '/health?status=dead&limit=50');
        } catch (err) {
          list = null;
        }
        if (deadBox !== mine) return;   // closed or rebuilt while waiting
        mine.replaceChildren();
        if (!Array.isArray(list)) mine.appendChild(el('div', 'hometv-dead-empty', 'Không tải được'));
        else if (list.length === 0) mine.appendChild(el('div', 'hometv-dead-empty', 'Không có stream chết'));
        else list.forEach((d) => mine.appendChild(el('div', 'hometv-dead-item',
          d.channel_name + ' · ' + (d.error || 'Lỗi') + ' · chết ' + d.fail_streak + ' lần')));
      }

      function run(act) {
        if (act === 'approve') patch({ rights_status: 'approved' });
        else if (act === 'enable') patch({ enabled: true });
        else if (act === 'disable') patch({ enabled: false });
        else if (act === 'refresh') {
          write(async () => {
            await ui.api('POST', sourcePath(cur.id) + '/refresh', {});
            queue(cur);   // show "Đang tải…" immediately, keep reloading until the background loop finishes
            paint();
          });
        }
        else if (act === 'check') {
          write(async () => {
            await ui.api('POST', sourcePath(cur.id) + '/check', {});
            queuedChecks.set(cur.id, { at: Date.now(), seen: false });   // show "Đang kiểm tra…" immediately
            paint();
          });
        }
        else if (act === 'dead') toggleDead();
        else if (act === 'edit') { editId = editId === cur.id ? null : cur.id; paint(); }
        else if (act === 'delete') {
          if (!root.confirm('Xoá nguồn ' + cur.name + '? Các kênh chỉ có ở nguồn này sẽ mất.')) return;
          write(() => ui.api('DELETE', sourcePath(cur.id), {}));
        }
      }

      // Edit block: name and URL are edited inline; priority and interval use (−)(+)
      function editBlock() {
        const box = el('div', 'hometv-edit');
        // save one field; errors are shown by the row builder under the right row
        const save = async (body) => {
          const prevUrl = cur.url;
          let next;
          try {
            next = await ui.api('PATCH', sourcePath(cur.id), body);
          } catch (err) {
            if (err && err.status === 404) { await reload(); return; }
            throw err;
          }
          // URL change: server sets next_fetch_at = now; disabled sources aren't loaded by the background loop
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
        repaintAll();   // opening this source closes the others
      });
      row.hfUpdate = (next) => { if (next) cur = next; paint(); };
      paint();
      return row;
    }

    // Inline-edited text (name, URL): tap to edit, Enter or blur saves, Esc cancels
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

      // show the new text immediately; on error restore the old text and report under the row
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
          ui.saveFailed(row, err, errorText);   // e.g. 422 "URL không hợp lệ"
        } finally {
          busy = false;
          ui.setSaving(row, false);
        }
      }

      btn.addEventListener('click', () => {
        ui.clearError(row);
        ui.editInline(btn, current, label, commit, null, maxLength);
      });
      // editing (button gave way to the input) or saving: don't overwrite
      row.hfSet = (v) => { if (!busy && btn.isConnected && typeof v === 'string') { current = v; paint(); } };
      return row;
    }

    // --- "+ Thêm nguồn": opens an inline form (Name, URL, Add); 422 errors show under the right field ---
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
        form.noValidate = true;   // URL is validated by the server (netguard), error shown under the field
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
          closed = true;   // removing the focused input fires focusout midway: don't close twice
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
            if (err && (err.status === 403 || err.status === 404)) { ui.render(); return; }   // rights lost, admin disabled
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
        // Enter in Name moves to URL (matches the "next" key on phone keyboards), Enter in URL submits
        nameF.input.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            if (e.isComposing || e.keyCode === 229) return;   // Enter confirming an IME word, not "next field"
            urlF.input.focus();
          }
        });
        // leaving the form before typing anything: collapse back into the button
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
