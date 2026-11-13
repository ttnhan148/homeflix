// Pure logic for the settings page: no DOM access, runs in both the browser and Node.
(function (root) {
  // "10 GB", "6 giờ"; without a unit, just the number
  function formatValue(item) {
    return item.unit ? item.value + ' ' + item.unit : String(item.value);
  }

  // Add/subtract one step then clamp to [min, max]
  function stepValue(item, value, dir) {
    const next = value + item.step * (dir < 0 ? -1 : 1);
    return Math.min(item.max, Math.max(item.min, next));
  }

  // Group devices by home, preserving order of appearance; this device's home comes first
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

  // Last-seen time (epoch seconds) as short text
  function lastSeenText(lastSeen, now) {
    const s = Math.max(0, Math.floor(now - lastSeen));
    if (s < 60) return 'vừa xong';
    if (s < 3600) return Math.floor(s / 60) + ' phút trước';
    if (s < 86400) return Math.floor(s / 3600) + ' giờ trước';
    return Math.floor(s / 86400) + ' ngày trước';
  }

  // Countdown "m:ss", never negative
  function countdownText(expiresAt, now) {
    const s = Math.max(0, Math.floor(expiresAt - now));
    return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
  }

  // Arrow-key navigation in a grid; column clamped to the row length
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
