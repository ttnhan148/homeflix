// Nối DOM cho tự qua tập (đếm ngược 10 giây, có nút Hủy). Cài đặt lưu theo thiết bị trong localStorage.
(function (root) {
  const U = root.HFPlayerUtils;
  const cfg = Object.assign({ tickMs: 1000, countdownSeconds: 10 }, root.__HF_PLAYER_CONFIG || {});
  let video = null, getEpisodes = null, playNext = null, storage = null;
  let prefs = U.DEFAULT_PREFS;
  let timer = null, secondsLeft = 0;
  let introHandled = false; // đã tự nhảy intro trong tập này

  const $ = (id) => document.getElementById(id);

  function safeStorage() {
    try { return root.localStorage; } catch (e) { return null; }
  }

  function getPrefs() { return prefs; }

  function setPrefs(partial) {
    prefs = U.savePrefs(storage, Object.assign({}, prefs, partial));
    document.dispatchEvent(new CustomEvent('hf-prefs', { detail: prefs }));
    return prefs;
  }

  function showOverlay(on) {
    const el = $('autoNextOverlay');
    if (el) el.hidden = !on;
  }

  function cancelCountdown() {
    if (timer !== null) { clearInterval(timer); timer = null; }
    showOverlay(false);
  }

  function finishCountdown() {
    cancelCountdown();
    playNext();
  }

  function onEnded() {
    cancelCountdown();
    const ep = getEpisodes();
    if (!prefs.autoNext || !U.hasNextEpisode(ep.index, ep.list.length)) return;
    secondsLeft = cfg.countdownSeconds;
    $('autoNextText').textContent = U.countdownLabel(secondsLeft);
    showOverlay(true);
    timer = setInterval(() => {
      secondsLeft -= 1;
      if (secondsLeft <= 0) { finishCountdown(); return; }
      $('autoNextText').textContent = U.countdownLabel(secondsLeft);
    }, cfg.tickMs);
  }

  function skipIntro() {
    if (!U.showSkipIntro(video.currentTime, video.duration, prefs)) return false;
    video.currentTime = U.introTarget(video.duration, prefs);
    updateSkipButton();
    return true;
  }

  function updateSkipButton() {
    const show = U.showSkipIntro(video.currentTime, video.duration, prefs);
    $('skipIntroBtn').hidden = !show;
    if (show && prefs.autoIntro && !introHandled) {
      introHandled = true;
      skipIntro();
    }
  }

  function closePrefsPanel() { const p = $('playerPrefs'); if (p) p.hidden = true; }

  function onEpisodeStart() {
    cancelCountdown();
    introHandled = false;
    $('skipIntroBtn').hidden = true;
  }

  function initPrefsPanel() {
    const panel = $('playerPrefs'), next = $('prefAutoNext'), intro = $('prefAutoIntro'), secs = $('prefIntroSeconds');
    const sync = () => { next.checked = prefs.autoNext; intro.checked = prefs.autoIntro; secs.value = prefs.introSeconds; };
    sync();
    document.addEventListener('hf-prefs', sync);
    $('playerPrefsBtn').addEventListener('click', () => { panel.hidden = !panel.hidden; if (!panel.hidden) next.focus(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !panel.hidden) panel.hidden = true; });
    next.addEventListener('change', () => setPrefs({ autoNext: next.checked }));
    intro.addEventListener('change', () => setPrefs({ autoIntro: intro.checked }));
    secs.addEventListener('change', () => {
      const n = Number(secs.value);
      if (secs.value.trim() !== '' && Number.isInteger(n) && n >= 10 && n <= 300) setPrefs({ introSeconds: n });
      else sync();
    });
  }

  function init(opts) {
    video = opts.video;
    getEpisodes = opts.getEpisodes;
    playNext = opts.playNext;
    storage = opts.storage || safeStorage();
    prefs = U.loadPrefs(storage);
    video.addEventListener('seeking', cancelCountdown);
    video.addEventListener('play', cancelCountdown);
    $('autoNextCancel').addEventListener('click', cancelCountdown);
    $('autoNextNow').addEventListener('click', finishCountdown);
    $('skipIntroBtn').addEventListener('click', skipIntro);
    ['timeupdate', 'loadedmetadata', 'seeked'].forEach((ev) => video.addEventListener(ev, updateSkipButton));
    document.addEventListener('hf-prefs', updateSkipButton);
    initPrefsPanel();
  }

  root.HFPlayerAuto = { init, getPrefs, setPrefs, onEnded, onEpisodeStart, cancelCountdown, skipIntro, closePrefsPanel };
})(typeof globalThis !== 'undefined' ? globalThis : this);
