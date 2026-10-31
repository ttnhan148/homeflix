import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const U = createRequire(import.meta.url)('../../static/home-utils.js');

test('heroMeta keeps year, first category, first country', () => {
  assert.equal(U.heroMeta({ year: 2026, category: ['Hành Động', 'Phiêu Lưu'], country: ['Mỹ', 'Anh'] }), '2026 · Hành Động · Mỹ');
});

test('shortEpisode shortens finished series and keeps the rest', () => {
  assert.equal(U.shortEpisode('Hoàn Tất (8/8)'), 'Đủ 8 tập');
  assert.equal(U.shortEpisode('Hoàn tất (40/40)'), 'Đủ 40 tập');
  assert.equal(U.shortEpisode('Hoàn Tất (12/16)'), 'Tập 12/16');
  assert.equal(U.shortEpisode('Tập 15'), 'Tập 15');
  assert.equal(U.shortEpisode(' Full '), 'Full');
  assert.equal(U.shortEpisode(null), '');
});

test('cardMeta joins short episode and year, skipping blanks', () => {
  assert.equal(U.cardMeta({ episode_current: 'Hoàn Tất (8/8)', year: 2021 }), 'Đủ 8 tập · 2021');
  assert.equal(U.cardMeta({ episode_current: 'Tập 7', year: '2026' }), 'Tập 7 · 2026');
  assert.equal(U.cardMeta({ year: 'undefined' }), '');
});

test('epShort turns "Tập 09" into "9" and leaves other names alone', () => {
  assert.equal(U.epShort('Tập 09'), '9');
  assert.equal(U.epShort('Tập 1.5'), '1.5');
  assert.equal(U.epShort('tập 120'), '120');
  assert.equal(U.epShort('Full'), 'Full');
  assert.equal(U.epShort('Tập Đặc Biệt'), 'Tập Đặc Biệt');
});

test('continueMeta shows episode and minutes left', () => {
  assert.equal(U.continueMeta({ last_watched_episode: 'Tập 09', position: 1080, duration: 2700 }), 'Tập 9 · còn 27 phút');
  assert.equal(U.continueMeta({ last_watched_episode: 'Full', position: 3600, duration: 6060 }), 'Còn 41 phút');
  assert.equal(U.continueMeta({ last_watched_episode: 'Tập 2', position: 2690, duration: 2700 }), 'Tập 2 · sắp hết');
  assert.equal(U.continueMeta({ last_watched_episode: 'Tập 2' }), 'Tập 2');
  assert.equal(U.continueMeta({}), '');
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

test('swipeAction: kéo đủ xa sang phải là phát, sang trái là bỏ', () => {
  assert.equal(U.swipeAction(130, 400), 'play');
  assert.equal(U.swipeAction(-130, 400), 'remove');
  assert.equal(U.swipeAction(60, 400), null);
  assert.equal(U.swipeAction(-60, 400), null);
  assert.equal(U.swipeAction(0, 400), null);
});

test('swipeAction: ngưỡng theo bề rộng, giữ trong 72..120px', () => {
  assert.equal(U.swipeAction(100, 300), 'play');   // 30% của 300 = 90
  assert.equal(U.swipeAction(85, 300), null);
  assert.equal(U.swipeAction(75, 200), 'play');    // 30% = 60, nâng lên 72
  assert.equal(U.swipeAction(-115, 1000), null);   // 30% = 300, hạ xuống 120
  assert.equal(U.swipeAction(-121, 1000), 'remove');
});

test('swipeAction: vuốt nhanh ngắn vẫn tính, vuốt chậm ngắn thì không', () => {
  assert.equal(U.swipeAction(50, 400, 0.8), 'play');
  assert.equal(U.swipeAction(-50, 400, -0.8), 'remove');
  assert.equal(U.swipeAction(50, 400, 0.2), null);
  assert.equal(U.swipeAction(20, 400, 1.5), null);   // quá ngắn: là chạm lệch tay
  assert.equal(U.swipeAction(50, 400, -0.8), null);  // hất ngược chiều kéo
});

test('heroNext đi tới thẻ kế, hết hàng thì quay về đầu', () => {
  assert.equal(U.heroNext(0, 10), 1);
  assert.equal(U.heroNext(8, 10), 9);
  assert.equal(U.heroNext(9, 10), 0);
  assert.equal(U.heroNext(4, 10, true), 0);  // đã cuộn tới cuối (nhiều thẻ hiện cùng lúc)
  assert.equal(U.heroNext(0, 1), 0);
});
