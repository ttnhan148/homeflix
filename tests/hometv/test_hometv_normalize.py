import random

import pytest

from hometv import normalize


@pytest.mark.parametrize("raw,norm", [
    ("VTV1 HD", "vtv1"), ("VTV1 FHD", "vtv1"), ("VTV 1", "vtv1"),
    ("HTV1 (720p)", "htv1"), ("HTV Thể Thao HD", "htv the thao"), ("THVL1 (1080i)", "thvl1"),
    ("Đồng Nai 1", "dong nai1"), ("VTV3 (Backup)", "vtv3"), ("HD", ""), ("  ", ""),
    ("VTV3 (HD) Plus", "vtv3 plus"), ("VTV1 HD [FHD]", "vtv1"), ("[4K] (UHD)", ""),
    ("ONE Muz", "one muz"), ("", ""), ("---", ""),
    ("ＶＴＶ１", "vtv1"), ("VTV 1 HD", "vtv1"), ("VTV1 📺 HD", "vtv1"), ("🔥🔥", ""),
    ("VĨNH LONG 1", "vinh long1"),
])
def test_normalize_name(raw, norm):
    assert normalize.normalize_name(raw) == norm


def test_normalize_name_idempotent_and_never_raises():
    rng = random.Random(7)
    pool = list("vtvhdfsk 1234p()[]-_.| Đđ́ＶＴ🔥") + ["hd", "fhd", "(", "720p", "backup", "4k", "\ud800", ""]
    samples = ["VTV1 HD", "Đồng Nai 1", "한국 TV", "İstanbul HD", "가 HD"]
    for _ in range(3000):
        samples.append("".join(rng.choice(pool) for _ in range(rng.randint(0, 14))))
    for s in samples:
        n = normalize.normalize_name(s)
        assert normalize.normalize_name(n) == n, repr(s)


def test_fold():
    assert normalize.fold("  Đồng  NAI 1 ") == "dong nai 1"
    assert normalize.fold("ＶＴＶ") == "vtv"


def test_quality_hint():
    assert normalize.quality_hint("VTV1 HD") == "HD" and normalize.quality_hint("HTV1 (720p)") == "720p"
    assert normalize.quality_hint("ONE Muz") == ""
    assert normalize.quality_hint("VTV1 4k") == "4K" and normalize.quality_hint("VTV1 fhd") == "FHD"
    assert normalize.quality_hint("VTV1 HD (720p)") == "HD"
    assert normalize.quality_hint("VTV3 (Backup)") == ""
    assert normalize.quality_hint("") == ""


@pytest.mark.parametrize("raw,clean", [("VTV1.vn", "vtv1.vn"), (" vtv1hd ", "vtv1hd"), ("no_epg", ""), ("NULL", ""),
                                       ("", ""), ("None", ""), ("undefined", ""), ("a b", "ab"), ("x" * 201, ""),
                                       ("x" * 200, "x" * 200)])
def test_clean_tvg_id(raw, clean):
    assert normalize.clean_tvg_id(raw) == clean


def test_strip_group():
    assert normalize.strip_group("🇻🇳| Địa phương") == "Địa phương"
    assert normalize.strip_group("⚽| Thể thao") == "Thể thao"
    assert normalize.strip_group("VN| Phim") == "Phim"
    assert normalize.strip_group("General") == "General"
    assert normalize.strip_group("🌈 Kids ✨") == "Kids"


@pytest.mark.parametrize("name,group,b", [
    ("vtv1", "", "vtv"), ("htv the thao", "", "htv"), ("dong nai1", "", "dong nai"),
    ("kenh x", "🇻🇳| SCTV", "sctv"), ("one muz", "General", ""),
    ("vtvcab1", "", "vtvcab"), ("vtv hd", "", "vtv"), ("thvl1", "", "thvl"),
    ("vinh long1", "", "vinh long"), ("ba ria vung tau", "", "ba ria vung tau"),
    ("truyen hinh hue", "", "thua thien hue"), ("vtvx", "", ""),
    ("kenh x", "", ""), ("kenh x", "Undefined", ""),
    ("thvl1", "🇻🇳| SCTV", "thvl"),
])
def test_broadcaster(name, group, b):
    assert normalize.broadcaster(name, group) == b


@pytest.mark.parametrize("group,b,cat", [
    ("🇻🇳| Địa phương", "", "Local TV"), ("", "dong nai", "Local TV"), ("", "vinh long", "Local TV"),
    ("🇻🇳| VTV", "vtv", "National TV"), ("", "htv", "National TV"), ("Quốc gia", "", "National TV"),
    ("📰| Tin tức", "", "News"), ("⚽| Thể thao", "", "Sports"),
    ("🌈| Hoạt hình", "", "Kids"), ("🎬| Phim truyện", "", "Movies"), ("📻| Radio", "", "Music"),
    ("🌿| Khám phá", "", "Entertainment"),
    ("🇷🇺| Nga và Cộng đồng các Quốc gia Độc lập", "", "International"),
    ("General", "", "General"), ("Undefined", "", "Unknown"), ("", "", "Unknown"),
    ("Banh", "", "Unknown"), ("Thành phố", "", "Unknown"),
    ("Quốc tế", "", "International"), ("🇰🇷| Han Quoc", "", "International"), ("Phim Anh", "", "International"),
])
def test_category(group, b, cat):
    assert normalize.category(group, b) == cat
    assert cat in normalize.CATEGORIES


def test_provinces():
    assert len(normalize.PROVINCES) == 63
    for p in ("ha noi", "ho chi minh", "da nang", "dong nai", "ba ria vung tau", "thua thien hue"):
        assert p in normalize.PROVINCES
    assert all(p == normalize.fold(p) for p in normalize.PROVINCES)
