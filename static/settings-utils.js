// Logic thuần cho trang cài đặt: không đụng DOM, chạy được trong trình duyệt lẫn Node.
(function (root) {
  // "10 GB", "6 giờ"; không có đơn vị thì chỉ số
  function formatValue(item) {
    return item.unit ? item.value + ' ' + item.unit : String(item.value);
  }

  // Cộng/trừ một bước rồi kẹp trong [min, max]
  function stepValue(item, value, dir) {
    const next = value + item.step * (dir < 0 ? -1 : 1);
    return Math.min(item.max, Math.max(item.min, next));
  }

  // Gom máy theo nhà, giữ thứ tự xuất hiện; nhà của máy này đứng đầu
  function groupDevices(devices) {
    const groups = [];
    const byHousehold = new Map();
    for (const d of devices || []) {
      let g = byHousehold.get(d.household);
      if (!g) {
        g = { household: d.household, devices: [] };
        byHousehold.set(d.household, g);
        groups.push(g);
      }
      g.devices.push(d);
    }
    const own = groups.findIndex((g) => g.devices.some((d) => d.is_self));
    if (own > 0) groups.unshift(groups.splice(own, 1)[0]);
    return groups;
  }

  // Thời điểm thấy gần nhất (giây epoch) thành chữ ngắn
  function lastSeenText(lastSeen, now) {
    const s = Math.max(0, Math.floor(now - lastSeen));
    if (s < 60) return 'vừa xong';
    if (s < 3600) return Math.floor(s / 60) + ' phút trước';
    if (s < 86400) return Math.floor(s / 3600) + ' giờ trước';
    return Math.floor(s / 86400) + ' ngày trước';
  }

  // Đếm ngược "m:ss", không âm
  function countdownText(expiresAt, now) {
    const s = Math.max(0, Math.floor(expiresAt - now));
    return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
  }

  // Di chuyển trong lưới bằng phím mũi tên; cột kẹp theo độ dài dòng
  function gridMove(lengths, row, col, key) {
    const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
    if (key === 'ArrowDown' || key === 'ArrowUp') {
      const r = clamp(row + (key === 'ArrowDown' ? 1 : -1), 0, lengths.length - 1);
      return [r, clamp(col, 0, Math.max(0, lengths[r] - 1))];
    }
    if (key === 'ArrowLeft' || key === 'ArrowRight') {
      const c = clamp(col + (key === 'ArrowRight' ? 1 : -1), 0, Math.max(0, lengths[row] - 1));
      return [row, c];
    }
    return [row, col];
  }

  const api = { formatValue, stepValue, groupDevices, lastSeenText, countdownText, gridMove };
  root.HFSettingsUtils = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
