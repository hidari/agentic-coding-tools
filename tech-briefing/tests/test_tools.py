import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from fakes import FakeFetcher, UnexpectedUrl  # noqa: E402
from test_config import ACCEPT_PATHS, REJECT_PATHS, valid  # noqa: E402
from tech_briefing.config import Config, ConfigError, FileRule, load_config  # noqa: E402
from tech_briefing.constants import WEB_FETCH_TOOL, WEB_SEARCH_TOOL  # noqa: E402
from tech_briefing.protocols import FetchError  # noqa: E402
from tech_briefing.tools import (  # noqa: E402
    _PATH_MESSAGES,
    ERROR_PREFIX,
    REPO_UNKNOWN,
    REPORT_SCHEMA,
    ToolBox,
)

LIMIT = 100_000
NPM = "https://registry.npmjs.org/"
CRATES = "https://crates.io/api/v1/crates/"
PYPI = "https://pypi.org/pypi/"
OSV = "https://api.osv.dev/v1/vulns/"
RELEASES = "https://api.github.com/repos/"
# 漏洩検査のメールアドレスの規則は user@host の literal に当たるので、@ を分けて組み立てる
AT = "@"
FIXED_HOSTS = {"api.osv.dev", "registry.npmjs.org", "crates.io", "pypi.org", "api.github.com"}


def config(files=None, issues_glob=None, registries=("npm", "crates", "pypi"), sources=("docs.example.org",)):
    if files is None:
        files = (
            FileRule("package.json", True),
            FileRule("Cargo.lock", True),
            FileRule("docs/*.md", False),
            FileRule("link*.json", True),
            FileRule("linkdir/*", True),
        )
    return Config("基準", tuple(sources), tuple(files), issues_glob, tuple(registries))


