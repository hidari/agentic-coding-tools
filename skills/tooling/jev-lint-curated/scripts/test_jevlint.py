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

最後に `main()` の compat を見る。比較の規則そのものは test_jevlint_compat.py が持ち、ここでは
2 つの版の host の用意、`rules --json` と設定の読み込みの起動の形 (argv・cwd・env)、報告と
終了コードへの写し方を見る。compat は git を使わないので一時リポジトリを作らない。

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
import shlex
import shutil
import signal
import tempfile
import types
import unittest
from pathlib import Path
from typing import Callable
from unittest import mock

import jevlint
import jevlint_host
import jevlint_result
import jevlint_tree
from test_jevlint_tree import GitRepo, worktree_count

HERE = Path(__file__).resolve().parent


class CuratedTests(unittest.TestCase):
    def test_no_duplicates_count_and_every_language_uses_the_typescript_ids(self):
        self.assertEqual(len(jevlint.CURATED), len(set(jevlint.CURATED)))
        self.assertEqual(len(jevlint.CURATED), 27)
        # 一覧を再掲せずに中身を見る: typescript は厳選の id を全部持つので、どの言語の id も
        # その部分集合になる。どこか 1 項目の綴りが崩れると (typescript 側でも) 包含が崩れる
        ids: dict[str, set] = {}
        for item in jevlint.CURATED:
            lang, _, rule_id = item.partition("/")
            ids.setdefault(lang, set()).add(rule_id)
        for lang, lang_ids in ids.items():
            with self.subTest(lang=lang):
                self.assertLessEqual(lang_ids, ids["typescript"])

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
        cli = jevlint_host.cli_path(where)
        cli.parent.mkdir(parents=True)
        cli.write_text("// fake cli\n", encoding="utf-8")
        jevlint_host.package_json_path(where).write_text(
            json.dumps({"version": jevlint.UPSTREAM_VERSION, "engines": {"node": ">=24"}}),
            encoding="utf-8",
        )
        return types.SimpleNamespace(returncode=0)


