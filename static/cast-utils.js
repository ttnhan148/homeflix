// Logic thuần cho cast: không đụng DOM, chạy được trong trình duyệt lẫn Node.
(function (root) {
  function backoffMs(attempt, base = 2000, max = 30000) {
    if (!Number.isFinite(attempt) || attempt < 0) return base;
    return Math.min(max, base * Math.pow(2, attempt));
  }

  function formatClock(sec) {
    if (!Number.isFinite(sec) || sec < 0) return '0:00';
    const s = Math.floor(sec);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const ss = String(s % 60).padStart(2, '0');
    return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
  }

  function isStale(lastTs, now, windowMs) {
    return lastTs == null || now - lastTs > windowMs;
  }

  function acceptState(state, linkedTargetId) {
    return !!(state && linkedTargetId && state.target_id === linkedTargetId);
  }

  function controllerLabel(name) {
    return `${name || 'Thiết bị khác'} đang kết nối`;
  }

  function remoteView(state) {
    const s = state || {};
    const pct = s.duration > 0 && s.position > 0 ? Math.max(0, Math.min(100, Math.round((s.position / s.duration) * 100))) : 0;
    return {
      title: s.title || '',
      episode: s.episode || '',
      pct,
      positionText: formatClock(s.position),
      durationText: formatClock(s.duration),
      playing: !!s.playing,
    };
  }

  function seekTarget(position, duration, delta) {
    const target = Math.max(0, (position || 0) + delta);
    return duration > 0 ? Math.min(duration, target) : target;
  }

  const api = { backoffMs, formatClock, isStale, acceptState, controllerLabel, remoteView, seekTarget };
  root.HFCastUtils = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
