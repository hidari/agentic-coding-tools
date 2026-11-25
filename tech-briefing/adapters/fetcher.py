"""Fetcher の実物。公開の JSON の API (registry、OSV、GitHub の releases) を認証なしで GET する。

資格情報はどのホストにも付けない。release notes を token 付きで引くと、LLM が選んだパッケージを経由して
private の release まで読めるため。redirect は追わず、応答の大きさと時間に上限を置き、失敗はすべて
FetchError に揃える (道具はこの型だけを LLM 向けの誤りの文字列に変える)。
"""

import http.client
import urllib.error
import urllib.request

from adapters.http_common import USER_AGENT, opener
from tech_briefing.protocols import FetchError
from tech_briefing.wire import read_json_limited

FETCH_TIMEOUT_SECONDS = 20
# 道具の結果は tools の側でさらに切るので、ここは通信の天井 (registry の metadata の大きいものが入る大きさ)
MAX_BYTES = 5 * 1024 * 1024


class UrllibFetcher:
    def __init__(self):
        self._opener = opener()

    def get_json(self, url: str) -> object:
        if not isinstance(url, str) or not url.startswith("https://"):
            raise FetchError("https の URL だけを開きます")
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}, method="GET")
        try:
            with self._opener.open(req, timeout=FETCH_TIMEOUT_SECONDS) as resp:
                return read_json_limited(resp, MAX_BYTES)
        except FetchError:
            raise
        except urllib.error.HTTPError as e:
            raise FetchError(f"HTTP {e.code}", e.code) from None
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as e:
            # URLError は接続の失敗、HTTPException と OSError (TimeoutError を含む) は読み取りの途中の失敗、
            # ValueError は不正な URL
            raise FetchError(f"通信に失敗しました: {type(e).__name__}") from None
