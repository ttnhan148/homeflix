import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const U = createRequire(import.meta.url)('../../static/home-utils.js');

test('heroMeta joins year, categories, countries', () => {
  assert.equal(U.heroMeta({ year: 2026, category: ['Hành Động', 'Phiêu Lưu'], country: ['Mỹ'] }), '2026 · Hành Động, Phiêu Lưu · Mỹ');
});
test('heroMeta skips empty parts', () => {
  assert.equal(U.heroMeta({ year: '2026', category: [], country: ['Mỹ'] }), '2026 · Mỹ');
  assert.equal(U.heroMeta({}), '');
  assert.equal(U.heroMeta({ year: 'undefined' }), '');
});

test('stripHtml removes tags and decodes entities', () => {
  assert.equal(U.stripHtml('<p>A &amp; B</p><img src=x onerror=alert(1)>'), 'A & B');
  assert.equal(U.stripHtml('  a &nbsp; &lt;b&gt; &quot;q&quot; &#39;s&#39;  '), 'a <b> "q" \'s\'');
  assert.equal(U.stripHtml(null), '');
});

const mv = (slug) => ({ slug });
const pg = (items, cur = 1, total = 2, n) => ({ items, pagination: { currentPage: cur, totalPages: total, totalItems: n } });

test('mergeTypePages interleaves, dedupes by slug, sums totals', () => {
  const r = U.mergeTypePages([pg([mv('a'), mv('b'), mv('c')], 1, 2, 10), pg([mv('x'), mv('a')], 1, 1, 5)]);
  assert.deepEqual(r.items.map(i => i.slug), ['a', 'x', 'b', 'c']);
  assert.equal(r.hasMore, true);
  assert.equal(r.total, 15);
});
test('mergeTypePages skips null pages; total null when a count is missing; hasMore false at last page', () => {
  const r = U.mergeTypePages([null, pg([mv('a')], 2, 2, undefined)]);
  assert.deepEqual(r.items.map(i => i.slug), ['a']);
  assert.equal(r.hasMore, false);
  assert.equal(r.total, null);
  assert.deepEqual(U.mergeTypePages([null, null]), { items: [], hasMore: false, total: null });
});

const eps = [{ name: 'T1', link_m3u8: 'u1' }, { name: 'T2', link_m3u8: 'u2' }, { name: 'T3', link_m3u8: 'u3' }];
test('resumeTarget prefers watching, then first unwatched, then last_watched_url, then 0', () => {
  assert.deepEqual(U.resumeTarget({ episode_states: { u1: 'watched', u2: 'watching' } }, eps), { index: 1, url: 'u2' });
  assert.deepEqual(U.resumeTarget({ episode_states: { u1: 'watched', u2: 'watched' } }, eps), { index: 2, url: 'u3' });
  assert.deepEqual(U.resumeTarget({ episode_states: { u1: 'watched', u2: 'watched', u3: 'watched' }, last_watched_url: 'u2' }, eps), { index: 1, url: 'u2' });
  assert.deepEqual(U.resumeTarget({ episode_states: {}, last_watched_url: 'zzz' }, eps), { index: 0, url: 'u1' });
  assert.deepEqual(U.resumeTarget(null, eps), { index: 0, url: 'u1' });
  assert.deepEqual(U.resumeTarget({}, []), { index: 0, url: null });
});

test('splitLibrary separates continue-watching and saved', () => {
  const rows = [
    { slug: 'hist', saved: false, last_watched_url: 'u', position: 5 },
    { slug: 'both', saved: true, last_watched_url: '', position: 12 },
    { slug: 'only-saved', saved: true, last_watched_url: '', position: null },
  ];
  const r = U.splitLibrary(rows);
  assert.deepEqual(r.continueWatching.map(x => x.slug), ['hist', 'both']);
  assert.deepEqual(r.saved.map(x => x.slug), ['both', 'only-saved']);
});

test('progressPct clamps and handles missing', () => {
  assert.equal(U.progressPct({ position: 50, duration: 200 }), 25);
  assert.equal(U.progressPct({ position: 50, duration: 0 }), 0);
  assert.equal(U.progressPct({ position: 999, duration: 200 }), 100);
  assert.equal(U.progressPct({}), 0);
});
