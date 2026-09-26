"""jevlint_host.py (env、host、node、上流の起動) の仕様。

見るのは次の 13 の入口: `build_env`、`install_env`、`key_status`、`host_dir`、
`prepare_host`、`host_reusable`、`parse_node_version`、`engines_ok`、`read_engines`、
`cli_path`、`package_json_path`、`upstream_argv`、`fixed_tail`。`prepare_host` のテスト
だけが `run` を偽物に差し替えて副作用 (argv・cwd・env・ファイルの生成) を見る。ネットワーク・pnpm 本体・node 本体・
実際のキーには依存しない。

`prepare_host` の偽の `run` は、`XDG_CACHE_HOME` を指す一時ディレクトリの中で
`cli_path` と `package_json_path` が指すファイルを実際に作ってから返り値を返す。
テストの `source` は `os.environ` を直接使わず、`XDG_CACHE_HOME` を差し替えた辞書を渡す
(host の置き場をテストごとに隔離するため)。cwd の検査は `os.chdir` で「消費側のリポジトリ」
を模した別ディレクトリへ一時的に移動し、`run` に渡った cwd がそれと一致しないことで見る。

権限エラー (`PermissionError`) と symlink ループの `resolve()` の扱いは Python の版で
挙動が違う (3.9.6 では例外が伝播するが、3.14.7 の pathlib はどちらも握りつぶして偽の
値を返す。実測)。該当のテストは `sys.version_info` で期待値を分けており、
`unittest.skip` は使わない (このリポジトリのテスト runner は skip を赤にする)。
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path

import jevlint_host


class BuildEnvTests(unittest.TestCase):
    SOURCE = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/home/user",
        "TMPDIR": "/tmp",
        "LANG": "en_US.UTF-8",
        "LC_TIME": "ja_JP.UTF-8",
        "NODE_OPTIONS": "--max-old-space-size=4096",
        "HTTPS_PROXY": "http://proxy.example:8080",
        "NODE_TLS_REJECT_UNAUTHORIZED": "0",
        "NODE_EXTRA_CA_CERTS": "/etc/ssl/custom.pem",
        "GIT_DIR": "/somewhere/else/.git",
        "GIT_CONFIG_PARAMETERS": "'core.fsmonitor=evil'",
        "JEV_LINT_AST_GREP": "/tmp/evil-ast-grep",
        "TYPESAFEAI_API_KEY": "legacy-secret",
        "TYPESAFE_BASE_URL": "https://evil.example",
        "TYPESAFE_API_KEY": "secret-key",
        # "L" で始まるが LC_ prefix ではない変数。`startswith("LC_")` が
        # `startswith("L")` に退行する変異を検出するために入れる
        "LD_PRELOAD": "/evil.so",
        "DYLD_INSERT_LIBRARIES": "/evil.dylib",
        "LANGUAGE": "de:en",
    }

    def test_without_key_output_is_subset_of_allowlist(self):
        env = jevlint_host.build_env(self.SOURCE, with_key=False)
        allowed = {
            "PATH",
            "HOME",
            "TMPDIR",
            "LANG",
            "LC_TIME",
            "GIT_NO_LAZY_FETCH",
            "GIT_NO_REPLACE_OBJECTS",
        }
        self.assertLessEqual(set(env), allowed)
        for forbidden in (
            "NODE_OPTIONS",
            "HTTPS_PROXY",
            "NODE_TLS_REJECT_UNAUTHORIZED",
            "NODE_EXTRA_CA_CERTS",
            "GIT_DIR",
            "GIT_CONFIG_PARAMETERS",
            "JEV_LINT_AST_GREP",
            "TYPESAFEAI_API_KEY",
            "TYPESAFE_BASE_URL",
            "TYPESAFE_API_KEY",
            "LD_PRELOAD",
            "DYLD_INSERT_LIBRARIES",
            "LANGUAGE",
        ):
            self.assertNotIn(forbidden, env)

    def test_without_key_copies_allowed_values_unchanged(self):
        env = jevlint_host.build_env(self.SOURCE, with_key=False)
        self.assertEqual(env["PATH"], "/usr/bin:/bin")
        self.assertEqual(env["HOME"], "/home/user")
        self.assertEqual(env["TMPDIR"], "/tmp")
        self.assertEqual(env["LANG"], "en_US.UTF-8")
        self.assertEqual(env["LC_TIME"], "ja_JP.UTF-8")

    def test_with_key_adds_only_typesafe_api_key(self):
        without = jevlint_host.build_env(self.SOURCE, with_key=False)
        with_key = jevlint_host.build_env(self.SOURCE, with_key=True)
        added = {k: v for k, v in with_key.items() if without.get(k) != v or k not in without}
        self.assertEqual(added, {"TYPESAFE_API_KEY": "secret-key"})
        without_the_key = {k: v for k, v in with_key.items() if k != "TYPESAFE_API_KEY"}
        self.assertEqual(without, without_the_key)

    def test_sets_fixed_git_safety_values_and_drops_other_git_vars(self):
        # GIT_NO_LAZY_FETCH と GIT_NO_REPLACE_OBJECTS は source から写すのではなく
        # build_env が固定で足す (lazy fetch / refs/replace の fail-closed 策。実測: git 2.55.0。
        # git help git (git 2.55.0) はどちらも documented: --no-lazy-fetch は「equivalent to
        # setting the GIT_NO_LAZY_FETCH environment variable to 1」、--no-replace-objects は
        # 「equivalent to exporting the GIT_NO_REPLACE_OBJECTS environment variable with any
        # value」)。source の GIT_DIR や他の GIT_* は落ちることを、この 2 つだけが残ることで見る。
        env = jevlint_host.build_env(self.SOURCE, with_key=False)
        self.assertEqual(env["GIT_NO_LAZY_FETCH"], "1")
        self.assertEqual(env["GIT_NO_REPLACE_OBJECTS"], "1")
        git_keys = {k for k in env if k.startswith("GIT_")}
        self.assertEqual(git_keys, {"GIT_NO_LAZY_FETCH", "GIT_NO_REPLACE_OBJECTS"})

    def test_does_not_inject_lc_all(self):
        # 上流の env は利用者の locale をそのまま保つ (LC_ALL=C への固定はラッパ自身の git
        # 呼び出し専用で jevlint_tree.py 側が行う)。source に LC_ALL が無ければ build_env は
        # 自分で足さない
        env = jevlint_host.build_env(self.SOURCE, with_key=False)
        self.assertNotIn("LC_ALL", env)

    def test_copies_lc_all_from_source_when_present(self):
        # 一方で利用者が LC_ALL を設定していれば、それは LC_ プレフィックスとして
        # そのまま写る (build_env が「足さない」のと「落とす」のは別)
        source = dict(self.SOURCE)
        source["LC_ALL"] = "fr_FR.UTF-8"
        env = jevlint_host.build_env(source, with_key=False)
        self.assertEqual(env["LC_ALL"], "fr_FR.UTF-8")

    def test_path_keeps_only_absolute_nonempty_entries(self):
        # node 24.18.0 の spawnSync("git", {cwd: worktree}) で実測:
        # 相対成分や空成分がある PATH は worktree 内のコミット済み実行ファイル
        # (mode 100755 の blob) を解決してしまう
        cases = [
            ("node_modules/.bin:/usr/bin:/bin", "/usr/bin:/bin"),
            (":/usr/bin:/bin", "/usr/bin:/bin"),
            ("/usr/bin:/bin:.", "/usr/bin:/bin"),
            ("/usr/bin:/bin:", "/usr/bin:/bin"),
            ("/usr/bin:/bin", "/usr/bin:/bin"),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                source = dict(self.SOURCE)
                source["PATH"] = raw
                env = jevlint_host.build_env(source, with_key=False)
                self.assertEqual(env["PATH"], expected)

    def test_path_with_no_absolute_entry_is_a_host_error(self):
        # 何も残らない PATH を "" で渡すと os.get_exec_path は [''] を返し、最初の git が
        # プロセスの cwd の ./git になる。PATH が無いときは os.defpath に任せるので別に扱う
        for raw in ("", ":", ".", "node_modules/.bin:relbin"):
            with self.subTest(raw=raw):
                with self.assertRaises(jevlint_host.HostError):
                    jevlint_host.build_env(dict(self.SOURCE, PATH=raw), with_key=False)
        # 対照: PATH そのものが無ければ、PATH を足さずに組み立てる
        source = {k: v for k, v in self.SOURCE.items() if k != "PATH"}
        self.assertNotIn("PATH", jevlint_host.build_env(source, with_key=False))


class InstallEnvTests(unittest.TestCase):
    SOURCE = {
        "PATH": "/usr/bin:/bin",
        "HTTPS_PROXY": "http://proxy.example:8080",
        "FOO": "bar",
        "TYPESAFE_API_KEY": "secret",
        "TYPESAFE_BASE_URL": "https://evil.example",
        "TYPESAFEAI_API_KEY": "legacy",
        "JEV_LINT_CACHE": "/tmp/cache",
        "JEV_LINT_AST_GREP": "/tmp/evil",
        "NODE_OPTIONS": "--require=/marker/node-options.js",
        "NODE_TLS_REJECT_UNAUTHORIZED": "0",
        "COREPACK_NPM_REGISTRY": "http://marker.example/corepack-registry",
        "COREPACK_INTEGRITY_KEYS": "marker-corepack-keys",
        "NODE_EXTRA_CA_CERTS": "/marker/extra-ca.pem",
    }

    def test_drops_typesafe_typesafeai_and_jevlint_prefixed(self):
        env = jevlint_host.install_env(self.SOURCE)
        for dropped in (
            "TYPESAFE_API_KEY",
            "TYPESAFE_BASE_URL",
            "TYPESAFEAI_API_KEY",
            "JEV_LINT_CACHE",
            "JEV_LINT_AST_GREP",
        ):
            self.assertNotIn(dropped, env)

    def test_keeps_unrelated_variables(self):
        env = jevlint_host.install_env(self.SOURCE)
        self.assertEqual(env["HTTPS_PROXY"], "http://proxy.example:8080")
        self.assertEqual(env["PATH"], "/usr/bin:/bin")
        self.assertEqual(env["FOO"], "bar")

    def test_drops_node_and_corepack_variables_that_can_swap_the_fetched_host(self):
        # 取得の段で置いた host は次のキー付きの起動がそのまま走らせる。node に任意のコードを
        # 読み込ませる変数、TLS の検証を切る変数、corepack の取得先と署名の鍵は pnpm に見せない
        env = jevlint_host.install_env(self.SOURCE)
        for dropped in (
            "NODE_OPTIONS",
            "NODE_TLS_REJECT_UNAUTHORIZED",
            "COREPACK_NPM_REGISTRY",
            "COREPACK_INTEGRITY_KEYS",
        ):
            with self.subTest(dropped=dropped):
                self.assertNotIn(dropped, env)
        # 対照: proxy と追加の CA は社内の構成で取得に要るので、同じ NODE_ の名前でも残る
        self.assertEqual(env["HTTPS_PROXY"], "http://proxy.example:8080")
        self.assertEqual(env["NODE_EXTRA_CA_CERTS"], "/marker/extra-ca.pem")

    def test_path_keeps_only_absolute_nonempty_entries(self):
        source = dict(self.SOURCE)
        source["PATH"] = "node_modules/.bin:/usr/bin::/bin:.:"
        env = jevlint_host.install_env(source)
        self.assertEqual(env["PATH"], "/usr/bin:/bin")

    def test_path_with_no_absolute_entry_is_a_host_error(self):
        # build_env と同じ理由 (pnpm が cwd の ./pnpm として起動されうる)
        for raw in ("", "node_modules/.bin:."):
            with self.subTest(raw=raw):
                with self.assertRaises(jevlint_host.HostError):
                    jevlint_host.install_env(dict(self.SOURCE, PATH=raw))
        source = {k: v for k, v in self.SOURCE.items() if k != "PATH"}
        self.assertNotIn("PATH", jevlint_host.install_env(source))

    def test_drops_npm_and_pnpm_config_vars_case_insensitively(self):
        # pnpm 12.3.4 で実測: `pnpm add` 自身が PNPM_CONFIG_REGISTRY を
        # 読み、到達不能な registry を指すとそこへ fetch しようとして失敗する
        # (`pnpm config get registry` だけの話ではない)
        source = dict(self.SOURCE)
        source.update(
            {
                "npm_config_registry": "http://evil.example",
                "PNPM_CONFIG_REGISTRY": "http://evil.example",
                "Npm_Config_Cafile": "/evil.pem",
                "pnpm_config_store_dir": "/evil-store",
            }
        )
        env = jevlint_host.install_env(source)
        for dropped in (
            "npm_config_registry",
            "PNPM_CONFIG_REGISTRY",
            "Npm_Config_Cafile",
            "pnpm_config_store_dir",
        ):
            self.assertNotIn(dropped, env)


class KeyStatusTests(unittest.TestCase):
    def test_ok_when_key_nonblank(self):
        self.assertEqual(jevlint_host.key_status({"TYPESAFE_API_KEY": "abc"}), "ok")

    def test_missing_when_empty(self):
        self.assertEqual(jevlint_host.key_status({"TYPESAFE_API_KEY": ""}), "missing")

    def test_missing_when_only_whitespace(self):
        self.assertEqual(jevlint_host.key_status({"TYPESAFE_API_KEY": "  \n"}), "missing")

    def test_missing_when_unset(self):
        self.assertEqual(jevlint_host.key_status({}), "missing")

    def test_legacy_only_when_only_typesafeai_key_present(self):
        self.assertEqual(jevlint_host.key_status({"TYPESAFEAI_API_KEY": "legacy"}), "legacy-only")

    def test_prefers_typesafe_over_legacy_when_both_present(self):
        status = jevlint_host.key_status(
            {"TYPESAFE_API_KEY": "abc", "TYPESAFEAI_API_KEY": "legacy"}
        )
        self.assertEqual(status, "ok")

    def test_legacy_key_blank_is_still_missing(self):
        status = jevlint_host.key_status({"TYPESAFE_API_KEY": "", "TYPESAFEAI_API_KEY": "  "})
        self.assertEqual(status, "missing")


class HostDirTests(unittest.TestCase):
    def test_prefers_xdg_cache_home(self):
        path = jevlint_host.host_dir(
            "0.7.0", {"XDG_CACHE_HOME": "/x/cache", "HOME": "/home/user"}
        )
        self.assertEqual(path, Path("/x/cache/jev-lint-curated/0.7.0"))

    def test_falls_back_to_home_cache_when_xdg_absent(self):
        path = jevlint_host.host_dir("0.7.0", {"HOME": "/home/user"})
        self.assertEqual(path, Path("/home/user/.cache/jev-lint-curated/0.7.0"))

    def test_falls_back_to_home_cache_when_xdg_empty(self):
        path = jevlint_host.host_dir("0.7.0", {"XDG_CACHE_HOME": "", "HOME": "/home/user"})
        self.assertEqual(path, Path("/home/user/.cache/jev-lint-curated/0.7.0"))

    def test_raises_when_neither_present(self):
        with self.assertRaises(jevlint_host.HostError):
            jevlint_host.host_dir("0.7.0", {})

    def test_relative_xdg_cache_home_is_ignored_and_falls_back_to_home(self):
        # XDG Base Directory の仕様: 相対な値は invalid で無視しなければならない。
        # 無視せず使うと host が起動時の cwd (消費側のリポジトリになりうる) の下に
        # 置かれる (実測)
        path = jevlint_host.host_dir(
            "0.7.0", {"XDG_CACHE_HOME": "cache", "HOME": "/home/user"}
        )
        self.assertEqual(path, Path("/home/user/.cache/jev-lint-curated/0.7.0"))

    def test_unexpanded_tilde_xdg_cache_home_is_ignored(self):
        path = jevlint_host.host_dir(
            "0.7.0", {"XDG_CACHE_HOME": "~/.cache", "HOME": "/home/user"}
        )
        self.assertEqual(path, Path("/home/user/.cache/jev-lint-curated/0.7.0"))

    def test_raises_when_xdg_relative_and_home_missing(self):
        with self.assertRaises(jevlint_host.HostError):
            jevlint_host.host_dir("0.7.0", {"XDG_CACHE_HOME": "cache"})

    def test_raises_when_home_is_relative(self):
        with self.assertRaises(jevlint_host.HostError):
            jevlint_host.host_dir("0.7.0", {"HOME": "relative/home"})

    def test_raises_when_home_is_unexpanded_tilde(self):
        with self.assertRaises(jevlint_host.HostError):
            jevlint_host.host_dir("0.7.0", {"HOME": "~"})

    def test_rejects_unsafe_version_component(self):
        for version in ("", ".", "..", "a/b", "a\\b"):
            with self.subTest(version=version):
                with self.assertRaises(jevlint_host.HostError):
                    jevlint_host.host_dir(version, {"HOME": "/home/user"})


def _populate_fake_package(root: Path, version: str) -> None:
    cli = jevlint_host.cli_path(root)
    cli.parent.mkdir(parents=True, exist_ok=True)
    cli.write_text("// fake cli\n", encoding="utf-8")
    jevlint_host.package_json_path(root).write_text(
        json.dumps({"version": version, "engines": {"node": ">=24"}}),
        encoding="utf-8",
    )


class _FakeRun:
    """`prepare_host` に渡す偽の `run`。argv・cwd・env を記録し、成功時のみパッケージを書く。"""

    def __init__(self, version: str, returncode: int = 0, populate: bool = True):
        self.version = version
        self.returncode = returncode
        self.populate = populate
        self.calls: list = []

    def __call__(self, argv, cwd, env):
        self.calls.append({"argv": list(argv), "cwd": str(cwd), "env": dict(env)})
        if self.populate:
            _populate_fake_package(Path(cwd), self.version)
        return types.SimpleNamespace(returncode=self.returncode)


class PrepareHostTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.cache_home = root / "cache"
        self.consumer_repo = root / "consumer-repo"
        self.consumer_repo.mkdir()
        self._orig_cwd = os.getcwd()
        os.chdir(self.consumer_repo)
        self.addCleanup(os.chdir, self._orig_cwd)
        self.source = {"XDG_CACHE_HOME": str(self.cache_home)}

    def test_argv_shape_places_package_spec_after_pnpm_add(self):
        run = _FakeRun("0.7.0")
        jevlint_host.prepare_host("0.7.0", self.source, run=run)
        self.assertEqual(len(run.calls), 1)
        argv = run.calls[0]["argv"]
        self.assertEqual(argv[0], "pnpm")
        self.assertIn("add", argv)
        self.assertIn("--allow-build=@ast-grep/cli", argv)
        self.assertEqual(argv[-1], "jev-lint@0.7.0")
        self.assertLess(argv.index("add"), argv.index("jev-lint@0.7.0"))

    def test_cwd_is_never_the_process_cwd_or_consumer_repo(self):
        run = _FakeRun("0.7.0")
        host = jevlint_host.prepare_host("0.7.0", self.source, run=run)
        cwd = Path(run.calls[0]["cwd"]).resolve()
        self.assertNotEqual(cwd, self.consumer_repo.resolve())
        self.assertNotEqual(cwd, Path(self._orig_cwd).resolve())
        self.assertEqual(cwd.parent.resolve(), host.parent.resolve())

    def test_env_has_no_key(self):
        source = dict(self.source)
        source["TYPESAFE_API_KEY"] = "secret"
        run = _FakeRun("0.7.0")
        jevlint_host.prepare_host("0.7.0", source, run=run)
        self.assertNotIn("TYPESAFE_API_KEY", run.calls[0]["env"])

    def test_failed_run_leaves_nothing_behind(self):
        run = _FakeRun("0.7.0", returncode=1, populate=False)
        with self.assertRaises(jevlint_host.HostError):
            jevlint_host.prepare_host("0.7.0", self.source, run=run)
        host = jevlint_host.host_dir("0.7.0", self.source)
        self.assertFalse(host.exists())
        remaining = list(host.parent.iterdir()) if host.parent.exists() else []
        self.assertEqual(remaining, [])

    def test_successful_run_renames_temp_into_host(self):
        run = _FakeRun("0.7.0")
        host = jevlint_host.prepare_host("0.7.0", self.source, run=run)
        self.assertTrue((host / "node_modules/jev-lint/dist/cli.js").is_file())
        self.assertEqual(len(run.calls), 1)
        # rename の残骸 (`.tmp-` の一時ディレクトリ) が親に残っていない
        siblings = [p.name for p in host.parent.iterdir()]
        self.assertEqual(siblings, [host.name])

    def test_reusable_host_skips_run(self):
        run1 = _FakeRun("0.7.0")
        host1 = jevlint_host.prepare_host("0.7.0", self.source, run=run1)
        run2 = _FakeRun("0.7.0")
        host2 = jevlint_host.prepare_host("0.7.0", self.source, run=run2)
        self.assertEqual(host1, host2)
        self.assertEqual(run2.calls, [])

    def test_version_mismatch_rebuilds_host(self):
        # 書き直しの分岐 (host_reusable(host) が偽で、既存の host をどかしてから置き換える
        # 経路) を実際に通す。別の版の host_dir を用意するだけではこの分岐に入らないので、
        # 同じ host_dir("0.7.0", ...) に直接古い version を置いてから、その同じ version で
        # prepare_host を呼び直す
        host = jevlint_host.host_dir("0.7.0", self.source)
        _populate_fake_package(host, "0.6.9")
        run = _FakeRun("0.7.0")
        result = jevlint_host.prepare_host("0.7.0", self.source, run=run)
        self.assertEqual(result, host)
        self.assertEqual(len(run.calls), 1)
        package_json = json.loads(
            (host / "node_modules/jev-lint/package.json").read_text(encoding="utf-8")
        )
        self.assertEqual(package_json["version"], "0.7.0")
        leftovers = [p.name for p in host.parent.iterdir() if p.name != host.name]
        self.assertEqual(leftovers, [])

    def test_pnpm_runs_in_a_tmp_dir_not_directly_in_host(self):
        # 「一時ディレクトリで取得してから host の名前へ置く」設計そのものを pin する。
        # pnpm は host に直接書かず、host の隣の一時ディレクトリで走る。最終状態 (cli.js が
        # host にある) だけを見るテストは、host へ直接インストールする実装でも緑になる
        host = jevlint_host.host_dir("0.7.0", self.source)
        seen = {}

        def probing_run(argv, cwd, env):
            seen["cwd"] = Path(cwd)
            seen["host_existed_during_run"] = host.exists()
            _populate_fake_package(Path(cwd), "0.7.0")
            return types.SimpleNamespace(returncode=0)

        jevlint_host.prepare_host("0.7.0", self.source, run=probing_run)
        self.assertNotEqual(seen["cwd"], host)
        self.assertTrue(seen["cwd"].name.startswith(".tmp-"), seen["cwd"].name)
        self.assertFalse(seen["host_existed_during_run"])

    def test_concurrent_winner_already_placed_host_is_respected(self):
        # 2 プロセスが同時に「再利用できない」と判定して pnpm を走らせた場面を模す。
        # 自分の run が返る前に、別プロセス (勝者) が同じ version を host に直接
        # 置いてしまっている。tmp.replace(host) は host が非空になっていて競合するが、
        # prepare_host は host を再確認して勝者に譲り、例外を出さない。
        #
        # 勝者と自分の取得物を同じ内容にすると、「再確認して譲る」と「確認せず自分の
        # もので上書きする」が区別できない (両方とも最終的に妥当な 0.7.0 の host になり、
        # post-pnpm の再確認を消す変異でも緑になる。変異注入で実測)。そこで cli.js の中身を
        # 勝者と自分とで変えて区別する
        host = jevlint_host.host_dir("0.7.0", self.source)
        winner_marker = "// winner cli (別プロセスが置いた)\n"

        def winner_run(argv, cwd, env):
            winner_cli = jevlint_host.cli_path(host)
            winner_cli.parent.mkdir(parents=True, exist_ok=True)
            winner_cli.write_text(winner_marker, encoding="utf-8")
            jevlint_host.package_json_path(host).write_text(
                json.dumps({"version": "0.7.0", "engines": {"node": ">=24"}}),
                encoding="utf-8",
            )
            _populate_fake_package(Path(cwd), "0.7.0")  # 自分の tmp 側も揃える
            return types.SimpleNamespace(returncode=0)

        result = jevlint_host.prepare_host("0.7.0", self.source, run=winner_run)
        self.assertEqual(result, host)
        self.assertEqual(
            (host / "node_modules/jev-lint/dist/cli.js").read_text(encoding="utf-8"),
            winner_marker,
        )
        # 自分の tmp が host の隣に残っていない (勝者の host だけが残る)
        siblings = [p.name for p in host.parent.iterdir()]
        self.assertEqual(siblings, [host.name])

    def test_run_succeeds_but_leaves_incomplete_package(self):
        # pnpm が rc=0 を返しても、取得物の形が想定と違えば (populate=False) 再利用
        # できないとみなして HostError にする。returncode!=0 の失敗経路と混同しない
        # ことを別のテストとして pin する
        run = _FakeRun("0.7.0", returncode=0, populate=False)
        with self.assertRaises(jevlint_host.HostError):
            jevlint_host.prepare_host("0.7.0", self.source, run=run)
        host = jevlint_host.host_dir("0.7.0", self.source)
        self.assertFalse(host.exists())
        remaining = list(host.parent.iterdir()) if host.parent.exists() else []
        self.assertEqual(remaining, [])

    def test_pnpm_workspace_ancestor_is_rejected(self):
        self.cache_home.mkdir(parents=True, exist_ok=True)
        (self.cache_home / "pnpm-workspace.yaml").write_text("packages: []\n", encoding="utf-8")
        run = _FakeRun("0.7.0")
        with self.assertRaises(jevlint_host.HostError):
            jevlint_host.prepare_host("0.7.0", self.source, run=run)
        self.assertEqual(run.calls, [])

    def test_symlink_loop_in_cache_path_becomes_hosterror(self):
        # 3.9.6: _reject_pnpm_workspace_ancestor の resolve() が RuntimeError になり
        # HostError に変わる (実測)。3.14.7: resolve() 自体は例外にならないが、その後の
        # parent.mkdir() がループを辿れず OSError (ELOOP) になり、それも HostError に
        # 変わる (jevlint_tree.py の _temp_base と同じ収束。両方とも実測)。
        # どちらの版でも HostError になるので version 分岐は要らない
        loop = self.cache_home.parent / "loop"
        loop.symlink_to(loop)
        source = {"XDG_CACHE_HOME": str(loop / "cache")}
        with self.assertRaises(jevlint_host.HostError):
            jevlint_host.prepare_host("0.7.0", source, run=_FakeRun("0.7.0"))

    def test_permission_denied_ancestor_becomes_hosterror(self):
        # 3.9.6: 祖先の exists() が PermissionError を再送出し HostError になる (実測)。
        # 3.14.7: exists() は握りつぶして False を返すが、その後 parent.mkdir() が
        # 同じ権限不足で OSError になり、それも HostError に変わる (実測)。
        # これも両方の版で HostError に収束する
        self.cache_home.mkdir(parents=True, exist_ok=True)
        os.chmod(self.cache_home, 0)
        self.addCleanup(os.chmod, self.cache_home, stat.S_IRWXU)
        with self.assertRaises(jevlint_host.HostError):
            jevlint_host.prepare_host("0.7.0", self.source, run=_FakeRun("0.7.0"))

    def test_package_json_written_is_private_true(self):
        seen = {}

        def recording_run(argv, cwd, env):
            seen["package_json"] = json.loads((Path(cwd) / "package.json").read_text())
            _populate_fake_package(Path(cwd), "0.7.0")
            return types.SimpleNamespace(returncode=0)

        jevlint_host.prepare_host("0.7.0", self.source, run=recording_run)
        self.assertEqual(seen["package_json"], {"private": True})

    def test_run_raising_oserror_becomes_hosterror_and_leaves_nothing(self):
        # `pnpm` が PATH に無いときの `subprocess.run` は FileNotFoundError (OSError の
        # 派生) を投げる。ここで HostError に変換されず素通りすると、呼び出し側が
        # 判定に使う終了コード 2 の契約が崩れる
        def raising_run(argv, cwd, env):
            raise FileNotFoundError("pnpm が見つからない")

        with self.assertRaises(jevlint_host.HostError):
            jevlint_host.prepare_host("0.7.0", self.source, run=raising_run)
        host = jevlint_host.host_dir("0.7.0", self.source)
        self.assertFalse(host.exists())
        remaining = list(host.parent.iterdir()) if host.parent.exists() else []
        self.assertEqual(remaining, [])


class HostReusableTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.host = Path(self._tmp.name)

    def test_true_when_version_matches_and_cli_present(self):
        _populate_fake_package(self.host, "0.7.0")
        self.assertTrue(jevlint_host.host_reusable(self.host, "0.7.0"))

    def test_false_when_version_differs(self):
        _populate_fake_package(self.host, "0.6.9")
        self.assertFalse(jevlint_host.host_reusable(self.host, "0.7.0"))

    def test_false_when_cli_js_missing(self):
        package_json = jevlint_host.package_json_path(self.host)
        package_json.parent.mkdir(parents=True)
        package_json.write_text(json.dumps({"version": "0.7.0"}), encoding="utf-8")
        self.assertFalse(jevlint_host.host_reusable(self.host, "0.7.0"))

    def test_false_when_host_empty(self):
        self.assertFalse(jevlint_host.host_reusable(self.host, "0.7.0"))

    def test_permission_denied_host_directory(self):
        # host_reusable は単独の関数で、prepare_host のように downstream の mkdir で
        # 例外を収束させる仕組みが無い。is_file() の PermissionError の扱いが版で
        # 違う (実測): 3.9.6 は再送出するので host_reusable は
        # HostError にする。3.14.7 の pathlib は PermissionError も握りつぶして
        # False を返すので、この版では「再利用できない」という通常の判定に落ちる。
        # unittest.skip は使わず (このリポジトリの runner は skip を赤にする)、
        # 版ごとの正しい期待値をそれぞれ検証する
        os.chmod(self.host, 0)
        self.addCleanup(os.chmod, self.host, stat.S_IRWXU)
        if sys.version_info >= (3, 13):
            self.assertFalse(jevlint_host.host_reusable(self.host, "0.7.0"))
        else:
            with self.assertRaises(jevlint_host.HostError):
                jevlint_host.host_reusable(self.host, "0.7.0")


class ParseNodeVersionTests(unittest.TestCase):
    def test_parses_v_prefixed_trailing_newline_form(self):
        self.assertEqual(jevlint_host.parse_node_version("v24.18.0\n"), (24, 18, 0))

    def test_rejects_malformed_output(self):
        for text in ("24.18.0", "v24.18", "node v24.18.0", "", "v24.18.0.1"):
            with self.subTest(text=text):
                with self.assertRaises(jevlint_host.HostError):
                    jevlint_host.parse_node_version(text)


class EnginesOkTests(unittest.TestCase):
    def test_satisfied_when_version_meets_minimum(self):
        version = jevlint_host.parse_node_version("v24.18.0\n")
        self.assertTrue(jevlint_host.engines_ok(">=24", version))

    def test_not_satisfied_when_major_below_minimum(self):
        version = jevlint_host.parse_node_version("v22.1.0\n")
        self.assertFalse(jevlint_host.engines_ok(">=24", version))

    def test_not_satisfied_when_minor_below_minimum(self):
        version = jevlint_host.parse_node_version("v24.0.5\n")
        self.assertFalse(jevlint_host.engines_ok(">=24.1", version))

    def test_rejects_unsupported_formats(self):
        version = (24, 0, 0)
        for engines in ("^24", ">=24 <26", "*", ">24", ">=", "24", ">=24\n"):
            with self.subTest(engines=engines):
                with self.assertRaises(jevlint_host.HostError):
                    jevlint_host.engines_ok(engines, version)


class ReadEnginesTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.host = Path(self._tmp.name)
        self.package_json = jevlint_host.package_json_path(self.host)
        self.package_json.parent.mkdir(parents=True)

    def test_reads_engines_node(self):
        self.package_json.write_text(
            json.dumps({"version": "0.7.0", "engines": {"node": ">=24"}}), encoding="utf-8"
        )
        self.assertEqual(jevlint_host.read_engines(self.host), ">=24")

    def test_raises_when_engines_missing(self):
        self.package_json.write_text(json.dumps({"version": "0.7.0"}), encoding="utf-8")
        with self.assertRaises(jevlint_host.HostError):
            jevlint_host.read_engines(self.host)

    def test_raises_when_package_json_missing(self):
        with self.assertRaises(jevlint_host.HostError):
            jevlint_host.read_engines(self.host)


class HostLayoutTests(unittest.TestCase):
    def test_paths_inside_the_host_follow_the_layout_pnpm_add_creates(self):
        # テストの偽物はこの 2 つの関数で host を作るので、レイアウトそのものはここで literal に pin する
        host = Path("/cache/jev-lint-curated/0.7.0")
        self.assertEqual(
            jevlint_host.cli_path(host),
            Path("/cache/jev-lint-curated/0.7.0/node_modules/jev-lint/dist/cli.js"),
        )
        self.assertEqual(
            jevlint_host.package_json_path(host),
            Path("/cache/jev-lint-curated/0.7.0/node_modules/jev-lint/package.json"),
        )


class FixedTailTests(unittest.TestCase):
    def test_eleven_elements_in_fixed_order(self):
        config = Path("/tmp/x/config.json")
        tail = jevlint_host.fixed_tail(config)
        self.assertEqual(
            tail,
            [
                "--json",
                "--config",
                str(config),
                "--cache",
                "none",
                "--retry",
                "3",
                "--model",
                "jev-latest",
                "--base-url",
                "https://api.typesafe.ai",
            ],
        )
        self.assertEqual(len(tail), 11)


class UpstreamArgvTests(unittest.TestCase):
    def setUp(self):
        self.node = "/usr/local/bin/node"
        self.host = Path("/cache/jev-lint-curated/0.7.0")
        self.config = Path("/scratch/config.json")

    def _argv(self, **overrides):
        kwargs = dict(
            base=None, excludes=[], paths=["a.py"], dry_run=False, record=None, config=self.config
        )
        kwargs.update(overrides)
        return jevlint_host.upstream_argv(self.node, self.host, "check", **kwargs)

    def test_does_not_include_pnpm(self):
        self.assertNotIn("pnpm", self._argv())

    def test_starts_with_node_and_cli_path(self):
        argv = self._argv()
        self.assertEqual(argv[0], self.node)
        self.assertEqual(argv[1], str(self.host / "node_modules/jev-lint/dist/cli.js"))
        self.assertEqual(argv[2], "check")

    def test_tail_is_fixed_part(self):
        argv = self._argv()
        self.assertEqual(argv[-11:], jevlint_host.fixed_tail(self.config))

    def test_base_value_is_the_given_sha(self):
        argv = self._argv(base="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")
        self.assertIn("--base", argv)
        self.assertEqual(
            argv[argv.index("--base") + 1], "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
        )

    def test_base_omitted_when_none(self):
        self.assertNotIn("--base", self._argv(base=None))

    def test_dry_run_without_record_omits_record_flag(self):
        argv = self._argv(dry_run=True, record=None)
        self.assertIn("--dry-run", argv)
        self.assertNotIn("--record", argv)

    def test_full_shape_matches_normative_order(self):
        record = Path("/scratch/record.json")
        argv = jevlint_host.upstream_argv(
            self.node,
            self.host,
            "review",
            base="deadbeef",
            excludes=["*.min.js", "vendor/**"],
            paths=["a.py", "b.py"],
            dry_run=True,
            record=record,
            config=self.config,
        )
        expected = [
            self.node,
            str(self.host / "node_modules/jev-lint/dist/cli.js"),
            "review",
            "--base",
            "deadbeef",
            "--exclude",
            "*.min.js",
            "--exclude",
            "vendor/**",
            "a.py",
            "b.py",
            "--dry-run",
            "--record",
            str(record),
            *jevlint_host.fixed_tail(self.config),
        ]
        self.assertEqual(argv, expected)


if __name__ == "__main__":
    unittest.main()
