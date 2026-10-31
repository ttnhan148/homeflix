import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const U = createRequire(import.meta.url)('../../static/hometv-utils.js');

const ch = (id, name, category, extra) => ({ id, name, category, favorite: false, recent_at: null, ...extra });

test('fold strips Vietnamese diacritics, case and extra spaces', () => {
  assert.equal(U.fold('  Thể  Thao HD '), 'the thao hd');
  assert.equal(U.fold('Đài Truyền hình'), 'dai truyen hinh');
});

test('buildChips: order, hides empty groups, unknown category folds into Unknown', () => {
  const list = [ch(1, 'a', 'Sports'), ch(2, 'b', 'National TV'), ch(3, 'c', 'Weird'), ch(4, 'd', 'Unknown')];
  assert.deepEqual(U.buildChips(list).map((c) => c.key), ['National TV', 'Sports', 'Unknown']);
});

test('buildChips: favorites and recent chips come first only when they have channels', () => {
  const list = [ch(1, 'a', 'News', { favorite: true }), ch(2, 'b', 'News', { recent_at: 5 })];
  assert.deepEqual(U.buildChips(list).map((c) => c.key), ['favorites', 'recent', 'News']);
  assert.deepEqual(U.buildChips([ch(1, 'a', 'News')]).map((c) => c.key), ['News']);
});

test('filterChannels: chip, recent order, blank query, diacritic-insensitive query', () => {
  const list = [ch(1, 'VTV1', 'National TV', { recent_at: 1, favorite: true }), ch(2, 'Đài Thể Thao', 'Sports', { recent_at: 9 }),
    ch(3, 'Weird', 'Odd')];
  assert.equal(U.filterChannels(list, { chip: '', query: '   ' }).length, 3);
  assert.deepEqual(U.filterChannels(list, { chip: 'Sports' }).map((c) => c.id), [2]);
  assert.deepEqual(U.filterChannels(list, { chip: 'Unknown' }).map((c) => c.id), [3]);
  assert.deepEqual(U.filterChannels(list, { chip: 'recent' }).map((c) => c.id), [2, 1]);
  assert.deepEqual(U.filterChannels(list, { chip: 'favorites' }).map((c) => c.id), [1]);
  assert.deepEqual(U.filterChannels(list, { chip: '', query: 'dai the thao' }).map((c) => c.id), [2]);
  assert.deepEqual(U.filterChannels(list, { chip: 'Sports', query: 'vtv' }), []);
});

test('stepChannel wraps around and returns null for an unknown id', () => {
  const list = [ch(1, 'a'), ch(2, 'b'), ch(3, 'c')];
  assert.equal(U.stepChannel(list, 3, 1), 1);
  assert.equal(U.stepChannel(list, 1, -1), 3);
  assert.equal(U.stepChannel(list, 2, 1), 3);
  assert.equal(U.stepChannel(list, 9, 1), null);
});

test('initial: first letter upper-cased, ? when empty', () => {
  assert.equal(U.initial('đài số 1'), 'Đ');
  assert.equal(U.initial('  vtv'), 'V');
  assert.equal(U.initial(''), '?');
});
