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
        self.assertIn("1", out)  # 走査件数を出す

    def test_all_checked_active_issue_is_a_violation(self):
        rc, _, err = self._run(lambda fx: fx.add_issue(
            "ISSUE-1_probe", issue_md("open", ["- [x] 済み", "- [x] 済み"])))
        self.assertEqual(rc, 1)
        self.assertIn("ISSUE-1_probe", err)

    def test_all_checked_closed_issue_passes(self):
        rc, _, _ = self._run(lambda fx: fx.add_issue(
            "closed/ISSUE-1_probe", issue_md("closed", ["- [x] 済み"])))
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
