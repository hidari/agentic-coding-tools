"""jevlint.py (入口) の仕様。

前半は jevlint.py が持つ引数と設定の関数 (`parse_args` / `parse_threshold` / `parse_version` /
`build_config`) と 2 つの定数。下流モジュール (jevlint_tree / jevlint_host / jevlint_result)
そのものの仕様はそれぞれのテストが持つ。

後半は `main()` の check と review で、下流をつないだときにだけ現れる性質を見る: 段の順序
(host の用意が展開より先)、キーが上流の起動の env にだけ渡ること、上流の cwd が展開した
コミットであること、パスの解釈が起動したディレクトリに依らないこと、判定を終了コードと
stdout / stderr へ写す形。git は本物の一時リポジトリを使い、pnpm と node (上流と
`node --version`) だけを偽物に差し替える。偽物は呼ばれた時点の argv・cwd・env と worktree
の中身を記録する。`main()` に渡す environ は `os.environ` を写さずに組む。開発機の実キー、
global の git 設定、実際の `~/.cache` にある host をテストに混ぜないため。

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
import contextlib
import io
import json
import os
import shutil
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import jevlint
import jevlint_host
import jevlint_result
import jevlint_tree
from test_jevlint_tree import GitRepo, worktree_count

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


SENTINEL = "SENTINEL-KEY-VALUE"

# 上流の `--json` の応答 (dry-run でない形と dry-run の形) と `--record` の記録。
# 判定そのもののデシジョンテーブルは test_jevlint_result.py が持つので、ここでは
# main() が終了コードへ写す形を見るのに要る分だけを置く
CLEAN_DOC = {
    "findings": [],
    "stats": {"subjects": 3, "missing": 0, "byFile": {"sub/file.py": 3}},
    "errors": [],
    "degraded": [],
    "spent": {"usd": 0.00123, "calls": 1},
}
DRY_DOC = {"dryRun": True, "subjects": 5, "usd": 0.0042}
RECORD = {"model": "jev-test-model"}


def _finding(file: str, value: float) -> dict:
    return {
        "file": file,
        "line": 1,
        "rule": "var-name-describes-value",
        "value": value,
        "cutoff": 0.44,
    }


class _FakePnpm:
    """`prepare_host` の `run` の偽物。呼ばれた時点の状態を記録し、host の形を作って成功を返す。"""

    def __init__(self, repo: Path):
        self.repo = repo
        self.calls: list = []

    def __call__(self, argv, cwd, env):
        where = Path(cwd)
        self.calls.append(
            {
                "argv": list(argv),
                "cwd": where,
                "env": dict(env),
                "worktrees": worktree_count(self.repo),
                "npmrc": (where / ".npmrc").exists(),
            }
        )
        package = where / "node_modules" / "jev-lint"
        (package / "dist").mkdir(parents=True)
        (package / "dist" / "cli.js").write_text("// fake cli\n", encoding="utf-8")
        (package / "package.json").write_text(
            json.dumps({"version": jevlint.UPSTREAM_VERSION, "engines": {"node": ">=24"}}),
            encoding="utf-8",
        )
        return types.SimpleNamespace(returncode=0)


class _FakeUpstream:
    """`run_upstream` の偽物。`node --version` と上流の起動を分けて記録する。

    上流の起動では、呼ばれた時点の worktree の数、cwd の下のファイル (worktree の `.git` を
    除く) の中身、`--config` の中身を記録する。`raises` があれば記録してから投げ、無ければ
    `--record` の置き場へ `record` を書いてから `stdout` を返す。
    """

    def __init__(
        self,
        repo: Path,
        *,
        stdout: str = "",
        record: "str | None" = None,
        node_version: str = "v24.18.0\n",
        raises: "BaseException | None" = None,
    ):
        self.repo = repo
        self.stdout = stdout
        self.record = record
        self.node_version = node_version
        self.raises = raises
        self.version_calls: list = []
        self.calls: list = []

    def __call__(self, argv, **kwargs):
        call = {"argv": list(argv), "cwd": Path(kwargs["cwd"]), "env": dict(kwargs["env"])}
        if list(argv[1:]) == ["--version"]:
            self.version_calls.append(call)
            return types.SimpleNamespace(returncode=0, stdout=self.node_version)
        cwd = call["cwd"]
        call["worktrees"] = worktree_count(self.repo)
        call["files"] = {
            path.relative_to(cwd).as_posix(): path.read_bytes()
            for path in sorted(cwd.rglob("*"))
            if path.is_file() and path.relative_to(cwd).parts[0] != ".git"
        }
        config = Path(argv[argv.index("--config") + 1])
        call["config"] = json.loads(config.read_text(encoding="utf-8"))
        self.calls.append(call)
        if self.raises is not None:
            raise self.raises
        if "--record" in argv and self.record is not None:
            Path(argv[argv.index("--record") + 1]).write_text(self.record, encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout=self.stdout)


class _MainTestCase(unittest.TestCase):
    """一時リポジトリ (`sub/file.py` と `top.py` をコミット済み) と、main() に渡す environ。

    environ の PATH は実際の PATH の前に偽の `node` (実行しない) のディレクトリを置く。
    HOME・TMPDIR・XDG_CACHE_HOME はテストごとの一時ディレクトリで、host はテストごとに
    作り直される (偽の pnpm が毎回呼ばれる)。TMPDIR は展開の置き場なので、後始末の検査は
    その中が空であることで見る。

    プロセスの cwd は一時ディレクトリへ移す。main() の `cwd` ではなくプロセスの cwd で
    パスを解釈する退行があると、テストの実行が scripts/ に `--json-out` のファイルを書く
    (変異注入で実測)。
    """

    def setUp(self):
        base = Path(tempfile.mkdtemp(prefix="jevlint-main-"))
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(base)
        self.tmpdir = base / "tmp"
        self.cache = base / "cache"
        self.home = base / "home"
        self.bin = base / "bin"
        for directory in (self.tmpdir, self.home, self.bin):
            directory.mkdir()
        self.node = self.bin / "node"
        self.node.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        self.node.chmod(0o755)
        self.repo = GitRepo(self)
        self.repo.write("sub/file.py", "value = 1\n")
        self.repo.write("top.py", "top = 1\n")
        self.sha = self.repo.commit("first")
        self.environ = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.home),
            "TMPDIR": str(self.tmpdir),
            "XDG_CACHE_HOME": str(self.cache),
        }

    def fake_upstream(self, **kwargs) -> _FakeUpstream:
        return _FakeUpstream(self.repo.path, **kwargs)

    def run_main(self, argv, *, cwd=None, environ=None, upstream=None, which=shutil.which):
        """main() を呼び、(終了コード, stdout, stderr) を返す。偽物は self に残す。"""
        if upstream is None:
            if "--dry-run" in argv:
                upstream = self.fake_upstream(stdout=json.dumps(DRY_DOC))
            else:
                upstream = self.fake_upstream(
                    stdout=json.dumps(CLEAN_DOC), record=json.dumps(RECORD)
                )
        self.upstream = upstream
        self.pnpm = _FakePnpm(self.repo.path)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = jevlint.main(
                argv,
                environ=self.environ if environ is None else environ,
                cwd=self.repo.path if cwd is None else cwd,
                run_pnpm=self.pnpm,
                run_upstream=upstream,
                which=which,
            )
        return code, out.getvalue(), err.getvalue()

    def keyed(self, value: str = SENTINEL) -> dict:
        return dict(self.environ, TYPESAFE_API_KEY=value)

    def assert_cleaned_up(self):
        self.assertEqual(self.repo.worktree_count(), 1)
        self.assertEqual(list(self.tmpdir.iterdir()), [])


class MainOrderTests(_MainTestCase):
    def test_host_is_prepared_without_key_before_the_worktree_exists(self):
        code, _, _ = self.run_main(["check", "sub/file.py"], environ=self.keyed())
        self.assertEqual(code, 0)
        self.assertEqual(len(self.pnpm.calls), 1)
        self.assertEqual(self.pnpm.calls[0]["worktrees"], 1)
        self.assertNotIn("TYPESAFE_API_KEY", self.pnpm.calls[0]["env"])
        # 対照: 上流の起動の時点では展開の worktree が登録されている
        self.assertEqual(self.upstream.calls[0]["worktrees"], 2)
        self.assert_cleaned_up()

    def test_upstream_is_node_running_the_host_cli(self):
        code, _, _ = self.run_main(["check", "--dry-run", "sub/file.py"])
        self.assertEqual(code, 0)
        cli = self.cache / "jev-lint-curated" / jevlint.UPSTREAM_VERSION / "node_modules/jev-lint/dist/cli.js"
        self.assertEqual(self.upstream.calls[0]["argv"][:2], [str(self.node), str(cli)])
        self.assertEqual(self.upstream.version_calls[0]["argv"], [str(self.node), "--version"])


class MainKeyTests(_MainTestCase):
    def test_key_reaches_only_the_upstream_and_never_the_output(self):
        environ = self.keyed()
        code, out, err = self.run_main(["check", "sub/file.py"], environ=environ)
        self.assertEqual(code, 0)
        self.assertNotIn(SENTINEL, out)
        self.assertNotIn(SENTINEL, err)
        self.assertEqual(
            self.upstream.calls[0]["env"], jevlint_host.build_env(environ, with_key=True)
        )
        self.assertEqual(self.upstream.calls[0]["env"]["TYPESAFE_API_KEY"], SENTINEL)
        self.assertEqual(
            self.upstream.version_calls[0]["env"], jevlint_host.build_env(environ, with_key=False)
        )
        self.assertNotIn("TYPESAFE_API_KEY", self.upstream.version_calls[0]["env"])
        self.assertNotIn("TYPESAFE_API_KEY", self.pnpm.calls[0]["env"])

    def test_dry_run_passes_no_key_to_the_upstream(self):
        environ = self.keyed()
        code, out, err = self.run_main(["check", "--dry-run", "sub/file.py"], environ=environ)
        self.assertEqual(code, 0)
        call = self.upstream.calls[0]
        self.assertEqual(call["env"], jevlint_host.build_env(environ, with_key=False))
        self.assertNotIn("TYPESAFE_API_KEY", call["env"])
        self.assertNotIn("TYPESAFE_API_KEY", self.upstream.version_calls[0]["env"])
        self.assertIn("--dry-run", call["argv"])
        self.assertNotIn("--record", call["argv"])
        self.assertNotIn(SENTINEL, out + err)

    def test_dry_run_needs_no_key(self):
        code, out, _ = self.run_main(["check", "--dry-run", "sub/file.py"])
        self.assertEqual(code, 0)
        self.assertEqual(
            out.splitlines(),
            [
                f"commit {self.sha}  jev-lint {jevlint.UPSTREAM_VERSION}",
                "見積もり: subject 5 件、費用 $0.00420",
            ],
        )

    def test_missing_or_blank_key_is_2_before_the_expansion(self):
        for value in (None, "", "  \t "):
            with self.subTest(value=value):
                environ = dict(self.environ) if value is None else self.keyed(value)
                code, out, err = self.run_main(["check", "sub/file.py"], environ=environ)
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertIn("TYPESAFE_API_KEY", err)
                self.assertEqual(self.upstream.calls, [])
                self.assert_cleaned_up()

    def test_legacy_only_key_is_2_and_names_the_variable_to_use(self):
        environ = dict(self.environ, TYPESAFEAI_API_KEY=SENTINEL)
        code, out, err = self.run_main(["check", "sub/file.py"], environ=environ)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        # "TYPESAFEAI_API_KEY" は "TYPESAFE_API_KEY" を部分文字列として含まない
        self.assertIn("TYPESAFE_API_KEY", err)
        self.assertNotIn(SENTINEL, err)
        self.assertEqual(self.upstream.calls, [])
        self.assert_cleaned_up()


class MainWorktreeTests(_MainTestCase):
    def test_upstream_runs_in_the_expanded_commit_not_the_repository(self):
        # 未コミットの編集と未追跡のファイルは展開に載らない
        self.repo.write("sub/file.py", "value = 'uncommitted'\n")
        self.repo.write("untracked.py", "untracked = 1\n")
        code, _, _ = self.run_main(["check", "--dry-run", "sub/file.py"])
        self.assertEqual(code, 0)
        call = self.upstream.calls[0]
        cwd = call["cwd"].resolve()
        self.assertNotEqual(cwd, self.repo.path.resolve())
        self.assertIn(self.tmpdir.resolve(), cwd.parents)
        self.assertEqual(call["files"], {"sub/file.py": b"value = 1\n", "top.py": b"top = 1\n"})
        self.assert_cleaned_up()

    def test_commit_option_selects_that_commit(self):
        self.repo.write("sub/file.py", "value = 2\n")
        self.repo.commit("second")
        code, out, _ = self.run_main(["check", "--dry-run", "--commit", "HEAD~1", "sub/file.py"])
        self.assertEqual(code, 0)
        self.assertEqual(self.upstream.calls[0]["files"]["sub/file.py"], b"value = 1\n")
        self.assertEqual(
            out.splitlines()[0], f"commit {self.sha}  jev-lint {jevlint.UPSTREAM_VERSION}"
        )

    def test_pnpm_runs_outside_the_repository_even_with_a_committed_npmrc(self):
        self.repo.write(".npmrc", "registry=http://127.0.0.1:9/\n")
        self.repo.commit("npmrc")
        code, _, _ = self.run_main(["check", "--dry-run", "sub/file.py"])
        self.assertEqual(code, 0)
        call = self.pnpm.calls[0]
        cwd = call["cwd"].resolve()
        repo = self.repo.path.resolve()
        self.assertNotEqual(cwd, repo)
        self.assertNotIn(repo, cwd.parents)
        self.assertIn(self.cache.resolve(), cwd.parents)
        self.assertFalse(call["npmrc"])
        # 対照: コミットした .npmrc は展開した worktree には居る
        self.assertIn(".npmrc", self.upstream.calls[0]["files"])

    def test_paths_are_relative_to_the_repository_root_wherever_it_starts(self):
        sub = self.repo.path / "sub"
        cases = (
            (self.repo.path, "sub/file.py", "sub/file.py"),
            (sub, "sub/file.py", "sub/file.py"),
            (sub, "./sub//file.py", "sub/file.py"),
            (sub, ".", "."),
        )
        for cwd, given, expected in cases:
            with self.subTest(cwd=cwd.name, given=given):
                code, _, _ = self.run_main(["check", "--dry-run", given], cwd=cwd)
                self.assertEqual(code, 0)
                argv = self.upstream.calls[0]["argv"]
                self.assertEqual(argv[2 : argv.index("--json")], ["check", expected, "--dry-run"])

    def test_config_and_record_live_outside_the_worktree(self):
        code, _, _ = self.run_main(
            ["check", "--threshold", "python/var-name-describes-value=0.6", "sub/file.py"],
            environ=self.keyed(),
        )
        self.assertEqual(code, 0)
        call = self.upstream.calls[0]
        argv = call["argv"]
        for flag in ("--config", "--record"):
            with self.subTest(flag=flag):
                path = Path(argv[argv.index(flag) + 1])
                self.assertTrue(path.is_absolute())
                self.assertNotIn(call["cwd"], path.parents)
                self.assertNotEqual(path.parent, self.repo.path)
        self.assertEqual(
            call["config"], jevlint.build_config({"python/var-name-describes-value": 0.6})
        )

    def test_mbt_files_under_the_paths_are_reported(self):
        self.repo.write("sub/a.mbt", "fn main {}\n")
        # 対象のパスの外にある .mbt は数えない
        self.repo.write("b.mbt", "fn main {}\n")
        self.repo.commit("mbt")
        code, out, _ = self.run_main(["check", "--dry-run", "sub"])
        self.assertEqual(code, 0)
        self.assertIn(".mbt 1 本は parser が無いので見ていない", out.splitlines())

    def test_relative_path_element_does_not_resolve_a_node_in_the_repository(self):
        rogue = self.repo.path / "relbin" / "node"
        rogue.parent.mkdir()
        rogue.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        rogue.chmod(0o755)
        self.repo.commit("consumer-committed node")
        environ = dict(self.environ, PATH=f"relbin{os.pathsep}{self.environ['PATH']}")
        # shutil.which は相対の要素をプロセスの cwd で解決する。実際の起動と同じく
        # プロセスの cwd を消費側のリポジトリにする
        previous = os.getcwd()
        os.chdir(self.repo.path)
        self.addCleanup(os.chdir, previous)
        # 対照: 生の PATH で解決すると消費側の node を拾う
        self.assertEqual(
            shutil.which("node", path=environ["PATH"]), os.path.join("relbin", "node")
        )
        code, _, _ = self.run_main(["check", "--dry-run", "sub/file.py"], environ=environ)
        self.assertEqual(code, 0)
        self.assertEqual(self.upstream.version_calls[0]["argv"][0], str(self.node))
        self.assertEqual(self.upstream.calls[0]["argv"][0], str(self.node))

    def test_absent_path_does_not_fall_back_to_the_process_path(self):
        # `shutil.which(path=None)` はプロセスの PATH を読む。environ に PATH が無いときに
        # そこへ戻ると、main() に渡した environ の外の PATH で node を解決してしまう。
        # 戻れば偽の node が見つかる状態にして、見つからない (2) ことを見る。ラッパ自身の
        # git は PATH の無い env では os.defpath (/bin:/usr/bin) から起動される
        environ = {k: v for k, v in self.environ.items() if k != "PATH"}
        with mock.patch.dict(os.environ, {"PATH": self.environ["PATH"]}):
            code, out, err = self.run_main(["check", "--dry-run", "sub/file.py"], environ=environ)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("node", err)
        self.assertEqual(self.upstream.version_calls + self.upstream.calls, [])
        # 対照: node の解決の手前 (git と host の用意) までは進んでいる
        self.assertEqual(len(self.pnpm.calls), 1)


class MainRejectionTests(_MainTestCase):
    def test_usage_errors_are_2_before_touching_git_or_the_host(self):
        for argv in (
            ["check"],
            ["check", "--threshold", "go/var-name-describes-value=0.5", "sub/file.py"],
            ["check", "--threshold", "python/var-name-describes-value=1", "sub/file.py"],
        ):
            with self.subTest(argv=argv):
                code, out, err = self.run_main(argv)
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertTrue(err)
                self.assertEqual(self.pnpm.calls, [])

    def test_path_missing_from_the_commit_is_2_before_the_host(self):
        self.repo.write("untracked.py", "untracked = 1\n")
        for path in ("nope.py", "untracked.py", "../outside.py", "/abs.py"):
            with self.subTest(path=path):
                code, out, err = self.run_main(["check", "--dry-run", path])
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertIn(path, err)
                self.assertEqual(self.pnpm.calls, [])
                self.assertEqual(self.upstream.version_calls + self.upstream.calls, [])

    def test_unresolvable_review_base_is_2_before_the_host(self):
        code, out, err = self.run_main(["review", "--dry-run", "--base", "no-such-ref"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("no-such-ref", err)
        self.assertEqual(self.pnpm.calls, [])

    def test_review_passes_the_resolved_base_sha(self):
        self.repo.write("sub/file.py", "value = 2\n")
        self.repo.commit("second")
        code, _, _ = self.run_main(["review", "--dry-run", "--base", "HEAD~1"])
        self.assertEqual(code, 0)
        call = self.upstream.calls[0]
        argv = call["argv"]
        self.assertEqual(argv[2 : argv.index("--json")], ["review", "--base", self.sha, "--dry-run"])
        self.assertEqual(call["files"]["sub/file.py"], b"value = 2\n")

    def test_node_missing_from_the_absolute_path_elements_is_2(self):
        code, out, err = self.run_main(
            ["check", "--dry-run", "sub/file.py"], which=lambda name, path=None: None
        )
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("node", err)
        self.assertEqual(self.upstream.version_calls + self.upstream.calls, [])

    def test_node_below_the_engines_is_2(self):
        upstream = self.fake_upstream(stdout=json.dumps(DRY_DOC), node_version="v20.11.0\n")
        code, out, err = self.run_main(["check", "--dry-run", "sub/file.py"], upstream=upstream)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn(">=24", err)
        self.assertEqual(len(upstream.version_calls), 1)
        self.assertEqual(upstream.calls, [])
        self.assert_cleaned_up()


class MainCleanupTests(_MainTestCase):
    def test_upstream_exception_is_2_and_leaves_no_worktree(self):
        upstream = self.fake_upstream(raises=RuntimeError("upstream exploded"))
        code, out, err = self.run_main(["check", "--dry-run", "sub/file.py"], upstream=upstream)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("upstream exploded", err)
        # 対照: 例外は worktree が登録された状態で上流の起動の中から出た
        self.assertEqual(upstream.calls[0]["worktrees"], 2)
        self.assert_cleaned_up()

    def test_interrupt_propagates_after_cleanup(self):
        upstream = self.fake_upstream(raises=KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.run_main(["check", "--dry-run", "sub/file.py"], upstream=upstream)
        self.assertEqual(upstream.calls[0]["worktrees"], 2)
        self.assert_cleaned_up()


class MainResultTests(_MainTestCase):
    def run_with(
        self, doc: dict, argv: "list | None" = None, record: "str | None" = json.dumps(RECORD)
    ):
        upstream = self.fake_upstream(stdout=json.dumps(doc), record=record)
        return self.run_main(
            argv or ["check", "sub/file.py"], environ=self.keyed(), upstream=upstream
        )

    def test_missing_answers_are_3_and_announced_before_the_findings(self):
        doc = dict(
            CLEAN_DOC,
            stats={"subjects": 200, "missing": 146, "byFile": {}},
            findings=[_finding("sub/file.py", 0.9), _finding("top.py", 0.8)],
        )
        code, out, _ = self.run_with(doc, ["check", "sub/file.py", "top.py"])
        self.assertEqual(code, 3)
        lines = out.splitlines()
        announce = lines.index("146 件は答えが無い")
        first = lines.index("sub/file.py:1 var-name-describes-value 0.90/0.44")
        self.assertLess(announce, first)
        self.assertIn("top.py:1 var-name-describes-value 0.80/0.44", lines)

    def test_findings_with_every_answer_are_1(self):
        code, out, _ = self.run_with(dict(CLEAN_DOC, findings=[_finding("sub/file.py", 0.9)]))
        self.assertEqual(code, 1)
        self.assertIn("応答したモデル: jev-test-model", out.splitlines())
        self.assertIn("sub/file.py:1 var-name-describes-value 0.90/0.44", out.splitlines())

    def test_unusable_upstream_output_is_2_and_saves_nothing(self):
        upstream = self.fake_upstream(stdout="not json", record=json.dumps(RECORD))
        code, out, err = self.run_main(
            ["check", "--json-out", "out.json", "sub/file.py"],
            environ=self.keyed(),
            upstream=upstream,
        )
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("JSON", err)
        self.assertFalse((self.repo.path / "out.json").exists())
        self.assert_cleaned_up()

    def test_missing_record_is_2(self):
        code, out, err = self.run_with(CLEAN_DOC, record=None)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("--record", err)

    def test_document_that_passes_classify_but_breaks_summarize_is_2(self):
        doc = dict(CLEAN_DOC, findings=[{"file": "sub/file.py"}])
        # 対照: classify はこの文書を判定に使える (1) とみなし、summarize まで届く
        outcome = jevlint_result.classify(0, json.dumps(doc), json.dumps(RECORD), False)
        self.assertEqual(outcome.code, 1)
        code, out, err = self.run_with(doc)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("KeyError", err)
        self.assert_cleaned_up()

    def test_json_out_saves_the_json_and_the_record_relative_to_cwd(self):
        stdout, record = json.dumps(CLEAN_DOC), json.dumps(RECORD)
        upstream = self.fake_upstream(stdout=stdout, record=record)
        # プロセスの cwd ではなく main() の cwd から解釈する
        code, _, _ = self.run_main(
            ["check", "--json-out", "out.json", "sub/file.py"],
            cwd=self.repo.path / "sub",
            environ=self.keyed(),
            upstream=upstream,
        )
        self.assertEqual(code, 0)
        target = self.repo.path / "sub" / "out.json"
        self.assertEqual(target.read_text(encoding="utf-8"), stdout)
        self.assertEqual(Path(f"{target}.record.json").read_text(encoding="utf-8"), record)

    def test_json_out_in_dry_run_saves_no_record(self):
        code, _, _ = self.run_main(["check", "--dry-run", "--json-out", "out.json", "sub/file.py"])
        self.assertEqual(code, 0)
        target = self.repo.path / "out.json"
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), DRY_DOC)
        self.assertFalse(Path(f"{target}.record.json").exists())

    def test_json_out_into_a_missing_directory_is_2_before_the_upstream(self):
        code, out, err = self.run_main(
            ["check", "--json-out", "missing/out.json", "sub/file.py"], environ=self.keyed()
        )
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("missing/out.json", err)
        self.assertEqual(self.upstream.calls, [])
        self.assert_cleaned_up()


class JsonOutTargetTests(unittest.TestCase):
    """`--json-out` の保存先の検査。展開の一時ディレクトリの名前は予測できないので、
    main() を通さず合成した `Expanded` で見る。"""

    def setUp(self):
        base = Path(tempfile.mkdtemp(prefix="jevlint-json-out-")).resolve()
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        self.cwd = base / "cwd"
        self.expanded = jevlint_tree.Expanded(
            tree=base / "expansion" / "tree", scratch=base / "expansion" / "scratch"
        )
        for directory in (self.cwd, self.expanded.tree, self.expanded.scratch):
            directory.mkdir(parents=True)

    def target(self, text: str) -> Path:
        return jevlint._json_out_target(text, self.cwd, self.expanded)

    def test_relative_path_resolves_against_cwd(self):
        self.assertEqual(self.target("out.json"), self.cwd / "out.json")

    def test_absolute_path_outside_the_expansion_is_kept(self):
        path = self.cwd.parent / "elsewhere.json"
        self.assertEqual(self.target(str(path)), path)

    def test_rejects_paths_inside_the_expansion(self):
        for text in (
            str(self.expanded.tree / "out.json"),
            str(self.expanded.tree / "not-yet" / "out.json"),
            str(self.expanded.scratch / "out.json"),
            "../expansion/tree/out.json",
        ):
            with self.subTest(text=text):
                with self.assertRaises(jevlint.UsageError):
                    self.target(text)

    def test_rejects_targets_that_cannot_be_written_as_a_file(self):
        for text in ("missing/out.json", "."):
            with self.subTest(text=text):
                with self.assertRaises(jevlint.UsageError):
                    self.target(text)


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
