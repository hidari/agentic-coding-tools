import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tech_briefing.config import Config, ConfigError, FileRule, load_config  # noqa: E402

REJECT = [
    "github.com", "com", "gist.github.com", "user.github.io", "github.io",
    "https://docs.python.org", "*.example.com", "docs.example.com/blog",
    "аmazon.com", "", "example..com", "1.2.3.4", "xn--80ak6aa92e.com",
    "github.com\n", "user.github.io\n", "docs.python.org\n", "github.com\t", " github.com",
]
ACCEPT = ["docs.github.com", "docs.python.org", "nvd.nist.gov", "osv.dev"]
# criteria と glob と道具の path に共通のパスの形の規則 (test_tools の SharedPathRule も同じ表を使う)。
# NUL と孤立したサロゲートは OS のパスへ変換できない。見えない文字なので code point で組む
REJECT_PATHS = [
    "/abs", "../x", "a/../b", "./package.json", "a/./b", "a//package.json", "a/", ".git/config",
    ".git/**", "a/.GIT/config", "b/.Git/*", ".git", "a" + chr(0x00) + "b", "a/" + chr(0xD800) + ".md",
]
ACCEPT_PATHS = ["docs/criteria.md", ".github/criteria.md", ".gitignore", "a/.gitx/b"]


def valid(**overrides):
    raw = {
        "criteria": "docs/criteria.md",
        "official_sources": ["docs.python.org"],
        "files": ["pyproject.toml"],
    }
    raw.update(overrides)
    return raw


class Domains(unittest.TestCase):
    def test_domain_table(self):
        for d in REJECT:
            with self.assertRaises(ConfigError, msg=d):
                load_config(valid(official_sources=[d]))
        self.assertEqual(load_config(valid(official_sources=ACCEPT)).official_sources, tuple(ACCEPT))

    def test_more_rejections(self):
        for d in ["raw.githubusercontent.com", "githubusercontent.com", "x.gitlab.io",
                  "a.b.pages.dev", "netlify.app", "foo.vercel.app", "example.com:8080",
                  "-a.example.com", "a_b.example.com", ".example.com", "example.com.",
                  "localhost", 1, None]:
            with self.assertRaises(ConfigError, msg=repr(d)):
                load_config(valid(official_sources=[d]))

    def test_case_is_lowered(self):
        self.assertEqual(load_config(valid(official_sources=["Docs.Python.ORG"])).official_sources,
                         ("docs.python.org",))
        with self.assertRaises(ConfigError):
            load_config(valid(official_sources=["GitHub.com"]))
        with self.assertRaises(ConfigError):
            load_config(valid(official_sources=["User.GitHub.IO"]))

    def test_empty_or_non_list_sources(self):
        for v in ([], "docs.python.org", None):
            with self.assertRaises(ConfigError, msg=repr(v)):
                load_config(valid(official_sources=v))


class Shape(unittest.TestCase):
    def test_full_config(self):
        c = load_config(valid(
            files=["a/*.toml", {"glob": "b/**", "search": True}, {"glob": "c"}],
            issues_glob="docs/issues/*", registries=["npm", "pypi"]))
        self.assertEqual(c, Config(
            criteria="docs/criteria.md",
            official_sources=("docs.python.org",),
            files=(FileRule("a/*.toml", False), FileRule("b/**", True), FileRule("c", False)),
            issues_glob="docs/issues/*",
            registries=("npm", "pypi")))

    def test_defaults(self):
        c = load_config(valid())
        self.assertIsNone(c.issues_glob)
        self.assertEqual(c.registries, ())

    def test_not_a_mapping(self):
        for raw in (None, [], "x"):
            with self.assertRaises(ConfigError):
                load_config(raw)

    def test_missing_required(self):
        for k in ("criteria", "official_sources", "files"):
            raw = valid()
            del raw[k]
            with self.assertRaises(ConfigError, msg=k):
                load_config(raw)

    def test_unknown_key(self):
        with self.assertRaises(ConfigError):
            load_config(valid(extra=1))

    def test_criteria_type(self):
        for v in ("", "  ", 1, None):
            with self.assertRaises(ConfigError, msg=repr(v)):
                load_config(valid(criteria=v))

    def test_criteria_path_shape(self):
        # criteria は呼び出し側のリポジトリの中のファイルのパスで、中身はそのまま LLM へ送られる。
        # .git/config には checkout の認証情報が残りうるので、glob と同じ形の検査で拒否する
        for v in REJECT_PATHS:
            with self.subTest(reject=v):
                with self.assertRaises(ConfigError):
                    load_config(valid(criteria=v))
        # 対照: .git で始まるだけの名前と .github の下は通る (前方一致で拒否する実装を赤にする)
        for v in ACCEPT_PATHS:
            with self.subTest(accept=v):
                self.assertEqual(load_config(valid(criteria=v)).criteria, v)

    def test_registries(self):
        for v in (["rubygems"], ["npm", "npm"], "npm", [1]):
            with self.assertRaises(ConfigError, msg=repr(v)):
                load_config(valid(registries=v))
        self.assertEqual(load_config(valid(registries=["npm", "crates", "pypi"])).registries,
                         ("npm", "crates", "pypi"))

    def test_files_invalid(self):
        bad = [[], "a", ["../x"], ["/etc/passwd"], ["a/../b"], [""], [1],
               [{"glob": "a", "search": "yes"}], [{"glob": "a", "x": 1}], [{"search": True}],
               [{"glob": "../a"}]]
        for v in bad:
            with self.assertRaises(ConfigError, msg=repr(v)):
                load_config(valid(files=v))

    def test_issues_glob_invalid(self):
        for v in (1, "", "../x", "/abs", ["a"]):
            with self.assertRaises(ConfigError, msg=repr(v)):
                load_config(valid(issues_glob=v))

    def test_globs_reject_non_canonical_and_git(self):
        # ToolBox は正規形の相対パス (./ も空の segment も無い) に glob を当て、.git の中を見ない。
        # 正規形でない glob や .git を含む glob はどのファイルにも当たらず、検索が黙って0件になる
        for v in REJECT_PATHS:
            with self.subTest(files=v):
                with self.assertRaises(ConfigError):
                    load_config(valid(files=[v]))
            with self.subTest(files_mapping=v):
                with self.assertRaises(ConfigError):
                    load_config(valid(files=[{"glob": v, "search": True}]))
            with self.subTest(issues_glob=v):
                with self.assertRaises(ConfigError):
                    load_config(valid(issues_glob=v))
        for v in ACCEPT_PATHS + [".github/workflows/*.yml", "docs/issues/*/issue.md"]:
            with self.subTest(accept=v):
                self.assertEqual(load_config(valid(files=[v], issues_glob=v)).issues_glob, v)


if __name__ == "__main__":
    unittest.main()
