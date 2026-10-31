// Logic thuần cho tự qua tập và bỏ qua mở đầu: không đụng DOM, chạy được trong trình duyệt lẫn Node.
(function (root) {
  const KEY = 'hf_player_prefs';
  const DEFAULT_PREFS = { autoNext: true, autoIntro: false, introSeconds: 85 };
  const INTRO_WINDOW = 90; // nút chỉ hiện trong 90 giây đầu của tập

  function normalize(raw) {
    const r = raw && typeof raw === 'object' ? raw : {};
    const sec = r.introSeconds;
    return {
      autoNext: typeof r.autoNext === 'boolean' ? r.autoNext : DEFAULT_PREFS.autoNext,
      autoIntro: typeof r.autoIntro === 'boolean' ? r.autoIntro : DEFAULT_PREFS.autoIntro,
      introSeconds: Number.isInteger(sec) && sec >= 10 && sec <= 300 ? sec : DEFAULT_PREFS.introSeconds,
    };
  }

  function loadPrefs(storage) {
    try { return normalize(JSON.parse(storage.getItem(KEY))); } catch (e) { return normalize(null); }
  }

  function savePrefs(storage, prefs) {
    const clean = normalize(prefs);
    try { storage.setItem(KEY, JSON.stringify(clean)); } catch (e) { /* storage bị chặn: giữ trong bộ nhớ */ }
    return clean;
  }

  function showSkipIntro(currentTime, duration, prefs) {
    if (!Number.isFinite(duration) || !Number.isFinite(currentTime)) return false;
    return currentTime < INTRO_WINDOW && currentTime < prefs.introSeconds && prefs.introSeconds < duration - 5;
  }

  function introTarget(duration, prefs) {
    return Math.min(prefs.introSeconds, duration - 5);
  }

  function hasNextEpisode(index, count) {
    return index >= 0 && index < count - 1;
  }

  function countdownLabel(secondsLeft) {
    return 'Tập kế sau ' + secondsLeft + ' giây';
  }

  // Nguồn video: mặc định qua proxy (có cache, tải trước). Khi proxy không lấy được nguồn
  // (CDN chặn IP máy chủ ngoài Việt Nam), thiết bị nhớ phát thẳng link gốc trong 12 giờ.
  const DIRECT_KEY = 'hf_direct_stream_until';
  const DIRECT_TTL_MS = 12 * 3600 * 1000;

  function proxyUrl(url) {
    return '/proxy/m3u8?url=' + encodeURIComponent(url);
  }

  function isProxied(src) {
    return String(src).indexOf('/proxy/m3u8?') === 0;
  }

  function streamSource(storage, url, now) {
    let until = 0;
    try { until = Number(storage.getItem(DIRECT_KEY)) || 0; } catch (e) { until = 0; }
    return until > now ? url : proxyUrl(url);
  }

  function rememberDirect(storage, now) {
    try { storage.setItem(DIRECT_KEY, String(now + DIRECT_TTL_MS)); } catch (e) { /* storage bị chặn */ }
  }

  // Cấu hình hls.js dùng chung cho phim và HomeTV
  function hlsConfig(isTV, proxied) {
    return {
      maxBufferLength: isTV ? 20 : 30,
      maxMaxBufferLength: isTV ? 120 : 600,
      manifestLoadingMaxRetry: proxied ? 1 : 5,
      lowLatencyMode: !isTV,
    };
  }

  const api = { hlsConfig, DEFAULT_PREFS, loadPrefs, savePrefs, showSkipIntro, introTarget, hasNextEpisode, countdownLabel,
    DIRECT_TTL_MS, proxyUrl, isProxied, streamSource, rememberDirect };
  root.HFPlayerUtils = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
