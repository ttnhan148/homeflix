"""SSRF guard: every playlist URL and every redirect must pass check()."""
import ipaddress
import re
import socket
from urllib.parse import urlsplit

import httpx

MAX_URL_LEN = 2048
INVALID = "URL không hợp lệ"
BLOCKED = "Địa chỉ bị chặn"

CGNAT = ipaddress.ip_network("100.64.0.0/10")
NAT64 = ipaddress.ip_network("64:ff9b::/96")
# IPv4-like numeric forms (decimal/octal/hex/shorthand): inet_aton accepts them but ipaddress does not
_HOSTNAME = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*\.?$")
_NUMERIC_HOST = re.compile(r"^(0x[0-9a-f]*|\d+)(\.(0x[0-9a-f]*|\d+))*$")


class BlockedURL(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Unresolvable(BlockedURL):
    """Domain could not be resolved (transient DNS error): unlike a blocked IP, so it can be retried."""


def _blocked_v4(ip: ipaddress.IPv4Address) -> bool:
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
            or ip.is_reserved or ip.is_unspecified or ip in CGNAT)


def _blocked(ip) -> bool:
    if ip.version == 4:
        return _blocked_v4(ip)
    # IPv6 embedding IPv4: block if the embedded IPv4 is blocked
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
    if embedded:  # only check the embedded IPv4 (ranges 2002::/16, ::ffff:0:0/96 are themselves treated as private)
        return any(_blocked_v4(v4) for v4 in embedded)
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
            or ip.is_reserved or ip.is_unspecified or ip.is_site_local)


def is_blocked_ip(ip: str) -> bool:
    """True if the IP may not be connected to; unparseable also counts as blocked."""
    try:
        addr = ipaddress.ip_address(str(ip))  # zone id is only valid for IPv6
    except ValueError:
        return True
    return _blocked(addr)


def check_url(url: str) -> str:
    """Check syntax, return the normalized host."""
    if not isinstance(url, str) or not url or len(url) > MAX_URL_LEN:
        raise BlockedURL(INVALID)
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - an invalid port raises ValueError
        # Host exactly as httpx will connect (IDNA2008, ASCII form)
        parsed = httpx.URL(url)
        host = parsed.raw_host.decode("ascii")
    except (ValueError, UnicodeError, httpx.InvalidURL):
        raise BlockedURL(INVALID)
    if parsed.scheme not in ("http", "https") or not host:
        raise BlockedURL(INVALID)
    host = host.lower()
    try:
        ipaddress.ip_address(host)
        return host.rstrip(".")
    except ValueError:
        pass
    if not _HOSTNAME.match(host):
        raise BlockedURL(INVALID)
    host = host.rstrip(".")
    if _NUMERIC_HOST.match(host):
        # Accept only standard decimal dotted-quad; 2130706433, 0x7f000001, 127.1... are rejected
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
        ipaddress.ip_address(host)
        ips = [host]  # IP literal: no resolution
    except ValueError:
        try:
            ips = resolve(host)
        except (OSError, UnicodeError):
            raise Unresolvable(BLOCKED)  # create/update still report 422 "Địa chỉ bị chặn"
    if not ips or any(is_blocked_ip(ip) for ip in ips):
        raise BlockedURL(BLOCKED)


def check(url: str) -> None:
    check_host(check_url(url))
