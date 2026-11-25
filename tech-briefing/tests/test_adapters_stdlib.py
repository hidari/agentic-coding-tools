"""標準ライブラリだけで書いたアダプタ (http_common・github_api・fetcher) の、token と取得先を守る検査。

アダプタは test_entrypoints が入口を読むのと同じく、importlib でファイルのパスから読む。読むのは依存の無い
アダプタだけで、依存のあるもの (anthropic_client・yaml_loader) は読まない (CI のテストに依存を増やさないため)。
送信の口 (`_opener`) を記録係に差し替え、通信はしない。
"""

import ast
import importlib.util
import os
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tech_briefing import wire  # noqa: E402
from tech_briefing.protocols import FetchError  # noqa: E402

TB = Path(__file__).resolve().parent.parent
STDLIB_ADAPTERS = ("http_common", "github_api", "fetcher")
# アダプタが import してよい、標準ライブラリの外の名前 (同じリポジトリの中のパッケージ)
_IN_REPO = {"adapters", "tech_briefing"}
# token は形だけの値。実物の token と取り違えない並びを変数で組む
TOKEN = "-".join(["test", "token", "value"])


def load_adapter(name: str):
    if name not in STDLIB_ADAPTERS:
        raise AssertionError(f"依存のあるアダプタはテストから読みません: {name}")
    spec = importlib.util.spec_from_file_location(f"tb_adapter_{name}", TB / "adapters" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def refuse_redirect_class(module):
    # github_api と fetcher は adapters.http_common を通常の import で読むので、ファイルから別に読んだ
    # http_common とはクラスの実体が違う。各アダプタが実際に使う opener の module から取る
    return sys.modules[module.opener.__module__].RefuseRedirect


class FakeResponse:
    """opener.open が返す応答の Fake。read に渡された上限を記録する。"""

    def __init__(self, body: bytes = b"[]", link: str | None = None):
        self._body = body
        self.headers = {"Link": link} if link is not None else {}
        self.read_limits = []

    def read(self, limit: int = -1) -> bytes:
        self.read_limits.append(limit)
        return self._body if limit < 0 else self._body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Recorder:
    """送信の口の Fake。送った要求と timeout を記録し、決まった応答を返す。"""

    def __init__(self, body: bytes = b"[]"):
        self.sent = []
        self.responses = []
        self._body = body

    def open(self, req, timeout=None):
        self.sent.append((req, timeout))
        resp = FakeResponse(self._body)
        self.responses.append(resp)
        return resp


def headers_of(req) -> dict[str, str]:
    # urllib は header の名前を capitalize して持つので、casefold して比べる
    return {k.casefold(): v for k, v in req.header_items()}


class StdlibOnly(unittest.TestCase):
    def test_loaded_adapters_import_only_stdlib_and_repo_packages(self):
        # 依存の無いことが、ファイルから読んでよい前提。依存が増えたらここで止める
        for name in STDLIB_ADAPTERS:
            with self.subTest(name):
                tree = ast.parse((TB / "adapters" / f"{name}.py").read_text(encoding="utf-8"))
                tops = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        tops.update(a.name.split(".")[0] for a in node.names)
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        tops.add(node.module.split(".")[0])
                self.assertNotEqual(tops, set())
                self.assertEqual(tops - set(sys.stdlib_module_names) - _IN_REPO, set())

    def test_loader_refuses_adapters_with_deps(self):
        for name in ("anthropic_client", "yaml_loader"):
            with self.subTest(name):
                with self.assertRaises(AssertionError):
                    load_adapter(name)


class Redirects(unittest.TestCase):
    def test_opener_refuses_redirects(self):
        hc = load_adapter("http_common")
        handlers = hc.opener().handlers
        redirectors = [h for h in handlers if isinstance(h, urllib.request.HTTPRedirectHandler)]
        # 素の HTTPRedirectHandler は redirect の先へ header を付けたまま送り直す
        self.assertEqual([h for h in redirectors if type(h) is urllib.request.HTTPRedirectHandler], [])
        self.assertEqual([type(h) for h in redirectors], [hc.RefuseRedirect])

    def test_refuse_redirect_raises_fetch_error(self):
        hc = load_adapter("http_common")
        req = urllib.request.Request(wire.API_ROOT + "/x")
        with self.assertRaises(FetchError) as cm:
            hc.RefuseRedirect().redirect_request(req, None, 302, "Found", {}, "https://example.org/y")
        self.assertEqual(cm.exception.status, 302)

    def test_each_adapter_wires_the_refusing_opener(self):
        # 記録係に差し替える前の、組み立てたままの opener を見る (差し替えた後では配線が見えない)
        gh = load_adapter("github_api")
        fe = load_adapter("fetcher")
        for label, module, instance in (
            ("github_api", gh, gh.GitHubRest(TOKEN, "o/r")),
            ("fetcher", fe, fe.UrllibFetcher()),
        ):
            with self.subTest(label):
                types = [type(h) for h in instance._opener.handlers]
                self.assertIn(refuse_redirect_class(module), types)
                self.assertNotIn(urllib.request.HTTPRedirectHandler, types)


class GitHubRestGuards(unittest.TestCase):
    def setUp(self):
        self.gh = load_adapter("github_api")
        self.rest = self.gh.GitHubRest(TOKEN, "o/r")
        self.recorder = Recorder()
        self.rest._opener = self.recorder

    def test_empty_token_is_refused(self):
        for token in ("", None):
            with self.subTest(token=token):
                with self.assertRaises(ValueError):
                    self.gh.GitHubRest(token, "o/r")

    def test_does_not_send_outside_api_root(self):
        for url in (
            "https://api.github.com.example.org/x",
            "http://api.github.com/x",
            "https://example.org/x",
            wire.API_ROOT,
        ):
            with self.subTest(url):
                with self.assertRaises(FetchError):
                    self.rest._request("GET", url)
                self.assertEqual(self.recorder.sent, [])
        # 対照: root の下の URL は token 付きで送る
        body, _ = self.rest._request("GET", wire.issues_url("o/r"))
        self.assertEqual(body, [])
        (req, timeout), = self.recorder.sent
        sent = headers_of(req)
        self.assertEqual(sent["authorization"], f"Bearer {TOKEN}")
        self.assertIn("user-agent", sent)
        self.assertEqual(timeout, self.gh.GITHUB_TIMEOUT_SECONDS)
        self.assertEqual(self.recorder.responses[0].read_limits, [self.gh.MAX_PAGE_BYTES + 1])

    def test_http_error_becomes_fetch_error_with_status(self):
        def failing(req, timeout=None):
            err = urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
            # HTTPError は一時ファイルを持ち、閉じずに捨てると ResourceWarning が出る (Python 3.15.0、2026-11-25 確認)
            self.addCleanup(err.close)
            raise err

        self.recorder.open = failing
        with self.assertRaises(FetchError) as cm:
            self.rest.comments(7)
        self.assertEqual(cm.exception.status, 404)


class FetcherGuards(unittest.TestCase):
    def setUp(self):
        self.fe = load_adapter("fetcher")
        self.fetcher = self.fe.UrllibFetcher()
        self.recorder = Recorder(b'{"ok": true}')
        self.fetcher._opener = self.recorder

    def test_does_not_send_non_https(self):
        for url in ("http://registry.npmjs.org/x", "file:///etc/passwd", "ftp://example.org/x", None):
            with self.subTest(url=url):
                with self.assertRaises(FetchError):
                    self.fetcher.get_json(url)
                self.assertEqual(self.recorder.sent, [])
        # 対照: https の URL は送る
        self.assertEqual(self.fetcher.get_json("https://registry.npmjs.org/x"), {"ok": True})
        self.assertEqual(len(self.recorder.sent), 1)

    def test_sends_no_credentials_and_keeps_limits(self):
        self.fetcher.get_json("https://api.osv.dev/v1/vulns/x")
        (req, timeout), = self.recorder.sent
        sent = headers_of(req)
        self.assertNotIn("authorization", sent)
        self.assertEqual(req.unredirected_hdrs, {})
        # 対照: header の集め方は働いている
        self.assertIn("user-agent", sent)
        self.assertEqual(timeout, self.fe.FETCH_TIMEOUT_SECONDS)
        self.assertEqual(self.recorder.responses[0].read_limits, [self.fe.MAX_BYTES + 1])


if __name__ == "__main__":
    unittest.main()
