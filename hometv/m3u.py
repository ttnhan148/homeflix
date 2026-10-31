"""Phân tích Extended M3U thành danh sách mục (Entry)."""
import re
from dataclasses import dataclass, field

MAX_TEXT = 200
MAX_URL = 2048

# Thuộc tính: giá trị trong nháy kép, nháy đơn hoặc không nháy (không backtrack nặng)
_ATTR = re.compile(r"""([\w-]+)=("[^"]*"|'[^']*'|[^\s,"']*)""")


@dataclass
class Entry:
    name: str
    url: str
    tvg_id: str = ""
    tvg_name: str = ""
    tvg_logo: str = ""
    group_title: str = ""
    http_headers: dict[str, str] = field(default_factory=dict)  # khoá: user_agent, referrer
    drm_hint: bool = False  # chỉ đánh dấu, không lưu key


@dataclass
class Playlist:
    entries: list[Entry]
    skipped_lines: int
    has_header: bool


def decode(body: bytes) -> str:
    """Bỏ BOM, giải mã UTF-8 lỗi thì thay ký tự."""
    if body.startswith(b"\xef\xbb\xbf"):
        body = body[3:]
    return body.decode("utf-8", errors="replace")


def _split_extinf(rest: str) -> tuple[str, str]:
    """Tách (phần thuộc tính, tên) tại dấu phẩy đầu tiên nằm ngoài nháy của giá trị."""
    i, n = 0, len(rest)
    while i < n:
        c = rest[i]
        if c == ",":
            return rest[:i], rest[i + 1:].strip()
        if c in "\"'" and i > 0 and rest[i - 1] == "=":
            j = rest.find(c, i + 1)
            if j == -1:  # nháy không đóng: lấy dấu phẩy đầu tiên
                k = rest.find(",", i)
                return (rest[:k], rest[k + 1:].strip()) if k != -1 else (rest, "")
            i = j + 1
            continue
        i += 1
    return rest, ""


def _attrs(head: str) -> dict[str, str]:
    out = {}
    for m in _ATTR.finditer(head):
        v = m.group(2)
        if len(v) >= 2 and v[0] in "\"'" and v[-1] == v[0]:
            v = v[1:-1]
        out[m.group(1).lower()] = v
    return out


def parse(text: str) -> Playlist:
    entries: list[Entry] = []
    skipped = 0
    has_header = False
    pending: dict | None = None  # mục đang chờ URL

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            up = line.upper()
            if up.startswith("#EXTM3U"):
                has_header = True
            elif up.startswith("#EXTINF:"):
                if pending is not None:  # EXTINF trước đó không có URL
                    skipped += 1
                head, name = _split_extinf(line[8:])
                a = _attrs(head)
                logo = a.get("tvg-logo", "")
                pending = {
                    "name": name[:MAX_TEXT],
                    "tvg_id": a.get("tvg-id", ""),
                    "tvg_name": a.get("tvg-name", "")[:MAX_TEXT],
                    "tvg_logo": logo if len(logo) <= MAX_URL else "",
                    "group_title": a.get("group-title", "")[:MAX_TEXT],
                    "http_headers": {},
                    "drm_hint": False,
                }
            elif pending is not None and up.startswith("#EXTVLCOPT:"):
                k, _, v = line[11:].partition("=")
                k = k.strip().lower()
                if k == "http-user-agent":
                    pending["http_headers"]["user_agent"] = v.strip()
                elif k in ("http-referrer", "http-referer"):
                    pending["http_headers"]["referrer"] = v.strip()
            elif pending is not None and up.startswith("#KODIPROP:"):
                k = line[10:].partition("=")[0].lower()
                if "license_key" in k or "license_type" in k:
                    pending["drm_hint"] = True
            continue  # dòng # khác: bỏ qua
        # dòng URL
        if pending is None:
            skipped += 1
            continue
        if len(line) > MAX_URL or not line.lower().startswith(("http://", "https://")):
            skipped += 1
        else:
            entries.append(Entry(url=line, **pending))
        pending = None
    if pending is not None:
        skipped += 1
    return Playlist(entries=entries, skipped_lines=skipped, has_header=has_header)