class _FakeUpstream:
    """`run_upstream` の偽物。`node --version` と上流の起動を分けて記録する。

    上流の起動では、呼ばれた時点の worktree の数、cwd の下のファイル (worktree の `.git` を
    除く) の中身、`--config` の中身を記録する。`raises` があれば記録してから投げ、無ければ
    `--record` の置き場へ `record` を書いてから `stdout` と `returncode` を返す。
    """

    def __init__(
        self,
        repo: Path,
        *,
        stdout: str = "",
        record: "str | None" = None,
        returncode: int = 0,
        node_version: str = "v24.18.0\n",
        raises: "BaseException | None" = None,
    ):
        self.repo = repo
        self.stdout = stdout
        self.record = record
        self.returncode = returncode
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
        return types.SimpleNamespace(returncode=self.returncode, stdout=self.stdout)


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
        self.base = base
        self.git_log = base / "git.log"
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

    def install_git_shim(self) -> None:
        """environ の PATH の先頭に、argv と env を記録してから本物の git へ exec する shim を置く。

        記録されるのは main() が起動する git (ラッパ自身の git) だけ。fixture の GitRepo と
        偽物が数える worktree は `os.environ` の PATH で本物の git を直接起動する。
        `keyed()` はこの後に呼ぶ (PATH を写すため)。
        """
        real = shutil.which("git", path=os.environ["PATH"])
        self.assertTrue(real and os.path.isabs(real), real)
        shim_dir = self.base / "git-shim"
        shim_dir.mkdir()
        shim = shim_dir / "git"
        log = shlex.quote(str(self.git_log))
        shim.write_text(
            "#!/bin/sh\n"
            f"{{ printf '%s\\n' \"--- git $*\"; env; }} >> {log}\n"
            f'exec {shlex.quote(real)} "$@"\n',
            encoding="utf-8",
        )
        shim.chmod(0o755)
        self.environ["PATH"] = f"{shim_dir}{os.pathsep}{self.environ['PATH']}"

    def git_calls(self) -> list:
        """shim が記録した呼び出しを `(" <argv> ", env の全文)` の列で返す。

        argv は前後に空白を付けて返すので、`" worktree " in argv` のようにサブコマンドの
        名前を語として探せる。
        """
        if not self.git_log.exists():
            return []
        text = self.git_log.read_text(encoding="utf-8", errors="replace")
        calls = []
        for chunk in text.split("--- git ")[1:]:
            argv, _, env = chunk.partition("\n")
            calls.append((f" {argv} ", env))
        return calls

    def assert_git_ran_without_expansion(self):
        calls = self.git_calls()
        # 対照: shim は main() の git を記録している (ref の解決は展開より前に走る)
        self.assertTrue(any(" rev-parse " in argv for argv, _ in calls), calls)
        self.assertFalse([argv for argv, _ in calls if " worktree " in argv])


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

    def test_wrapper_git_never_sees_the_key(self):
        self.install_git_shim()
        code, _, _ = self.run_main(["check", "sub/file.py"], environ=self.keyed())
        self.assertEqual(code, 0)
        calls = self.git_calls()
        # 対照: ref の解決、パスの検査、展開 (登録・書き出し・後始末) の git がすべて shim を
        # 通っており、記録した env はラッパが組み立てたもの
        for sub in (" rev-parse ", " ls-tree ", " worktree ", " cat-file "):
            self.assertTrue(any(sub in argv for argv, _ in calls), sub)
        for argv, env in calls:
            with self.subTest(argv=argv):
                self.assertIn("GIT_NO_LAZY_FETCH=1", env.splitlines())
                self.assertNotIn(SENTINEL, env)
                self.assertNotIn("TYPESAFE_API_KEY", env)
        # 対照: 同じ実行で上流にはキーが渡っている
        self.assertEqual(self.upstream.calls[0]["env"]["TYPESAFE_API_KEY"], SENTINEL)

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
        self.install_git_shim()
        for value in (None, "", "  \t "):
            with self.subTest(value=value):
                environ = dict(self.environ) if value is None else self.keyed(value)
                code, out, err = self.run_main(["check", "sub/file.py"], environ=environ)
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertIn("TYPESAFE_API_KEY", err)
                self.assertEqual(self.upstream.calls, [])
                self.assert_git_ran_without_expansion()
                self.assert_cleaned_up()

    def test_legacy_only_key_is_2_and_names_the_variable_to_use(self):
        self.install_git_shim()
        environ = dict(self.environ, TYPESAFEAI_API_KEY=SENTINEL)
        code, out, err = self.run_main(["check", "sub/file.py"], environ=environ)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        # "TYPESAFEAI_API_KEY" は "TYPESAFE_API_KEY" を部分文字列として含まない
        self.assertIn("TYPESAFE_API_KEY", err)
        self.assertNotIn(SENTINEL, err)
        self.assertEqual(self.upstream.calls, [])
        self.assert_git_ran_without_expansion()
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
        self.install_git_shim()
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
                self.assertEqual(self.git_calls(), [])
                self.assertEqual(self.pnpm.calls, [])
        # 対照: 同じ shim の下で、正しい引数なら main() の git が記録される
        code, _, _ = self.run_main(["check", "--dry-run", "sub/file.py"])
        self.assertEqual(code, 0)
        self.assertTrue(self.git_calls())

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

    def test_path_with_no_absolute_element_is_2_before_any_git(self):
        # 相対と空の要素を落として何も残らない PATH を "" で渡すと、ラッパ自身の最初の git は
        # プロセスの cwd (setUp で base に移してある) の `./git` として起動される。起動されれば
        # 目印を残す `git` をそこに置き、終了コードではなく目印の有無で見る (どちらでも 2 になる)。
        # 起動された git の PATH も空なので、目印は外部コマンドではなく shell の redirect で作る
        marker = self.base / "rogue-git-ran"
        rogue = self.base / "git"
        rogue.write_text(f"#!/bin/sh\n: > {shlex.quote(str(marker))}\nexit 1\n", encoding="utf-8")
        rogue.chmod(0o755)
        for value in ("", "relbin"):
            with self.subTest(PATH=value):
                code, out, err = self.run_main(
                    ["check", "--dry-run", "sub/file.py"], environ=dict(self.environ, PATH=value)
                )
                self.assertFalse(marker.exists(), "cwd の ./git が起動された")
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertIn("PATH", err)
                self.assertEqual(self.pnpm.calls, [])
        # 対照: 同じ目印の git を置いたまま、絶対パスの要素がある PATH なら本物の git で通る
        code, _, _ = self.run_main(["check", "--dry-run", "sub/file.py"])
        self.assertEqual(code, 0)
        self.assertFalse(marker.exists())

    def test_unresolvable_node_is_2_before_any_node_process(self):
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

    def test_sigterm_during_the_upstream_cleans_up_and_exits_128_plus_signum(self):
        upstream = self.fake_upstream(stdout=json.dumps(DRY_DOC))
        previous = signal.getsignal(signal.SIGTERM)

        def terminate(argv, **kwargs):
            result = upstream(argv, **kwargs)
            if list(argv[1:]) == ["--version"]:
                return result
            # ハンドラが無いまま SIGTERM を送るとテストの runner ごと終わるので、送る前に
            # main() が置いたハンドラを確かめる。無ければ例外にして 2 で赤くする
            current = signal.getsignal(signal.SIGTERM)
            if current is previous or not callable(current):
                raise AssertionError("SIGTERM のハンドラが置かれていない")
            os.kill(os.getpid(), signal.SIGTERM)
            raise AssertionError("SIGTERM が例外として届いていない")

        try:
            code, out, err = self.run_main(
                ["check", "--dry-run", "sub/file.py"], upstream=terminate
            )
        except jevlint_tree.SignalInterrupt:
            # KeyboardInterrupt の派生なので、素通しにすると unittest は実行全体を止める
            self.fail("SignalInterrupt が main() の外へ漏れた")
        self.assertEqual(code, 128 + signal.SIGTERM)
        self.assertEqual(out, "")
        self.assertIn("SIGTERM", err)
        # 対照: シグナルは worktree が登録された状態で上流の起動の中に届いた
        self.assertEqual(upstream.calls[0]["worktrees"], 2)
        self.assert_cleaned_up()
        self.assertIs(signal.getsignal(signal.SIGTERM), previous)


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

    def test_upstream_exit_outside_0_1_3_is_2_even_with_a_well_formed_result(self):
        # 上流が途中で終わりつつ形の揃った JSON と記録を残しても、終了コードが 0/1/3 以外なら
        # 判定に使わない。文書と記録は判定の後段をすべて通る形 (CLEAN_DOC と RECORD) にする
        upstream = self.fake_upstream(
            stdout=json.dumps(CLEAN_DOC), record=json.dumps(RECORD), returncode=2
        )
        code, out, err = self.run_main(
            ["check", "--json-out", "out.json", "sub/file.py"],
            environ=self.keyed(),
            upstream=upstream,
        )
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("終了コード", err)
        target = self.repo.path / "out.json"
        self.assertFalse(target.exists())
        self.assertFalse(Path(f"{target}.record.json").exists())
        self.assert_cleaned_up()
        # 対照: 同じ文書と記録で上流が 0 で終われば 0 で、`--json-out` にも書く
        upstream = self.fake_upstream(stdout=json.dumps(CLEAN_DOC), record=json.dumps(RECORD))
        code, _, _ = self.run_main(
            ["check", "--json-out", "out.json", "sub/file.py"],
            environ=self.keyed(),
            upstream=upstream,
        )
        self.assertEqual(code, 0)
        self.assertTrue(target.is_file())

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

    def test_unwritable_record_path_is_2_before_the_upstream_unless_dry_run(self):
        (self.repo.path / "out.json.record.json").mkdir()
        code, out, err = self.run_main(
            ["check", "--json-out", "out.json", "sub/file.py"], environ=self.keyed()
        )
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("out.json.record.json", err)
        self.assertEqual(self.upstream.calls, [])
        self.assert_cleaned_up()
        # dry-run は記録を書かないので、記録の置き場は検査しない
        code, _, _ = self.run_main(["check", "--dry-run", "--json-out", "out.json", "sub/file.py"])
        self.assertEqual(code, 0)
        self.assertTrue((self.repo.path / "out.json").is_file())


