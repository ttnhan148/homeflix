"""Chặn SSRF: mọi URL playlist và mọi lần chuyển hướng phải qua check()."""
import ipaddress
import re
import socket
from urllib.parse import urlsplit

MAX_URL_LEN = 2048
INVALID = "URL không hợp lệ"
BLOCKED = "Địa chỉ bị chặn"

CGNAT = ipaddress.ip_network("100.64.0.0/10")
NAT64 = ipaddress.ip_network("64:ff9b::/96")
# Dạng số kiểu IPv4 (thập phân/bát phân/hex/rút gọn): inet_aton hiểu nhưng ipaddress thì không
_NUMERIC_HOST = re.compile(r"^(0x[0-9a-f]*|\d+)(\.(0x[0-9a-f]*|\d+))*$")


class BlockedURL(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _blocked_v4(ip: ipaddress.IPv4Address) -> bool:
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
            or ip.is_reserved or ip.is_unspecified or ip in CGNAT)


def _blocked(ip) -> bool:
    if ip.version == 4:
        return _blocked_v4(ip)
    # IPv6 chứa IPv4: chặn nếu IPv4 bên trong bị chặn
    embedded = []
    if ip.ipv4_mapped:
        embedded.append(ip.ipv4_mapped)
    if ip.sixtofour:
        embedded.append(ip.sixtofour)
    if ip.teredo:
        embedded.extend(ip.teredo)
    if ip in NAT64:
        embedded.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    if ip.packed[:12] == bytes(12):  # ::a.b.c.d (IPv4-compatible)
        embedded.append(ipaddress.IPv4Address(ip.packed[12:]))
    if embedded:  # chỉ xét IPv4 bên trong (dải 2002::/16, ::ffff:0:0/96 tự bị coi là private)
        return any(_blocked_v4(v4) for v4 in embedded)
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
            or ip.is_reserved or ip.is_unspecified or ip.is_site_local)


def is_blocked_ip(ip: str) -> bool:
    """True nếu IP không được phép kết nối; không parse được cũng coi là chặn."""
    try:
        addr = ipaddress.ip_address(str(ip).split("%", 1)[0])  # bỏ zone id
    except ValueError:
        return True
    return _blocked(addr)


def check_url(url: str) -> str:
    """Kiểm tra cú pháp, trả host đã chuẩn hóa."""
    if not isinstance(url, str) or not url or len(url) > MAX_URL_LEN:
        raise BlockedURL(INVALID)
    try:
        parts = urlsplit(url)
        host = parts.hostname
        parts.port  # noqa: B018 - cổng sai thì ném ValueError
    except ValueError:
        raise BlockedURL(INVALID)
    if parts.scheme.lower() not in ("http", "https") or not host:
        raise BlockedURL(INVALID)
    host = host.lower().rstrip(".")
    if not host:
        raise BlockedURL(INVALID)
    if _NUMERIC_HOST.match(host):
        # Chỉ nhận dotted-quad thập phân chuẩn; 2130706433, 0x7f000001, 127.1... bị từ chối
        try:
            ipaddress.IPv4Address(host)
        except ValueError:
            raise BlockedURL(INVALID)
    return host


def resolve(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(i[4][0] for i in infos))


def check_host(host: str) -> None:
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
        ips = [host]  # IP literal: không phân giải
    except ValueError:
        try:
            ips = resolve(host)
        except (OSError, UnicodeError):
            raise BlockedURL(BLOCKED)
    if not ips or any(is_blocked_ip(ip) for ip in ips):
        raise BlockedURL(BLOCKED)


def check(url: str) -> None:
    check_host(check_url(url))
