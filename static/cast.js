// Cast phía client: heartbeat, luồng sự kiện SSE có nối lại, gọi API. Giao diện nằm ở các phần sau của file này.
(function () {
    const defaults = { heartbeatMs: 15000, reconnectBaseMs: 2000, reconnectMaxMs: 30000, stateMs: 2000, silenceMs: 6000 };
    const EVENTS = ['hello', 'connect_request', 'connect_result', 'controller_joined', 'controller_left', 'command', 'state'];

    const HFCast = window.HFCast = {
        config: Object.assign({}, defaults, window.__HF_CAST_CONFIG || {}),
        started: false,
        linked: null,       // {id, name} của TV đang điều khiển (iPhone)
        attempt: 0,         // số lần nối lại liên tiếp chưa nhận được hello
        es: null,
        wantEvents: false,  // đang cần luồng sự kiện (TV/desktop luôn cần; iPhone khi có tờ hoặc liên kết)
        handlers: {},
        reconnectTimer: null,
    };

    HFCast.kind = function () {
        const cls = document.body.classList;
        return cls.contains('device-tv') ? 'tv' : cls.contains('device-mobile') ? 'phone' : 'desktop';
    };

    HFCast.on = function (name, fn) {
        (HFCast.handlers[name] = HFCast.handlers[name] || []).push(fn);
    };

    HFCast.emit = function (name, data) {
        (HFCast.handlers[name] || []).slice().forEach(fn => {
            try { fn(data); } catch (e) { console.warn('[cast] handler lỗi', name, e); }
        });
    };

    HFCast.api = async function (method, path, body) {
        const res = await fetch(path, {
            method,
            headers: { 'Content-Type': 'application/json' },
            body: body === undefined ? undefined : JSON.stringify(body),
        });
        let data = null;
        try { data = await res.json(); } catch (e) { /* thân rỗng */ }
        return { status: res.status, data };
    };

    HFCast.heartbeat = function () {
        return HFCast.api('POST', '/api/devices/heartbeat', { kind: HFCast.kind() }).catch(() => null);
    };

    function dropSource() {
        clearTimeout(HFCast.reconnectTimer);
        HFCast.reconnectTimer = null;
        if (HFCast.es) {
            HFCast.es.onerror = null;
            HFCast.es.close();
            HFCast.es = null;
        }
    }

    HFCast.openEvents = function () {
        dropSource();
        HFCast.wantEvents = true;
        const es = new EventSource('/api/cast/events');
        HFCast.es = es;
        EVENTS.forEach(name => es.addEventListener(name, (e) => {
            let data;
            try { data = JSON.parse(e.data); } catch (err) { return; }
            if (name === 'hello') HFCast.attempt = 0;
            HFCast.emit(name, data);
        }));
        es.onerror = () => {
            es.onerror = null;
            es.close();
            if (HFCast.es === es) HFCast.es = null;
            const delay = HFCastUtils.backoffMs(HFCast.attempt, HFCast.config.reconnectBaseMs, HFCast.config.reconnectMaxMs);
            HFCast.attempt += 1;
            HFCast.reconnectTimer = setTimeout(() => { if (HFCast.wantEvents) HFCast.openEvents(); }, delay);
        };
    };

    HFCast.closeEvents = function () {
        HFCast.wantEvents = false;
        dropSource();
    };

    // iOS ngắt kết nối nền khi khóa màn hình: nối lại khi trang hiện lại và báo để lấy trạng thái mới
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState !== 'visible' || !HFCast.wantEvents) return;
        HFCast.openEvents();
        HFCast.emit('resume', {});
    });

    HFCast.start = function () {
        if (HFCast.started) return;
        HFCast.started = true;
        HFCast.heartbeat();
        setInterval(HFCast.heartbeat, HFCast.config.heartbeatMs);
        if (HFCast.kind() !== 'phone') HFCast.openEvents();
    };

    // ---------- Dùng chung: nhãn nút Phát, định tuyến Phát lên TV ----------
    function playLabel() { return HFCast.linked ? 'Phát lên TV' : 'Phát'; }

    window.refreshPlayLabels = function () {
        document.querySelectorAll('.hero-btn-play').forEach(b => { b.textContent = playLabel(); });
        document.querySelectorAll('.detail-action-play .btn-label').forEach(n => { n.textContent = playLabel(); });
    };

    let toastTimer = null;
    HFCast.notify = function (message) {
        const toast = document.getElementById('castToast');
        if (!toast) return;
        toast.textContent = message;
        toast.hidden = false;
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => { toast.hidden = true; }, 3500);
    };

    // Trả true khi đã gửi lệnh phát lên TV (chặn phát cục bộ). Kết quả đến sau: lỗi thì báo và quay về phát trên iPhone.
    HFCast.markUnresponsive = HFCast.markUnresponsive || function () {};
    window.maybeCast = function (slug, episodeUrl) {
        const target = HFCast.linked;
        if (!target) return false;
        HFCast.api('POST', `/api/cast/${encodeURIComponent(target.id)}/command`,
            { type: 'play', slug, episode_url: episodeUrl || null })
            .then(res => { if (res.status !== 200) HFCast.markUnresponsive(); })
            .catch(() => HFCast.markUnresponsive());
        return true;
    };

    // ---------- Bên điều khiển (iPhone): nút cast, tờ "Phát lên", kết nối ----------
    function initController() {
        const $ = (id) => document.getElementById(id);
        const STORE_KEY = 'hf_cast_last_tv';
        const inflight = new Set();
        let devices = [];
        HFCast.pending = null;   // {id, name} TV đang chờ cho phép

        function remember(dev) {
            try { localStorage.setItem(STORE_KEY, JSON.stringify({ id: dev.id, name: dev.name })); } catch (e) { /* bỏ qua */ }
        }
        function recall() {
            try { return JSON.parse(localStorage.getItem(STORE_KEY) || 'null'); } catch (e) { return null; }
        }
        function sheetOpen() { return !$('castSheet').hidden; }

        function render() {
            const list = $('castDeviceList');
            list.replaceChildren();
            devices.forEach(d => {
                const btn = el('button', 'cast-device');
                btn.type = 'button';
                btn.dataset.id = d.id;
                btn.appendChild(el('span', 'cast-device-name', d.name));
                const status = HFCast.linked && HFCast.linked.id === d.id ? 'Đã kết nối'
                    : HFCast.pending && HFCast.pending.id === d.id ? 'Chờ TV cho phép…' : 'Sẵn sàng';
                btn.appendChild(el('span', 'cast-device-status', status));
                btn.addEventListener('click', () => connectAndReport(d));
                list.appendChild(btn);
            });
            $('castEmpty').hidden = devices.length > 0;
        }

        function refreshUi() {
            $('castDot').hidden = !HFCast.linked;
            refreshPlayLabels();
            render();
        }

        function releaseEventsIfIdle() {
            if (!HFCast.linked && !HFCast.pending && !sheetOpen()) HFCast.closeEvents();
        }

        function setLinked(dev) {
            HFCast.linked = { id: dev.id, name: dev.name };
            HFCast.pending = null;
            remember(dev);
            if (!HFCast.es) HFCast.openEvents();
            refreshUi();
            HFCast.emit('linked', HFCast.linked);
        }

        HFCast.markUnresponsive = function () {
            HFCast.notify('TV không phản hồi');
            HFCast.linked = null;
            HFCast.pending = null;
            releaseEventsIfIdle();
            refreshUi();
            HFCast.emit('unlinked', {});
        };

        // 'connected' | 'pending' | 'denied' | 'offline' | 'error'
        HFCast.connect = async function (device) {
            if (inflight.has(device.id) || (HFCast.pending && HFCast.pending.id === device.id)) return 'pending';
            if (HFCast.linked && HFCast.linked.id === device.id) return 'connected';
            inflight.add(device.id);
            try {
                const res = await HFCast.api('POST', `/api/cast/${encodeURIComponent(device.id)}/connect`);
                if (res.status === 200) { setLinked(device); return 'connected'; }
                if (res.status === 202) { HFCast.pending = device; render(); return 'pending'; }
                if (res.status === 409) return 'offline';
                if (res.status === 403) return 'denied';
                return 'error';
            } catch (e) {
                return 'error';
            } finally {
                inflight.delete(device.id);
            }
        };

        async function connectAndReport(device) {
            if (!HFCast.es) HFCast.openEvents();
            const outcome = await HFCast.connect(device);
            if (outcome === 'offline') HFCast.notify('TV không phản hồi');
            else if (outcome === 'denied') HFCast.notify('TV không cho phép điều khiển');
            else if (outcome === 'error') HFCast.notify('Không kết nối được. Thử lại.');
        }

        HFCast.disconnect = async function () {
            HFCast.api('DELETE', '/api/cast/link').catch(() => null);
            HFCast.linked = null;
            HFCast.pending = null;
            releaseEventsIfIdle();
            refreshUi();
            HFCast.emit('unlinked', {});
        };

        HFCast.on('connect_result', (d) => {
            if (!HFCast.pending || d.target_id !== HFCast.pending.id) return;
            if (d.allowed) return setLinked(HFCast.pending);
            HFCast.pending = null;
            HFCast.notify('TV đã từ chối kết nối');
            releaseEventsIfIdle();
            render();
        });

        async function loadDevices() {
            const res = await HFCast.api('GET', '/api/devices/nearby').catch(() => null);
            devices = res && res.status === 200 && Array.isArray(res.data) ? res.data : [];
            render();
            return devices;
        }

        async function openSheet() {
            $('castSheet').hidden = false;
            $('castScrim').hidden = false;
            if (!HFCast.es) HFCast.openEvents();
            await loadDevices();
        }
        function closeSheet() {
            $('castSheet').hidden = true;
            $('castScrim').hidden = true;
            releaseEventsIfIdle();
        }
        $('castBtn').addEventListener('click', openSheet);
        $('castSheetClose').addEventListener('click', closeSheet);
        $('castScrim').addEventListener('click', closeSheet);

        // --- Thanh nhỏ và bảng điều khiển ---
        let remote = null;      // trạng thái phát mới nhất của TV đang liên kết
        let lastStateTs = null;

        function renderRemote() {
            const v = HFCastUtils.remoteView(remote);
            const active = !!(HFCast.linked && remote && remote.slug);
            $('castMiniBar').hidden = !active;
            if (!active) $('castRemote').hidden = true;
            $('castMiniTitle').textContent = v.title;
            $('castMiniEp').textContent = v.episode;
            $('castMiniFill').style.width = v.pct + '%';
            $('castMiniState').textContent = v.playing ? '❚❚' : '▶';
            $('castRemoteTitle').textContent = v.title;
            $('castRemoteEp').textContent = v.episode;
            $('castRemoteFill').style.width = v.pct + '%';
            $('castRemotePos').textContent = v.positionText;
            $('castRemoteDur').textContent = v.durationText;
            $('castPlayPause').textContent = v.playing ? '❚❚' : '▶';
        }

        function applyState(state) {
            remote = state;
            lastStateTs = Date.now();
            renderRemote();
        }

        async function refreshRemote(strict) {
            if (!HFCast.linked) return;
            const res = await HFCast.api('GET', `/api/cast/state/${encodeURIComponent(HFCast.linked.id)}`).catch(() => null);
            if (res && res.status === 200 && res.data && res.data.target_id) applyState(res.data);
            else if (strict) HFCast.markUnresponsive();   // sau khi iPhone thức dậy: không có trạng thái = TV đã mất
        }

        HFCast.on('state', (state) => {
            if (HFCastUtils.acceptState(state, HFCast.linked && HFCast.linked.id)) applyState(state);
        });
        HFCast.on('linked', () => { remote = null; lastStateTs = null; renderRemote(); refreshRemote(false); });
        HFCast.on('unlinked', () => { remote = null; renderRemote(); });
        HFCast.on('resume', () => refreshRemote(true));

        // Đang phát mà TV im lặng quá lâu: coi như mất kết nối (TV dừng thì không có state nên không tính)
        setInterval(() => {
            if (HFCast.linked && remote && remote.playing && HFCastUtils.isStale(lastStateTs, Date.now(), HFCast.config.silenceMs)) {
                HFCast.markUnresponsive();
            }
        }, 200);

        function sendCmd(cmd) {
            if (!HFCast.linked) return;
            HFCast.api('POST', `/api/cast/${encodeURIComponent(HFCast.linked.id)}/command`, cmd)
                .then(res => { if (res.status !== 200) HFCast.markUnresponsive(); })
                .catch(() => HFCast.markUnresponsive());
        }

        $('castMiniBar').addEventListener('click', () => { $('castRemote').hidden = false; });
        $('castRemoteClose').addEventListener('click', () => { $('castRemote').hidden = true; });
        $('castPlayPause').addEventListener('click', () => sendCmd({ type: remote && remote.playing ? 'pause' : 'resume' }));
        $('castBack10').addEventListener('click', () => sendCmd({ type: 'seek', delta: -10 }));
        $('castFwd10').addEventListener('click', () => sendCmd({ type: 'seek', delta: 10 }));
        $('castPrevEp').addEventListener('click', () => sendCmd({ type: 'episode', delta: -1 }));
        $('castNextEp').addEventListener('click', () => sendCmd({ type: 'episode', delta: 1 }));
        $('castAutoNext').addEventListener('change', (e) => sendCmd({ type: 'options', autoNext: e.target.checked }));
        $('castAutoIntro').addEventListener('change', (e) => sendCmd({ type: 'options', autoIntro: e.target.checked }));
        $('castStop').addEventListener('click', () => sendCmd({ type: 'stop' }));
        $('castDisconnect').addEventListener('click', () => { HFCast.disconnect(); $('castRemote').hidden = true; });

        // Nối lại TV lần trước nếu còn trực tuyến. Im lặng: không tờ, không thông báo, không để trạng thái chờ.
        (async function silentReconnect() {
            const last = recall();
            if (!last || !last.id) return;
            const res = await HFCast.api('GET', '/api/devices/nearby').catch(() => null);
            const dev = res && Array.isArray(res.data) ? res.data.find(d => d.id === last.id) : null;
            if (!dev) return;
            const outcome = await HFCast.connect(dev);
            if (outcome === 'pending') HFCast.pending = null;
        })();
    }

    // ---------- Bên nhận (TV, máy tính): nhận lệnh, hỏi xác nhận, báo trạng thái ----------
    function initReceiver() {
        const $ = (id) => document.getElementById(id);
        const controllers = new Map();   // id -> tên
        const confirmQueue = [];

        function show(node, on) { node.hidden = !on; }

        function updateLabel() {
            const label = $('castLabel');
            if (controllers.size === 0) return show(label, false);
            label.textContent = HFCastUtils.controllerLabel([...controllers.values()].join(', '));
            show(label, true);
        }

        // --- trạng thái phát ---
        function currentState() {
            const active = document.body.classList.contains('player-active') && playbackEpisodes.length > 0 && currentPlaybackIndex >= 0;
            if (!active) return { slug: null, title: null, episode: null, position: null, duration: null, playing: false };
            const ep = playbackEpisodes[currentPlaybackIndex];
            return {
                slug: currentMovie && currentMovie.slug ? currentMovie.slug : null,
                title: currentMovie && currentMovie.name ? String(currentMovie.name).slice(0, 200) : null,
                episode: ep && ep.name ? String(ep.name).slice(0, 200) : null,
                position: Number.isFinite(video.currentTime) ? video.currentTime : null,
                duration: Number.isFinite(video.duration) ? video.duration : null,
                playing: !video.paused && !video.ended,
            };
        }

        function reportState() {
            if (controllers.size === 0) return;
            HFCast.api('POST', '/api/cast/state', currentState()).catch(() => null);
        }

        setInterval(() => { if (!video.paused) reportState(); }, HFCast.config.stateMs);
        ['play', 'pause', 'ended', 'loadedmetadata'].forEach(ev => video.addEventListener(ev, reportState));

        // --- hộp xác nhận kết nối (lần đầu một controller xin điều khiển) ---
        function showNextConfirm() {
            const next = confirmQueue[0];
            if (!next) return show($('castConfirm'), false);
            $('castConfirmText').textContent = `${next.controller_name || 'Một thiết bị'} muốn kết nối. Cho phép?`;
            show($('castConfirm'), true);
            $('castAllow').focus();
        }

        function answerConfirm(allow) {
            const req = confirmQueue.shift();
            if (req) HFCast.api('POST', `/api/cast/requests/${encodeURIComponent(req.controller_id)}`, { allow }).catch(() => null);
            showNextConfirm();
        }

        $('castAllow').addEventListener('click', () => answerConfirm(true));
        $('castDeny').addEventListener('click', () => answerConfirm(false));

        // --- trình duyệt chặn autoplay: lệnh cast đến mà chưa có thao tác người dùng ---
        const prompt = $('castAutoplayPrompt');
        const realPlay = video.play;
        video.play = function () {
            const p = HTMLMediaElement.prototype.play.apply(video, arguments);
            if (p && p.catch) p.catch(err => {
                if (err && err.name === 'NotAllowedError') {
                    show(prompt, true);
                    $('castAutoplayBtn').focus();
                    reportState();
                }
            });
            return p;
        };
        $('castAutoplayBtn').addEventListener('click', () => {
            show(prompt, false);
            video.play().catch(() => null);
        });
        video.addEventListener('playing', () => show(prompt, false));

        // --- lệnh ---
        async function castPlay(cmd) {
            const res = await fetch(`/api/movie/${encodeURIComponent(cmd.slug)}`);
            const data = await res.json();
            if (data.status !== 'success' || !data.episodes || data.episodes.length === 0) return;
            await fetchSavedMovies(); // tiến độ mới nhất của chính thiết bị này
            const eps = data.episodes;
            const row = savedMoviesList.find(i => i.slug === cmd.slug);
            let index = cmd.episode_url ? eps.findIndex(e => e.link_m3u8 === cmd.episode_url) : -1;
            if (index < 0) index = HFUtils.resumeTarget(row, eps).index;
            const url = eps[index].link_m3u8;
            if (cmd.position !== null && cmd.position !== undefined) {
                castStartAt = cmd.position;
            } else if (row && row.last_watched_url === url && row.position > 5 && row.duration && row.position < row.duration - 30) {
                castStartAt = row.position;
            } else {
                castStartAt = 0;
            }
            currentMovie = { slug: data.movie.slug, name: data.movie.name, poster_url: data.movie.poster_url, year: data.movie.year };
            playEpisodesListDirect(eps, index);
        }

        function playing() { return document.body.classList.contains('player-active'); }

        function runCommand(cmd) {
            switch (cmd.type) {
                case 'play': return castPlay(cmd);
                case 'pause': if (playing()) video.pause(); break;
                case 'resume': if (playing()) video.play().catch(() => null); break;
                case 'seek': {
                    if (!playing()) break;
                    const dur = Number.isFinite(video.duration) ? video.duration : null;
                    video.currentTime = cmd.delta !== undefined
                        ? HFCastUtils.seekTarget(video.currentTime, dur, cmd.delta)
                        : HFCastUtils.seekTarget(0, dur, cmd.position);
                    break;
                }
                case 'episode': {
                    if (!playing()) break;
                    const idx = currentPlaybackIndex + cmd.delta;
                    if (idx >= 0 && idx < playbackEpisodes.length) play(playbackEpisodes[idx].link_m3u8, playbackEpisodes[idx].name, idx);
                    break;
                }
                case 'options':
                    ['autoNext', 'autoIntro'].forEach(k => { if (typeof cmd[k] === 'boolean') castOptions[k] = cmd[k]; });
                    break;
                case 'stop': if (playing()) exitPlayer(); break;
            }
        }

        HFCast.on('command', (cmd) => {
            Promise.resolve(runCommand(cmd)).catch(e => console.warn('[cast] lệnh lỗi', cmd.type, e));
            setTimeout(reportState, 150);
        });
        HFCast.on('connect_request', (req) => {
            confirmQueue.push(req);
            if (confirmQueue.length === 1) showNextConfirm();
        });
        HFCast.on('hello', (d) => {
            controllers.clear();
            (d.controllers || []).forEach(c => controllers.set(c.id, c.name));
            updateLabel();
            reportState();
        });
        HFCast.on('controller_joined', (d) => { controllers.set(d.controller_id, d.name); updateLabel(); reportState(); });
        HFCast.on('controller_left', (d) => { controllers.delete(d.controller_id); updateLabel(); });

        // --- cài đặt: công tắc "Cho phép điều khiển từ thiết bị khác" ---
        const panel = $('castSettings');
        const toggle = $('castAllowControl');
        $('castSettingsBtn').addEventListener('click', async () => {
            show(panel, panel.hidden);
            if (!panel.hidden) {
                const res = await HFCast.api('GET', '/api/cast/settings').catch(() => null);
                if (res && res.data) toggle.checked = !!res.data.allow_control;
                toggle.focus();
            }
        });
        toggle.addEventListener('change', () => HFCast.api('PATCH', '/api/cast/settings', { allow_control: toggle.checked }).catch(() => null));
        document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !panel.hidden) show(panel, false); });
    }

    HFCast.start();
    if (HFCast.kind() !== 'phone') initReceiver();
    else initController();
})();
