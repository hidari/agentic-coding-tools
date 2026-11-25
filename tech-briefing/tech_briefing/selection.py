"""select job の判定表と input.json の組み立て。LLM は呼ばない。

本文とコメントは外から集めた信頼できない文章なので、ここでは構造だけを扱い、中身の指示には従わない。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

from tech_briefing.caller_check import check_caller
from tech_briefing.config import ConfigError, load_config
from tech_briefing.constants import (
    BOT_LOGIN,
    SELECT_ALREADY_PROCESSED,
    SELECT_CONFIG_ERROR,
    SELECT_INPUT_TOO_LARGE,
    SELECT_NO_ISSUE,
    SELECT_RED_RESULTS,
    SELECT_TITLE_MISMATCH,
    SELECT_TRIAGE,
    SELECT_UNEXPECTED,
    SELECT_WRONG_AUTHOR,
    TIMEZONE,
    TITLE_PREFIX,
)
from tech_briefing.marker import is_bot_report, is_marked
from tech_briefing.protocols import CommentRef, GitHubApi, IssueRef
from tech_briefing.textproc import extract_advisory_ids, replace_urls


# 本文とコメントの文字数の合計の上限 (仮の値)。Issue の本文の上限 65536 文字に数件のコメントが乗る大きさで、
# 実際のブリーフィングの大きさを測ってから決め直す
MAX_INPUT_CHARS = 100_000


@dataclass(frozen=True)
class Decision:
    result: str
    issue: IssueRef | None
    input: dict | None
    summary: str


def today_jst(now_utc: datetime) -> str:
    # タイトルの日付が JST で書かれる前提に合わせて JST で決める。UTC で切ると 15:00 UTC 以降に前日のままになる
    return now_utc.astimezone(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")


def is_red(result: str) -> bool:
    return result in SELECT_RED_RESULTS


def _same_login(a: str, b: str) -> bool:
    # GitHub のログインは大文字小文字を区別しない
    return a.casefold() == b.casefold()


def _newest(issues: list[IssueRef]) -> IssueRef:
    # GitHubApi の契約 (全ページ・PR 除外・created_at は ISO 8601 UTC Z) に依存する。
    # created_at は ISO 8601 (UTC, Z) なので文字列順が時刻順。同時刻は番号の大きい方を新しいとする
    return max(issues, key=lambda i: (i.created_at, i.number))


def _decision(result: str, issue: IssueRef | None, summary: str, input_: dict | None = None) -> Decision:
    return Decision(result, issue, input_, f"{result}: {summary}")


def _build_input(issue: IssueRef, comments: list[CommentRef], author: str):
    """入力の dict と原文の文字数を返す。

    入力に載せるのは指定アカウントのコメントだけ。そのうち印で始まるもの (書き込みの名義を装ったもの) を外し、
    外した件数を数える。ほかのアカウントのコメントは印の有無を問わず最初から載せない。
    名義を問わない is_marked で外す。is_bot_report で外すと、指定アカウントの印付きのコメントが入力に残る
    """
    by_author = [c for c in comments if _same_login(c.author, author)]
    removed = sum(1 for c in by_author if is_marked(c))
    kept = sorted((c for c in by_author if not is_marked(c)), key=lambda c: c.created_at)
    body = issue.body or ""
    size = len(body) + sum(len(c.body) for c in kept)
    # ID の取り出しは URL の置き換えより先。置き換えで URL 内の ID が消えるため
    ids = extract_advisory_ids("\n".join([body] + [c.body for c in kept]))
    data = {
        "issue_number": issue.number,
        "title": issue.title,
        "body": replace_urls(body),
        "comments": [
            {"author": c.author, "created_at": c.created_at, "body": replace_urls(c.body)} for c in kept
        ],
        "advisory_ids": ids,
        "removed_marked_by_author": removed,
    }
    return data, size


def decide(api: GitHubApi, author: str, now_utc: datetime, max_input_chars: int) -> Decision:
    if not author:
        return _decision(SELECT_CONFIG_ERROR, None, "TECH_BRIEFING_AUTHOR が空です")
    if _same_login(author, BOT_LOGIN):
        return _decision(
            SELECT_CONFIG_ERROR, None, f"TECH_BRIEFING_AUTHOR に書き込みの名義 {BOT_LOGIN} は指定できません"
        )

    date = today_jst(now_utc)
    title = TITLE_PREFIX + date
    issues = api.open_issues()
    exact = [i for i in issues if i.title == title]
    mine = [i for i in exact if _same_login(i.author, author)]

    if not mine:
        # 接頭辞は末尾の空白を外して比べる。空白が無い・タブになった形も「近い」に入れ、no-issue の緑で見逃さない
        prefix = TITLE_PREFIX.rstrip()
        near = [
            i
            for i in issues
            if _same_login(i.author, author) and i.title.startswith(prefix) and date in i.title
        ]
        # 指定アカウントの近いタイトルは、他者の完全一致より先に見る。形が変わった投稿を緑で見逃さないため
        if near:
            return _decision(
                SELECT_TITLE_MISMATCH, _newest(near), f"タイトルが {title!r} と完全一致しない Issue があります"
            )
        if exact:
            return _decision(SELECT_WRONG_AUTHOR, _newest(exact), "当日のタイトルの Issue の作者が指定アカウントではありません")
        return _decision(SELECT_NO_ISSUE, None, "当日の Issue はありません")

    target = _newest(mine)
    comments = api.comments(target.number)
    if any(is_bot_report(c) for c in comments):
        return _decision(SELECT_ALREADY_PROCESSED, target, f"#{target.number} は処理済みです")
    if len(mine) >= 2:
        return _decision(SELECT_UNEXPECTED, target, f"指定アカウントの当日の Issue が {len(mine)} 件あります")

    data, size = _build_input(target, comments, author)
    if size > max_input_chars:
        return _decision(
            SELECT_INPUT_TOO_LARGE, target, f"入力が {size} 文字で上限 {max_input_chars} を超えます"
        )
    return _decision(SELECT_TRIAGE, target, f"#{target.number} を分類します", data)


def run_select(
    raw_config: object,
    caller_workflow: object,
    reusable: str,
    workflow_sha: str,
    read_text: Callable[[str], str | None],
    api: GitHubApi,
    author: str,
    now_utc: datetime,
    max_input_chars: int,
) -> Decision:
    """read_text は呼び出し側のリポジトリの root からの相対パスを読み、読めなければ None を返す。"""
    # 設定の誤りは Issue を特定する前に終える。api には触れない
    try:
        config = load_config(raw_config)
    except ConfigError as e:
        return _decision(SELECT_CONFIG_ERROR, None, f"設定が不正です: {e}")
    # criteria が読めない誤りを triage まで持ち越すと、失敗の報告を投稿してその日を処理済みにしてしまう。
    # 中身は要約にも入力にも載せず、パスだけを書く
    criteria = read_text(config.criteria)
    if criteria is None or not criteria.strip():
        return _decision(SELECT_CONFIG_ERROR, None, f"criteria のファイルを読めないか、中身が空です: {config.criteria}")
    violations = check_caller(caller_workflow, reusable, workflow_sha)
    if violations:
        return _decision(SELECT_CONFIG_ERROR, None, "呼び出し側の検査に違反があります: " + "; ".join(violations))
    return decide(api, author, now_utc, max_input_chars)
