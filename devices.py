"""Pure functions for device identity: normalize IPs, compute "home", infer device type."""
import hashlib
import ipaddress
import re

# RFC1918 + unique-local IPv6. Not using ip.is_private because it also treats
# documentation ranges (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24, 2001:db8::/32) as private.
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
    """Return 'lan' for private/loopback/link-local IPs; IPv4 kept as-is, IPv6 truncated to /64."""
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
    """Read the TRUSTED_PROXIES env var: comma-separated IPs."""
    if not value:
        return frozenset()
    return frozenset(p.strip() for p in value.split(",") if p.strip())


def client_ip(peer: str | None, forwarded_for: str | None, trusted: frozenset[str]) -> str:
    """Real client IP. Trust X-Forwarded-For only when the peer is a trusted proxy; take the rightmost hop that is not a trusted proxy."""
    if not peer:
        return "unknown"
    if peer in trusted and forwarded_for:
        hops = [h.strip() for h in forwarded_for.split(",") if h.strip()]
        # Walk right to left, skipping trusted proxies; the first hop is client-supplied so it is not trusted
        for hop in reversed(hops):
            if hop not in trusted:
                return hop
        if hops:
            return hops[0]
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
