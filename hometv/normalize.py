"""Channel name normalization, broadcaster detection, grouping and quality labels."""
import re
import unicodedata

CATEGORIES = ("General", "National TV", "Local TV", "News", "Entertainment", "Movies", "Sports", "Kids",
              "Music", "International", "Unknown")

# National broadcasters (per spec)
NATIONAL_BROADCASTERS = frozenset({"vtv", "htv", "vtc", "thvl", "vov", "antv", "qpvn", "vnews", "sctv",
                                   "vtvcab", "hplus", "vinh long"})

# 63 provinces/cities (pre-2025 list), no diacritics, lowercase
PROVINCES = frozenset("""an giang|ba ria vung tau|bac giang|bac kan|bac lieu|bac ninh|ben tre|binh dinh|binh duong
|binh phuoc|binh thuan|ca mau|can tho|cao bang|da nang|dak lak|dak nong|dien bien|dong nai|dong thap|gia lai
|ha giang|ha nam|ha noi|ha tinh|hai duong|hai phong|hau giang|hoa binh|ho chi minh|hung yen|khanh hoa|kien giang
|kon tum|lai chau|lam dong|lang son|lao cai|long an|nam dinh|nghe an|ninh binh|ninh thuan|phu tho|phu yen
|quang binh|quang nam|quang ngai|quang ninh|quang tri|soc trang|son la|tay ninh|thai binh|thai nguyen|thanh hoa
|thua thien hue|tien giang|tra vinh|tuyen quang|vinh long|vinh phuc|yen bai""".replace("\n", "").split("|"))

# Common abbreviations/spellings of province names
_PROVINCE_ALIASES = {"hue": "thua thien hue", "hanoi": "ha noi", "vung tau": "ba ria vung tau"}

COUNTRIES = frozenset({"nga", "my", "han quoc", "nhat ban", "trung quoc", "phap", "anh", "duc", "uc",
                       "thai lan", "russia", "usa", "korea", "japan", "china", "france", "uk"})

_QUALITY = frozenset({"hd", "fhd", "sd", "uhd", "4k", "1080p", "1080i", "720p", "576p", "480p", "backup"})
# iptv-org labels in brackets: removed from the name entirely (not quality)
_TAGS = frozenset({"geo blocked", "not 24 7"})
_FAKE_IDS = frozenset({"", "no_epg", "none", "null", "undefined"})
_MAX_TVG_ID = 200
_MARK = "\ue000"  # marks quality words inside brackets
_BRACKET = re.compile(r"[(\[{][^)\]}]*[)\]}]")


def fold(text: str) -> str:
    """NFKC, lowercase, strip diacritics (đ to d), collapse whitespace."""
    s = unicodedata.normalize("NFKC", text).lower().replace("đ", "d")
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    return " ".join(s.split())


def _split_quality(name: str) -> tuple[list[str], list[str]]:
    """Return (kept tokens, removed quality suffixes in order of appearance)."""
    s = fold(name.replace(_MARK, " "))

    def unwrap(m: re.Match) -> str:
        words = re.sub(r"[^\w]+", " ", m.group(0)).split()
        if " ".join(words) in _TAGS:
            return " "
        if words and all(w in _QUALITY for w in words):
            return " " + " ".join(_MARK + w for w in words) + " "
        return m.group(0)

    s = _BRACKET.sub(unwrap, s)
    # only letters/digits (including non-Latin characters) and the marker are kept
    s = "".join(c if (c.isalnum() or c == _MARK) else " " for c in s)
    toks = s.split()
    drop = {i for i, t in enumerate(toks) if t.startswith(_MARK)}
    live = [i for i in range(len(toks)) if i not in drop]
    while live and toks[live[-1]] in _QUALITY:  # trailing suffixes
        drop.add(live.pop())
    removed = [toks[i].lstrip(_MARK) for i in sorted(drop)]
    return [toks[i] for i in live], removed


