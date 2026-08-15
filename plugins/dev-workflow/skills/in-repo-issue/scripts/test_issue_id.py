"""issue-id.py の仕様 pin。

git を歩く経路 (--next の ref 横断と --check の追跡下ファイル列挙) は、実ツリーではなく
tempfile + git init の fixture リポジトリで検証する。実ツリーに依存させると、実ツリーが
たまたま条件を満たしているだけで緑になり dead pin になる。

検査対象を壊す変異だけでなく、検査機構そのものを壊す変異 (免除定数を空にする・フェンス
閉じ忘れの報告を落とす・ls-tree の -r を外す) と、免除が広がりすぎる方向の変異
(後読みへ英数字を足す) が赤になることを狙って pin を置く。
"""

from __future__ import annotations

import importlib.util
import io
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

HERE = Path(__file__).resolve().parent

# ハイフン名のスクリプトは import 文では読めないため importlib で読む
# (scripts/test_run_python_tests.py と同じ定型)
spec = importlib.util.spec_from_file_location("issue_id", HERE / "issue-id.py")
assert spec is not None and spec.loader is not None
issue_id = importlib.util.module_from_spec(spec)
sys.modules["issue_id"] = issue_id
spec.loader.exec_module(issue_id)

PREFIX = issue_id.PREFIX

# ケース文字列の記号は変数で埋める。literal で書くとこのファイル自身が --check の違反に
# なり、取り付け後に自分のテストで自分が赤くなる (scripts/check-leak-guard-rules.py が
# 同じ理由で検出側のユーザー名を変数にしている先例)。
SIGIL = "#"


def fence(n: int = 3) -> str:
    """N 連バッククォートの行を作る。

    リテラルの複数行文字列で書くと、このファイルのソース行そのものがフェンス行に見え、
    --check がこのファイルを「閉じ忘れ」で赤くする。行として組み立てて避ける。
    """
    return "`" * n


# 実行環境の global / system 設定を読ませない。hooksPath や commit.gpgsign が入っていると
# fixture の commit が環境依存で落ちる
GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "probe",
    "GIT_AUTHOR_EMAIL": "probe@example.invalid",
    "GIT_COMMITTER_NAME": "probe",
    "GIT_COMMITTER_EMAIL": "probe@example.invalid",
}


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, env=GIT_ENV
    )


def write(root: Path, rel: str, text: str = "本文\n") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def commit(root: Path, message: str = "probe") -> None:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)


def init_repo(root: Path, dirs: tuple[str, ...] = ()) -> None:
    """docs/issues 配下に dirs を持つ fixture リポジトリを作り、1 コミットする。"""
    git(root, "init", "-q", "-b", "main")
    for name in dirs:
        write(root, f"docs/issues/{name}/issue.md")
    write(root, "README.md")
    commit(root, "init")


def run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = issue_id.main(argv)
    return rc, out.getvalue(), err.getvalue()


class NextIdentifier(unittest.TestCase):
    def test_empty_repository_starts_at_one(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root)
            rc, out, err = run(["--next", "--root", str(root)])
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, f"{PREFIX}1\n")

    def test_legacy_directories_share_the_number_space(self):
        # 移行前の `<N>_<title>` を数えないと、新形式だけを見て既存番号を再発行する
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, ("1_最初の課題", "13_十三番目の課題"))
            rc, out, err = run(["--next", "--root", str(root)])
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, f"{PREFIX}14\n")

    def test_new_format_directories_are_counted(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題", f"{PREFIX}9_九番目の課題"))
            rc, out, err = run(["--next", "--root", str(root)])
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, f"{PREFIX}10\n")

    def test_mixed_formats_take_one_maximum(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, ("9_旧形式の課題", f"{PREFIX}7_新形式の課題"))
            rc, out, err = run(["--next", "--root", str(root)])
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, f"{PREFIX}10\n")

    def test_closed_directory_in_unmerged_branch_is_counted(self):
        # ls-tree に -r が無いと docs/issues/closed が tree 1 件として出るだけで配下が
        # 列挙されず、ブランチ内で起票と close を同梱した Issue を取りこぼす。
        # タイトルを日本語にしてあるのは、-z を落とすとパスが C クォートされて解析から
        # 全滅する経路も同時に踏むため (実測)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            git(root, "checkout", "-q", "-b", "topic")
            write(root, f"docs/issues/closed/{PREFIX}9_閉じた課題/issue.md")
            commit(root, "close")
            git(root, "checkout", "-q", "main")
            # 現ツリーに残っていると ref 経路を見なくても緑になり dead pin になる
            leftover = (root / "docs" / "issues" / "closed").exists()
            rc, out, err = run(["--next", "--root", str(root)])
        self.assertFalse(leftover, "checkout 後に closed/ が現ツリーへ残っている")
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, f"{PREFIX}10\n")

    def test_duplicate_number_is_reported_with_source_and_path(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}8_こちらの課題",))
            git(root, "checkout", "-q", "-b", "topic")
            write(root, f"docs/issues/{PREFIX}8_あちらの課題/issue.md")
            commit(root, "duplicate")
            git(root, "checkout", "-q", "main")
            rc, out, err = run(["--next", "--root", str(root)])
        self.assertEqual(rc, 1)
        # 識別子を出さないこと。出すと呼び出し側が重複に気づかず採番を続けられる
        self.assertEqual(out, "")
        self.assertIn("番号 8", err)
        self.assertIn(f"docs/issues/{PREFIX}8_こちらの課題", err)
        self.assertIn(f"docs/issues/{PREFIX}8_あちらの課題", err)
        self.assertIn("refs/heads/topic", err)


