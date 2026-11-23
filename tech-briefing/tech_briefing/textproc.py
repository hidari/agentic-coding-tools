"""advisory の ID の取り出しと、本文中の URL の置き換え。

ID は URL の置き換えより先に取る。GHSA の ID は URL のパスにも現れ、
置き換えた後では失われるため。
"""
import re
from urllib.parse import urlsplit

from .constants import URL_REPLACEMENT

_ID_PATTERN = re.compile(
    r"GHSA(?:-[a-z0-9]{4}){3}(?![a-z0-9])"
    r"|CVE-\d{4}-\d{4,}"
    r"|RUSTSEC-\d{4}-\d{4}(?!\d)",
    re.IGNORECASE,
)

# URL の終端: 空白・閉じ括弧類・全角の句読点と全角空白。
# 国際化ドメインを通すため、終端以外は ASCII に限らず URL に含める
_URL_BODY = r"https?://[^\s)>\]、。，．）」　]+"
_URL_PATTERN = re.compile(
    # [text](url) と <url> は囲み記号ごと 1 つの置き換えにする
    r"\[[^\]]*\]\(\s*(?P<md>" + _URL_BODY + r")[^)]*\)"
    r"|<(?P<angle>https?://[^\s>]+)>"
    r"|(?P<bare>" + _URL_BODY + r")"
)
# URL の一部ではなく文の句読点として扱う末尾の ASCII 文字
_TRAILING_PUNCT = ".,;:!?"


def _normalize_id(raw: str) -> str:
    if raw[:4].upper() == "GHSA":
        # GHSA の本体は小文字が正規形
        return "GHSA" + raw[4:].lower()
    return raw.upper()


def extract_advisory_ids(text: str) -> list[str]:
    """GHSA / CVE / RUSTSEC の ID を出現順・重複なしで返す。"""
    seen: dict[str, None] = {}
    for m in _ID_PATTERN.finditer(text):
        seen.setdefault(_normalize_id(m.group(0)), None)
    return list(seen)


def _link(url: str) -> str:
    # 国際化ドメインは書かれたまま残す (punycode へ変換しない)
    host = urlsplit(url).hostname or ""
    return URL_REPLACEMENT.format(host=host)


def _replace(m: re.Match) -> str:
    if m.group("md") is not None:
        return _link(m.group("md"))
    if m.group("angle") is not None:
        return _link(m.group("angle"))
    url = m.group("bare")
    stripped = url.rstrip(_TRAILING_PUNCT)
    return _link(stripped) + url[len(stripped):]


def replace_urls(text: str) -> str:
    """URL をホスト名だけの `[link: <host>]` へ置き換える。"""
    return _URL_PATTERN.sub(_replace, text)
