"""run-python-tests.py の仕様 pin。

検査対象を壊す変異だけでなく、検査機構そのものを壊す変異 (manifest 照合の削除・
--update-manifest の赤時拒否の削除・全体走査から列挙方式への回帰) が赤になることを
狙って pin を置く。機構は fixture のミニリポジトリ (一時ツリー) に対して
end-to-end で検証する。

main() へ実リポジトリの ROOT を渡すと、この自己テスト自身が収集され子プロセスで
再帰実行される。テストは必ず fixture ツリー限定で main() を呼ぶこと。fixture には
自己テストのコピーを置かず、再帰をここで断つ。
"""
from __future__ import annotations

import importlib.util
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent

# ハイフン名のスクリプトは import 文では読めないため importlib で読む。
# `scripts/` の検査スクリプトをテストする定型として、今後の自己テストもこの形を使う。
spec = importlib.util.spec_from_file_location("run_python_tests", SCRIPTS / "run-python-tests.py")
assert spec is not None and spec.loader is not None
runner = importlib.util.module_from_spec(spec)
sys.modules["run_python_tests"] = runner
spec.loader.exec_module(runner)

PASSING_TESTS = '''\
import unittest


class Probe(unittest.TestCase):
    def test_a(self):
        self.assertEqual(1 + 1, 2)

    def test_b(self):
        self.assertEqual(2 + 2, 4)
'''

# 各テストが自分の id を記録する。「列挙した集合 = 実行した集合」を実行の側から
# 観測するための fixture
TRACKING_TESTS = '''\
import unittest


class Probe(unittest.TestCase):
    def _mark(self):
        with open("executed.log", "a", encoding="utf-8") as f:
            f.write(self.id() + "\\n")

    def test_a(self):
        self._mark()

    def test_b(self):
        self._mark()
'''


def write_fixture_tree(root: Path, test_source: str = PASSING_TESTS) -> Path:
    """skills/pkg/test_probe.py を持つミニリポジトリを作り、テストファイルを返す。"""
    pkg = root / "skills" / "pkg"
    pkg.mkdir(parents=True)
    (root / "scripts").mkdir()
    test_file = pkg / "test_probe.py"
    test_file.write_text(test_source, encoding="utf-8")
    return test_file


def run_main(root: Path, argv: list[str] | None = None) -> tuple[int, str]:
    out = io.StringIO()
    with redirect_stdout(out):
        rc = runner.main(argv or [], root=root)
    return rc, out.getvalue()


class Discover(unittest.TestCase):
    def test_new_top_level_directory_is_scanned(self):
        # 全体走査の pin。ディレクトリ列挙 (旧 SEARCH_DIRS) 方式へ戻す変異は、
        # 列挙に無いトップレベルディレクトリを見失ってここが赤になる
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            known = write_fixture_tree(root)
            fresh = root / "tools" / "test_fresh.py"
            fresh.parent.mkdir()
            fresh.write_text(PASSING_TESTS, encoding="utf-8")
            found = runner.discover(root)
        self.assertEqual(found, sorted([known, fresh]))

    def test_skip_dirs_are_excluded_by_relative_parts(self):
        # .cache には隔離実験のテストコピーが置かれるので、収集すると実験が本物の
        # 検査を汚す。逆に、リポジトリ自体が SKIP_DIRS の名前を含む場所へ checkout
        # されても全件除外にならないこと (root 相対 parts での判定) を同時に pin する
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / ".cache" / "somewhere"
            test_file = write_fixture_tree(root)
            hidden = root / ".cache" / "test_hidden.py"
            hidden.parent.mkdir()
            hidden.write_text(PASSING_TESTS, encoding="utf-8")
            found = runner.discover(root)
        self.assertEqual(found, [test_file])


class Bootstrap(unittest.TestCase):
    def test_enumerated_ids_are_exactly_the_executed_ids(self):
        # 列挙した集合 = 実行した集合の pin。列挙と実行を別ロードに分ける変異は
        # ここで食い違いとして現れる
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            test_file = write_fixture_tree(root, TRACKING_TESTS)
            ok, ids, _ = runner.run_one(test_file)
            log = test_file.parent / "executed.log"
            executed = set(log.read_text(encoding="utf-8").split())
        self.assertTrue(ok)
        self.assertEqual(set(ids), executed)
        self.assertEqual(set(ids), {"test_probe.Probe.test_a", "test_probe.Probe.test_b"})


class ManifestParse(unittest.TestCase):
    def test_header_and_blank_lines_are_ignored(self):
        parsed = runner.parse_manifest("# ヘッダ\n\na.py::m.C.t\n")
        self.assertEqual(parsed, {"a.py::m.C.t"})


