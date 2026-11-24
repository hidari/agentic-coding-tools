"""urllib の redirect を拒否する handler。

urllib.request.HTTPRedirectHandler は redirect の先へ要求の header (add_header で足したもの) を
そのまま付けて送り直す (Python 3.15 の urllib/request.py の redirect_request、2026-11-24 確認)。
別のホストへ token が渡るのと、LLM の選んだ URL から行き先が広がるのを防ぐため、redirect は追わずに失敗にする。
"""

import urllib.request

from tech_briefing.protocols import FetchError


class RefuseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise FetchError(f"redirect は追いません (HTTP {code})", code)


def opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(RefuseRedirect)