class RepoCase(unittest.TestCase):
    """一時ディレクトリにリポジトリを作る。symlink は本物を置く。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.write("package.json", '{"dependencies": {"lodash": "4.17.21"}}\nliteral a.c here\n')
        self.write("Cargo.lock", 'name = "serde"\nversion = "1.0.0"\nabc only\n')
        self.write("docs/criteria.md", "lodash は使っている\n")
        self.write("secret.txt", "lodash secret\n")
        # リポジトリの中を指す symlink。到達先は許したファイルなので、拒否できるのは symlink の検査だけ
        os.symlink("package.json", self.root / "link.json")
        os.symlink("docs", self.root / "linkdir")
        self.outside = tempfile.TemporaryDirectory()
        self.addCleanup(self.outside.cleanup)
        Path(self.outside.name, "x.json").write_text("outside lodash\n")
        os.symlink(Path(self.outside.name, "x.json"), self.root / "link_out.json")

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def box(self, cfg=None, fetcher=None, limit=LIMIT):
        self.fetcher = fetcher if fetcher is not None else FakeFetcher()
        return ToolBox(self.root, cfg or config(), self.fetcher, limit)


class ReadFile(RepoCase):
    def test_reads_allowed_file(self):
        out = self.box().call("read_file", {"path": "package.json"})
        self.assertIn("lodash", out)
        self.assertFalse(out.startswith(ERROR_PREFIX))

    def test_reads_non_searchable_allowed_file(self):
        out = self.box().call("read_file", {"path": "docs/criteria.md"})
        self.assertEqual(out, "lodash は使っている\n")

    def test_rejects_outside_files(self):
        out = self.box().call("read_file", {"path": "secret.txt"})
        self.assertTrue(out.startswith(ERROR_PREFIX))
        self.assertNotIn("lodash secret", out)

    def test_rejects_dotdot_and_absolute(self):
        b = self.box(config(files=(FileRule("*", False),)))
        for path in ("../x", "docs/../package.json", "/etc/passwd", str(self.root / "package.json"), "./package.json", "docs//criteria.md", ""):
            with self.subTest(path=path):
                self.assertTrue(b.call("read_file", {"path": path}).startswith(ERROR_PREFIX))

    def test_rejects_symlink_file_inside_repo(self):
        out = self.box().call("read_file", {"path": "link.json"})
        self.assertTrue(out.startswith(ERROR_PREFIX))
        self.assertNotIn("lodash", out)

    def test_rejects_symlink_parent_dir(self):
        out = self.box().call("read_file", {"path": "linkdir/criteria.md"})
        self.assertTrue(out.startswith(ERROR_PREFIX))
        self.assertNotIn("lodash", out)

    def test_rejects_symlink_to_outside(self):
        out = self.box().call("read_file", {"path": "link_out.json"})
        self.assertTrue(out.startswith(ERROR_PREFIX))
        self.assertNotIn("outside", out)

    def test_rejects_non_regular_and_missing(self):
        os.mkfifo(self.root / "fifo.json")
        b = self.box(config(files=(FileRule("*", False),)))
        for path in ("fifo.json", "docs", "missing.json", "a\x00b"):
            with self.subTest(path=path):
                self.assertTrue(b.call("read_file", {"path": path}).startswith(ERROR_PREFIX))

    def test_rejects_git_dir_even_under_broad_glob(self):
        # .git/config には checkout の認証情報が残りうるので、glob が広くても読ませない
        self.write(".git/config", "extraheader = AUTHORIZATION: token\n")
        b = self.box(config(files=(FileRule("*", True),)))
        self.assertTrue(b.call("read_file", {"path": ".git/config"}).startswith(ERROR_PREFIX))
        out = b.call("search_files", {"term": "AUTHORIZATION"})
        self.assertNotIn(".git/config", out)
        self.assertNotIn("token", out)

    def test_rejects_git_names_case_insensitively(self):
        # 大小を区別しない FS (macOS の既定) では .GIT も同じ実体を指すので、名前は casefold して比べる。
        # 実体を大文字の名前で作るので、大小を区別する FS (Linux) でも名前の規則を通る経路が赤になる
        planted = {
            "a/.GIT/config": "extraheader = AUTHORIZATION: token\n",
            "b/.Git/HEAD": "AUTHORIZATION: token\n",
            # worktree や submodule の gitdir の指し先を書いた .git という名前のファイル
            "c/.git": "gitdir: AUTHORIZATION token\n",
            ".git/config": "extraheader = AUTHORIZATION: token\n",
        }
        for rel, text in planted.items():
            self.write(rel, text)
        b = self.box(config(files=(FileRule("*", True),)))
        # .GIT/config は大小を区別する FS では存在しないパスなので、そこでは名前の規則だけを確かめることになる
        for path in ("a/.GIT/config", "b/.Git/HEAD", "c/.git", ".GIT/config"):
            with self.subTest(read=path):
                out = b.call("read_file", {"path": path})
                self.assertTrue(out.startswith(ERROR_PREFIX), out)
                self.assertNotIn("token", out)
        out = b.call("search_files", {"term": "AUTHORIZATION"})
        for path in ("a/.GIT/config", "b/.Git/HEAD", "c/.git", ".git/config"):
            with self.subTest(search=path):
                self.assertNotIn(path, out)
        self.assertNotIn("token", out)


class SharedPathRule(RepoCase):
    """設定の criteria・files・issues_glob と read_file の path が、同じ正規形の規則で受け、同じ規則で拒む。"""

    def config_positions(self, v):
        return {
            "criteria": valid(criteria=v),
            "files": valid(files=[v]),
            "files mapping": valid(files=[{"glob": v, "search": True}]),
            "issues_glob": valid(issues_glob=v),
        }

    def test_rejected_everywhere(self):
        b = self.box(config(files=(FileRule("*", True),)))
        for v in REJECT_PATHS:
            for position, raw in self.config_positions(v).items():
                with self.subTest(v=v, position=position):
                    with self.assertRaises(ConfigError):
                        load_config(raw)
            with self.subTest(v=v, position="read_file"):
                out = b.call("read_file", {"path": v})
                # 形の規則の文面で止まっている。ファイルを開きに行って失敗したのではない
                self.assertIn(out.removeprefix(ERROR_PREFIX), set(_PATH_MESSAGES.values()), out)
                self.assertTrue(out.startswith(ERROR_PREFIX), out)

    def test_accepted_everywhere(self):
        b = self.box(config(files=(FileRule("*", True),)))
        for v in ACCEPT_PATHS:
            self.write(v, f"中身 {v}\n")
            for position, raw in self.config_positions(v).items():
                with self.subTest(v=v, position=position):
                    load_config(raw)
            with self.subTest(v=v, position="read_file"):
                self.assertEqual(b.call("read_file", {"path": v}), f"中身 {v}\n")


class SearchFiles(RepoCase):
    def test_only_searchable_rules(self):
        out = self.box().call("search_files", {"term": "lodash"})
        self.assertIn("package.json:1:", out)
        self.assertNotIn("docs/criteria.md", out)
        self.assertNotIn("secret", out)

    def test_fixed_string(self):
        out = self.box().call("search_files", {"term": "a.c"})
        self.assertIn("package.json:2:", out)
        self.assertNotIn("Cargo.lock", out)

    def test_does_not_follow_symlinks(self):
        out = self.box().call("search_files", {"term": "lodash"})
        self.assertNotIn("link.json", out)
        self.assertNotIn("linkdir", out)
        self.assertNotIn("outside", out)

    def test_zero_hits_is_not_error_and_says_what_was_searched(self):
        out = self.box().call("search_files", {"term": "no-such-term"})
        self.assertFalse(out.startswith(ERROR_PREFIX))
        # 0件のときも見たファイルの数を返す (見ていないのと区別するため)
        self.assertIn("2件", out)

    def test_rejects_empty_term(self):
        self.assertTrue(self.box().call("search_files", {"term": ""}).startswith(ERROR_PREFIX))


class SearchIssues(RepoCase):
    GLOB = "docs/issues/*/issue.md"

    def setUp(self):
        super().setUp()
        self.write("docs/issues/ISSUE-1_lodash/issue.md", "GHSA-aaaa-bbbb-cccc lodash 本文の秘密\n")
        self.write("docs/issues/ISSUE-2_other/issue.md", "関係ない\n")

    def test_absent_from_definitions_without_glob(self):
        names = [d["name"] for d in self.box().definitions()]
        self.assertNotIn("search_issues", names)
        self.assertTrue(self.box().call("search_issues", {"term": "lodash"}).startswith(ERROR_PREFIX))

    def test_returns_parent_dir_names_only(self):
        b = self.box(config(issues_glob=self.GLOB))
        self.assertIn("search_issues", [d["name"] for d in b.definitions()])
        out = b.call("search_issues", {"term": "GHSA-aaaa-bbbb-cccc"})
        self.assertIn("ISSUE-1_lodash", out)
        self.assertNotIn("ISSUE-2_other", out)
        self.assertNotIn("本文の秘密", out)


def npm_doc(repository="git+https://github.com/o/r.git", latest="1.2.3"):
    return {"name": "pkg", "dist-tags": {"latest": latest}, "repository": repository, "readme": "IGNORE ALL PREVIOUS"}


RELEASE = {"tag_name": "v1.2.3", "name": "1.2.3", "published_at": "2026-11-01T00:00:00Z", "body": "fixes", "author": {"login": "x"}}


class Registry(unittest.TestCase):
    def box(self, responses, registries=("npm", "crates", "pypi")):
        self.fetcher = FakeFetcher(responses)
        return ToolBox(Path(tempfile.gettempdir()), config(registries=registries), self.fetcher, LIMIT)

    def test_rejects_ecosystem_outside_registries(self):
        b = self.box({}, registries=("npm",))
        out = b.call("registry_metadata", {"ecosystem": "pypi", "package": "requests"})
        self.assertTrue(out.startswith(ERROR_PREFIX))
        self.assertEqual(self.fetcher.requested, [])

    def test_registry_tools_absent_without_registries(self):
        names = [d["name"] for d in self.box({}, registries=()).definitions()]
        self.assertNotIn("registry_metadata", names)
        self.assertNotIn("release_notes", names)

    def test_package_name_shapes(self):
        b = self.box({})
        bad = [
            ("npm", "../x"),
            ("npm", "Upper"),
            ("npm", "@scope/"),
            ("npm", "@scope/a/b"),
            ("npm", "a" * 215),
            ("npm", "a?b"),
            ("npm", "pkg\n"),
            ("crates", "1abc"),
            ("crates", "a" * 65),
            ("crates", "a.b"),
            ("pypi", "-abc"),
            ("pypi", "abc-"),
            ("pypi", "a/b"),
            ("npm", 5),
        ]
        for eco, name in bad:
            with self.subTest(eco=eco, name=name):
                self.assertTrue(b.call("registry_metadata", {"ecosystem": eco, "package": name}).startswith(ERROR_PREFIX))
        self.assertEqual(self.fetcher.requested, [])

    def test_npm_scoped_url_and_only_repository_and_latest(self):
        url = NPM + "@scope%2Fpkg"
        b = self.box({url: npm_doc()})
        out = json.loads(b.call("registry_metadata", {"ecosystem": "npm", "package": "@scope/pkg"}))
        self.assertEqual(self.fetcher.requested, [url])
        self.assertEqual(
            out, {"ecosystem": "npm", "package": "@scope/pkg", "latest_version": "1.2.3", "repository": "https://github.com/o/r"}
        )

    def test_crates_and_pypi(self):
        b = self.box(
            {
                CRATES + "serde": {"crate": {"max_stable_version": "1.0.1", "max_version": "2.0.0-rc1", "repository": "https://github.com/serde-rs/serde"}},
                PYPI + "requests/json": {"info": {"version": "2.32.0", "project_urls": {"Homepage": "https://requests.example", "Source": "https://github.com/psf/requests"}}},
            }
        )
        c = json.loads(b.call("registry_metadata", {"ecosystem": "crates", "package": "serde"}))
        self.assertEqual((c["latest_version"], c["repository"]), ("1.0.1", "https://github.com/serde-rs/serde"))
        p = json.loads(b.call("registry_metadata", {"ecosystem": "pypi", "package": "requests"}))
        self.assertEqual((p["latest_version"], p["repository"]), ("2.32.0", "https://github.com/psf/requests"))

    def test_odd_metadata_shapes_do_not_raise(self):
        url = NPM + "pkg"
        for doc in ([], None, "x", {"dist-tags": []}, {"dist-tags": {"latest": 5}, "repository": ["x"]}, {"dist-tags": {"latest": "bad version!"}}):
            with self.subTest(doc=doc):
                b = self.box({url: doc})
                out = b.call("registry_metadata", {"ecosystem": "npm", "package": "pkg"})
                self.assertIsInstance(out, str)
                if not out.startswith(ERROR_PREFIX):
                    parsed = json.loads(out)
                    self.assertIsNone(parsed["repository"])
                    self.assertNotIn("bad version", out)

    def test_fetch_error_becomes_error_string(self):
        b = self.box({NPM + "pkg": FetchError("HTTP 500", 500)})
        self.assertTrue(b.call("registry_metadata", {"ecosystem": "npm", "package": "pkg"}).startswith(ERROR_PREFIX))


class ReleaseNotes(unittest.TestCase):
    def run_release(self, repository, extra=None):
        reg = NPM + "pkg"
        responses = {reg: npm_doc(repository)}
        responses.update(extra or {})
        self.fetcher = FakeFetcher(responses)
        b = ToolBox(Path(tempfile.gettempdir()), config(), self.fetcher, LIMIT)
        return b.call("release_notes", {"ecosystem": "npm", "package": "pkg", "version": "1.2.3"})

    def test_repository_comes_from_registry(self):
        url = RELEASES + "o/r/releases/tags/v1.2.3"
        for repo in ("git+https://github.com/o/r.git", {"url": "https://github.com/o/r"}, f"git{AT}github.com:o/r.git", "https://github.com/o/r"):
            with self.subTest(repo=repo):
                out = json.loads(self.run_release(repo, {url: RELEASE}))
                self.assertEqual(self.fetcher.requested, [NPM + "pkg", url])
                self.assertEqual(out["repository"], "o/r")
                self.assertEqual(out["body"], "fixes")
                self.assertNotIn("author", out)

    def test_falls_back_to_plain_tag(self):
        v = RELEASES + "o/r/releases/tags/v1.2.3"
        plain = RELEASES + "o/r/releases/tags/1.2.3"
        out = json.loads(self.run_release("https://github.com/o/r", {v: FetchError("HTTP 404", 404), plain: RELEASE}))
        self.assertEqual(self.fetcher.requested, [NPM + "pkg", v, plain])
        self.assertEqual(out["tag"], "1.2.3")

    def test_both_tags_missing(self):
        v = RELEASES + "o/r/releases/tags/v1.2.3"
        plain = RELEASES + "o/r/releases/tags/1.2.3"
        out = self.run_release("https://github.com/o/r", {v: FetchError("HTTP 404", 404), plain: FetchError("HTTP 404", 404)})
        self.assertTrue(out.startswith(ERROR_PREFIX))

    def test_rejects_untrusted_repository_field(self):
        bad = [
            "https://evil.example/o/r",
            "https://github.com.evil.example/o/r",
            "https://evil.example/github.com/o/r",
            "https://GITHUB.com/o/r",
            "http://github.com/o/r",
            "https://github.com/o/r/tree/main",
            "https://github.com/o/r/",
            "https://github.com/o",
            "https://github.com/../r",
            "https://github.com/./r",
            "https://github.com/o/..",
            "https://github.com/o/r?x=1",
            "https://github.com/o/r?",
            "https://github.com/o/r#x",
            f"https://user{AT}github.com/o/r",
            "https://github.com:443/o/r",
            "https://github.com/o/r\n",
            "https://github.com/o%2F../r",
            f"git+ssh://git{AT}github.com/o/r.git",
            f"git{AT}evil.example:o/r.git",
            "github:o/r",
            "o/r",
            42,
            None,
            {"url": 5},
            {"type": "git"},
        ]
        for repo in bad:
            with self.subTest(repo=repo):
                out = self.run_release(repo)
                self.assertTrue(out.startswith(ERROR_PREFIX + REPO_UNKNOWN), out)
                # registry だけを叩き、リリースの API へは一度も行かない
                self.assertEqual(self.fetcher.requested, [NPM + "pkg"])

    def test_repository_cannot_be_passed_as_argument(self):
        fetcher = FakeFetcher({})
        b = ToolBox(Path(tempfile.gettempdir()), config(), fetcher, LIMIT)
        out = b.call("release_notes", {"ecosystem": "npm", "package": "pkg", "version": "1.2.3", "repository": "evil/x"})
        self.assertTrue(out.startswith(ERROR_PREFIX))
        self.assertEqual(fetcher.requested, [])
        schema = next(d for d in b.definitions() if d["name"] == "release_notes")["input_schema"]
        self.assertEqual(set(schema["properties"]), {"ecosystem", "package", "version"})

    def test_rejects_bad_version(self):
        fetcher = FakeFetcher({})
        b = ToolBox(Path(tempfile.gettempdir()), config(), fetcher, LIMIT)
        for v in ("../1", "1/2", "", "-1", "1?x", "a" * 65, 1):
            with self.subTest(v=v):
                self.assertTrue(b.call("release_notes", {"ecosystem": "npm", "package": "pkg", "version": v}).startswith(ERROR_PREFIX))
        self.assertEqual(fetcher.requested, [])


class RegistryMemo(unittest.TestCase):
    """1つの ToolBox (1 run) の中では、同じパッケージの registry を2回取らない。"""

    def box(self, responses):
        self.fetcher = FakeFetcher(responses)
        return ToolBox(Path(tempfile.gettempdir()), config(), self.fetcher, LIMIT)

    def test_metadata_then_release_notes_fetch_registry_once(self):
        rel = RELEASES + "o/r/releases/tags/v1.2.3"
        b = self.box({NPM + "pkg": npm_doc(), rel: RELEASE})
        args = {"ecosystem": "npm", "package": "pkg"}
        first = b.call("registry_metadata", args)
        self.assertFalse(b.call("release_notes", dict(args, version="1.2.3")).startswith(ERROR_PREFIX))
        self.assertEqual(b.call("registry_metadata", args), first)
        self.assertEqual(self.fetcher.requested, [NPM + "pkg", rel])

    def test_memo_is_keyed_by_ecosystem_and_package(self):
        b = self.box({NPM + "pkg": npm_doc(), NPM + "other": npm_doc(), CRATES + "pkg": {"crate": {}}})
        for eco, pkg in (("npm", "pkg"), ("npm", "other"), ("crates", "pkg"), ("npm", "pkg")):
            b.call("registry_metadata", {"ecosystem": eco, "package": pkg})
        self.assertEqual(self.fetcher.requested, [NPM + "pkg", NPM + "other", CRATES + "pkg"])

    def test_failed_fetch_is_not_remembered(self):
        b = self.box({NPM + "pkg": FetchError("HTTP 500", 500)})
        for _ in range(2):
            self.assertTrue(b.call("registry_metadata", {"ecosystem": "npm", "package": "pkg"}).startswith(ERROR_PREFIX))
        self.assertEqual(self.fetcher.requested, [NPM + "pkg", NPM + "pkg"])

    def test_memo_does_not_outlive_the_toolbox(self):
        responses = {NPM + "pkg": npm_doc()}
        fetcher = FakeFetcher(responses)
        for _ in range(2):
            ToolBox(Path(tempfile.gettempdir()), config(), fetcher, LIMIT).call("registry_metadata", {"ecosystem": "npm", "package": "pkg"})
        self.assertEqual(fetcher.requested, [NPM + "pkg", NPM + "pkg"])


class Advisory(unittest.TestCase):
    def box(self, responses):
        self.fetcher = FakeFetcher(responses)
        return ToolBox(Path(tempfile.gettempdir()), config(registries=()), self.fetcher, LIMIT)

    def test_fixed_database_and_normalized_id(self):
        url = OSV + "CVE-2024-12345"
        doc = {
            "id": "CVE-2024-12345",
            "summary": "s",
            "aliases": ["GHSA-aaaa-bbbb-cccc"],
            "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N"}],
            "affected": [{"package": {"ecosystem": "npm", "name": "pkg"}, "ranges": [{"type": "SEMVER", "events": [{"introduced": "0"}, {"fixed": "1.2.4"}]}], "versions": ["1.0.0"] * 3}],
            "references": [{"type": "WEB", "url": "https://example.org/a"}],
            "details": "long text",
        }
        out = json.loads(self.box({url: doc}).call("advisory", {"id": "cve-2024-12345"}))
        self.assertEqual(self.fetcher.requested, [url])
        self.assertEqual(out["affected"][0]["ranges"][0]["events"][1], {"fixed": "1.2.4"})
        self.assertEqual(out["references"], ["https://example.org/a"])
        self.assertNotIn("versions", out["affected"][0])

    def test_ghsa_and_rustsec(self):
        g = OSV + "GHSA-abcd-efgh-ijkl"
        r = OSV + "RUSTSEC-2024-0001"
        b = self.box({g: {"id": "x"}, r: {"id": "y"}})
        b.call("advisory", {"id": "GHSA-ABCD-efgh-ijkl"})
        b.call("advisory", {"id": "RUSTSEC-2024-0001"})
        self.assertEqual(self.fetcher.requested, [g, r])

    def test_rejects_id_shapes(self):
        b = self.box({})
        bad = ("../x", "CVE-1", "GHSA-xxxx", "CVE-2024-12345/../../x", "CVE-2024-12345\n", "", 7, "https://evil.example",
               "CVE-２０２４-12345", "CVE-2024-" + "1" * 40)
        for i in bad:
            with self.subTest(i=i):
                self.assertTrue(b.call("advisory", {"id": i}).startswith(ERROR_PREFIX))
        self.assertEqual(self.fetcher.requested, [])


class Destinations(unittest.TestCase):
    def test_every_tool_hits_only_fixed_hosts(self):
        responses = {
            NPM + "pkg": npm_doc(),
            CRATES + "serde": {"crate": {"max_stable_version": "1.0.0", "repository": "https://github.com/serde-rs/serde"}},
            PYPI + "requests/json": {"info": {"version": "2.0.0", "project_urls": {"Source": "https://github.com/psf/requests"}}},
            RELEASES + "o/r/releases/tags/v1.2.3": RELEASE,
            OSV + "CVE-2024-12345": {"id": "CVE-2024-12345"},
        }
        fetcher = FakeFetcher(responses)
        b = ToolBox(Path(tempfile.gettempdir()), config(), fetcher, LIMIT)
        calls = [
            ("registry_metadata", {"ecosystem": "npm", "package": "pkg"}),
            ("registry_metadata", {"ecosystem": "crates", "package": "serde"}),
            ("registry_metadata", {"ecosystem": "pypi", "package": "requests"}),
            ("release_notes", {"ecosystem": "npm", "package": "pkg", "version": "1.2.3"}),
            ("advisory", {"id": "CVE-2024-12345"}),
        ]
        for name, args in calls:
            self.assertFalse(b.call(name, args).startswith(ERROR_PREFIX), name)
        hosts = {urlsplit(u).hostname for u in fetcher.requested}
        # 対照: 5つのホストすべてに実際に行ったことを確かめ、空の記録で緑にならないようにする
        self.assertEqual(hosts, FIXED_HOSTS)
        self.assertTrue(all(u.startswith("https://") for u in fetcher.requested))

    def test_fake_fetcher_fails_loudly_on_unknown_url(self):
        b = ToolBox(Path(tempfile.gettempdir()), config(), FakeFetcher({}), LIMIT)
        with self.assertRaises(UnexpectedUrl):
            b.call("advisory", {"id": "CVE-2024-12345"})


class NeverRaises(RepoCase):
    def test_bad_inputs_return_error_strings(self):
        b = self.box(config(issues_glob="docs/*.md"))
        cases = [
            ("no_such_tool", {}),
            ("submit_report", {}),
            ("read_file", None),
            ("read_file", []),
            ("read_file", {}),
            ("read_file", {"path": 5}),
            ("read_file", {"path": "package.json", "extra": 1}),
            ("search_files", {"term": None}),
            ("search_issues", {"term": ["x"]}),
            ("registry_metadata", {"ecosystem": "npm"}),
            ("advisory", {"id": None}),
            ([], {}),
            (None, {}),
        ]
        for name, args in cases:
            with self.subTest(name=name, args=args):
                out = b.call(name, args)
                self.assertIsInstance(out, str)
                self.assertTrue(out.startswith(ERROR_PREFIX))

    def test_unencodable_path_returns_error_string(self):
        # JSON の入力は孤立したサロゲートを運べる。os のパスへ変換できず ValueError になる経路を塞ぐ
        b = self.box(config(files=(FileRule("*", False),)))
        out = b.call("read_file", {"path": "\ud800"})
        self.assertTrue(out.startswith(ERROR_PREFIX))


class Truncation(RepoCase):
    def test_truncates_with_one_line_note(self):
        self.write("docs/long.md", "x" * 200)
        out = self.box(limit=50).call("read_file", {"path": "docs/long.md"})
        head, note = out.rsplit("\n", 1)
        self.assertEqual(head, "x" * 50)
        self.assertIn("150", note)

    def test_under_limit_is_untouched(self):
        self.write("docs/short.md", "x" * 50)
        self.assertEqual(self.box(limit=50).call("read_file", {"path": "docs/short.md"}), "x" * 50)


def walk_objects(schema):
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            yield schema
        for v in schema.values():
            yield from walk_objects(v)
    elif isinstance(schema, list):
        for v in schema:
            yield from walk_objects(v)


class Definitions(unittest.TestCase):
    def test_all_strict_and_closed(self):
        b = ToolBox(Path(tempfile.gettempdir()), config(issues_glob="docs/*.md"), FakeFetcher(), LIMIT)
        defs = b.definitions()
        self.assertEqual(
            [d["name"] for d in defs],
            ["search_files", "read_file", "search_issues", "registry_metadata", "release_notes", "advisory", "submit_report"],
        )
        for d in defs:
            with self.subTest(name=d["name"]):
                self.assertEqual(set(d), {"name", "description", "input_schema", "strict"})
                self.assertIs(d["strict"], True)
                for obj in walk_objects(d["input_schema"]):
                    self.assertIs(obj["additionalProperties"], False)
                    self.assertEqual(sorted(obj["required"]), sorted(obj["properties"]))
                if d["name"] != "submit_report":
                    self.assertIn("信頼できない", d["description"])
        report = next(d for d in defs if d["name"] == "submit_report")
        self.assertIs(report["input_schema"], REPORT_SCHEMA)
        eco = next(d for d in defs if d["name"] == "registry_metadata")["input_schema"]["properties"]["ecosystem"]
        self.assertEqual(eco["enum"], ["npm", "crates", "pypi"])

    def test_report_schema_shape(self):
        self.assertEqual(set(REPORT_SCHEMA["properties"]), {"items", "unreachable_sources", "notes"})
        item = REPORT_SCHEMA["properties"]["items"]["items"]
        self.assertEqual(set(item["properties"]), {"title", "classification", "findings", "sources"})
        self.assertEqual(item["properties"]["classification"]["enum"], ["A", "B", "C", "D"])
        for obj in walk_objects(REPORT_SCHEMA):
            self.assertIs(obj["additionalProperties"], False)
            self.assertEqual(sorted(obj["required"]), sorted(obj["properties"]))


class ServerTools(unittest.TestCase):
    def test_shape(self):
        cfg = config(sources=("docs.example.org", "nodejs.org"))
        fetch, search = ToolBox(Path(tempfile.gettempdir()), cfg, FakeFetcher(), LIMIT).server_tools()
        self.assertEqual(fetch["type"], WEB_FETCH_TOOL)
        self.assertEqual(fetch["type"], "web_fetch_20260318")
        self.assertEqual(fetch["name"], "web_fetch")
        self.assertEqual(fetch["url_sources"], {"client_tool_results": {"type": "none"}})
        self.assertEqual(fetch["allowed_domains"], ["docs.example.org", "nodejs.org"])
        self.assertIsInstance(fetch["max_uses"], int)
        self.assertIsInstance(fetch["max_content_tokens"], int)
        self.assertEqual(search["type"], WEB_SEARCH_TOOL)
        self.assertEqual(search["type"], "web_search_20260209")
        self.assertEqual(search["name"], "web_search")
        self.assertEqual(search["allowed_domains"], ["docs.example.org", "nodejs.org"])
        self.assertIsInstance(search["max_uses"], int)
        self.assertNotIn("url_sources", search)


if __name__ == "__main__":
    unittest.main()
