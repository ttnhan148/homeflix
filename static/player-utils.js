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

  const api = { DEFAULT_PREFS, loadPrefs, savePrefs, showSkipIntro, introTarget, hasNextEpisode, countdownLabel };
  root.HFPlayerUtils = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