class MainEndToEnd(unittest.TestCase):
    def test_matching_manifest_is_green_and_reports_counts(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fixture_tree(root)
            rc, out = run_main(root, ["--update-manifest"])
            self.assertEqual(rc, 0, out)
            rc, out = run_main(root)
        self.assertEqual(rc, 0, out)
        # 緑でも「何を何件見たか」が出力に現れること (空と健全を区別する方針)
        self.assertIn("1 ファイル", out)
        self.assertIn("2 件", out)

    def test_thinned_run_names_the_missing_test(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            test_file = write_fixture_tree(root)
            run_main(root, ["--update-manifest"])
            thinned = PASSING_TESTS.replace(
                "    def test_b(self):\n        self.assertEqual(2 + 2, 4)\n", ""
            )
            test_file.write_text(thinned, encoding="utf-8")
            rc, out = run_main(root)
        self.assertEqual(rc, 1)
        self.assertIn("消えたテスト: skills/pkg/test_probe.py::test_probe.Probe.test_b", out)
        # 復旧コマンドが赤のメッセージに直書きされていること
        self.assertIn("python3 scripts/run-python-tests.py --update-manifest", out)

    def test_unrecorded_test_is_red(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            test_file = write_fixture_tree(root)
            run_main(root, ["--update-manifest"])
            test_file.write_text(
                PASSING_TESTS + "\n    def test_c(self):\n        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            rc, out = run_main(root)
        self.assertEqual(rc, 1)
        self.assertIn("未記録のテスト: skills/pkg/test_probe.py::test_probe.Probe.test_c", out)

    def test_same_count_swap_is_red_in_both_directions(self):
        # 同数入れ替え (1 件消して 1 件足す) は件数方式では原理的に検出できない。
        # ID 集合方式を選んだ理由そのものの pin
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            test_file = write_fixture_tree(root)
            run_main(root, ["--update-manifest"])
            test_file.write_text(
                PASSING_TESTS.replace("def test_b", "def test_z"), encoding="utf-8"
            )
            rc, out = run_main(root)
        self.assertEqual(rc, 1)
        self.assertIn("消えたテスト: skills/pkg/test_probe.py::test_probe.Probe.test_b", out)
        self.assertIn("未記録のテスト: skills/pkg/test_probe.py::test_probe.Probe.test_z", out)

    def test_missing_manifest_is_exit_2(self):
        # 照合基準が無いのは違反 (1) ではなく検査不能 (2)。混ぜると manifest の
        # 置き忘れがテストの失敗に見える (check-leak-guard-rules.py と同じ分離)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fixture_tree(root)
            rc, out = run_main(root)
        self.assertEqual(rc, 2)
        self.assertIn("python3 scripts/run-python-tests.py --update-manifest", out)


class UpdateRefusal(unittest.TestCase):
    def test_update_refuses_when_a_test_fails(self):
        # 赤のまま更新できると、壊れた収集や落ちるテストの集合が baseline に
        # 焼き込まれ、次回から「痩せた集合との一致」が緑になる
        failing = PASSING_TESTS + "\n    def test_red(self):\n        self.fail()\n"
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fixture_tree(root, failing)
            rc, _ = run_main(root, ["--update-manifest"])
            manifest_written = (root / "scripts" / runner.MANIFEST_NAME).exists()
        self.assertEqual(rc, 1)
        self.assertFalse(manifest_written, "赤のまま manifest が書かれた")


class ThinningVectors(unittest.TestCase):
    def test_skip_is_red_and_names_the_skipped_test(self):
        # skip されたテストも suite (= 列挙) に載るので manifest では捕まらない (実測)。
        # 「実行していないのに緑」を許さないため独立に赤へ倒す
        skipping = PASSING_TESTS.replace(
            "    def test_b", "    @unittest.skip('probe')\n    def test_b"
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            test_file = write_fixture_tree(root, skipping)
            ok, ids, summary = runner.run_one(test_file)
        self.assertFalse(ok)
        self.assertIn("test_probe.Probe.test_b", summary)
        # skip されても列挙には載ること自体も pin する (manifest 側が立つ前提)
        self.assertIn("test_probe.Probe.test_b", ids)

    def test_expected_failure_is_red(self):
        # expectedFailure は wasSuccessful() が成功に数え、件数にも exit code にも
        # 現れない (実測)。子プロセスは緑で返るので、この検出は列挙側の担当
        xfail = PASSING_TESTS.replace(
            "    def test_b(self):\n        self.assertEqual(2 + 2, 4)\n",
            "    @unittest.expectedFailure\n    def test_b(self):\n        self.fail()\n",
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            test_file = write_fixture_tree(root, xfail)
            ok, _, summary = runner.run_one(test_file)
        self.assertFalse(ok)
        self.assertIn("test_probe.Probe.test_b", summary)

    def test_zero_tests_is_red(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            test_file = write_fixture_tree(root, "import unittest\n")
            ok, _, summary = runner.run_one(test_file)
        self.assertFalse(ok)
        self.assertIn("0 件", summary)

    def test_import_error_is_red(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            test_file = write_fixture_tree(root, "import missing_module_xyz\n")
            ok, _, _ = runner.run_one(test_file)
        self.assertFalse(ok)


class Attachment(unittest.TestCase):
    """取り付けの pin。片側を外すともう片側の実行でここが赤くなる。

    両側を同時に外すとこのテスト自身が走らず検出できない (runner の docstring が
    言う自己ホスト盲点)。stdlib に YAML パーサが無いため非コメント行の部分文字列で
    見る。YAML 構造としての妥当性までは見ない。
    """

    def assert_invoked(self, config: Path):
        lines = [
            line
            for line in config.read_text(encoding="utf-8").splitlines()
            if "scripts/run-python-tests.py" in line and not line.lstrip().startswith("#")
        ]
        self.assertTrue(lines, f"{config.name} が run-python-tests.py を呼んでいない")

    def test_pre_commit_invokes_the_runner(self):
        self.assert_invoked(ROOT / ".pre-commit-config.yaml")

    def test_ci_invokes_the_runner(self):
        self.assert_invoked(ROOT / ".github" / "workflows" / "ci.yml")


if __name__ == "__main__":
    unittest.main()
