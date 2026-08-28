"""check-issue-closure.py の仕様。

git を歩くので実ツリーではなく tempfile + git init の fixture で検証する。実ツリーに
依存すると、現ツリーがたまたま合格していることに寄りかかった dead pin になる。
GIT_CONFIG_GLOBAL / GIT_CONFIG_SYSTEM を潰すのは、global の core.excludesfile が
`*.md` を ignore していると git ls-files の走査集合だけが縮むため (同じ理由と形を
scripts/test_check_related_refs.py が持つ)。
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKER = "scripts/check-issue-closure.py"

GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "probe",
    "GIT_AUTHOR_EMAIL": "probe@example.invalid",
    "GIT_COMMITTER_NAME": "probe",
    "GIT_COMMITTER_EMAIL": "probe@example.invalid",
}


def load():
    """ハイフン名のスクリプトは import 文では読めないため importlib で読む。"""
    path = ROOT / CHECKER
    spec = importlib.util.spec_from_file_location("check_issue_closure", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = load()


def issue_md(status: str, tasks: list[str], heading: str = "## タスク") -> str:
    body = "\n".join(tasks)
    return f"---\nstatus: {status}\n---\n\n# probe\n\n{heading}\n\n{body}\n"


class Fixture:
    """docs/issues/ を持つ使い捨てリポジトリ。"""

    def __init__(self, tmp: str):
        self.root = Path(tmp)
        self._git("init", "-q")

    def _git(self, *args):
        subprocess.run(["git", *args], cwd=self.root, env=GIT_ENV, check=True,
                       capture_output=True)

    def add_issue(self, rel: str, text: str, filename: str = "issue.md"):
        path = self.root / "docs" / "issues" / rel
        path.mkdir(parents=True, exist_ok=True)
        (path / filename).write_text(text, encoding="utf-8")

    def add_issue_encoded(self, rel: str, text: str, encoding: str, filename: str = "issue.md"):
        """UTF-8 以外のエンコーディングで issue.md を書く。read_text の例外パスを検証する。"""
        path = self.root / "docs" / "issues" / rel
        path.mkdir(parents=True, exist_ok=True)
        (path / filename).write_bytes(text.encode(encoding))

    def commit(self):
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "probe")


class FixtureCase(unittest.TestCase):
    """fixture を組んで検査スクリプトを走らせる共通ヘルパ。テストメソッドは持たない。

    InvariantB 以降がここを継承する形にするのは、unittest.TestCase のサブクラス化が
    テストメソッドまで継承してしまうため。InvariantB(InvariantA) にすると InvariantA の
    3 テストが InvariantB 名義でも実行され、manifest に実体のない重複 ID が入る。
    """

    def _run(self, build) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            fx = Fixture(tmp)
            build(fx)
            fx.commit()
            proc = subprocess.run(
                [sys.executable, str(ROOT / CHECKER), "--root", str(fx.root)],
                capture_output=True, text=True, env=GIT_ENV,
            )
            return proc.returncode, proc.stdout, proc.stderr


class InvariantA(FixtureCase):
    """タスクを全部消化した Issue が active に残っていないこと。"""

    def test_unfinished_issue_passes(self):
        rc, out, _ = self._run(lambda fx: fx.add_issue(
            "ISSUE-1_probe", issue_md("open", ["- [x] 済み", "- [ ] 未"])))
        self.assertEqual(rc, 0)
        # "1" だけだと closed/missing/unscanned のどの桁にも化けて通ってしまう弱い
        # assertion だったので、active の件数として出ていることまで確かめる
        self.assertIn("active 1 個", out)

    def test_all_checked_active_issue_is_a_violation(self):
        rc, _, err = self._run(lambda fx: fx.add_issue(
            "ISSUE-1_probe", issue_md("open", ["- [x] 済み", "- [x] 済み"])))
        self.assertEqual(rc, 1)
        self.assertIn("ISSUE-1_probe", err)

    def test_all_checked_closed_issue_passes(self):
        rc, _, _ = self._run(lambda fx: fx.add_issue(
            "closed/ISSUE-1_probe", issue_md("closed", ["- [x] 済み"])))
        self.assertEqual(rc, 0)

    def test_unclosed_fence_is_unscanned_not_a_violation(self):
        """全箱チェック済みでも、フェンスが閉じていなければ走査できず違反にもならない。

        閉じ忘れ自体は issue-id.py --check が別途検出するので、ここでは二重に違反として
        報告せず「走査できなかった」件数へ寄せる (コントローラの裁定)。
        """
        text = (
            "---\nstatus: open\n---\n\n# probe\n\n"
            "```\n"
            "## タスク\n\n- [x] 済み\n"
        )
        rc, out, err = self._run(lambda fx: fx.add_issue("ISSUE-1_probe", text))
        self.assertEqual(rc, 0)
        self.assertIn("走査できなかった 1 個", out)
        self.assertIn("違反 0 件", out)
        self.assertIn("ISSUE-1_probe", err)
        # 走査できなかった注記は違反 ([x]) とは別記号で出す
        self.assertIn("[-]", err)
        self.assertNotIn("[x]", err)

    def test_scan_tasks_respects_closing_fence_length(self):
        """4 連バッククォートで開いたフェンスは、内側の 3 連の行では閉じない。

        単純トグルだと 3 連の行を閉じと誤認し、直後の本物のタスク節がフェンス内へ
        吸い込まれて消える (コントローラの実測)。CommonMark どおり、開始と同じ長さ以上の
        情報文字列なしの行でなければ閉じないことを確かめる。
        """
        text = (
            "````\n"
            "```\n"
            "````\n"
            "\n"
            "## タスク\n\n- [x] 済み\n"
        )
        self.assertEqual(checker.scan_tasks(text), (True, 1, 0))

    def test_cp932_issue_is_unscanned_not_a_crash(self):
        """UTF-8 で読めない issue.md は例外を伝播させず「走査できなかった」件数へ寄せる。

        修正前は UnicodeDecodeError が未捕捉のまま抜け、Python 既定の rc 1 が
        「違反あり」と誤認される形になっていた (コントローラの実測)。
        """
        text = issue_md("open", ["- [x] 済み"])
        rc, out, err = self._run(
            lambda fx: fx.add_issue_encoded("ISSUE-1_probe", text, "cp932"))
        self.assertEqual(rc, 0)
        self.assertIn("走査できなかった 1 個", out)
        self.assertIn("違反 0 件", out)
        self.assertNotIn("Traceback", err)


class FormatVariants(unittest.TestCase):
    """行頭アンカーの素朴な正規表現だと、完了済み Issue が 9 通りで素通りする
    (fixture で実測)。GitHub 上では完了済みとして正常に描画されるので、レビューでも
    気づけない。吸収する範囲を仕様として固定する。
    """

    def _scan(self, body: str, heading: str = "## タスク"):
        return checker.scan_tasks(f"{heading}\n\n{body}\n")

    def test_uppercase_x_counts_as_checked(self):
        self.assertEqual(self._scan("- [X] 済み"), (True, 1, 0))

    def test_asterisk_and_plus_markers_count(self):
        self.assertEqual(self._scan("* [x] 済み\n+ [x] 済み"), (True, 2, 0))

    def test_indented_list_counts(self):
        self.assertEqual(self._scan("  - [x] 済み"), (True, 1, 0))

    def test_fullwidth_space_is_unchecked(self):
        self.assertEqual(self._scan("- [　] 未"), (True, 1, 1))

    def test_heading_level_and_spacing_variants(self):
        self.assertTrue(self._scan("- [x] 済み", heading="### タスク")[0])
        self.assertTrue(self._scan("- [x] 済み", heading="##タスク")[0])

    def test_boxes_inside_a_code_fence_are_ignored(self):
        body = "- [x] 済み\n\n```\n- [ ] 囮\n```"
        self.assertEqual(self._scan(body), (True, 1, 0))

    def test_boxes_inside_an_html_comment_are_ignored(self):
        body = "- [x] 済み\n\n<!--\n- [ ] 囮\n-->"
        self.assertEqual(self._scan(body), (True, 1, 0))

    def test_a_box_outside_the_task_section_still_counts(self):
        # 節の外へ囮を 1 個置くだけで免除される形にしない。C.3 も全文を数える
        body = "- [x] 済み"
        text = f"## タスク\n\n{body}\n\n## 関連\n\n- [ ] 囮\n"
        self.assertEqual(checker.scan_tasks(text), (True, 2, 1))


if __name__ == "__main__":
    unittest.main()
