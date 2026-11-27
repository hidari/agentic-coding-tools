"""テスト用の Fake。呼び出しを記録し、設定の誤りで api に触れないことを確かめられるようにする。"""

import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tech_briefing import wire  # noqa: E402
from tech_briefing.protocols import CommentRef, IssueRef  # noqa: E402

# Fake が返してよい応答の形 (Messages API の応答の JSON。docs の Messages の応答と claude-api skill 2.1.331、
# 2026-11-24 確認)。ループの前提 (読む鍵・扱う stop_reason) がこの中にあることを test_fakes_contract が見る。
# 綴りの誤った鍵や存在しない type を Fake が受けると、ループのテストが実物と食い違ったまま緑になる
STOP_REASONS = frozenset({"end_turn", "max_tokens", "stop_sequence", "tool_use", "pause_turn", "refusal"})
USAGE_KEYS = frozenset({
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "cache_creation",
    "server_tool_use",
    "service_tier",
    "iterations",
})
BLOCK_TYPES = frozenset({
    "text",
    "thinking",
    "redacted_thinking",
    "tool_use",
    "server_tool_use",
    "web_search_tool_result",
    "web_fetch_tool_result",
    # dynamic filtering の code execution の結果 (docs の code execution と programmatic tool calling、2026-11-27 確認)
    "code_execution_tool_result",
    "bash_code_execution_tool_result",
    "text_editor_code_execution_tool_result",
    "fallback",
})


class FakeGitHub:
    """page_size を渡すと、GitHub の REST と同じ形の JSON をページに分けて Link の header で返し、
    アダプタと同じ tech_briefing.wire の paginate と変換を通す (全ページ・PR を除く・Z の形の契約を Fake も持つ)。
    raw_issues は Issue の一覧の API が返す生の要素 (pull request など) をそのまま混ぜる。
    """

    def __init__(self, issues=None, comments=None, page_size=None, raw_issues=()):
        self._issues: list[IssueRef] = list(issues or [])
        self._comments: dict[int, list[CommentRef]] = dict(comments or {})
        self._page_size = page_size
        self._raw_issues = list(raw_issues)
        self.calls: list[tuple] = []
        self.posted: list[tuple[int, str]] = []
        self.pages_served = 0

    def _paged(self, raw: list) -> list:
        size = self._page_size
        pages = [raw[i:i + size] for i in range(0, len(raw), size)] or [[]]
        urls = [f"{wire.API_ROOT}/fake?page={i + 1}" for i in range(len(pages))]

        def get_page(url):
            self.pages_served += 1
            i = urls.index(url)
            link = f'<{urls[i + 1]}>; rel="next", <{urls[-1]}>; rel="last"' if i + 1 < len(pages) else None
            return copy.deepcopy(pages[i]), link

        return wire.paginate(get_page, urls[0])

    def open_issues(self) -> list[IssueRef]:
        self.calls.append(("open_issues",))
        if self._page_size is None:
            return list(self._issues)
        raw = [_issue_json(i) for i in self._issues] + self._raw_issues
        return [i for i in map(wire.to_issue, self._paged(raw)) if i is not None]

    def comments(self, number: int) -> list[CommentRef]:
        self.calls.append(("comments", number))
        if self._page_size is None:
            return list(self._comments.get(number, []))
        return [wire.to_comment(c) for c in self._paged([_comment_json(c) for c in self._comments.get(number, [])])]

    def post_comment(self, number: int, body: str) -> None:
        self.calls.append(("post_comment", number))
        self.posted.append((number, body))


def _issue_json(issue: IssueRef) -> dict:
    return {
        "number": issue.number,
        "title": issue.title,
        "user": {"login": issue.author},
        "created_at": issue.created_at,
        "body": issue.body,
    }


def _comment_json(comment: CommentRef) -> dict:
    return {"id": comment.id, "user": {"login": comment.author}, "body": comment.body, "created_at": comment.created_at}


def _check_response(resp: dict) -> None:
    if not isinstance(resp, dict):
        raise AssertionError(f"応答が dict ではありません: {resp!r}")
    content, usage = resp.get("content"), resp.get("usage")
    if not isinstance(content, list) or any(not isinstance(b, dict) or b.get("type") not in BLOCK_TYPES for b in content):
        raise AssertionError(f"content の形が API と違います: {content!r}")
    if resp.get("stop_reason") not in STOP_REASONS:
        raise AssertionError(f"stop_reason が API に無い値です: {resp.get('stop_reason')!r}")
    if not isinstance(usage, dict) or not set(usage) <= USAGE_KEYS:
        raise AssertionError(f"usage の鍵が API と違います: {usage!r}")


class UnexpectedUrl(KeyError):
    """FakeFetcher に登録していない URL へ要求した。想定外の行き先を黙って通さないための失敗。

    FetchError とは別の型にし、道具が誤りの文字列へ変えずに上まで落ちるようにする。
    """


class FakeFetcher:
    """URL ごとに用意した JSON を返す。値が例外のインスタンスなら、それを投げる (FetchError の再現用)。"""

    def __init__(self, responses=None):
        self._responses: dict[str, object] = dict(responses or {})
        self.requested: list[str] = []

    def get_json(self, url: str) -> object:
        # 失敗する要求も行き先の証拠なので、投げる前に記録する
        self.requested.append(url)
        if url not in self._responses:
            raise UnexpectedUrl(url)
        value = self._responses[url]
        if isinstance(value, BaseException):
            raise value
        return value


class FakeMessages:
    """用意した応答を順に返し、受けた params を記録する。値が例外のインスタンスなら、それを投げる。"""

    def __init__(self, responses=None):
        self._responses: list[object] = list(responses or [])
        self.requests: list[dict] = []

    def create(self, params: dict) -> dict:
        # ループは messages を伸ばし続けるので、記録は複製にする (同じ list を指すと各回の要求を区別できない)
        self.requests.append(copy.deepcopy(params))
        if not self._responses:
            raise AssertionError("FakeMessages の応答を使い切りました")
        value = self._responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        _check_response(value)
        return copy.deepcopy(value)
