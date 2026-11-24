"""select job の判定表と input.json の組み立て。LLM は呼ばない。

本文とコメントは外から集めた信頼できない文章なので、ここでは構造だけを扱い、中身の指示には従わない。
"""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from tech_briefing.caller_check import check_caller
from tech_briefing.config import Config, ConfigError, load_config
from tech_briefing.constants import (
    BOT_LOGIN,
    MARKER,
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
from tech_briefing.protocols import CommentRef, GitHubApi, IssueRef
from tech_briefing.textproc import extract_advisory_ids, replace_urls


@dataclass(frozen=True)
class Decision:
    result: str
    issue: IssueRef | None
    input: dict | None
    summary: str


def today_jst(now_utc: datetime) -> str:
    # 日付はブリーフィングの側の規約に合わせて JST で決める。UTC で切ると 15:00 UTC 以降に前日のままになる
    return now_utc.astimezone(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")


def is_red(result: str) -> bool:
    return result in SELECT_RED_RESULTS


def _is_marked(comment: CommentRef) -> bool:
    return comment.body.lstrip().startswith(MARKER)


def _same_login(a: str, b: str) -> bool:
    # GitHub のログインは大文字小文字を区別しない
    return a.casefold() == b.casefold()


def _newest(issues: list[IssueRef]) -> IssueRef:
    # created_at は ISO 8601 (UTC, Z) なので文字列順が時刻順。同時刻は番号の大きい方を新しいとする
    return max(issues, key=lambda i: (i.created_at, i.number))


def _decision(result: str, issue: IssueRef | None, summary: str, input_: dict | None = None) -> Decision:
    return Decision(result, issue, input_, f"{result}: {summary}")


def _build_input(issue: IssueRef, comments: list[CommentRef], author: str):
    """入力の dict と原文の文字数を返す。印で始まるコメントはどのアカウントのものも外す。"""
    by_author = [c for c in comments if _same_login(c.author, author)]
    removed = sum(1 for c in by_author if _is_marked(c))
    kept = sorted((c for c in by_author if not _is_marked(c)), key=lambda c: c.created_at)
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

    title = TITLE_PREFIX + today_jst(now_utc)
    date = today_jst(now_utc)
    issues = api.open_issues()
    exact = [i for i in issues if i.title == title]
    mine = [i for i in exact if _same_login(i.author, author)]

    if not mine:
        near = [
            i
            for i in issues
            if _same_login(i.author, author) and i.title.startswith(TITLE_PREFIX) and date in i.title
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
    processed = any(_is_marked(c) and c.author == BOT_LOGIN for c in comments)
    if processed:
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
    api: GitHubApi,
    author: str,
    now_utc: datetime,
    max_input_chars: int,
) -> tuple[Decision, Config | None]:
    # 設定の誤りは Issue を特定する前に終える。api には触れない
    try:
        config = load_config(raw_config)
    except ConfigError as e:
        return _decision(SELECT_CONFIG_ERROR, None, f"設定が不正です: {e}"), None
    violations = check_caller(caller_workflow, reusable)
    if violations:
        return _decision(SELECT_CONFIG_ERROR, None, "呼び出し側の検査に違反があります: " + "; ".join(violations)), None
    decision = decide(api, author, now_utc, max_input_chars)
    return decision, (None if decision.result == SELECT_CONFIG_ERROR else config)