COMPAT_NEW = "0.6.1"
TS_PURE = "typescript/pure-name-is-pure"
PY_VAR = "python/var-name-describes-value"


def _compat_rules(changes: "dict | None" = None) -> dict:
    """偽の host の rule の表。キーは `<languageDir>/<id>`、値は (kind, cutoff, rule.yml の中身)。

    厳選の全項目と、厳選の外の 1 項目を持つ。`changes` の値が None の項目は消す。
    """
    rules = {key: ("noul", 0.5, f"id: {key}\nthreshold: 0.5\n") for key in jevlint.CURATED}
    rules["go/fn-name-promises"] = ("noul", 0.5, "id: fn-name-promises\n")
    for key, value in (changes or {}).items():
        if value is None:
            del rules[key]
        else:
            rules[key] = value
    return rules


def _rejects_threshold(config: dict) -> int:
    """`threshold` を知らない版 (0.6.7 以前) の設定の読み込み。知らないフィールドで 2 を返す。"""
    return 2 if any(isinstance(value, dict) for value in config["rules"].values()) else 0


class _FakeCompatPnpm:
    """compat の `prepare_host` の `run` の偽物。`jev-lint@<版>` の版の host を `versions` から作る。

    rule.yml は `<package>/rules/<key>/rule.yml` に書く (偽の上流の `source` がここを指す)。
    `engines` が None の版は package.json に engines を書かない。`fail` の版は 1 を返す。
    """

    def __init__(self, versions: dict, fail: tuple = ()):
        self.versions = versions
        self.fail = fail
        self.calls: list = []

    def __call__(self, argv, cwd, env):
        version = argv[-1].partition("@")[2]
        self.calls.append({"argv": list(argv), "cwd": Path(cwd), "env": dict(env)})
        if version in self.fail:
            return types.SimpleNamespace(returncode=1)
        spec = self.versions[version]
        cli = jevlint_host.cli_path(Path(cwd))
        cli.parent.mkdir(parents=True)
        cli.write_text("// fake cli\n", encoding="utf-8")
        manifest: dict = {"version": version}
        if spec["engines"] is not None:
            manifest["engines"] = {"node": spec["engines"]}
        package_json = jevlint_host.package_json_path(Path(cwd))
        package_json.write_text(json.dumps(manifest), encoding="utf-8")
        package = package_json.parent
        for key, (_, _, text) in spec["rules"].items():
            path = package / "rules" / key / "rule.yml"
            path.parent.mkdir(parents=True)
            path.write_text(text, encoding="utf-8")
        return types.SimpleNamespace(returncode=0)


