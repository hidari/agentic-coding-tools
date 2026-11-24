"""LLM が書いた文字列の無害化と、投稿するコメントの整形、post の判定。

post の job は issues: write を持つ唯一の job なので、LLM の文字列がメンション・他 Issue への自動リンク・
HTML・Markdown のリンク・表の列の増加になる経路をここで塞ぐ。ここの関数は例外を上げず、常に str を返す。
"""

import re

from tech_briefing.constants import (
    BOT_LOGIN,
    FAIL_INTERNAL_ERROR,
    FAILURE_KINDS,
    MARKER,
    SELECT_INPUT_TOO_LARGE,
    SELECT_TRIAGE,
    SELECT_UNEXPECTED,
)
from tech_briefing.protocols import GitHubApi

# 見えない文字は code point で組む。リテラルで書くとエディタや差分で見分けがつかなくなる
_ZWSP = chr(0x200B)
_LONE_SURROGATE = re.compile("[" + chr(0xD800) + "-" + chr(0xDFFF) + "]")
_NEWLINE = re.compile("\r\n|[\r\n\x0b\x0c\x85" + chr(0x2028) + chr(0x2029) + "]")
# select が URL を置き換え済みなので裸の URL は来ないはずだが、自動リンクを二重に防ぐ
_URL_SCHEME = re.compile(r"(https?:)(?=//)", re.IGNORECASE)

# 1 文字ずつの置き換え。1 回の走査で済ませ、追加した文字を後段が再び加工しないようにする。
# 先に \ を潰さないと、LLM が書いた \| が自分の \ で | のエスケープを打ち消し、表の列が増える
_CHAR_MAP = {
    "\\": "\\\\",
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "@": "@" + _ZWSP,
    "#": "#" + _ZWSP,
    "[": "\\[",
    "]": "\\]",
    "(": "\\(",
    ")": "\\)",
    "!": "\\!",
    "|": "\\|",
    "`": "\\`",
}

_CLASSIFICATIONS = ("A", "B", "C", "D")

_REPORT_HEADER = "## 技術情報のトリアージ結果"
_FAILURE_PREFIX = "トリアージできなかった: "
_UNEXPECTED_BODY = (
    "想定外の状態のため、自動の報告をしなかった。当日のタイトルの Issue が複数あるなど、"
    "対象を1つに決められなかった。人が Issue の状態を確認する。"
)
_TOO_LARGE_BODY = "入力が上限を超えたため、自動の報告をしなかった。Issue の本文とコメントの量を確認する。"


def sanitize(text: str) -> str:
    text = _LONE_SURROGATE.sub(chr(0xFFFD), str(text))
    text = _NEWLINE.sub(" ", text)
    text = _URL_SCHEME.sub(lambda m: m.group(1) + _ZWSP, text)
    return "".join(_CHAR_MAP.get(ch, ch) for ch in text)


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else []


def _row(entry: object) -> str:
    entry = entry if isinstance(entry, dict) else {}
    classification = entry.get("classification")
    # 許可した 4 値だけを通す。それ以外を sanitize して載せず "?" に固定する
    shown = classification if classification in _CLASSIFICATIONS else "?"
    sources = ", ".join(sanitize(s) for s in _as_list(entry.get("sources")) if isinstance(s, str))
    title = sanitize(entry.get("title") or "")
    findings = sanitize(entry.get("findings") or "")
    return f"| {title} | {shown} | {findings} | {sources} |"


def render_report(report: dict) -> str:
    report = report if isinstance(report, dict) else {}
    lines = [
        MARKER,
        _REPORT_HEADER,
        "",
        "| 項目 | 分類 | 所見 | 取得元 |",
        "| --- | --- | --- | --- |",
    ]
    lines += [_row(e) for e in _as_list(report.get("items"))]
    lines += ["", "### 取得できなかった取得元", ""]
    unreachable = [sanitize(s) for s in _as_list(report.get("unreachable_sources")) if isinstance(s, str)]
    lines += [f"- {s}" for s in unreachable] or ["なし"]
    notes = report.get("notes")
    lines += ["", "### 補足", "", sanitize(notes) if isinstance(notes, str) and notes else "なし"]
    return "\n".join(lines) + "\n"


def render_failure(kind: str) -> str:
    # 種類は固定の語彙だけを通す。想定外の値を本文へ写さない
    shown = kind if kind in FAILURE_KINDS else FAIL_INTERNAL_ERROR
    return f"{MARKER}\n{_FAILURE_PREFIX}{shown}\n"


def render_unexpected() -> str:
    return f"{MARKER}\n{_UNEXPECTED_BODY}\n"


def render_too_large() -> str:
    return f"{MARKER}\n{_TOO_LARGE_BODY}\n"


def should_post(api: GitHubApi, number: int) -> bool:
    """書き込みの名義の印付きのコメントが既に在れば False。名義は完全一致で比べる。"""
    for c in api.comments(number):
        if c.author == BOT_LOGIN and c.body.lstrip().startswith(MARKER):
            return False
    return True


def plan_post(select_result: str, report: dict | None, failure: str | None) -> tuple[str, int]:
    """投稿する本文と、投稿のあとの終了コードを返す。dry-run かどうかは呼び出し側が決める。"""
    if select_result == SELECT_UNEXPECTED:
        return render_unexpected(), 1
    if select_result == SELECT_INPUT_TOO_LARGE:
        return render_too_large(), 1
    if select_result == SELECT_TRIAGE:
        if report is not None:
            return render_report(report), 0
        if failure is not None:
            return render_failure(failure), 1
    return render_failure(FAIL_INTERNAL_ERROR), 1
