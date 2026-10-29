"""Hàm thuần cho định danh thiết bị: chuẩn hóa IP, tính "nhà", suy ra loại thiết bị."""
import hashlib
import ipaddress
import re

# RFC1918 + unique-local IPv6. Không dùng ip.is_private vì nó coi cả các dải
# tài liệu (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24, 2001:db8::/32) là riêng.
_PRIVATE_NETS = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7",
))

_TV_RE = re.compile(
    r"tv|smarttv|googletv|appletv|hbbtv|netcast|webos|tizen|rokutv|ctv|box|playstation|xbox|nintendo|large-screen",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(r"iphone|ipad|ipod|android", re.IGNORECASE)

DEVICE_KINDS = ("phone", "tv", "desktop")


def normalize_ip(raw: str) -> str:
    """Trả 'lan' cho IP riêng/loopback/link-local, IPv4 giữ nguyên, IPv6 cắt về /64."""
    value = (raw or "").strip()
    if not value:
        return "unknown"
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return value
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_loopback or ip.is_link_local or any(ip in net for net in _PRIVATE_NETS):
        return "lan"
    if ip.version == 6:
        return str(ipaddress.ip_network(f"{ip}/64", strict=False).network_address)
    return str(ip)


def parse_trusted_proxies(value: str | None) -> frozenset[str]:
    """Đọc biến môi trường TRUSTED_PROXIES: các IP cách nhau bằng dấu phẩy."""
    if not value:
        return frozenset()
    return frozenset(p.strip() for p in value.split(",") if p.strip())


def client_ip(peer: str | None, forwarded_for: str | None, trusted: frozenset[str]) -> str:
    """IP client thật. Chỉ tin X-Forwarded-For khi peer nằm trong danh sách proxy tin cậy."""
    if not peer:
        return "unknown"
    if peer in trusted and forwarded_for:
        first = forwarded_for.split(",")[0].strip()
        if first:
            return first
    return peer


def household_id(ip: str, salt: str) -> str:
    return hashlib.sha256((salt + normalize_ip(ip)).encode("utf-8")).hexdigest()[:16]


def device_kind(user_agent: str) -> str:
    ua = user_agent or ""
    if _TV_RE.search(ua):
        return "tv"
    if _PHONE_RE.search(ua):
        return "phone"
    return "desktop"


def default_device_name(kind: str, user_agent: str) -> str:
    if kind == "tv":
        return "TV"
    if kind == "phone":
        return "iPhone" if re.search(r"iphone", user_agent or "", re.IGNORECASE) else "Điện thoại"
    return "Máy tính"
