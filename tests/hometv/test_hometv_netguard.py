import pytest

from hometv import netguard


@pytest.fixture(autouse=True)
def no_real_dns(monkeypatch):
    def boom(host):
        raise AssertionError(f"DNS thật bị gọi: {host}")
    monkeypatch.setattr(netguard, "resolve", boom)


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.1.2.3", "172.20.0.2", "192.168.1.5", "169.254.1.1",
    "100.64.0.1", "100.127.255.255", "0.0.0.0", "::1", "::", "fe80::1", "fc00::1",
    "::ffff:127.0.0.1", "::ffff:10.0.0.1", "224.0.0.1", "240.0.0.1",
    "fe80::1%eth0",                      # zone id
    "::127.0.0.1",                       # IPv4-compatible
    "2002:7f00:1::1",                    # 6to4 chứa 127.0.0.1
    "2002:0a00:0001::",                  # 6to4 chứa 10.0.0.1
    "64:ff9b::7f00:1",                   # NAT64 chứa 127.0.0.1
    "not-an-ip", "", "999.1.1.1",        # không parse được thì chặn
])
def test_blocked_ips(ip):
    assert netguard.is_blocked_ip(ip)


def test_teredo_embedded_blocked():
    # Teredo: server 127.0.0.1 (7f00:0001)
    assert netguard.is_blocked_ip("2001:0:7f00:1::1")


@pytest.mark.parametrize("ip", [
    "8.8.8.8", "185.199.108.153", "2606:4700::1", "100.63.255.255", "100.128.0.1",
    "::ffff:8.8.8.8", "2002:0808:0808::1",
])
def test_public_ips(ip):
    assert not netguard.is_blocked_ip(ip)


@pytest.mark.parametrize("url", [
    "ftp://x.com/a.m3u", "file:///etc/passwd", "http://", "javascript:alert(1)",
    "http://x/" + "a" * 2050, "", "http:///path", "//x.com/a", "x.com/a",
    "http://x.com:99999/", "http://x.com:abc/",
    "http://2130706433/", "http://0x7f000001/", "http://0177.0.0.1/", "http://127.1/",
    "http://017700000001/", "http://0x7f.0.0.1/",
])
def test_bad_urls(url):
    with pytest.raises(netguard.BlockedURL) as e:
        netguard.check_url(url)
    assert e.value.reason == "URL không hợp lệ"


@pytest.mark.parametrize("url,host", [
    ("http://Example.COM/a", "example.com"),
    ("https://example.com./a", "example.com"),
    ("http://example.com:8080/a", "example.com"),
    ("http://user:pw@example.com/a", "example.com"),
    ("http://[::1]/", "::1"),
    ("http://[::1]:8080/", "::1"),
    ("http://user@127.0.0.1/", "127.0.0.1"),
    ("http://127.0.0.1:80/", "127.0.0.1"),
])
def test_check_url_host(url, host):
    assert netguard.check_url(url) == host


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://user@127.0.0.1/", "http://127.0.0.1:8080/",
    "http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://[fe80::1%25eth0]/",
    "http://169.254.169.254/latest/meta-data", "http://100.64.0.1/",
])
def test_ip_literal_not_resolved(url):
    # resolve bị thay bằng hàm ném: IP literal không được phân giải
    with pytest.raises(netguard.BlockedURL) as e:
        netguard.check(url)
    assert e.value.reason == "Địa chỉ bị chặn"


def test_userinfo_cannot_disguise_host(monkeypatch):
    monkeypatch.setattr(netguard, "resolve", lambda h: ["10.0.0.5"] if h == "internal" else ["8.8.8.8"])
    with pytest.raises(netguard.BlockedURL):
        netguard.check("http://example.com@internal/x")


def test_host_resolving_to_any_private_ip_is_blocked(monkeypatch):
    monkeypatch.setattr(netguard, "resolve", lambda h: ["8.8.8.8", "10.0.0.5"])
    with pytest.raises(netguard.BlockedURL) as e:
        netguard.check("https://evil.example/x.m3u")
    assert e.value.reason == "Địa chỉ bị chặn"


def test_public_host_passes(monkeypatch):
    monkeypatch.setattr(netguard, "resolve", lambda h: ["185.199.108.153"])
    netguard.check("https://cdn.example/x.m3u")


def test_unresolvable_host_blocked(monkeypatch):
    def fail(h):
        raise OSError("no dns")
    monkeypatch.setattr(netguard, "resolve", fail)
    with pytest.raises(netguard.BlockedURL) as e:
        netguard.check("https://nope.example/x.m3u")
    assert e.value.reason == "Địa chỉ bị chặn"


def test_empty_resolution_blocked(monkeypatch):
    monkeypatch.setattr(netguard, "resolve", lambda h: [])
    with pytest.raises(netguard.BlockedURL):
        netguard.check_host("x.example")


def test_public_dns_fixture(public_dns):
    netguard.check("https://anything.example/x.m3u")


def test_resolve_uses_getaddrinfo(monkeypatch):
    monkeypatch.undo()  # bỏ no_real_dns, thay getaddrinfo giả
    monkeypatch.setattr(netguard.socket, "getaddrinfo", lambda h, p, **k: [
        (2, 1, 6, "", ("8.8.8.8", 0)), (10, 1, 6, "", ("2606:4700::1", 0, 0, 0)),
        (2, 1, 6, "", ("8.8.8.8", 0))])
    assert netguard.resolve("x.example") == ["8.8.8.8", "2606:4700::1"]


# --- vòng sửa 1: host kiểm tra phải trùng host httpx sẽ kết nối ---

def test_ipv4_with_percent_is_not_an_ip_literal():
    assert netguard.is_blocked_ip("8.8.8.8%foo")


@pytest.mark.parametrize("url", ["http://8.8.8.8%25.evil.com/", "http://8.8.8.8%2e.evil.com/"])
def test_ipv4_percent_host_never_skips_dns(monkeypatch, url):
    calls = []
    monkeypatch.setattr(netguard, "resolve", lambda h: calls.append(h) or ["8.8.8.8"])
    try:
        netguard.check(url)
    except netguard.BlockedURL:
        pass  # từ chối cũng được; không được bỏ qua DNS mà cho qua
    else:
        assert calls, "host đi qua mà không phân giải"


@pytest.mark.parametrize("url,ascii_host", [
    ("http://faß.evil.com/", "xn--fa-hia.evil.com"),
    ("http://straße.evil.com/", "xn--strae-oqa.evil.com"),
    ("http://ς.evil.com/", "xn--3xa.evil.com"),
])
def test_idn_resolved_as_httpx_name(monkeypatch, url, ascii_host):
    calls = []
    monkeypatch.setattr(netguard, "resolve", lambda h: calls.append(h) or ["185.199.108.153"])
    assert netguard.check_url(url) == ascii_host
    netguard.check(url)
    assert calls == [ascii_host]


@pytest.mark.parametrize("url", [
    "http://127.0.0.1\t.evil.com/", " http://x.com/", "http://127.0.0.1 .evil.com/",
    "http://１２７．０．０．１/", "http://x_y.com/", "http://-a.com/", "http://a..com/",
])
def test_odd_hosts_rejected(url):
    with pytest.raises(netguard.BlockedURL) as e:
        netguard.check_url(url)
    assert e.value.reason == "URL không hợp lệ"


def test_ideographic_dots_never_resolved_as_other_name(monkeypatch):
    calls = []
    monkeypatch.setattr(netguard, "resolve", lambda h: calls.append(h) or ["8.8.8.8"])
    with pytest.raises(netguard.BlockedURL):
        netguard.check("http://127。0。0。1/")
    assert calls == []
