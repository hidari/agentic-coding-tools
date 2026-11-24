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


class FetchError(Exception):
    """Fetcher の宣言された失敗。HTTP の失敗・JSON でない応答・通信の失敗をこの型に揃える。

    status は HTTP の状態コード (応答が無い失敗では None)。道具はこの型だけを捕まえて
    LLM 向けの誤りの文字列に変える。これ以外の例外は想定外として呼び出し側まで上げる。
    """

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class Fetcher(Protocol):
    """外部の JSON の API を読む口。行き先の URL は常に tools が組み立て、LLM は URL を渡せない。

    - get_json(url) は応答の JSON を復号した値を返す
    - 失敗は FetchError で知らせる (実装の Task のアダプタがこの契約を守る)
    """

    def get_json(self, url: str) -> object: ...


class MessagesClient(Protocol):
    """Messages API の口。params と応答は API の JSON の形の dict で、SDK との変換はアダプタが持つ。

    - create(params) は1回の要求を送り、応答の dict (content / stop_reason / usage) を返す
    - 再試行はアダプタが持つ。再試行のあとも失敗したら例外を上げる (ループは種類を問わず api-error にする)
    """

    def create(self, params: dict) -> dict: ...
