// Logic thuần cho HomeTV: không đụng DOM, chạy được trong trình duyệt lẫn Node.
(function (root) {
  const CATEGORY_ORDER = ['National TV', 'News', 'Sports', 'Kids', 'Movies', 'Music', 'Entertainment',
    'International', 'Local TV', 'General', 'Unknown'];
  const CATEGORY_LABELS = {
    'National TV': 'Quốc gia', News: 'Tin tức', Sports: 'Thể thao', Kids: 'Thiếu nhi', Movies: 'Phim',
    Music: 'Âm nhạc', Entertainment: 'Giải trí', International: 'Quốc tế', 'Local TV': 'Địa phương',
    General: 'Tổng hợp', Unknown: 'Khác',
  };

  // Chữ thường, bỏ dấu tiếng Việt (đ thành d), gộp khoảng trắng
  function fold(text) {
    return String(text == null ? '' : text).normalize('NFKC').toLowerCase()
      .normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/đ/g, 'd')
      .replace(/\s+/g, ' ').trim();
  }

  // Nhóm lạ gộp vào Unknown
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
    else if (chip === 'recent') list = list.filter((c) => c.recent_at != null).sort((a, b) => b.recent_at - a.recent_at);
    else if (chip && chip !== 'all') list = list.filter((c) => groupOf(c) === chip);
    if (q) list = list.filter((c) => fold(c.name).includes(q));
    return list;
  }

  // Kênh kế/trước trong danh sách, quay vòng; null nếu id không có trong danh sách
  function stepChannel(list, currentId, dir) {
    const i = list.findIndex((c) => c.id === currentId);
    if (i < 0) return null;
    return list[(i + (dir < 0 ? -1 : 1) + list.length) % list.length].id;
  }

  function initial(name) {
    const t = String(name == null ? '' : name).trim();
    return t ? t.charAt(0).toLocaleUpperCase('vi') : '?';
  }

  const api = { CATEGORY_ORDER, CATEGORY_LABELS, fold, buildChips, filterChannels, stepChannel, initial };
  root.HFHomeTV = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