class ScanText(unittest.TestCase):
    def flags(self, text: str, label: str = "probe.md") -> list[str]:
        return issue_id.scan_text(text, label)

    def assert_location(self, violation: str, expected: str) -> None:
        self.assertEqual(violation.split(": ", 1)[0], expected)

    def test_bare_reference_is_flagged_with_location_and_replacement(self):
        found = self.flags(f"一行目\n直したのは {SIGIL}8 のバグ\n", "docs/x.md")
        self.assertEqual(len(found), 1)
        self.assert_location(found[0], "docs/x.md:2")
        self.assertIn(f"{PREFIX}8", found[0])

    def test_reference_at_line_start_is_flagged(self):
        found = self.flags(f"{SIGIL}8 を閉じる\n")
        self.assertEqual(len(found), 1)
        self.assert_location(found[0], "probe.md:1")

    def test_issue_prefixed_reference_is_flagged(self):
        found = self.flags(f"Issue {SIGIL}8 を参照\n")
        self.assertEqual(len(found), 1)
        self.assertIn(f"{PREFIX}8", found[0])

    def test_markdown_link_reference_is_flagged(self):
        found = self.flags(f"Closes [Issue {SIGIL}8](../../docs/issues/8_x/issue.md)\n")
        self.assertEqual(len(found), 1)
        self.assertIn(f"{PREFIX}8", found[0])

    def test_pr_without_space_is_flagged(self):
        # 後読みへ英数字を足すと、この形が無検査で通る (免除が広がりすぎる方向)
        found = self.flags(f"PR{SIGIL}9 を参照\n")
        self.assertEqual(len(found), 1)
        self.assertIn(f"{PREFIX}9", found[0])

    def test_two_references_on_one_line_are_both_flagged(self):
        found = self.flags(f"{SIGIL}8 と {SIGIL}9\n")
        self.assertEqual(len(found), 2)
        self.assertIn(f"{PREFIX}8", found[0])
        self.assertIn(f"{PREFIX}9", found[1])

    def test_pr_with_space_is_allowed(self):
        self.assertEqual(self.flags(f"PR {SIGIL}9 でマージした\n"), [])

    def test_cross_repo_reference_is_allowed(self):
        self.assertEqual(self.flags(f"owner/repo{SIGIL}9 を参照\n"), [])

    def test_html_entity_is_allowed(self):
        self.assertEqual(self.flags(f"&{SIGIL}39; はアポストロフィ\n"), [])

    def test_fenced_block_is_exempt(self):
        text = "\n".join([fence(), f"git commit -m 'fix {SIGIL}8'", fence(), ""])
        self.assertEqual(self.flags(text), [])

    def test_inline_code_is_exempt(self):
        tick = fence(1)
        self.assertEqual(self.flags(f"{tick}{SIGIL}8{tick} は素の文字列\n"), [])

    def test_scanning_resumes_after_a_closed_fence(self):
        # 免除がフェンスの外へ漏れないこと。漏れると以降が全部無検査になる
        text = "\n".join([fence(), "code", fence(), f"あとで {SIGIL}8", ""])
        found = self.flags(text)
        self.assertEqual(len(found), 1)
        self.assert_location(found[0], "probe.md:4")

    def test_unclosed_fence_is_reported(self):
        text = "\n".join(["前書き", fence(), f"fix {SIGIL}8", ""])
        found = self.flags(text)
        self.assertEqual(len(found), 1)
        self.assert_location(found[0], "probe.md:2")
        self.assertIn("閉じていない", found[0])

    def test_longer_closing_fence_closes_the_block(self):
        # 3 連で開いて 4 連で閉じる形は閉じ忘れではない (CommonMark)
        text = "\n".join([fence(3), f"fix {SIGIL}8", fence(4), ""])
        self.assertEqual(self.flags(text), [])

    def test_shorter_fence_does_not_close_a_longer_one(self):
        # 4 連で開いた中の 3 連は閉じにならない。閉じ扱いすると以降の行が誤検出される
        text = "\n".join([fence(4), fence(3), f"fix {SIGIL}8", fence(3), fence(4), ""])
        self.assertEqual(self.flags(text), [])


