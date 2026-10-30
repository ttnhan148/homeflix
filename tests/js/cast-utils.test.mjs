import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const U = createRequire(import.meta.url)('../../static/cast-utils.js');

test('backoffMs doubles and clamps', () => {
  assert.equal(U.backoffMs(0), 2000);
  assert.equal(U.backoffMs(1), 4000);
  assert.equal(U.backoffMs(3), 16000);
  assert.equal(U.backoffMs(4), 30000);
  assert.equal(U.backoffMs(99), 30000);
  assert.equal(U.backoffMs(-1), 2000);
  assert.equal(U.backoffMs(NaN), 2000);
  assert.equal(U.backoffMs(2, 20, 80), 80);
  assert.equal(U.backoffMs(1, 20, 80), 40);
});

test('formatClock', () => {
  assert.equal(U.formatClock(65), '1:05');
  assert.equal(U.formatClock(9), '0:09');
  assert.equal(U.formatClock(3723), '1:02:03');
  assert.equal(U.formatClock(null), '0:00');
  assert.equal(U.formatClock(-5), '0:00');
  assert.equal(U.formatClock(NaN), '0:00');
});

test('isStale', () => {
  assert.equal(U.isStale(null, 1000, 500), true);
  assert.equal(U.isStale(900, 1000, 500), false);
  assert.equal(U.isStale(400, 1000, 500), true);
});

test('acceptState only for the linked target', () => {
  assert.equal(U.acceptState({ target_id: 'tv1' }, 'tv1'), true);
  assert.equal(U.acceptState({ target_id: 'tv2' }, 'tv1'), false);
  assert.equal(U.acceptState({ target_id: 'tv1' }, null), false);
  assert.equal(U.acceptState(null, 'tv1'), false);
});

test('controllerLabel', () => {
  assert.equal(U.controllerLabel('iPhone'), 'iPhone đang kết nối');
  assert.equal(U.controllerLabel(''), 'Thiết bị khác đang kết nối');
  assert.equal(U.controllerLabel(undefined), 'Thiết bị khác đang kết nối');
});

test('remoteView', () => {
  assert.deepEqual(U.remoteView(null), { title: '', episode: '', pct: 0, positionText: '0:00', durationText: '0:00', playing: false });
  const v = U.remoteView({ title: 'Phim', episode: 'Tập 2', position: 50, duration: 200, playing: true });
  assert.deepEqual(v, { title: 'Phim', episode: 'Tập 2', pct: 25, positionText: '0:50', durationText: '3:20', playing: true });
  assert.equal(U.remoteView({ position: 999, duration: 100 }).pct, 100);
  assert.equal(U.remoteView({ position: 5, duration: null }).pct, 0);
});

test('seekTarget clamps', () => {
  assert.equal(U.seekTarget(5, 100, -10), 0);
  assert.equal(U.seekTarget(95, 100, 10), 100);
  assert.equal(U.seekTarget(5, null, 10), 15);
  assert.equal(U.seekTarget(5, null, -10), 0);
  assert.equal(U.seekTarget(50, 100, 10), 60);
});
