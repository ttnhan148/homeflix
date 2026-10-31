from devices import (normalize_ip, parse_trusted_proxies, client_ip,
                     household_id, device_kind, default_device_name)

def test_private_and_loopback_ips_collapse_to_lan():
    for ip in ("192.168.1.20", "10.0.0.5", "172.16.4.4", "127.0.0.1", "169.254.1.1", "fe80::1", "::1"):
        assert normalize_ip(ip) == "lan"

def test_public_ipv4_kept():
    assert normalize_ip("203.0.113.7") == "203.0.113.7"

def test_ipv6_cut_to_64_prefix():
    assert normalize_ip("2001:db8:1:2:aaaa:bbbb:cccc:dddd") == normalize_ip("2001:db8:1:2:1:2:3:4")
    assert normalize_ip("2001:db8:1:2::1") != normalize_ip("2001:db8:1:3::1")

def test_ipv4_mapped_ipv6_becomes_ipv4():
    assert normalize_ip("::ffff:203.0.113.7") == "203.0.113.7"

def test_garbage_ip_does_not_raise():
    assert normalize_ip("testclient") == "testclient"
    assert normalize_ip("") == "unknown"

def test_trusted_proxy_forwarded_for_used():
    trusted = parse_trusted_proxies("10.0.0.2, 10.0.0.3")
    assert client_ip("10.0.0.2", "203.0.113.9, 10.0.0.2", trusted) == "203.0.113.9"

def test_forwarded_for_from_untrusted_peer_is_ignored():
    trusted = parse_trusted_proxies("10.0.0.2")
    assert client_ip("198.51.100.4", "203.0.113.9", trusted) == "198.51.100.4"

def test_no_peer_gives_unknown():
    assert client_ip(None, None, frozenset()) == "unknown"

def test_household_stable_and_salted():
    a = household_id("203.0.113.7", "s1")
    assert a == household_id("203.0.113.7", "s1") and len(a) == 16
    assert a != household_id("203.0.113.7", "s2")
    assert a != household_id("203.0.113.8", "s1")

def test_device_kind_and_default_name():
    iphone = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"
    tv = "Mozilla/5.0 (Web0S; Linux/SmartTV) AppleWebKit/537.36 webOS.TV-2023"
    desktop = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120"
    assert device_kind(iphone) == "phone" and default_device_name("phone", iphone) == "iPhone"
    assert device_kind(tv) == "tv" and default_device_name("tv", tv) == "TV"
    assert device_kind(desktop) == "desktop" and default_device_name("desktop", desktop) == "Máy tính"
    assert default_device_name("phone", "Mozilla/5.0 (Linux; Android 14)") == "Điện thoại"


def test_client_ip_ignores_spoofed_leading_hop():
    trusted = frozenset({"10.0.0.2"})
    assert client_ip("10.0.0.2", "1.2.3.4, 203.0.113.9", trusted) == "203.0.113.9"


def test_client_ip_multi_proxy_chain_skips_trusted_hops():
    trusted = frozenset({"10.0.0.2", "10.0.0.3"})
    assert client_ip("10.0.0.2", "1.2.3.4, 203.0.113.9, 10.0.0.3", trusted) == "203.0.113.9"


def test_client_ip_all_hops_trusted_uses_leftmost():
    trusted = frozenset({"10.0.0.2", "10.0.0.3"})
    assert client_ip("10.0.0.2", "10.0.0.3, 10.0.0.2", trusted) == "10.0.0.3"


def test_client_ip_untrusted_peer_ignores_xff():
    assert client_ip("198.51.100.4", "1.2.3.4, 5.6.7.8", frozenset({"10.0.0.2"})) == "198.51.100.4"


def test_client_ip_empty_xff_returns_peer():
    trusted = frozenset({"10.0.0.2"})
    assert client_ip("10.0.0.2", "", trusted) == "10.0.0.2"
    assert client_ip("10.0.0.2", " , ", trusted) == "10.0.0.2"