class CheckRepository(unittest.TestCase):
    def test_clean_repository_is_green_and_reports_counts(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            rc, out, err = run(["--check", "--root", str(root)])
        self.assertEqual(rc, 0, err)
        self.assertIn("検査した Issue ディレクトリ: 1 個", out)
        self.assertIn("走査したファイル: 2 個", out)
        self.assertIn("違反なし", out)

    def test_bare_reference_in_tracked_file_is_flagged(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            write(root, "notes.md", f"直したのは {SIGIL}8\n")
            commit(root, "add notes")
            rc, out, err = run(["--check", "--root", str(root)])
        self.assertEqual(rc, 1)
        self.assertIn("notes.md:1:", err)
        self.assertIn("違反 1 件", out)

    def test_untracked_file_is_not_scanned(self):
        # 走査対象は追跡下だけ。作業中のスクラッチで赤くしない
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            write(root, "scratch.md", f"直したのは {SIGIL}8\n")
            rc, out, err = run(["--check", "--root", str(root)])
        self.assertEqual(rc, 0, err)
        self.assertIn("走査したファイル: 2 個", out)

    def test_legacy_directory_name_is_flagged(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, ("13_旧形式の課題",))
            rc, out, err = run(["--check", "--root", str(root)])
        self.assertEqual(rc, 1)
        self.assertIn("docs/issues/13_旧形式の課題:", err)
        self.assertIn("違反 1 件", out)

    def test_templates_directory_is_exempt(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題", "templates"))
            rc, out, err = run(["--check", "--root", str(root)])
        self.assertEqual(rc, 0, err)
        self.assertIn("検査した Issue ディレクトリ: 1 個", out)

    def test_duplicate_number_across_active_and_closed_is_flagged(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(
                root, (f"{PREFIX}8_開いている課題", f"closed/{PREFIX}8_閉じた課題")
            )
            rc, out, err = run(["--check", "--root", str(root)])
        self.assertEqual(rc, 1)
        self.assertIn("番号 8 が重複している", err)
        self.assertIn(f"docs/issues/{PREFIX}8_開いている課題", err)
        self.assertIn(f"docs/issues/closed/{PREFIX}8_閉じた課題", err)

    def test_stylesheets_are_excluded(self):
        # 色指定は数字記法と同じ形になるので走査から外す
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            write(root, "theme.css", "a { color: " + SIGIL + "336699; }\n")
            commit(root, "add css")
            rc, out, err = run(["--check", "--root", str(root)])
        self.assertEqual(rc, 0, err)
        self.assertIn("拡張子で除外 1", out)

    def test_undecodable_file_is_counted_as_skipped(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            (root / "blob.dat").write_bytes(b"\xff\xfe\x00\x80")
            commit(root, "add blob")
            rc, out, err = run(["--check", "--root", str(root)])
        self.assertEqual(rc, 0, err)
        self.assertIn("読めずに飛ばした 1", out)
        self.assertIn("走査したファイル: 2 個", out)

    def test_repository_without_tracked_files_is_exit_2(self):
        # 0 件は「違反なし」ではなく「何も見ていない」
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            git(root, "init", "-q", "-b", "main")
            rc, out, err = run(["--check", "--root", str(root)])
        self.assertEqual(rc, 2)
        self.assertIn("走査対象ゼロは合格ではない", err)

    def test_non_git_root_is_exit_2(self):
        # git を走らせられないのは違反 (1) ではなく検査不能 (2)
        with TemporaryDirectory() as tmp:
            rc, out, err = run(["--check", "--root", tmp])
        self.assertEqual(rc, 2)
        self.assertIn("[x]", err)


class CheckText(unittest.TestCase):
    def test_file_violation_is_labelled_with_its_path(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "message.txt"
            path.write_text(f"fix: 直した ({SIGIL}8)\n", encoding="utf-8")
            rc, out, err = run(["--check-text", str(path)])
        self.assertEqual(rc, 1)
        self.assertIn(f"{path}:1:", err)
        self.assertIn("違反 1 件", out)

    def test_clean_text_is_green_and_reports_line_count(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "message.txt"
            path.write_text(f"fix: 直した ({PREFIX}8)\n二行目\n", encoding="utf-8")
            rc, out, err = run(["--check-text", str(path)])
        self.assertEqual(rc, 0, err)
        self.assertIn("走査した行: 2 行 / 違反 0 件", out)

    def test_stdin_is_scanned_and_labelled(self):
        original = sys.stdin
        sys.stdin = io.StringIO(f"直すのは {SIGIL}8\n")
        try:
            rc, out, err = run(["--check-text", "-"])
        finally:
            sys.stdin = original
        self.assertEqual(rc, 1)
        self.assertIn("stdin:1:", err)

    def test_missing_file_is_exit_2(self):
        with TemporaryDirectory() as tmp:
            rc, out, err = run(["--check-text", str(Path(tmp) / "absent.txt")])
        self.assertEqual(rc, 2)
        self.assertIn("読めない", err)


class ArgumentSurface(unittest.TestCase):
    def test_no_mode_is_rejected(self):
        # 入口を指定しない呼び出しが「何もせず緑」にならないこと
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
            issue_id.main([])
        self.assertEqual(ctx.exception.code, 2)

    def test_abbreviated_flag_is_rejected(self):
        # allow_abbrev の既定 (True) は --che を別の入口として受理する。
        # typo が静かに別モードへ落ちないよう完全形だけに絞る
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
            issue_id.main(["--che"])
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
