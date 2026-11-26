"""アダプタが外と交わす形のうち、通信を伴わない部分。アダプタと Fake の両方がここを使う。

GitHubApi の契約 (全ページを読む・pull request を除く・created_at は Z の形) と、Fetcher の契約
(応答の大きさの上限・JSON でない応答は FetchError) の判定をここに置き、アダプタは urllib で
読み書きするだけにする。判定をここに置けば、アダプタと Fake の両方の側のテストが同じ判定に届く。
"""

import json
import re
from typing import Callable

from tech_briefing.protocols import CommentRef, FetchError, IssueRef

API_ROOT = "https://api.github.com"
# GitHub の REST の1ページの上限 (docs.github.com の pagination、2026-11-24 確認)
PER_PAGE = 100
# 全ページを読むが、終わらない Link の連鎖で run が止まらないように打ち切る。打ち切りは黙らずに例外にする
MAX_PAGES = 50

_SEGMENT = re.compile(r"[A-Za-z0-9._-]+")
_Z_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_LINK = re.compile(r"\s*<([^>]*)>\s*;(.*)")
_REL = re.compile(r'rel="([^"]*)"')


def is_repo_segment(value: str) -> bool:
    """GitHub の owner / repo の名前の1つの segment として受ける形か。. と .. はパスとして別の意味になるので外す。"""
    return _SEGMENT.fullmatch(value) is not None and value not in (".", "..")


def _repo(repo: str) -> str:
    parts = repo.split("/") if isinstance(repo, str) else []
    if len(parts) != 2 or not all(is_repo_segment(p) for p in parts):
        raise ValueError(f"リポジトリの名前が owner/repo の形ではありません: {repo!r}")
    return repo


def is_api_url(url: object) -> bool:
    """token を付けて送ってよい URL か (API の root の下の https)。ホスト名の前方一致で別のホストを通さないよう / まで見る。"""
    return isinstance(url, str) and url.startswith(API_ROOT + "/")


def issues_url(repo: str) -> str:
    return f"{API_ROOT}/repos/{_repo(repo)}/issues?state=open&per_page={PER_PAGE}"


def comments_url(repo: str, number: int) -> str:
    return f"{API_ROOT}/repos/{_repo(repo)}/issues/{int(number)}/comments?per_page={PER_PAGE}"


def post_comment_url(repo: str, number: int) -> str:
    return f"{API_ROOT}/repos/{_repo(repo)}/issues/{int(number)}/comments"


def next_link(header: str | None) -> str | None:
    """Link の header から rel="next" の URL を返す。無ければ None (最後のページ)。"""
    for part in (header or "").split(","):
        m = _LINK.fullmatch(part)
        if m is None:
            continue
        # rel は空白区切りで複数の値を持てる (RFC 8288)
        if any("next" in rel.split() for rel in _REL.findall(m.group(2))):
            return m.group(1)
    return None


def paginate(get_page: Callable[[str], tuple[object, str | None]], url: str, max_pages: int = MAX_PAGES) -> list:
    """get_page(url) が返す (本文, Link の header) を、次のページが無くなるまで辿って本文の配列をつなぐ。"""
    items: list = []
    for _ in range(max_pages):
        body, link = get_page(url)
        if not isinstance(body, list):
            raise ValueError(f"ページの本文が配列ではありません: {type(body).__name__}")
        items.extend(body)
        url = next_link(link)
        if url is None:
            return items
        # token を付けて送るので、次のページも同じ API の root の下に限る
        if not is_api_url(url):
            raise ValueError("次のページの URL が API の root の外を指しています")
    raise ValueError(f"ページが {max_pages} を超えました")


def _z_time(value: object) -> str:
    if not isinstance(value, str) or not _Z_TIME.fullmatch(value):
        raise ValueError(f"created_at が ISO 8601 の UTC (末尾 Z) の形ではありません: {value!r}")
    return value


def _login(item: dict) -> str:
    # 消えたアカウント (ghost) の投稿は user が null で届く
    user = item.get("user")
    login = user.get("login") if isinstance(user, dict) else None
    return login if isinstance(login, str) else ""


def to_issue(item: dict) -> IssueRef | None:
    """issues の一覧の1件を IssueRef にする。pull request は None (Issue の一覧の API は PR も返すため)。"""
    if "pull_request" in item:
        return None
    body = item.get("body")
    return IssueRef(
        number=int(item["number"]),
        title=str(item["title"]),
        author=_login(item),
        created_at=_z_time(item.get("created_at")),
        body=body if isinstance(body, str) else None,
    )


def to_comment(item: dict) -> CommentRef:
    body = item.get("body")
    return CommentRef(
        id=int(item["id"]),
        author=_login(item),
        body=body if isinstance(body, str) else "",
        created_at=_z_time(item.get("created_at")),
    )


def read_json_limited(stream, limit: int) -> object:
    """stream から上限まで読んで JSON を復号する。上限を超えた応答と JSON でない応答は FetchError。"""
    # 1バイト多く読めたら上限を超えている。全部を読んでから測らないのは、巨大な応答でメモリを食わないため
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise FetchError(f"応答が上限の {limit} バイトを超えました")
    try:
        return json.loads(data.decode("utf-8"))
    except ValueError as e:
        # UnicodeDecodeError と JSONDecodeError はどちらも ValueError
        raise FetchError(f"応答が JSON ではありません: {type(e).__name__}") from None
