"""テスト用の Fake。呼び出しを記録し、設定の誤りで api に触れないことを確かめられるようにする。"""

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
