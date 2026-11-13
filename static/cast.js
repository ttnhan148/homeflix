// Client-side cast: heartbeat, SSE event stream with reconnect, API calls. The UI is in later sections of this file.
(function () {
    const defaults = { heartbeatMs: 15000, reconnectBaseMs: 2000, reconnectMaxMs: 30000, stateMs: 2000, silenceMs: 6000 };
    const EVENTS = ['hello', 'connect_request', 'connect_result', 'controller_joined', 'controller_left', 'command', 'state', 'unlinked'];

    const HFCast = window.HFCast = {
        config: Object.assign({}, defaults, window.__HF_CAST_CONFIG || {}),
        started: false,
        linked: null,       // {id, name} of the TV being controlled (iPhone)
        attempt: 0,         // consecutive reconnects without receiving hello
        es: null,
        wantEvents: false,  // event stream needed (TV/desktop always; iPhone when the sheet is open or linked)
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
        try { data = await res.json(); } catch (e) { /* empty body */ }
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

    // iOS drops background connections on screen lock: reconnect when the page becomes visible and signal to fetch fresh state
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

    // ---------- Shared: Play button label, routing Play to the TV ----------
    function playLabel() { return HFCast.linked ? 'Phát lên TV' : 'Phát'; }

    window.refreshPlayLabels = function () {
        document.querySelectorAll('#hero .hero-btn-play').forEach(b => { b.textContent = playLabel(); });
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

    // Returns true when a play command was sent to the TV (blocks local playback). Result arrives later: on error, notify and fall back to playing on the iPhone.
    HFCast.markUnresponsive = HFCast.markUnresponsive || function () {};
    // Send a command to a TV; any failure marks it unresponsive (looked up at call time, initController may replace it)
    function postCommand(targetId, cmd) {
        HFCast.api('POST', `/api/cast/${encodeURIComponent(targetId)}/command`, cmd)
            .then(res => { if (res.status !== 200) HFCast.markUnresponsive(); })
            .catch(() => HFCast.markUnresponsive());
    }
    window.__castRoute = function (slug, episodeUrl) {
        const target = HFCast.linked;
        if (!target) return false;
        postCommand(target.id, { type: 'play', slug, episode_url: episodeUrl || null });
        return true;
    };

    // ---------- Controller side (iPhone): cast button, "Phát lên" sheet, connection ----------
    function initController() {
        const $ = (id) => document.getElementById(id);
        const STORE_KEY = 'hf_cast_last_tv';
        const inflight = new Set();
        let devices = [];
        HFCast.pending = null;   // {id, name} of the TV awaiting permission

        function remember(dev) {
            try { localStorage.setItem(STORE_KEY, JSON.stringify({ id: dev.id, name: dev.name })); } catch (e) { /* ignore */ }
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

        HFCast.markUnresponsive = function (message) {
            HFCast.notify(typeof message === 'string' ? message : 'TV không phản hồi');
            if (HFCast.linked) HFCast.api('DELETE', '/api/cast/link').catch(() => null);   // TV drops the "đang kết nối" label
            HFCast.linked = null;
            HFCast.pending = null;
            releaseEventsIfIdle();
            refreshUi();
            HFCast.emit('link_lost', {});
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
            HFCast.emit('link_lost', {});
        };

        // Server-side link lives in memory: lost when the server restarts or the iPhone stays silent for over 40 seconds.
        // Whenever the event stream (reconnect) reports hello or the page becomes visible again, re-link so the TV reports state again.
        async function restoreLink() {
            if (!HFCast.linked) return;
            const target = HFCast.linked;
            const res = await HFCast.api('POST', `/api/cast/${encodeURIComponent(target.id)}/connect`).catch(() => null);
            if (!res || !HFCast.linked || HFCast.linked.id !== target.id) return;
            if (res.status !== 200) HFCast.markUnresponsive();
        }
        HFCast.on('hello', restoreLink);
        HFCast.on('unlinked', (d) => {
            if (HFCast.linked && d.target_id === HFCast.linked.id) HFCast.markUnresponsive('TV đã tắt điều khiển từ xa');
        });

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

        // --- Mini bar and control panel ---
        let remote = null;      // latest playback state of the linked TV
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
            if (remote && typeof remote.autoNext === 'boolean') $('castAutoNext').checked = remote.autoNext;
            if (remote && typeof remote.autoIntro === 'boolean') $('castAutoIntro').checked = remote.autoIntro;
        }

        function applyState(state) {
            remote = state;
            lastStateTs = Date.now();
            renderRemote();
        }

        let refreshing = 0;   // fetching fresh state: the silence watchdog pauses until there's a result

        async function refreshRemote(strict) {
            if (!HFCast.linked) return;
            refreshing += 1;
            try {
                const res = await HFCast.api('GET', `/api/cast/state/${encodeURIComponent(HFCast.linked.id)}`).catch(() => null);
                if (res && res.status === 200 && res.data && res.data.target_id) applyState(res.data);
                else if (strict) HFCast.markUnresponsive();   // after the iPhone wakes up: no state = TV is gone
            } finally {
                refreshing -= 1;
                lastStateTs = Date.now();
            }
        }

        HFCast.on('state', (state) => {
            if (HFCastUtils.acceptState(state, HFCast.linked && HFCast.linked.id)) applyState(state);
        });
        HFCast.on('linked', () => { remote = null; lastStateTs = null; renderRemote(); refreshRemote(false); });
        HFCast.on('link_lost', () => { remote = null; renderRemote(); });
        HFCast.on('resume', () => {
            lastStateTs = Date.now();   // timers keep running after sleep: don't count the sleep period as TV silence
            restoreLink();
            refreshRemote(true);
        });

        // Playing but the TV has been silent too long: treat as disconnected (a stopped TV sends no state, so it doesn't count)
        setInterval(() => {
            if (!refreshing && HFCast.linked && remote && remote.playing && HFCastUtils.isStale(lastStateTs, Date.now(), HFCast.config.silenceMs)) {
                HFCast.markUnresponsive();
            }
        }, 200);

        function sendCmd(cmd) {
            if (HFCast.linked) postCommand(HFCast.linked.id, cmd);
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

        // Reconnect to the last TV if it's still online. Silently: no sheet, no notification, no pending state.
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

    // ---------- Receiver side (TV, desktop): receive commands, ask for confirmation, report state ----------
    function initReceiver() {
        const $ = (id) => document.getElementById(id);
        const controllers = new Map();   // id -> name
        const confirmQueue = [];

        function show(node, on) { node.hidden = !on; }

        function updateLabel() {
            const label = $('castLabel');
            if (controllers.size === 0) return show(label, false);
            label.textContent = HFCastUtils.controllerLabel([...controllers.values()].join(', '));
            show(label, true);
        }

        // --- playback state ---
        function currentState() {
            const active = document.body.classList.contains('player-active') && playbackEpisodes.length > 0 && currentPlaybackIndex >= 0;
            const prefs = HFPlayerAuto.getPrefs();
            const options = { autoNext: prefs.autoNext, autoIntro: prefs.autoIntro };
            if (!active) return { slug: null, title: null, episode: null, position: null, duration: null, playing: false, ...options };
            const ep = playbackEpisodes[currentPlaybackIndex];
            return {
                slug: currentMovie && currentMovie.slug ? currentMovie.slug : null,
                title: currentMovie && currentMovie.name ? String(currentMovie.name).slice(0, 200) : null,
                episode: ep && ep.name ? String(ep.name).slice(0, 200) : null,
                position: Number.isFinite(video.currentTime) ? video.currentTime : null,
                duration: Number.isFinite(video.duration) ? video.duration : null,
                playing: !video.paused && !video.ended,
                ...options,
            };
        }

        function reportState() {
            if (controllers.size === 0) return;
            HFCast.api('POST', '/api/cast/state', currentState()).catch(() => null);
        }

        setInterval(() => { if (!video.paused) reportState(); }, HFCast.config.stateMs);
        ['play', 'pause', 'ended', 'loadedmetadata'].forEach(ev => video.addEventListener(ev, reportState));

        // --- connection confirmation dialog (first time a controller asks to take control) ---
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

        // --- browser blocks autoplay: cast command arrives before any user interaction ---
        const prompt = $('castAutoplayPrompt');
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

        // --- commands ---
        async function castPlay(cmd) {
            const res = await fetch(`/api/movie/${encodeURIComponent(cmd.slug)}`);
            const data = await res.json();
            if (data.status !== 'success' || !data.episodes || data.episodes.length === 0) return;
            await fetchSavedMovies(); // latest progress of this device itself
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
                    HFPlayerAuto.setPrefs(['autoNext', 'autoIntro'].reduce((o, k) => (typeof cmd[k] === 'boolean' ? Object.assign(o, { [k]: cmd[k] }) : o), {}));
                    break;
                case 'stop': if (playing()) exitPlayer(); break;
            }
        }

        HFCast.on('command', (cmd) => {
            Promise.resolve(runCommand(cmd)).catch(e => console.warn('[cast] lệnh lỗi', cmd.type, e));
            setTimeout(reportState, 150);
        });
        function enqueueRequest(req) {
            if (confirmQueue.some(r => r.controller_id === req.controller_id)) return;   // already asked: don't duplicate the dialog
            confirmQueue.push(req);
            if (confirmQueue.length === 1) showNextConfirm();
        }
        HFCast.on('connect_request', enqueueRequest);
        HFCast.on('hello', (d) => {
            controllers.clear();
            (d.controllers || []).forEach(c => controllers.set(c.id, c.name));
            updateLabel();
            reportState();
            (d.pending || []).forEach(enqueueRequest);   // TV reloaded while a request is still pending: ask again
        });
        HFCast.on('controller_joined', (d) => { controllers.set(d.controller_id, d.name); updateLabel(); reportState(); });
        HFCast.on('controller_left', (d) => { controllers.delete(d.controller_id); updateLabel(); });
        // the "Cho phép điều khiển từ xa" toggle lives on the Settings page (static/settings.js)
    }

    HFCast.start();
    if (HFCast.kind() !== 'phone') initReceiver();
    else initController();
})();
