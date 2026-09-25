"""jevlint.py (入口) の仕様。

このファイルが見るのは jevlint.py が持つ 4 つの入口関数と 2 つの定数のみ。他のモジュール
(jevlint_tree / jevlint_host / jevlint_result / jevlint_compat) と main() は後続タスクで
追加されるので、ここでは扱わない。

CURATED の件数・言語ごとの内訳は spec (`ISSUE-65-spec.md` の「厳選する rule」節) の一覧との
一致を見るのが目的で、この一覧自体が唯一の真実 (canonical) になる。数を書いたコメントを
別の場所に置くと drift するので、このテストの assertion 以外に件数を書かない。

parse_threshold / parse_version / parse_args の異常系は、値そのものではなく「境界のどちら側で
拒否するか」を見るために 0・1・NaN のような際どい値を混ぜてある。

Python 3.9 構文の検査 (test_py39_source) は、このディレクトリの jevlint*.py と
test_jevlint*.py の両方 (このファイル自身を含む) を対象にする。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

import jevlint

HERE = Path(__file__).resolve().parent


class CuratedTests(unittest.TestCase):
    def test_membership_and_no_duplicates(self):
        self.assertEqual(len(jevlint.CURATED), len(set(jevlint.CURATED)))
        self.assertEqual(len(jevlint.CURATED), 27)

    def test_per_language_counts(self):
        by_lang: dict[str, int] = {}
        for item in jevlint.CURATED:
            lang, _, rule_id = item.partition("/")
            self.assertTrue(lang and rule_id, f"<languageDir>/<id> の形でない: {item!r}")
            by_lang[lang] = by_lang.get(lang, 0) + 1
        self.assertEqual(
            by_lang,
            {"typescript": 7, "rust": 5, "python": 7, "moonbit": 7, "javascript": 1},
        )

    def test_rust_missing_two_ids(self):
        self.assertNotIn("rust/pure-name-is-pure", jevlint.CURATED)
        self.assertNotIn("rust/tests-cover-failure-paths", jevlint.CURATED)

    def test_no_go_items(self):
        self.assertFalse([item for item in jevlint.CURATED if item.startswith("go/")])


class UpstreamVersionTests(unittest.TestCase):
    def test_matches_own_parse_version(self):
        # pin した定数自体が「latest」のような書き方に退行していないかを、
        # 同じモジュールの parse_version に通して確かめる
        self.assertEqual(jevlint.parse_version(jevlint.UPSTREAM_VERSION), jevlint.UPSTREAM_VERSION)


class BuildConfigTests(unittest.TestCase):
    FORBIDDEN_KEYS = {"baseUrl", "apiKeyEnv", "languages", "cache", "model", "files"}

    def _assert_no_forbidden_keys(self, value: object) -> None:
        if isinstance(value, dict):
            for key, v in value.items():
                self.assertNotIn(key, self.FORBIDDEN_KEYS)
                self._assert_no_forbidden_keys(v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                self._assert_no_forbidden_keys(v)

    def test_empty_thresholds_all_on(self):
        config = jevlint.build_config({})
        self.assertEqual(set(config.keys()), {"rules"})
        self.assertEqual(set(config["rules"].keys()), set(jevlint.CURATED))
        for value in config["rules"].values():
            self.assertEqual(value, "on")
        self._assert_no_forbidden_keys(config)

    def test_single_threshold_overrides_only_that_rule(self):
        config = jevlint.build_config({"python/var-name-describes-value": 0.6})
        rules = config["rules"]
        self.assertEqual(rules["python/var-name-describes-value"], {"threshold": 0.6})
        for key, value in rules.items():
            if key != "python/var-name-describes-value":
                self.assertEqual(value, "on")
        self._assert_no_forbidden_keys(config)


class ParseThresholdTests(unittest.TestCase):
    def test_accepts_curated_key_with_value_in_range(self):
        key, value = jevlint.parse_threshold("python/var-name-describes-value=0.6")
        self.assertEqual(key, "python/var-name-describes-value")
        self.assertEqual(value, 0.6)

    def test_rejects_key_outside_curated(self):
        with self.assertRaises(jevlint.UsageError):
            jevlint.parse_threshold("go/var-name-describes-value=0.5")

    def test_rejects_key_without_language_qualifier(self):
        with self.assertRaises(jevlint.UsageError):
            jevlint.parse_threshold("var-name-describes-value=0.5")

    def test_rejects_boundary_and_invalid_values(self):
        for text in (
            "python/var-name-describes-value=0",
            "python/var-name-describes-value=1",
            "python/var-name-describes-value=1.5",
            "python/var-name-describes-value=-0.1",
            "python/var-name-describes-value=nan",
            "python/var-name-describes-value=abc",
        ):
            with self.subTest(text=text):
                with self.assertRaises(jevlint.UsageError):
                    jevlint.parse_threshold(text)

    def test_rejects_missing_equals(self):
        with self.assertRaises(jevlint.UsageError):
            jevlint.parse_threshold("python/var-name-describes-value")


class ParseVersionTests(unittest.TestCase):
    def test_accepts_well_formed_versions(self):
        self.assertEqual(jevlint.parse_version("0.7.0"), "0.7.0")
        self.assertEqual(jevlint.parse_version("10.20.30"), "10.20.30")

    def test_rejects_malformed_versions(self):
        for text in (
            "latest",
            "0.7",
            "v0.7.0",
            "0.7.0-beta",
            "0.7.0 ",
            "../0.7.0",
            # str.isdigit() は上付き文字や全角数字も真を返す (実測)。`X.Y.Z` の
            # `jev-lint@<版>` への埋め込みに使う文字列なので、ASCII の 0-9 だけを通す
            "1.2.²",  # 上付き数字の 2 (superscript two)
        ):
            with self.subTest(text=text):
                with self.assertRaises(jevlint.UsageError):
                    jevlint.parse_version(text)


class ParseArgsTests(unittest.TestCase):
    def test_check_happy_path(self):
        args = jevlint.parse_args(
            [
                "check",
                "--commit",
                "HEAD~1",
                "--exclude",
                "*.min.js",
                "--threshold",
                "python/var-name-describes-value=0.6",
                "--json-out",
                "out.json",
                "a.py",
                "b.py",
            ]
        )
        self.assertEqual(args.command, "check")
        self.assertEqual(args.paths, ["a.py", "b.py"])
        self.assertEqual(args.commit, "HEAD~1")
        self.assertEqual(args.exclude, ["*.min.js"])
        self.assertEqual(args.threshold, ["python/var-name-describes-value=0.6"])
        self.assertEqual(args.json_out, "out.json")
        self.assertFalse(args.dry_run)

    def test_review_happy_path_with_zero_paths(self):
        args = jevlint.parse_args(["review", "--base", "main"])
        self.assertEqual(args.command, "review")
        self.assertEqual(args.base, "main")
        self.assertEqual(args.paths, [])

    def test_compat_happy_path(self):
        args = jevlint.parse_args(["compat", "0.7.0"])
        self.assertEqual(args.command, "compat")
        self.assertEqual(args.version, "0.7.0")

    def test_rejects_unknown_subcommands(self):
        for sub in ("init", "run", "eval"):
            with self.subTest(sub=sub):
                with self.assertRaises(jevlint.UsageError):
                    jevlint.parse_args([sub])

    def test_check_requires_at_least_one_path(self):
        with self.assertRaises(jevlint.UsageError):
            jevlint.parse_args(["check"])

    def test_review_requires_base(self):
        with self.assertRaises(jevlint.UsageError):
            jevlint.parse_args(["review"])

    def test_rejects_abbreviated_option(self):
        with self.assertRaises(jevlint.UsageError):
            jevlint.parse_args(
                ["check", "a.py", "--thresh", "python/var-name-describes-value=0.6"]
            )

    def test_rejects_dash_positional_after_double_dash(self):
        with self.assertRaises(jevlint.UsageError):
            jevlint.parse_args(["check", "--", "-x"])

    def test_rejects_dash_positional_without_double_dash(self):
        with self.assertRaises(jevlint.UsageError):
            jevlint.parse_args(["check", "-x"])


class Py39SourceTests(unittest.TestCase):
    def test_py39_source(self):
        # future import は先頭の文 (docstring の次) でなければ SyntaxError になり、
        # tomllib は 3.11 からなのでどちらも 3.9 互換性の直接の目印になる
        paths = sorted(HERE.glob("jevlint*.py")) + sorted(HERE.glob("test_jevlint*.py"))
        self.assertTrue(paths, "対象の jevlint*.py / test_jevlint*.py が見つからない")
        for path in paths:
            with self.subTest(path=path.name):
                tree = ast.parse(path.read_text(encoding="utf-8"), feature_version=(3, 9))
                first = tree.body[1] if isinstance(tree.body[0], ast.Expr) else tree.body[0]
                self.assertIsInstance(first, ast.ImportFrom)
                self.assertEqual(first.module, "__future__")
                self.assertEqual([a.name for a in first.names], ["annotations"])
                imported = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imported |= {a.name for a in node.names}
                    elif isinstance(node, ast.ImportFrom):
                        imported.add(node.module or "")
                self.assertNotIn("tomllib", imported)


if __name__ == "__main__":
    unittest.main()
