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

  function onEpisodeStart() {
    cancelCountdown();
    introHandled = false;
    $('skipIntroBtn').hidden = true;
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
  }

  root.HFPlayerAuto = { init, getPrefs, setPrefs, onEnded, onEpisodeStart, cancelCountdown, skipIntro };
})(typeof globalThis !== 'undefined' ? globalThis : this);
