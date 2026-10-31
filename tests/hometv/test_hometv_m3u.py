import time
from pathlib import Path

from hometv import m3u

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "hometv" / "sample.m3u"


def test_parses_entries_and_headers():
    p = m3u.parse(FIX.read_text(encoding="utf-8"))
    assert p.has_header and [e.name for e in p.entries] == ["VTV1 HD", "VTV 1 (1080p)", "Kênh, có dấu phẩy", "K+ SPORT 1"]
    e = p.entries[0]
    assert e.tvg_id == "vtv1hd" and e.group_title == "🇻🇳| VTV" and e.tvg_logo.endswith("nfkmvAY.png")
    assert e.http_headers == {"user_agent": "ReactNativeVideo/3.4.4 (Linux;Android 9) ExoPlayerLib/2.13.3"}
    assert p.entries[2].tvg_id == "no_epg" and p.entries[2].group_title == "Undefined"
    assert p.entries[2].http_headers == {"referrer": "https://ref.example/"}
    assert p.entries[3].drm_hint is True and "abc" not in repr(p.entries[3])
    assert p.entries[0].drm_hint is False
    assert p.skipped_lines == 2


def test_decode_strips_bom_and_handles_crlf():
    p = m3u.parse(m3u.decode(b"\xef\xbb\xbf#EXTM3U\r\n#EXTINF:-1,A\r\nhttps://a.example/x.m3u8\r\n"))
    assert p.has_header and p.entries[0].url == "https://a.example/x.m3u8"


def test_no_header():
    assert m3u.parse("#EXTINF:-1,A\nhttps://a/x\n").has_header is False


def test_invalid_utf8_does_not_raise():
    assert m3u.parse(m3u.decode(b"#EXTM3U\n#EXTINF:-1,\xff\xfe\nhttps://a/x\n")).entries


def test_long_fields_capped():
    p = m3u.parse(f'#EXTM3U\n#EXTINF:-1 tvg-logo="https://l/{"a"*3000}",{"N"*500}\nhttps://a/x\n#EXTINF:-1,B\nhttps://a/{"u"*3000}\n')
    assert len(p.entries) == 1 and len(p.entries[0].name) == 200 and p.entries[0].tvg_logo == "" and p.skipped_lines == 1


def test_attribute_value_with_comma():
    p = m3u.parse('#EXTM3U\n#EXTINF:-1 tvg-id="a" group-title="Phim, Truyện" tvg-name="X, Y",Tên, hay\nhttps://a/x\n')
    e = p.entries[0]
    assert e.group_title == "Phim, Truyện" and e.tvg_name == "X, Y" and e.name == "Tên, hay"


def test_unrecognized_comments_and_blanks_not_skipped():
    p = m3u.parse("#EXTM3U\n\n#EXTGRP:News\n#-----VTV-----\n#EXTINF:-1,A\n\n#EXTGRP:x\nhttps://a/x\n")
    assert len(p.entries) == 1 and p.skipped_lines == 0


def test_options_apply_only_to_next_entry():
    p = m3u.parse(
        "#EXTM3U\n#EXTINF:-1,A\n#EXTVLCOPT:http-user-agent=UA\n#KODIPROP:inputstream.adaptive.license_key=k\nhttps://a/1\n"
        "#EXTINF:-1,B\nhttps://a/2\n"
    )
    assert p.entries[0].http_headers == {"user_agent": "UA"} and p.entries[0].drm_hint
    assert p.entries[1].http_headers == {} and not p.entries[1].drm_hint


def test_unbalanced_quote_falls_back_to_first_comma():
    p = m3u.parse('#EXTM3U\n#EXTINF:-1 tvg-id="a,Tên\nhttps://a/x\n')
    assert len(p.entries) == 1 and p.entries[0].name == "Tên"


def test_large_playlist_is_fast():
    body = "#EXTM3U\n" + "".join(
        f'#EXTINF:-1 tvg-id="id{i}" tvg-logo="https://l/{i}.png" group-title="G, {i}",Kênh {i}\nhttps://a.example/{i}.m3u8\n'
        for i in range(20000)
    )
    t = time.perf_counter()
    p = m3u.parse(body)
    assert len(p.entries) == 20000 and time.perf_counter() - t < 1.0
