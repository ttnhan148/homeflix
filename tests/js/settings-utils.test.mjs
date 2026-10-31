import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const U = createRequire(import.meta.url)('../../static/settings-utils.js');

test('formatValue', () => {
  assert.equal(U.formatValue({ value: 10, unit: 'GB' }), '10 GB');
  assert.equal(U.formatValue({ value: 4, unit: '' }), '4');
});
test('stepValue clamps', () => {
  const it = { min: 5, max: 120, step: 5 };
  assert.equal(U.stepValue(it, 10, 1), 15); assert.equal(U.stepValue(it, 120, 1), 120); assert.equal(U.stepValue(it, 5, -1), 5);
});
test('groupDevices puts own household first', () => {
  const g = U.groupDevices([{ household: 'a', id: 1 }, { household: 'b', id: 2, is_self: true }, { household: 'a', id: 3 }]);
  assert.deepEqual(g.map(x => [x.household, x.devices.map(d => d.id)]), [['b', [2]], ['a', [1, 3]]]);
});
test('lastSeenText', () => {
  assert.equal(U.lastSeenText(1000, 1030), 'vừa xong'); assert.equal(U.lastSeenText(0, 125), '2 phút trước');
  assert.equal(U.lastSeenText(0, 7200), '2 giờ trước'); assert.equal(U.lastSeenText(0, 172800), '2 ngày trước');
});
test('countdownText', () => { assert.equal(U.countdownText(700, 100), '10:00'); assert.equal(U.countdownText(100, 200), '0:00'); });
test('gridMove', () => {
  assert.deepEqual(U.gridMove([1, 3, 1], 1, 2, 'ArrowDown'), [2, 0]);
  assert.deepEqual(U.gridMove([1, 3], 0, 0, 'ArrowUp'), [0, 0]);
  assert.deepEqual(U.gridMove([1, 3], 1, 2, 'ArrowRight'), [1, 2]);
  assert.deepEqual(U.gridMove([1, 3], 1, 1, 'ArrowLeft'), [1, 0]);
  assert.deepEqual(U.gridMove([1, 3], 1, 1, 'Enter'), [1, 1]);
});
