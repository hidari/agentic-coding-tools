"""テスト用の Fake。呼び出しを記録し、設定の誤りで api に触れないことを確かめられるようにする。"""

import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tech_briefing.protocols import CommentRef, IssueRef  # noqa: E402


class FakeGitHub:
    def __init__(self, issues=None, comments=None):
        self._issues: list[IssueRef] = list(issues or [])
        self._comments: dict[int, list[CommentRef]] = dict(comments or {})
        self.calls: list[tuple] = []
        self.posted: list[tuple[int, str]] = []

    def open_issues(self) -> list[IssueRef]:
        self.calls.append(("open_issues",))
        return list(self._issues)

    def comments(self, number: int) -> list[CommentRef]:
        self.calls.append(("comments", number))
        return list(self._comments.get(number, []))

    def post_comment(self, number: int, body: str) -> None:
        self.calls.append(("post_comment", number))
        self.posted.append((number, body))


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
        return copy.deepcopy(value)
