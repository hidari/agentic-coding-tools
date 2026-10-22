#!/usr/bin/env python3
"""check-package-shape.py の検査機構と、その取り付けを検証する。

実ツリーではなく tempfile に組んだ最小のリポジトリで検証する。実ツリーに依存すると、
現ツリーがたまたま合格していることに寄りかかった dead pin になる。この検査は git を
呼ばないので、隣のテスト (test_check_related_refs.py など) が持つ GIT_* の消毒は要らない。

ExitCodes が checker を subprocess で起動して終了コードを pin する。関数を直接呼ぶ
テストだけだと、違反ありで `return 0` にする 1 行の変異が全緑のまま生き残る
(同じ判断を scripts/test_check_related_refs.py が持つ)。
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

CHECKER = "scripts/check-package-shape.py"
RUNNER = "scripts/run-python-tests.py"
PRE_COMMIT_CONFIG = ROOT / ".pre-commit-config.yaml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# 取り付けの pin が探す CHECKER と同じパスから読む。CHECKER が実在しないパスへ drift すると、
# ここの読み込みが落ちてファイルごと赤になる
shape = _load("check_package_shape_under_test", ROOT / CHECKER)


class Fixture:
    """規約 1〜8 をすべて満たす最小のリポジトリ。各テストはここから 1 箇所だけ崩す。"""

    def __init__(self, case: unittest.TestCase):
        tmp = tempfile.TemporaryDirectory()
        case.addCleanup(tmp.cleanup)
        # macOS の tempdir は /var -> /private/var の symlink 越しなので、比較の前に解決する
        self.root = Path(tmp.name).resolve()
        self.write("plugins/alpha/SKILL.md", "---\nname: alpha\ndescription: d\n---\n")
        self.plugin_json("alpha", {"name": "alpha"})
        self.write("skills/tooling/beta/SKILL.md", "---\nname: beta\ndescription: d\n---\n")

    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def plugin_json(self, pkg: str, data: dict) -> None:
        self.write(f"plugins/{pkg}/.claude-plugin/plugin.json", json.dumps(data))

    def check(self) -> list[str]:
        violations, _ = shape.check(self.root)
        return violations

    def run_cli(self) -> subprocess.CompletedProcess[str]:
        # CLI は引数を取らず自分の置き場から root を決めるので、checker と借用先を fixture の
        # scripts/ へ写して起動する。借用先を自分の隣から読む経路もこれで通る
        scripts = self.root / "scripts"
        scripts.mkdir(exist_ok=True)
        shutil.copy(ROOT / CHECKER, scripts)
        shutil.copy(ROOT / RUNNER, scripts)
        return subprocess.run(
            [sys.executable, "-B", str(self.root / CHECKER)],
            capture_output=True,
            text=True,
            check=False,
        )


class ExistingRules(unittest.TestCase):
    """関数化で壊れうる既存の規約の代表。違反なしの対照と違反ありを対で置く。"""

    def test_clean_fixture_has_no_violations_and_reports_what_it_saw(self):
        fx = Fixture(self)
        violations, counts = shape.check(fx.root)
        self.assertEqual([], violations)
        self.assertEqual(1, counts["plugin"])
        self.assertEqual(1, counts["skill"])
        self.assertGreater(counts["file"], 0)

    def test_rule1_plugin_without_root_skill_md(self):
        fx = Fixture(self)
        (fx.root / "plugins/alpha/SKILL.md").unlink()
        self.assertEqual(
            ["plugins/alpha: root に SKILL.md がない (apm が verbatim 経路を選ばない)"],
            fx.check(),
        )

    def test_rule2_plugin_name_differs_from_directory(self):
        fx = Fixture(self)
        fx.plugin_json("alpha", {"name": "other"})
        self.assertEqual(
            ["plugins/alpha: plugin.json の name (other) がディレクトリ名と違う"], fx.check()
        )

    def test_rule4_skills_declared_as_root(self):
        fx = Fixture(self)
        fx.plugin_json("alpha", {"name": "alpha", "skills": ["./"]})
        self.assertEqual(
            ['plugins/alpha: plugin.json の skills に "./" がある (apm が無限再帰する)'],
            fx.check(),
        )

    def test_rule5_author_email(self):
        fx = Fixture(self)
        fx.plugin_json("alpha", {"name": "alpha", "author": {"name": "a", "email": "x"}})
        self.assertEqual(
            ["plugins/alpha: plugin.json の author にメールアドレスが入っている"], fx.check()
        )

    def test_rule6_missing_description_in_standalone_skill(self):
        fx = Fixture(self)
        fx.write("skills/tooling/beta/SKILL.md", "---\nname: beta\n---\n")
        self.assertEqual(
            ["skills/tooling/beta: SKILL.md の frontmatter に description がない"], fx.check()
        )

    def test_rule7_install_path_of_own_package(self):
        fx = Fixture(self)
        fx.write("skills/tooling/beta/notes.md", "x\n~/.claude/skills/beta/run.sh\n")
        self.assertEqual(
            ["skills/tooling/beta/notes.md:2: インストール先を絶対パスで名指ししている (beta)"],
            fx.check(),
        )

    def test_check_does_not_carry_state_between_calls(self):
        # module-level の可変状態が残ると、2 回目の呼び出しに 1 回目の違反と件数が混ざる
        dirty = Fixture(self)
        dirty.plugin_json("alpha", {"name": "other"})
        self.assertEqual(1, len(dirty.check()))
        clean = Fixture(self)
        violations, counts = shape.check(clean.root)
        self.assertEqual([], violations)
        self.assertEqual(1, counts["plugin"])


class NoTestsInPackages(unittest.TestCase):
    """規約 8: plugins/ と skills/ の中にテストのファイルを置かない。"""

    def test_python_test_under_plugins(self):
        fx = Fixture(self)
        fx.write("plugins/alpha/scripts/test_probe.py", "")
        self.assertEqual(
            ["plugins/alpha/scripts/test_probe.py: パッケージの中にテストがある (配布物に入る)"],
            fx.check(),
        )

    def test_python_test_under_skills(self):
        fx = Fixture(self)
        fx.write("skills/tooling/beta/test_probe.py", "")
        self.assertEqual(
            ["skills/tooling/beta/test_probe.py: パッケージの中にテストがある (配布物に入る)"],
            fx.check(),
        )

    def test_mjs_test_under_plugins_and_skills(self):
        fx = Fixture(self)
        fx.write("plugins/alpha/schemas/tests/x.test.mjs", "")
        fx.write("skills/tooling/beta/y.test.mjs", "")
        self.assertEqual(
            [
                "plugins/alpha/schemas/tests/x.test.mjs: パッケージの中にテストがある (配布物に入る)",
                "skills/tooling/beta/y.test.mjs: パッケージの中にテストがある (配布物に入る)",
            ],
            fx.check(),
        )

    def test_tests_outside_packages_are_allowed(self):
        fx = Fixture(self)
        fx.write("scripts/package-tests/test_probe.py", "")
        fx.write("scripts/package-tests/x.test.mjs", "")
        self.assertEqual([], fx.check())

    def test_names_that_merely_contain_test_are_allowed(self):
        fx = Fixture(self)
        for name in ("contest.py", "latest.py", "testing.py", "x.test.js", "test.mjs"):
            fx.write(f"plugins/alpha/scripts/{name}", "")
        self.assertEqual([], fx.check())

    def test_exclusion_does_not_reach_ordinary_directories(self):
        # 除外を広げすぎる方向の対照。`tests` や `cache` (ドット無し) は配布物に入る
        fx = Fixture(self)
        fx.write("plugins/alpha/tests/test_a.py", "")
        fx.write("plugins/alpha/cache/b.test.mjs", "")
        self.assertEqual(2, len(fx.check()))

    def test_by_products_are_excluded_on_both_halves(self):
        # Python 側と .mjs 側で除外の集合が違うと、同じ置き場の片方だけが報告される
        fx = Fixture(self)
        for d in (".cache", "node_modules", ".venv"):
            fx.write(f"plugins/alpha/{d}/test_a.py", "")
            fx.write(f"plugins/alpha/{d}/b.test.mjs", "")
        self.assertEqual([], fx.check())

    def test_real_tree_has_tests_but_none_inside_packages(self):
        # 実ツリーの 0 件が「見ていない」ではないことの対照。移した先のテストは見えている
        runner = shape.load_runner()
        found = [p.relative_to(ROOT).as_posix() for p in runner.discover(ROOT)]
        self.assertTrue([p for p in found if p.startswith("scripts/package-tests/")])
        violations, _ = shape.check(ROOT)
        self.assertEqual([], [v for v in violations if "テストがある" in v])


class BorrowedRunner(unittest.TestCase):
    """Python のテストの定義は run-python-tests.py から借りる。借用が切れたら rc 2 で落ちる。"""

    def test_all_borrowed_names_exist(self):
        runner = shape.load_runner()
        self.assertEqual([], [n for n in shape.BORROWED if not hasattr(runner, n)])

    def test_python_side_follows_the_runner_definition(self):
        # パターンを checker へ写すと、runner の定義が変わっても規約 8 だけが古い形で判定する
        fx = Fixture(self)
        fx.write("plugins/alpha/anything.py", "")
        fake = fx.write(
            "fake-runner.py",
            "SKIP_DIRS = set()\n"
            "def discover(root):\n"
            "    return [root / 'plugins/alpha/anything.py']\n",
        )
        violations, _ = shape.check(fx.root, runner_path=fake)
        self.assertEqual(
            ["plugins/alpha/anything.py: パッケージの中にテストがある (配布物に入る)"], violations
        )

    def test_missing_borrowed_name_raises(self):
        fx = Fixture(self)
        fake = fx.write("fake-runner.py", "SKIP_DIRS = set()\n")
        with self.assertRaises(shape.CheckError):
            shape.check(fx.root, runner_path=fake)

    def test_unreadable_runner_raises(self):
        fx = Fixture(self)
        with self.assertRaises(shape.CheckError):
            shape.check(fx.root, runner_path=fx.root / "missing.py")

    def test_cli_exits_two_when_the_runner_is_gone(self):
        fx = Fixture(self)
        fx.run_cli()
        (fx.root / RUNNER).unlink()
        proc = subprocess.run(
            [sys.executable, "-B", str(fx.root / CHECKER)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(2, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("run-python-tests.py", proc.stdout)


class ExitCodes(unittest.TestCase):
    """終了コードの写像を subprocess で pin する。"""

    def test_clean_fixture_exits_zero(self):
        proc = Fixture(self).run_cli()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("違反なし", proc.stdout)

    def test_violation_exits_one_and_names_the_file(self):
        fx = Fixture(self)
        fx.write("plugins/alpha/scripts/test_probe.py", "")
        proc = fx.run_cli()
        self.assertEqual(1, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("違反 1 件:", proc.stdout)
        self.assertIn("  [x] plugins/alpha/scripts/test_probe.py: ", proc.stdout)

    def test_real_repository_exits_zero(self):
        # pre-commit と CI が根拠にしているのはこの rc そのもの
        proc = subprocess.run(
            [sys.executable, "-B", str(ROOT / CHECKER)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("違反なし", proc.stdout)


class Attachment(unittest.TestCase):
    """取り付けを pin する。読み方の補助と限界は scripts/hook_config_lines.py が持つ。"""

    @classmethod
    def setUpClass(cls):
        helpers = _load("hook_config_lines", HERE / "hook_config_lines.py")
        cls.helpers = helpers
        # entry にフラグが無いので、パス自体を空白区切りの 1 語として照合する
        cls.block = helpers.hook_block(helpers.live_lines(PRE_COMMIT_CONFIG), CHECKER, CHECKER)
        cls.ci = helpers.invocations(helpers.live_lines(CI_WORKFLOW), CHECKER, CHECKER)

    def test_checker_path_exists(self):
        self.assertTrue((ROOT / CHECKER).is_file())

    def test_pre_commit_runs_the_checker(self):
        self.assertTrue(self.block, f"{CHECKER} を呼ぶ hook が見つからない")
        self.assertEqual([CHECKER], self.helpers.hook_values(self.block, "entry"))
        self.assertEqual(
            [self.helpers.HOOK_LANGUAGE], self.helpers.hook_values(self.block, "language")
        )

    def test_pre_commit_hook_is_not_narrowed(self):
        # 規約 8 も 7 もリポジトリ全体を見るので、変更ファイルで絞ると走査集合が縮む
        self.assertEqual(["false"], self.helpers.hook_values(self.block, "pass_filenames"))
        self.assertEqual(["true"], self.helpers.hook_values(self.block, "always_run"))
        narrowing = {"files", "exclude", "types", "exclude_types", "stages"}
        self.assertEqual(set(), self.helpers.hook_keys(self.block) & narrowing)

    def test_ci_runs_the_checker(self):
        # 起動行の形まで pin する。パスの存在だけだと `run: echo <path>` が緑のまま通る
        self.assertEqual(
            [f"run: python3 {CHECKER}"],
            [line.strip().removeprefix("- ") for line in self.ci],
        )


if __name__ == "__main__":
    unittest.main()
