// Pure logic for HomeTV: no DOM access, runs in both the browser and Node.
(function (root) {
  const CATEGORY_ORDER = ['National TV', 'News', 'Sports', 'Kids', 'Movies', 'Music', 'Entertainment',
    'International', 'Local TV', 'General', 'Unknown'];
  const CATEGORY_LABELS = {
    'National TV': 'Quốc gia', News: 'Tin tức', Sports: 'Thể thao', Kids: 'Thiếu nhi', Movies: 'Phim',
    Music: 'Âm nhạc', Entertainment: 'Giải trí', International: 'Quốc tế', 'Local TV': 'Địa phương',
    General: 'Tổng hợp', Unknown: 'Khác',
  };

  // Lowercase, strip Vietnamese diacritics (đ becomes d), collapse whitespace
  function fold(text) {
    return String(text == null ? '' : text).normalize('NFKC').toLowerCase()
      .normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/đ/g, 'd')
      .replace(/\s+/g, ' ').trim();
  }

  // Unknown groups are merged into Unknown
  function groupOf(channel) {
    return CATEGORY_ORDER.includes(channel.category) ? channel.category : 'Unknown';
  }

  function buildChips(channels) {
    const chips = [];
    if (channels.some((c) => c.favorite)) chips.push({ key: 'favorites', label: 'Yêu thích' });
    if (channels.some((c) => c.recent_at != null)) chips.push({ key: 'recent', label: 'Xem gần đây' });
    const present = new Set(channels.map(groupOf));
    for (const key of CATEGORY_ORDER) if (present.has(key)) chips.push({ key, label: CATEGORY_LABELS[key] });
    return chips;
  }

  function filterChannels(channels, opts) {
    const chip = (opts && opts.chip) || '';
    const q = fold(opts && opts.query);
    let list = channels;
    if (chip === 'favorites') list = list.filter((c) => c.favorite);
    else if (chip === 'recent') list = recentChannels(list, Infinity);
    else if (chip && chip !== 'all') list = list.filter((c) => groupOf(c) === chip);
    if (opts && opts.group) list = list.filter((c) => groupOf(c) === opts.group);
    if (q) list = list.filter((c) => fold(c.name).includes(q));
    return list;
  }

  function groupOptions(channels) {
    const present = new Set(channels.map(groupOf));
    return CATEGORY_ORDER.filter((key) => present.has(key)).map((key) => ({ value: key, label: CATEGORY_LABELS[key] }));
  }

  function recentChannels(channels, max) {
    return channels.filter((c) => c.recent_at != null).sort((a, b) => b.recent_at - a.recent_at).slice(0, max);
  }

  function favoriteChannels(channels) {
    return channels.filter((c) => c.favorite);
  }

  // Big card row on HomeTV Home: favorites when there are any, otherwise the first max channels that have a logo
  function heroChannels(channels, max) {
    const favs = favoriteChannels(channels);
    if (favs.length) return { title: 'Kênh yêu thích', items: favs.slice(0, max) };
    return { title: 'Kênh nổi bật', items: channels.filter((c) => c.logo).slice(0, max) };
  }

  // Next/previous channel in the list, wrapping around; null if the id isn't in the list
  function stepChannel(list, currentId, dir) {
    const i = list.findIndex((c) => c.id === currentId);
    if (i < 0) return null;
    return list[(i + (dir < 0 ? -1 : 1) + list.length) % list.length].id;
  }

  function initial(name) {
    const t = String(name == null ? '' : name).trim();
    return t ? t.charAt(0).toLocaleUpperCase('vi') : '?';
  }

  const api = { CATEGORY_ORDER, CATEGORY_LABELS, fold, groupOf, buildChips, filterChannels, groupOptions, recentChannels,
    favoriteChannels, heroChannels, stepChannel, initial };
  root.HFHomeTV = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