def normalize_name(name: str) -> str:
    """Name for matching: drop quality suffixes and punctuation; join words with a directly following number."""
    kept, _ = _split_quality(name)
    return re.sub(r"(?<=[^\W\d_]) (?=\d)", "", " ".join(kept))


def quality_hint(name: str) -> str:
    """First quality label in the name (informational only); empty if none."""
    for t in _split_quality(name)[1]:
        if t != "backup":
            return t if re.fullmatch(r"\d+[pi]", t) else t.upper()
    return ""


def clean_tvg_id(tvg_id: str) -> str:
    s = re.sub(r"\s+", "", tvg_id).lower()
    return "" if s in _FAKE_IDS or len(s) > _MAX_TVG_ID else s


_GROUP_PREFIX = re.compile(r"^\s*[A-Za-z]{2}\s*\|\s*")
_GROUP_JUNK = ("So", "Sk", "Cf", "Me", "Cs", "Co", "Cn")


def strip_group(group_title: str) -> str:
    """Strip emoji, symbol characters and `xx| `-style prefixes."""
    s = "".join(c for c in group_title if unicodedata.category(c) not in _GROUP_JUNK and c != "\ufe0f")
    s = _GROUP_PREFIX.sub("", re.sub(r"^\s*\|\s*", "|", s))
    s = re.sub(r"^\|\s*", "", s)
    return " ".join(s.split())


def _tokens(text: str) -> list[str]:
    return re.sub(r"[^\w]+|_", " ", text).split()


def _find_seq(tokens: list[str], phrase: str) -> int:
    """Start index of the whole word sequence in tokens, -1 if absent."""
    p = phrase.split()
    for i in range(len(tokens) - len(p) + 1):
        if tokens[i:i + len(p)] == p:
            return i
    return -1


def _national_prefix(text: str) -> str:
    """Longest national broadcaster that prefixes text, ending at a word boundary."""
    for b in sorted(NATIONAL_BROADCASTERS, key=len, reverse=True):
        if text.startswith(b) and not text[len(b):len(b) + 1].isalpha():
            return b
    return ""


def broadcaster(normalized: str, group_title: str) -> str:
    """Broadcaster: national broadcaster prefix, then province name in the name, then group-title; empty if unknown."""
    b = _national_prefix(normalized)
    if b:
        return b
    toks = _tokens(re.sub(r"(?<=[^\W\d_])(?=\d)", " ", normalized))
    best, best_len = "", 0
    for cand in [*PROVINCES, *_PROVINCE_ALIASES]:
        n = len(cand.split())
        if n > best_len and _find_seq(toks, cand) >= 0:
            best, best_len = _PROVINCE_ALIASES.get(cand, cand), n
    if best:
        return best
    return _national_prefix(fold(strip_group(group_title)))


def _has(tokens: list[str], keywords) -> bool:
    return any(_find_seq(tokens, k) >= 0 for k in keywords)


_RULES = (
    ("News", ("tin tuc", "news", "vnews")),
    ("Sports", ("the thao", "sport", "sports", "bong da", "football")),
    ("Kids", ("hoat hinh", "thieu nhi", "kids", "cartoon")),
    ("Movies", ("phim", "movie", "movies", "cinema")),
    ("Music", ("am nhac", "music", "radio")),
    ("Entertainment", ("giai tri", "entertainment", "kham pha", "discovery")),
    ("International", ("quoc te", "international", *COUNTRIES)),
    ("National TV", ("vtv", "htv", "vtc", "quoc gia")),
    ("General", ("general",)),
)


def category(group_title: str, broadcaster: str) -> str:
    """Automatic group, checked in spec order (Local, genre, International, National, ...)."""
    toks = _tokens(fold(strip_group(group_title)))
    if _find_seq(toks, "dia phuong") >= 0 or broadcaster in PROVINCES:
        return "Local TV"
    for cat, kws in _RULES:
        if cat == "National TV" and broadcaster in NATIONAL_BROADCASTERS:
            return cat
        if _has(toks, kws):
            return cat
    return "Unknown"