class _FakeCompatUpstream:
    """compat の `run_upstream` の偽物。`node --version`、`rules --json`、設定の読み込み
    (`check --dry-run`) を分けて記録する。

    どの版の起動かは argv の `cli.js` のパス (`<host>/node_modules/jev-lint/dist/cli.js`、host の
    名前が版) から読む。`rules --json` は `versions` の表を上流の形で返し、`source` は偽の pnpm が
    host に書いた rule.yml を指す。`rules_result` は版ごとに (終了コード, stdout) を差し替え、
    stdout が None なら表をそのまま返す。記録する `files` は呼ばれた時点の cwd の中身
    (相対パスの列)。
    """

    def __init__(
        self,
        versions: dict,
        *,
        node_version: str = "v24.18.0\n",
        config_rc: Callable = lambda config: 0,
        rules_result: "dict | None" = None,
    ):
        self.versions = versions
        self.node_version = node_version
        self.config_rc = config_rc
        self.rules_result = rules_result or {}
        self.version_calls: list = []
        self.rules_calls: list = []
        self.check_calls: list = []

    def all_calls(self) -> list:
        return self.version_calls + self.rules_calls + self.check_calls

    def __call__(self, argv, **kwargs):
        cwd = Path(kwargs["cwd"])
        call = {
            "argv": list(argv),
            "cwd": cwd,
            "env": dict(kwargs["env"]),
            "files": sorted(path.relative_to(cwd).as_posix() for path in cwd.rglob("*")),
        }
        if list(argv[1:]) == ["--version"]:
            self.version_calls.append(call)
            return types.SimpleNamespace(returncode=0, stdout=self.node_version)
        package = Path(argv[1]).parent.parent
        version = package.parent.parent.name
        if argv[2] == "rules":
            self.rules_calls.append(call)
            returncode, stdout = self.rules_result.get(version, (0, None))
            if stdout is None:
                rules = [
                    {
                        "id": key.rpartition("/")[2],
                        "languageDir": key.rpartition("/")[0],
                        "kind": kind,
                        "cutoff": cutoff,
                        "source": str(package / "rules" / key / "rule.yml"),
                    }
                    for key, (kind, cutoff, _) in self.versions[version]["rules"].items()
                ]
                stdout = json.dumps({"rules": rules, "errors": [], "warnings": []})
            return types.SimpleNamespace(returncode=returncode, stdout=stdout)
        config = Path(argv[argv.index("--config") + 1])
        call["config"] = json.loads(config.read_text(encoding="utf-8"))
        self.check_calls.append(call)
        return types.SimpleNamespace(returncode=self.config_rc(call["config"]), stdout="")


