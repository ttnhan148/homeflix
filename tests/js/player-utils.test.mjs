import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const U = createRequire(import.meta.url)('../../static/player-utils.js');

const mem = (init) => {
  const d = { ...(init || {}) };
  return { getItem: (k) => (k in d ? d[k] : null), setItem: (k, v) => { d[k] = String(v); } };
};
const boom = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } };
const P = U.DEFAULT_PREFS;

test('defaults', () => {
  assert.deepEqual(P, { autoNext: true, autoIntro: false, introSeconds: 85 });
});

test('loadPrefs: empty, corrupt, blocked give defaults', () => {
  assert.deepEqual(U.loadPrefs(mem()), P);
  assert.deepEqual(U.loadPrefs(mem({ hf_player_prefs: '{nope' })), P);
  assert.deepEqual(U.loadPrefs(boom), P);
});

test('loadPrefs: introSeconds out of 10-300 falls back to 85', () => {
  assert.equal(U.loadPrefs(mem({ hf_player_prefs: '{"introSeconds":5}' })).introSeconds, 85);
  assert.equal(U.loadPrefs(mem({ hf_player_prefs: '{"introSeconds":9999}' })).introSeconds, 85);
  assert.equal(U.loadPrefs(mem({ hf_player_prefs: '{"introSeconds":60}' })).introSeconds, 60);
});

test('savePrefs round-trips and survives blocked storage', () => {
  const s = mem();
  const saved = U.savePrefs(s, { autoNext: false, introSeconds: 60 });
  assert.deepEqual(saved, { autoNext: false, autoIntro: false, introSeconds: 60 });
  assert.deepEqual(U.loadPrefs(s), saved);
  assert.deepEqual(U.savePrefs(boom, { autoIntro: true }), { ...P, autoIntro: true });
});

test('showSkipIntro', () => {
  assert.equal(U.showSkipIntro(10, 1400, P), true);
  assert.equal(U.showSkipIntro(95, 1400, P), false);
  assert.equal(U.showSkipIntro(10, 60, P), false);
  assert.equal(U.showSkipIntro(10, NaN, P), false);
  assert.equal(U.showSkipIntro(90, 1400, { ...P, introSeconds: 300 }), false);
  assert.equal(U.showSkipIntro(70, 1400, P), true);
  assert.equal(U.showSkipIntro(86, 1400, P), false);
});

test('introTarget clamps to duration - 5', () => {
  assert.equal(U.introTarget(1400, P), 85);
  assert.equal(U.introTarget(70, P), 65);
});

test('hasNextEpisode', () => {
  assert.equal(U.hasNextEpisode(0, 1), false);
  assert.equal(U.hasNextEpisode(0, 2), true);
  assert.equal(U.hasNextEpisode(1, 2), false);
  assert.equal(U.hasNextEpisode(-1, 5), false);
});

test('countdownLabel', () => {
  assert.equal(U.countdownLabel(7), 'Tập kế sau 7 giây');
});
