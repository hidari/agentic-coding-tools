"""境界を流れる dataclass と、外部へ出る口の Protocol。本体は具体の実装を知らず、ここだけを見る。"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class IssueRef:
    number: int
    title: str
    author: str
    created_at: str
    # GitHub は本文が空の Issue で null を返す
    body: str | None


@dataclass(frozen=True)
class CommentRef:
    id: int
    author: str
    body: str
    created_at: str


class GitHubApi(Protocol):
    def open_issues(self) -> list[IssueRef]: ...

    def comments(self, number: int) -> list[CommentRef]: ...

    def post_comment(self, number: int, body: str) -> None: ...