class _CompatTestCase(unittest.TestCase):
    """compat の main() に渡す environ と、2 つの版の偽の host の中身。

    environ にはキーを 2 つの名前で入れておき、どの子プロセスにも渡らないことを見る。
    XDG_CACHE_HOME は `run_compat` のたびに新しいディレクトリにする (host は版ごとに再利用
    されるので、前の実行の偽の host を次の実行に持ち越さないため)。TMPDIR は compat の一時
    ディレクトリの置き場で、後始末の検査はその中が空であることで見る。
    """

    def setUp(self):
        base = Path(tempfile.mkdtemp(prefix="jevlint-compat-test-"))
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(base)
        self.base = base
        self.tmpdir = base / "tmp"
        self.bin = base / "bin"
        for directory in (self.tmpdir, base / "home", self.bin):
            directory.mkdir()
        self.node = self.bin / "node"
        self.node.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        self.node.chmod(0o755)
        self.environ = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(base / "home"),
            "TMPDIR": str(self.tmpdir),
            "TYPESAFE_API_KEY": SENTINEL,
            "TYPESAFEAI_API_KEY": SENTINEL,
        }
        self.versions = {
            jevlint.UPSTREAM_VERSION: {"rules": _compat_rules(), "engines": ">=24"},
            COMPAT_NEW: {"rules": _compat_rules(), "engines": ">=20"},
        }
        self.runs = 0

    def fake_upstream(self, **kwargs) -> _FakeCompatUpstream:
        return _FakeCompatUpstream(self.versions, **kwargs)

    def run_compat(self, version=COMPAT_NEW, *, upstream=None, pnpm=None, which=shutil.which):
        """main() の compat を呼び、(終了コード, stdout, stderr) を返す。偽物は self に残す。"""
        self.runs += 1
        self.cache = self.base / f"cache-{self.runs}"
        self.environ["XDG_CACHE_HOME"] = str(self.cache)
        self.pnpm = pnpm or _FakeCompatPnpm(self.versions)
        self.upstream = upstream or self.fake_upstream()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = jevlint.main(
                ["compat", version],
                environ=self.environ,
                cwd=self.base,
                run_pnpm=self.pnpm,
                run_upstream=self.upstream,
                which=which,
            )
        return code, out.getvalue(), err.getvalue()

    def host(self, version: str) -> Path:
        return self.cache / "jev-lint-curated" / version

    def cli(self, version: str) -> str:
        return str(self.host(version) / "node_modules/jev-lint/dist/cli.js")

    def header(self, new: str = COMPAT_NEW) -> str:
        count = len(_compat_rules())
        return (
            f"jev-lint {jevlint.UPSTREAM_VERSION} (pin、rule {count} 件) と {new} "
            f"(rule {count} 件) を、厳選の {len(jevlint.CURATED)} 項目で比べた"
        )

    def assert_cleaned_up(self):
        self.assertEqual(list(self.tmpdir.iterdir()), [])


