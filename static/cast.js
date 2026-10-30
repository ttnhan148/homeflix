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

    HFCast.start();
})();
