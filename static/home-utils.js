// Logic thuần cho trang chủ: không đụng DOM, chạy được trong trình duyệt lẫn Node.
(function (root) {
  const ENTITIES = { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'", '&nbsp;': ' ' };

  function stripHtml(s) {
    if (s == null) return '';
    return String(s)
      .replace(/<[^>]*>/g, '')
      .replace(/&(amp|lt|gt|quot|nbsp|#39);/g, (m) => ENTITIES[m])
      .replace(/\s+/g, ' ')
      .trim();
  }

  function heroMeta(m) {
    m = m || {};
    const year = m.year && m.year !== 'undefined' && m.year !== 'null' ? String(m.year) : '';
    const join = (a) => (Array.isArray(a) ? a.filter(Boolean).join(', ') : '');
    return [year, join(m.category), join(m.country)].filter(Boolean).join(' · ');
  }

  function mergeTypePages(pages) {
    const live = pages.filter(Boolean);
    const seen = new Set();
    const items = [];
    const longest = Math.max(0, ...live.map((p) => (p.items || []).length));
    for (let i = 0; i < longest; i++) {
      for (const p of live) {
        const it = (p.items || [])[i];
        if (it && !seen.has(it.slug)) { seen.add(it.slug); items.push(it); }
      }
    }
    const pag = (p) => p.pagination || {};
    const hasMore = live.some((p) => pag(p).currentPage < pag(p).totalPages);
    const counts = live.map((p) => pag(p).totalItems);
    const total = live.length && counts.every((n) => Number.isFinite(n)) ? counts.reduce((a, b) => a + b, 0) : null;
    return { items, hasMore, total };
  }

  function resumeTarget(row, episodes) {
    if (!episodes || episodes.length === 0) return { index: 0, url: null };
    const at = (i) => ({ index: i, url: episodes[i].link_m3u8 });
    const states = (row && row.episode_states) || {};
    let i = episodes.findIndex((e) => states[e.link_m3u8] === 'watching');
    if (i >= 0) return at(i);
    i = episodes.findIndex((e) => states[e.link_m3u8] !== 'watched');
    if (i >= 0 && Object.keys(states).length) return at(i);
    if (row && row.last_watched_url) {
      i = episodes.findIndex((e) => e.link_m3u8 === row.last_watched_url);
      if (i >= 0) return at(i);
    }
    return at(0);
  }

  function splitLibrary(rows) {
    rows = rows || [];
    return {
      continueWatching: rows.filter((r) => r.last_watched_url || r.position > 0),
      saved: rows.filter((r) => r.saved === true),
    };
  }

  function progressPct(row) {
    if (!row || !(row.duration > 0) || !(row.position > 0)) return 0;
    return Math.max(0, Math.min(100, Math.round((row.position / row.duration) * 100)));
  }

  const api = { stripHtml, heroMeta, mergeTypePages, resumeTarget, splitLibrary, progressPct };
  root.HFUtils = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