class MainCompatTests(_CompatTestCase):
    def test_rules_are_listed_without_a_config_in_an_empty_directory_for_both_versions(self):
        code, _, _ = self.run_compat()
        self.assertEqual(code, 0)
        self.assertEqual(
            [call["argv"] for call in self.upstream.rules_calls],
            [
                [str(self.node), self.cli(version), "rules", "--json", "--no-config"]
                for version in (jevlint.UPSTREAM_VERSION, COMPAT_NEW)
            ],
        )
        for call in self.upstream.rules_calls:
            with self.subTest(cli=call["argv"][1]):
                self.assertEqual(call["files"], [])
                self.assertIn(self.tmpdir.resolve(), call["cwd"].resolve().parents)
        self.assert_cleaned_up()

    def test_generated_configs_are_loaded_by_the_argument_version_among_four_samples(self):
        code, _, _ = self.run_compat()
        self.assertEqual(code, 0)
        calls = self.upstream.check_calls
        self.assertEqual(len(calls), 2)
        default, single = (call["config"] for call in calls)
        self.assertEqual(default, jevlint.build_config({}))
        overridden = {key: value for key, value in single["rules"].items() if value != "on"}
        self.assertEqual(len(overridden), 1, single)
        ((key, value),) = overridden.items()
        self.assertEqual(set(value), {"threshold"})
        self.assertTrue(0 < value["threshold"] < 1)
        self.assertEqual(single, jevlint.build_config({key: value["threshold"]}))
        for index, call in enumerate(calls):
            with self.subTest(index=index):
                argv = call["argv"]
                config = Path(argv[argv.index("--config") + 1])
                self.assertEqual(
                    argv,
                    jevlint_host.upstream_argv(
                        str(self.node),
                        self.host(COMPAT_NEW),
                        "check",
                        base=None,
                        excludes=[],
                        paths=["."],
                        dry_run=True,
                        record=None,
                        config=config,
                    ),
                )
                self.assertEqual(
                    sorted(Path(name).suffix for name in call["files"]), [".js", ".py", ".rs", ".ts"]
                )
                self.assertNotIn(call["cwd"].resolve(), config.resolve().parents)
        self.assert_cleaned_up()

    def test_no_child_process_sees_the_key(self):
        code, out, err = self.run_compat()
        self.assertEqual(code, 0)
        calls = self.upstream.all_calls()
        # 対照: 子プロセスはすべて記録されている (pnpm 2、node --version 1、rules 2、設定 2)
        self.assertEqual((len(self.pnpm.calls), len(calls)), (2, 5))
        keyless = jevlint_host.build_env(self.environ, with_key=False)
        for call in calls:
            self.assertEqual(call["env"], keyless)
        for call in self.pnpm.calls + calls:
            with self.subTest(argv=call["argv"]):
                self.assertNotIn(SENTINEL, "\n".join(call["env"].values()))
                self.assertNotIn("TYPESAFE_API_KEY", call["env"])
        self.assertNotIn(SENTINEL, out + err)

    def test_same_version_prepares_one_host_and_reports_no_change(self):
        version = jevlint.UPSTREAM_VERSION
        code, out, _ = self.run_compat(version)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.pnpm.calls), 1)
        self.assertEqual(
            out.splitlines(),
            [
                self.header(version),
                "失敗 0 件",
                "cutoff の変化 0 件",
                "rule.yml の変化 0 件",
                "増えた rule 0 件",
                "設定の読み込み (既定): 通った",
                f"設定の読み込み ({TS_PURE} に threshold 0.5): 通った",
                "engines.node >=24、手元の node 24.18.0: 満たす",
            ],
        )

    def test_changes_are_reported_and_a_config_that_fails_to_load_is_1(self):
        self.versions[COMPAT_NEW]["rules"] = _compat_rules(
            {
                TS_PURE: ("noul", 0.63, f"id: {TS_PURE}\nthreshold: 0.5\n"),
                PY_VAR: ("noul", 0.5, f"id: {PY_VAR}\nat: 0.5\n"),
                "go/pure-name-is-pure": ("noul", 0.5, "id: pure-name-is-pure\n"),
            }
        )
        upstream = self.fake_upstream(config_rc=_rejects_threshold)
        code, out, err = self.run_compat(upstream=upstream)
        self.assertEqual(code, 1)
        count = len(_compat_rules())
        self.assertEqual(
            out.splitlines(),
            [
                f"jev-lint {jevlint.UPSTREAM_VERSION} (pin、rule {count} 件) と {COMPAT_NEW} "
                f"(rule {count + 1} 件) を、厳選の {len(jevlint.CURATED)} 項目で比べた",
                "失敗 0 件",
                "cutoff の変化 1 件",
                f"  {TS_PURE}: 0.5 -> 0.63",
                "rule.yml の変化 1 件",
                f"  {PY_VAR}",
                f"    --- {jevlint.UPSTREAM_VERSION}/{PY_VAR}/rule.yml",
                f"    +++ {COMPAT_NEW}/{PY_VAR}/rule.yml",
                "    @@ -1,2 +1,2 @@",
                f"     id: {PY_VAR}",
                "    -threshold: 0.5",
                "    +at: 0.5",
                "増えた rule 1 件",
                "  go/pure-name-is-pure (厳選の id)",
                "設定の読み込み (既定): 通った",
                f"設定の読み込み ({TS_PURE} に threshold 0.5): 終了コード 2 で失敗 (理由は上流の stderr)",
                "engines.node >=20、手元の node 24.18.0: 満たす",
            ],
        )
        # 上流の stderr はそのまま流れるので、どの設定を読ませた起動なのかを stderr で先に告げる
        self.assertIn(f"設定 ({TS_PURE} に threshold 0.5) を jev-lint {COMPAT_NEW} に読ませる", err)
        self.assert_cleaned_up()

    def test_each_failure_is_1(self):
        item = "rust/var-name-describes-value"
        cases = {
            "厳選の項目が消えた": ({item: None}, ">=20", lambda config: 0),
            "kind が変わった": ({item: ("score", 0.5, f"id: {item}\nthreshold: 0.5\n")}, ">=20", lambda config: 0),
            "既定の設定が読めない": ({}, ">=20", lambda config: 1),
            "node が engines を満たさない": ({}, ">=26", lambda config: 0),
            "engines が >=N の形でない": ({}, "^24", lambda config: 0),
            "engines が無い": ({}, None, lambda config: 0),
        }
        for name, (changes, engines, config_rc) in cases.items():
            with self.subTest(name=name):
                self.versions[COMPAT_NEW] = {"rules": _compat_rules(changes), "engines": engines}
                code, out, _ = self.run_compat(upstream=self.fake_upstream(config_rc=config_rc))
                self.assertEqual(code, 1)
                # 失敗でも報告は出る (何が失敗したかを stdout で読めるように)
                self.assertTrue(out.startswith("jev-lint "), out)
                self.assert_cleaned_up()
        # 対照: 同じ組み立てで何も変えなければ 0
        self.versions[COMPAT_NEW] = {"rules": _compat_rules(), "engines": ">=20"}
        self.assertEqual(self.run_compat()[0], 0)

    def test_what_cannot_be_checked_is_2_with_nothing_on_stdout(self):
        missing = json.dumps(
            {
                "rules": [
                    {
                        "id": "pure-name-is-pure",
                        "languageDir": "typescript",
                        "kind": "noul",
                        "cutoff": 0.5,
                        "source": str(self.base / "no-such-rule.yml"),
                    }
                ]
            }
        )
        cases = {
            "rules --json が JSON でない": dict(upstream=dict(rules_result={COMPAT_NEW: (0, "not json")})),
            "rules --json に rules が無い": dict(upstream=dict(rules_result={COMPAT_NEW: (0, "{}")})),
            "rules --json が 0 以外で終わった": dict(upstream=dict(rules_result={COMPAT_NEW: (2, None)})),
            "rule.yml が読めない": dict(upstream=dict(rules_result={COMPAT_NEW: (0, missing)})),
            "pnpm が失敗した": dict(pnpm_fail=(COMPAT_NEW,)),
            "node が見つからない": dict(which=lambda name, path=None: None),
        }
        for name, case in cases.items():
            with self.subTest(name=name):
                code, out, err = self.run_compat(
                    upstream=self.fake_upstream(**case.get("upstream", {})),
                    pnpm=_FakeCompatPnpm(self.versions, fail=case.get("pnpm_fail", ())),
                    which=case.get("which", shutil.which),
                )
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertTrue(err.startswith("jevlint: "), err)
                self.assertEqual(self.upstream.check_calls, [])
                self.assert_cleaned_up()

    def test_sgconfig_above_the_scratch_is_2_before_any_config_is_loaded(self):
        # 設定の読み込みは一時ディレクトリの `tree` を cwd にして上流を起動し、上流は ast-grep を
        # 起動する。ast-grep は cwd の祖先の sgconfig を読むので、共有の置き場の上に別の利用者が
        # 置いた sgconfig.yml があれば、上流を起動する前に止める
        shared = self.base / "shared"
        (shared / "tmp").mkdir(parents=True)
        (shared / "sgconfig.yml").write_text("ruleDirs: []\n", encoding="utf-8")
        self.environ["TMPDIR"] = str(shared / "tmp")
        code, out, err = self.run_compat()
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn(str(shared / "sgconfig.yml"), err)
        self.assertEqual(self.upstream.check_calls, [])
        self.assertEqual(list((shared / "tmp").iterdir()), [])
        # 対照: 同じ置き場から sgconfig.yml を除けば、同じ組み立てで設定を 2 通り読ませる
        (shared / "sgconfig.yml").unlink()
        code, _, _ = self.run_compat()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.upstream.check_calls), 2)

    def test_malformed_version_is_2_before_any_child_process(self):
        for version in ("latest", "0.7", "v0.7.0"):
            with self.subTest(version=version):
                code, out, err = self.run_compat(version)
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertIn(version, err)
                self.assertEqual(self.pnpm.calls, [])
                self.assertEqual(self.upstream.all_calls(), [])


