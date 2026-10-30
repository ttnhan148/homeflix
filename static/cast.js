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
})();
