"""GitHubApi の実物。GitHub の REST を urllib と GITHUB_TOKEN で呼ぶ。

全ページを読む・pull request を除く・created_at を Z の形のまま返す、の判定は tech_briefing.wire が持ち、
ここは送受信だけをする。redirect は追わない (token を付けた header が別のホストへ送り直されるため)。
"""

import json
import urllib.error
import urllib.request

from adapters.http_common import USER_AGENT, opener
from tech_briefing import wire
from tech_briefing.protocols import CommentRef, FetchError, IssueRef

GITHUB_TIMEOUT_SECONDS = 30
# 1ページ (100件) のコメントは本文が長くても数 MB に収まる
MAX_PAGE_BYTES = 20 * 1024 * 1024
# REST の版を固定する (docs.github.com の API versions、2026-11-24 確認)
API_VERSION = "2022-11-28"


class GitHubError(Exception):
    def __init__(self, message: str, status: int | None):
        super().__init__(message)
        self.status = status


class GitHubRest:
    def __init__(self, token: str, repo: str):
        if not token:
            raise ValueError("GITHUB_TOKEN が空です")
        self._token = token
        self._repo = repo
        self._opener = opener()

    def _request(self, method: str, url: str, body: dict | None = None) -> tuple[object, str | None]:
        if not url.startswith(wire.API_ROOT + "/"):
            raise GitHubError("API の root の外へは token を送りません", None)
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", API_VERSION)
        req.add_header("User-Agent", USER_AGENT)
        req.add_header("Authorization", f"Bearer {self._token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with self._opener.open(req, timeout=GITHUB_TIMEOUT_SECONDS) as resp:
                return wire.read_json_limited(resp, MAX_PAGE_BYTES), resp.headers.get("Link")
        except urllib.error.HTTPError as e:
            raise GitHubError(f"GitHub の API が HTTP {e.code} を返しました: {method} {url}", e.code) from None
        except FetchError as e:
            # redirect の拒否と、大きさ・JSON の誤り
            raise GitHubError(f"GitHub の API の応答を受けられません: {e}", e.status) from None

    def _get_page(self, url: str) -> tuple[object, str | None]:
        return self._request("GET", url)

    def open_issues(self) -> list[IssueRef]:
        items = wire.paginate(self._get_page, wire.issues_url(self._repo))
        return [i for i in map(wire.to_issue, items) if i is not None]

    def comments(self, number: int) -> list[CommentRef]:
        return [wire.to_comment(c) for c in wire.paginate(self._get_page, wire.comments_url(self._repo, number))]

    def post_comment(self, number: int, body: str) -> None:
        # 2xx 以外は HTTPError として上の _request が例外にする
        self._request("POST", wire.post_comment_url(self._repo, number), {"body": body})