class JsonOutTargetsTests(unittest.TestCase):
    """`--json-out` の保存先 (JSON と `<path>.record.json`) の検査。展開の一時ディレクトリの
    名前は予測できないので、main() を通さず合成した `Expanded` で見る。

    包含の拒否を見るケースは、どれも親ディレクトリが実在する形にしてある (親が無いことの
    拒否が先に当たると、包含の検査を外しても赤くならない)。
    """

    def setUp(self):
        base = Path(tempfile.mkdtemp(prefix="jevlint-json-out-")).resolve()
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        self.base = base
        self.cwd = base / "cwd"
        self.expanded = jevlint_tree.Expanded(
            tree=base / "expansion" / "tree", scratch=base / "expansion" / "scratch"
        )
        for directory in (self.cwd, self.expanded.tree / "sub", self.expanded.scratch):
            directory.mkdir(parents=True)

    def targets(self, text: str, record: bool = True) -> tuple:
        return jevlint._json_out_targets(text, self.cwd, self.expanded, record=record)

    def link(self, name: str, to: Path) -> None:
        (self.cwd / name).symlink_to(to)

    def test_relative_path_resolves_against_cwd(self):
        self.assertEqual(
            self.targets("out.json"), (self.cwd / "out.json", self.cwd / "out.json.record.json")
        )

    def test_absolute_path_outside_the_expansion_is_kept(self):
        path = self.base / "elsewhere.json"
        self.assertEqual(self.targets(str(path), record=False), (path, None))

    def test_record_name_follows_the_path_as_given_not_a_symlink_target(self):
        (self.base / "real").mkdir()
        self.link("link.json", self.base / "real" / "data.json")
        self.assertEqual(
            self.targets("link.json"),
            (self.cwd / "link.json", self.cwd / "link.json.record.json"),
        )

    def test_rejects_paths_inside_the_expansion(self):
        self.link("into-tree", self.expanded.tree)
        self.link("dangling.json", self.expanded.tree / "out.json")
        for text in (
            str(self.expanded.tree / "out.json"),
            str(self.expanded.tree / "sub" / "out.json"),
            str(self.expanded.scratch / "out.json"),
            "../expansion/tree/out.json",
            "into-tree/out.json",
            "dangling.json",
        ):
            with self.subTest(text=text):
                with self.assertRaises(jevlint.UsageError):
                    self.targets(text, record=False)

    def test_rejects_a_record_path_inside_the_expansion(self):
        self.link("out.json.record.json", self.expanded.scratch / "record.json")
        with self.assertRaises(jevlint.UsageError):
            self.targets("out.json")
        # dry-run は記録を書かないので、その置き場を見ない
        self.assertEqual(self.targets("out.json", record=False), (self.cwd / "out.json", None))

    def test_rejects_targets_that_cannot_be_written_as_a_file(self):
        (self.cwd / "taken.json.record.json").mkdir()
        for text, record in (("missing/out.json", False), (".", False), ("taken.json", True)):
            with self.subTest(text=text, record=record):
                with self.assertRaises(jevlint.UsageError):
                    self.targets(text, record=record)


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
