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
    """GitHub の API の口。実装 (Task 9 のアダプタ) と Fake が次の契約を守る。

    - open_issues() と comments() は全ページ分を返す (1ページの上限で打ち切らない)
    - open_issues() は pull request を含まない
    - created_at は ISO 8601 の UTC (末尾 Z) の文字列。selection は文字列順を時刻順として使う
    契約の担保はアダプタと Fake の契約テストが持つ。
    """

    def open_issues(self) -> list[IssueRef]: ...

    def comments(self, number: int) -> list[CommentRef]: ...

    def post_comment(self, number: int, body: str) -> None: ...
