"""jevlint_host.py (env、host、node、上流の起動) の仕様。

見るのは次の 9 つの入口: `build_env`、`install_env`、`key_status`、`host_dir`、
`prepare_host`、`host_reusable`、`parse_node_version`、`engines_ok`、`read_engines`、
`upstream_argv`、`fixed_tail`。`prepare_host` のテストだけが `run` を偽物に差し替えて
副作用 (argv・cwd・env・ファイルの生成) を見る。ネットワーク・pnpm 本体・node 本体・
実際のキーには依存しない。

`prepare_host` の偽の `run` は、`XDG_CACHE_HOME` を指す一時ディレクトリの中で
`node_modules/jev-lint/{package.json,dist/cli.js}` を実際に作ってから返り値を返す。
テストの `source` は `os.environ` を直接使わず、`XDG_CACHE_HOME` を差し替えた辞書を渡す
(host の置き場をテストごとに隔離するため)。cwd の検査は `os.chdir` で「消費側のリポジトリ」
を模した別ディレクトリへ一時的に移動し、`run` に渡った cwd がそれと一致しないことで見る。
"""

from __future__ import annotations

import json
import os
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
        "JEV_LINT_AST_GREP": "/tmp/evil-ast-grep",
        "TYPESAFEAI_API_KEY": "legacy-secret",
        "TYPESAFE_BASE_URL": "https://evil.example",
        "TYPESAFE_API_KEY": "secret-key",
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
            "JEV_LINT_AST_GREP",
            "TYPESAFEAI_API_KEY",
            "TYPESAFE_BASE_URL",
            "TYPESAFE_API_KEY",
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
        # build_env が固定で足す (Task 3 で測定した lazy fetch / refs/replace の fail-closed 策。
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


def _populate_fake_package(root: Path, version: str) -> None:
    package_dir = root / "node_modules" / "jev-lint"
    (package_dir / "dist").mkdir(parents=True, exist_ok=True)
    (package_dir / "dist" / "cli.js").write_text("// fake cli\n", encoding="utf-8")
    (package_dir / "package.json").write_text(
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
        run1 = _FakeRun("0.7.0")
        jevlint_host.prepare_host("0.7.0", self.source, run=run1)
        run2 = _FakeRun("0.8.0")
        host = jevlint_host.prepare_host("0.8.0", self.source, run=run2)
        self.assertEqual(len(run2.calls), 1)
        package_json = json.loads(
            (host / "node_modules/jev-lint/package.json").read_text(encoding="utf-8")
        )
        self.assertEqual(package_json["version"], "0.8.0")

    def test_pnpm_workspace_ancestor_is_rejected(self):
        self.cache_home.mkdir(parents=True, exist_ok=True)
        (self.cache_home / "pnpm-workspace.yaml").write_text("packages: []\n", encoding="utf-8")
        run = _FakeRun("0.7.0")
        with self.assertRaises(jevlint_host.HostError):
            jevlint_host.prepare_host("0.7.0", self.source, run=run)
        self.assertEqual(run.calls, [])

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
        package_dir = self.host / "node_modules" / "jev-lint"
        package_dir.mkdir(parents=True)
        (package_dir / "package.json").write_text(
            json.dumps({"version": "0.7.0"}), encoding="utf-8"
        )
        self.assertFalse(jevlint_host.host_reusable(self.host, "0.7.0"))

    def test_false_when_host_empty(self):
        self.assertFalse(jevlint_host.host_reusable(self.host, "0.7.0"))


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
        self.package_dir = self.host / "node_modules" / "jev-lint"
        self.package_dir.mkdir(parents=True)

    def test_reads_engines_node(self):
        (self.package_dir / "package.json").write_text(
            json.dumps({"version": "0.7.0", "engines": {"node": ">=24"}}), encoding="utf-8"
        )
        self.assertEqual(jevlint_host.read_engines(self.host), ">=24")

    def test_raises_when_engines_missing(self):
        (self.package_dir / "package.json").write_text(
            json.dumps({"version": "0.7.0"}), encoding="utf-8"
        )
        with self.assertRaises(jevlint_host.HostError):
            jevlint_host.read_engines(self.host)

    def test_raises_when_package_json_missing(self):
        with self.assertRaises(jevlint_host.HostError):
            jevlint_host.read_engines(self.host)


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
