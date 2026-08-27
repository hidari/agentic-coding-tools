"""issue-id.py の仕様 pin。

git を歩く経路 (--next の ref 横断と --check の追跡下ファイル列挙) は、実ツリーではなく
tempfile + git init の fixture リポジトリで検証する。実ツリーに依存させると、実ツリーが
たまたま条件を満たしているだけで緑になり dead pin になる。

検査対象を壊す変異だけでなく、検査機構そのものを壊す変異 (免除定数を空にする・フェンス
閉じ忘れの報告を落とす・ls-tree の -r を外す) と、免除が広がりすぎる方向の変異
(後読みへ英数字を足す) が赤になることを狙って pin を置く。

増分モード (--check-diff) は「見る範囲を狭める」機構なので、狙う方向は緩めすぎる側に寄る。
本来止めるべき新規違反が素通りする経路を pin の重点にし、対照として「既存違反は報告しない」
も同時に置く。ratchet 側だけを見ると、何も報告しない実装でも緑になる。
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


def git_out(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        env=GIT_ENV,
    )
    return proc.stdout.strip()


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

    def test_remote_tracking_ref_is_counted(self):
        # refs/remotes を列挙から落とすと push 済みの番号を再発行する。ローカルブランチを
        # 消して、番号 9 が remote-tracking ref からしか辿れない形にする
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            git(root, "checkout", "-q", "-b", "topic")
            write(root, f"docs/issues/{PREFIX}9_遠くの課題/issue.md")
            commit(root, "remote work")
            sha = git_out(root, "rev-parse", "HEAD")
            git(root, "checkout", "-q", "main")
            git(root, "update-ref", "refs/remotes/origin/topic", sha)
            git(root, "branch", "-q", "-D", "topic")
            refs = git_out(
                root, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes"
            ).split()
            rc, out, err = run(["--next", "--root", str(root)])
        # refs/heads 側に 9 が残っていると refs/remotes を見なくても緑になり dead pin になる
        self.assertEqual(refs, ["refs/heads/main", "refs/remotes/origin/topic"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, f"{PREFIX}10\n")

    def test_prefix_migration_rename_is_not_a_duplicate(self):
        # 接頭辞の有無を同一視しないと、旧形式が残る main と新形式へ揃えたブランチが
        # 「番号 8 の重複」に見えて --next が exit 1 で止まり、新規起票が一切できなくなる。
        # Task 2 の rename がちょうどこの形を作る
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, ("8_移行する課題",))
            git(root, "checkout", "-q", "-b", "topic")
            git(
                root,
                "mv",
                "docs/issues/8_移行する課題",
                f"docs/issues/{PREFIX}8_移行する課題",
            )
            git(root, "commit", "-q", "-m", "rename")
            git(root, "checkout", "-q", "main")
            rc, out, err = run(["--next", "--root", str(root)])
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, f"{PREFIX}9\n")

    def test_non_git_root_is_exit_2(self):
        # git を走らせられないのは違反 (1) ではなく検査不能 (2)。ここを緑にすると
        # 「ref が 1 つも無い」と読めてしまい、採番が ISSUE-1 へ巻き戻って既存番号を
        # 再発行する (実測: 検査を落とすと ISSUE-1 を rc 0 で返した)
        with TemporaryDirectory() as tmp:
            rc, out, err = run(["--next", "--root", tmp])
        self.assertEqual(rc, 2)
        # 識別子を出さないこと。出すと検査不能が採番成功に化ける
        self.assertEqual(out, "")
        self.assertIn("git for-each-ref", err)

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
        # scan_text は増分モードのフィルタ用に (行番号, 種別, メッセージ) を返す。
        # 位置と種別を直接 pin するのは Located クラスの担当で、ここは文言だけを見る
        return [v.message for v in issue_id.scan_text(text, label)]

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

    def test_empty_allowed_prefix_does_not_exempt_everything(self):
        # 免除は truthy を要求する (fail closed)。ガードを外すと endswith("") が常に真に
        # なり、定数を空にする変更が「全件免除」へ静かに広がる。定数を空にしたときに
        # 免除が消えることを直接見ないと、ガードの有無が挙動に現れず pin にならない
        original = issue_id.GITHUB_REF_ALLOWED_PREFIX
        issue_id.GITHUB_REF_ALLOWED_PREFIX = ""
        try:
            found = self.flags(f"PR {SIGIL}9 でマージした\n")
        finally:
            issue_id.GITHUB_REF_ALLOWED_PREFIX = original
        self.assertEqual(len(found), 1)
        self.assertIn(f"{PREFIX}9", found[0])

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

    def test_form_feed_is_not_a_line_boundary(self):
        # git の diff が行区切りにするのは \n だけ。splitlines に合わせると増分モードの
        # 行番号が git とずれ、追加行に載った違反が絞り込みから落ちる
        found = self.flags(f"1 行目\n2 行目\x0c違反 {SIGIL}8\n")
        self.assertEqual(len(found), 1)
        self.assert_location(found[0], "probe.md:2")

    def test_lone_carriage_return_is_not_a_line_boundary(self):
        found = self.flags(f"1 行目\n2 行目\r違反 {SIGIL}8\n")
        self.assertEqual(len(found), 1)
        self.assert_location(found[0], "probe.md:2")

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


class LocatedViolation(unittest.TestCase):
    """走査結果が行番号と種別を構造として持つこと。

    増分モードは行番号で違反を絞り、フェンス閉じ忘れだけを絞りから外す。メッセージ文字列を
    パースして両者を得る実装だと、文言を変えた瞬間にフィルタが黙って全通しへ倒れる。
    """

    def test_bare_reference_carries_its_line_and_kind(self):
        found = issue_id.scan_text(f"一行目\n二行目 {SIGIL}8\n", "probe.md")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].lineno, 2)
        self.assertEqual(found[0].kind, issue_id.KIND_BARE_REF)

    def test_unclosed_fence_has_a_distinct_kind(self):
        # 種別が同じだと増分モードが閉じ忘れを行番号で落とせる (罠 1 の入口)
        found = issue_id.scan_text("\n".join(["前書き", fence(), f"fix {SIGIL}8", ""]), "p.md")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].kind, issue_id.KIND_UNCLOSED_FENCE)
        self.assertNotEqual(issue_id.KIND_UNCLOSED_FENCE, issue_id.KIND_BARE_REF)

    def test_message_and_lineno_agree(self):
        # 同じ行番号を 2 つの形で持つので、食い違わないことを見る
        found = issue_id.scan_text(f"a\nb\nc {SIGIL}8\n", "probe.md")
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].message.startswith(f"probe.md:{found[0].lineno}: "))


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

    def test_untracked_issue_directory_is_flagged(self):
        # --check は filesystem を見る。増分モードと同じ index ベースへ寄せると、
        # 作りかけの Issue ディレクトリが検査から落ちる。全走査の backstop としては
        # 追跡前でも見える方が正しく、増分モード側とは意図的に非対称にしてある
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            write(root, "docs/issues/13_未追跡の課題/issue.md")
            rc, out, err = run(["--check", "--root", str(root)])
        self.assertEqual(rc, 1)
        self.assertIn("docs/issues/13_未追跡の課題:", err)

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
        # 追跡下 0 件の経路も 2 を返すので、失敗した git コマンドが名指しされていることまで
        # 見る。「[x] が出ている」だけでは 2 つの経路を区別できず dead pin になる (実測)
        self.assertIn("git ls-files", err)
        self.assertNotIn("走査対象ゼロ", err)


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


class CheckDiff(unittest.TestCase):
    """増分モード (--check-diff) の仕様 pin。

    11 件は spec のテスト方針表に対応する。重点は緩めすぎる方向で、
    「範囲を絞ったせいで新規違反が素通りする」経路を潰す。
    """

    def stage(self, root: Path, rel: str, text: str = "本文\n") -> None:
        write(root, rel, text)
        git(root, "add", "-A")

    # --- 1: 新規 Issue ディレクトリの名前違反 ---------------------------------

    def test_new_issue_directory_with_legacy_name_is_flagged(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            self.stage(root, "docs/issues/13_旧形式の課題/issue.md")
            rc, out, err = run(["--check-diff", "--root", str(root)])
        self.assertEqual(rc, 1)
        self.assertIn("docs/issues/13_旧形式の課題:", err)

    def test_existing_legacy_directory_is_not_flagged(self):
        # ratchet の本体。取り付けた瞬間に既存 938 件が赤くなるのを避ける理由がここ
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, ("13_旧形式の課題",))
            self.stage(root, "notes.md", "無害な行\n")
            rc, out, err = run(["--check-diff", "--root", str(root)])
        self.assertEqual(rc, 0, err)

    def test_editing_an_existing_legacy_issue_does_not_flag_its_name(self):
        # 名前検査の対象を M まで広げると、旧記法の Issue の本文を直すたびに赤くなる。
        # 移行期に最も多い操作がこれなので、ここで ratchet が崩れる
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, ("13_旧記法の課題",))
            self.stage(root, "docs/issues/13_旧記法の課題/issue.md", "本文を書き換えた\n")
            rc, out, err = run(["--check-diff", "--root", str(root)])
        self.assertEqual(rc, 0, err)
        self.assertIn("Issue ディレクトリ: 0 個", out)
        self.assertIn("走査したファイル: 1 個", out)

    def test_adding_a_file_to_a_legacy_directory_does_not_flag_the_name(self):
        # 名前検査の起点は issue.md の追加だけ。配下の任意ファイルで起点にすると、
        # 旧記法ディレクトリを触るたび赤くなり ratchet が成立しない
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, ("13_旧形式の課題",))
            self.stage(root, "docs/issues/13_旧形式の課題/13-spec.md")
            rc, out, err = run(["--check-diff", "--root", str(root)])
        self.assertEqual(rc, 0, err)
        self.assertIn("Issue ディレクトリ: 0 個", out)

    # --- 2: 追加行の数字記法 ---------------------------------------------------

    def test_added_line_with_a_bare_reference_is_flagged(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            self.stage(root, "notes.md", f"直したのは {SIGIL}8\n")
            rc, out, err = run(["--check-diff", "--root", str(root)])
        self.assertEqual(rc, 1)
        self.assertIn("notes.md:1:", err)
        self.assertIn("違反 1 件", out)

    # --- 3: --base 指定時の range ----------------------------------------------

    def test_base_ref_sees_only_the_branch_side(self):
        # three-dot にしないと base 側だけにあるコミットが差分へ混ざる (実測)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            git(root, "checkout", "-q", "-b", "feat")
            write(root, "feature.md", f"新しい違反 {SIGIL}8\n")
            commit(root, "feat")
            git(root, "checkout", "-q", "main")
            write(root, "on-main.md", "main 側の無害な変更\n")
            commit(root, "main only")
            git(root, "checkout", "-q", "feat")
            rc, out, err = run(["--check-diff", "--base", "main", "--root", str(root)])
        self.assertEqual(rc, 1)
        self.assertIn("feature.md:1:", err)
        self.assertIn("main...HEAD", out)
        # base 側のファイルは差分に入らない
        self.assertIn("走査したファイル: 1 個", out)

    def test_base_ref_with_no_difference_is_green(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            rc, out, err = run(["--check-diff", "--base", "main", "--root", str(root)])
        self.assertEqual(rc, 0, err)
        self.assertIn("追加行: 0 行", out)

    # --- 4: フェンス内の追加行は免除 -------------------------------------------

    def test_added_line_inside_a_fence_is_exempt(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            body = "\n".join([fence(), f"git commit -m 'fix {SIGIL}8'", fence(), ""])
            self.stage(root, "notes.md", body)
            rc, out, err = run(["--check-diff", "--root", str(root)])
        self.assertEqual(rc, 0, err)

    # --- 5: 既存行は報告しない (ratchet) ---------------------------------------

    def test_pre_existing_violation_on_an_untouched_line_is_not_reported(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            write(root, "notes.md", f"古い違反 {SIGIL}8\n")
            commit(root, "legacy violation")
            self.stage(root, "notes.md", f"古い違反 {SIGIL}8\n無害な追記\n")
            rc, out, err = run(["--check-diff", "--root", str(root)])
        self.assertEqual(rc, 0, err)
        self.assertIn("追加行: 1 行", out)

    def test_touching_a_line_that_holds_a_violation_reports_it(self):
        # 5 の裏。既存違反でも、その行を書き換えたら追加行になるので報告される
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            write(root, "notes.md", f"古い違反 {SIGIL}8\n")
            commit(root, "legacy violation")
            self.stage(root, "notes.md", f"直した違反 {SIGIL}8\n")
            rc, out, err = run(["--check-diff", "--root", str(root)])
        self.assertEqual(rc, 1)
        self.assertIn("notes.md:1:", err)

    # --- 6: 番号の重複は全体で見る ---------------------------------------------

    def test_duplicate_number_is_seen_across_the_whole_tree(self):
        # 増分でも全体を見る規則。緩めると同じ番号の Issue が静かに 2 つできる
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}8_開いている課題",))
            self.stage(root, f"docs/issues/closed/{PREFIX}8_閉じた課題/issue.md")
            rc, out, err = run(["--check-diff", "--root", str(root)])
        self.assertEqual(rc, 1)
        self.assertIn("番号 8 が重複している", err)

    def test_duplicate_number_between_two_untouched_directories_is_still_seen(self):
        # 6 の要。差分に出ないディレクトリどうしの重複も見る。増分で絞ると落ちる経路
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(
                root, (f"{PREFIX}8_開いている課題", f"closed/{PREFIX}8_閉じた課題")
            )
            self.stage(root, "notes.md", "無害な行\n")
            rc, out, err = run(["--check-diff", "--root", str(root)])
        self.assertEqual(rc, 1)
        self.assertIn("番号 8 が重複している", err)

    # --- 7: base の閉じ忘れフェンス (罠 1) --------------------------------------

    def test_unclosed_fence_in_base_does_not_turn_the_increment_green(self):
        # base のフェンスが開きっぱなしだと、追加行の違反は「フェンス内」として消え、
        # 閉じ忘れ違反は旧行番号に付くので行番号フィルタでも消える。完全な緑になる
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            write(root, "doc.md", "\n".join(["前書き", fence(), "開いたまま", ""]))
            commit(root, "unclosed fence")
            self.stage(
                root,
                "doc.md",
                "\n".join(["前書き", fence(), "開いたまま", f"あとで {SIGIL}8", ""]),
            )
            rc, out, err = run(["--check-diff", "--root", str(root)])
        self.assertEqual(rc, 1)
        self.assertIn("閉じていない", err)

    def test_unclosed_fence_opened_by_the_increment_is_reported(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            self.stage(root, "doc.md", "\n".join(["前書き", fence(), "開いたまま", ""]))
            rc, out, err = run(["--check-diff", "--root", str(root)])
        self.assertEqual(rc, 1)
        self.assertIn("閉じていない", err)

    # --- 8: partial staging (罠 4) ---------------------------------------------

    def test_unstaged_violation_is_not_reported(self):
        # index を走査しないと、worktree の未 stage 違反で偽陽性になる
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            write(root, "doc.md", "1 行目\n2 行目\n")
            commit(root, "base")
            write(root, "doc.md", "1 行目\n2 行目\n3 行目\n")
            git(root, "add", "doc.md")
            # stage したあとで、上の行へ未 stage の違反を足す
            write(root, "doc.md", f"違反 {SIGIL}8\n1 行目\n2 行目\n3 行目\n")
            rc, out, err = run(["--check-diff", "--root", str(root)])
        self.assertEqual(rc, 0, err)

    def test_staged_violation_is_reported_even_if_the_worktree_hides_it(self):
        # 8 の裏。worktree を走査していると、コミットされる違反を見落とす
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            write(root, "doc.md", "1 行目\n")
            commit(root, "base")
            write(root, "doc.md", f"1 行目\n違反 {SIGIL}8\n")
            git(root, "add", "doc.md")
            write(root, "doc.md", "1 行目\n直したので worktree には無い\n")
            rc, out, err = run(["--check-diff", "--root", str(root)])
        self.assertEqual(rc, 1)
        self.assertIn("doc.md:2:", err)

    # --- 9: 非 ASCII のパス (罠 2) ---------------------------------------------

    def test_violation_under_a_non_ascii_directory_is_found(self):
        # diff のヘッダは非 ASCII パスを C クォートする。ヘッダでパスを対応付ける実装だと
        # 日本語タイトルの Issue が全部落ちる。主流ケースなので端ケースではない
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            rel = f"docs/issues/{PREFIX}50_日本語のタイトル/issue.md"
            self.stage(root, rel, f"参照は {SIGIL}8\n")
            rc, out, err = run(["--check-diff", "--root", str(root)])
        self.assertEqual(rc, 1)
        self.assertIn(f"{rel}:1:", err)
        self.assertIn("走査したファイル: 1 個", out)

    def test_non_ascii_directory_name_violation_is_found(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            self.stage(root, "docs/issues/50_日本語のタイトル/issue.md")
            rc, out, err = run(["--check-diff", "--root", str(root)])
        self.assertEqual(rc, 1)
        self.assertIn("docs/issues/50_日本語のタイトル:", err)

    # --- 10: rename (罠 3) ------------------------------------------------------

    def test_rename_into_a_bad_name_is_flagged_regardless_of_rename_detection(self):
        # --diff-filter=A は rename 検出下で空になる。マシンの git 設定で検査結果が
        # 変わるのはそれ自体が欠陥なので、両設定で同じ結果になることを pin する
        for renames in ("true", "false"):
            with self.subTest(renames=renames), TemporaryDirectory() as tmp:
                root = Path(tmp)
                init_repo(root, (f"{PREFIX}43_元の課題",))
                git(root, "config", "diff.renames", renames)
                git(
                    root,
                    "mv",
                    f"docs/issues/{PREFIX}43_元の課題",
                    "docs/issues/43_接頭辞を落とした",
                )
                git(root, "add", "-A")
                rc, out, err = run(["--check-diff", "--root", str(root)])
                self.assertEqual(rc, 1)
                self.assertIn("docs/issues/43_接頭辞を落とした:", err)

    def test_rename_into_a_good_name_is_green_regardless_of_rename_detection(self):
        for renames in ("true", "false"):
            with self.subTest(renames=renames), TemporaryDirectory() as tmp:
                root = Path(tmp)
                init_repo(root, ("43_旧形式",))
                git(root, "config", "diff.renames", renames)
                git(root, "mv", "docs/issues/43_旧形式", f"docs/issues/{PREFIX}43_旧形式")
                git(root, "add", "-A")
                rc, out, err = run(["--check-diff", "--root", str(root)])
                self.assertEqual(rc, 0, err)
                self.assertIn("Issue ディレクトリ: 1 個", out)

    # --- rename で位置だけが変わる操作 (Issue を閉じる) --------------------------

    def test_closing_a_legacy_issue_does_not_flag_its_name(self):
        # docs/issues 直下から closed/ への移動は名前を変えない。ここを名前検査の対象に
        # すると、旧記法の Issue を閉じるという日常操作のたびに赤くなり ratchet が壊れる
        for renames in ("true", "false"):
            with self.subTest(renames=renames), TemporaryDirectory() as tmp:
                root = Path(tmp)
                init_repo(root, ("13_旧記法の課題",))
                git(root, "config", "diff.renames", renames)
                (root / "docs" / "issues" / "closed").mkdir(parents=True, exist_ok=True)
                git(
                    root,
                    "mv",
                    "docs/issues/13_旧記法の課題",
                    "docs/issues/closed/13_旧記法の課題",
                )
                git(root, "add", "-A")
                rc, out, err = run(["--check-diff", "--root", str(root)])
                self.assertEqual(rc, 0, err)
                self.assertIn("Issue ディレクトリ: 0 個", out)

    def test_closing_an_issue_does_not_resurface_its_existing_violations(self):
        # per-file の pathspec は rename の対応付けを壊し、git が new file mode として
        # 全行を追加行で返す。本文の既存違反が丸ごと再浮上する (実測)
        for renames in ("true", "false"):
            with self.subTest(renames=renames), TemporaryDirectory() as tmp:
                root = Path(tmp)
                init_repo(root, ())
                git(root, "config", "diff.renames", renames)
                write(
                    root,
                    f"docs/issues/{PREFIX}77_古い課題/issue.md",
                    f"前書き\n古い参照 {SIGIL}77\n",
                )
                commit(root, "legacy issue")
                (root / "docs" / "issues" / "closed").mkdir(parents=True, exist_ok=True)
                git(
                    root,
                    "mv",
                    f"docs/issues/{PREFIX}77_古い課題",
                    f"docs/issues/closed/{PREFIX}77_古い課題",
                )
                git(root, "add", "-A")
                rc, out, err = run(["--check-diff", "--root", str(root)])
                self.assertEqual(rc, 0, err)
                self.assertIn("追加行: 0 行", out)

    def test_content_changed_during_a_move_reports_only_the_new_line(self):
        # 移動と編集が同じコミットに入る形。変えた行だけが追加行になること
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, ())
            write(
                root,
                f"docs/issues/{PREFIX}55_課題/issue.md",
                f"前書き\n古い参照 {SIGIL}55\n",
            )
            commit(root, "legacy issue")
            (root / "docs" / "issues" / "closed").mkdir(parents=True, exist_ok=True)
            git(
                root,
                "mv",
                f"docs/issues/{PREFIX}55_課題",
                f"docs/issues/closed/{PREFIX}55_課題",
            )
            write(
                root,
                f"docs/issues/closed/{PREFIX}55_課題/issue.md",
                f"前書き\n古い参照 {SIGIL}55\n新しい参照 {SIGIL}99\n",
            )
            git(root, "add", "-A")
            rc, out, err = run(["--check-diff", "--root", str(root)])
        self.assertEqual(rc, 1)
        self.assertIn("追加行: 1 行", out)
        # 3 行目 (新しく足した行) だけが報告され、2 行目の既存違反は報告されない。
        # 識別子で見るとディレクトリ名側の一致を拾うので行番号で見る
        self.assertIn("/issue.md:3:", err)
        self.assertNotIn("/issue.md:2:", err)
        self.assertIn("違反 1 件", out)

    def test_renaming_a_bad_name_into_another_bad_name_is_still_flagged(self):
        # 位置だけの移動を外す条件が広すぎないこと。名前が変われば検査対象に戻る
        for renames in ("true", "false"):
            with self.subTest(renames=renames), TemporaryDirectory() as tmp:
                root = Path(tmp)
                init_repo(root, ("13_もとの名前",))
                git(root, "config", "diff.renames", renames)
                git(root, "mv", "docs/issues/13_もとの名前", "docs/issues/13_べつの名前")
                git(root, "add", "-A")
                rc, out, err = run(["--check-diff", "--root", str(root)])
                self.assertEqual(rc, 1)
                self.assertIn("docs/issues/13_べつの名前:", err)

    # --- 11: submodule ----------------------------------------------------------

    def test_submodule_entry_does_not_raise(self):
        # gitlink は diff に hunk 付きで出るが blob を持たないので内容取得が失敗する。
        # 例外にすると差分に submodule が含まれるだけで検査全体が rc 2 になる
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            sha = git_out(root, "rev-parse", "HEAD")
            git(root, "update-index", "--add", "--cacheinfo", f"160000,{sha},child")
            rc, out, err = run(["--check-diff", "--root", str(root)])
        self.assertEqual(rc, 0, err)
        self.assertIn("submodule 1", out)

    def test_submodule_alongside_a_real_violation_still_reports_it(self):
        # submodule の skip が、同じ差分の他のファイルまで落とさないこと
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            sha = git_out(root, "rev-parse", "HEAD")
            git(root, "update-index", "--add", "--cacheinfo", f"160000,{sha},child")
            self.stage(root, "notes.md", f"違反 {SIGIL}8\n")
            rc, out, err = run(["--check-diff", "--root", str(root)])
        self.assertEqual(rc, 1)
        self.assertIn("notes.md:1:", err)

    # --- pathspec と走査対象の一致 ----------------------------------------------

    def test_glob_characters_in_a_path_do_not_pull_in_another_file(self):
        # pathspec を素で渡すと `[` が文字クラスとして効き、note[1].md の行番号を引くつもりで
        # note1.md の hunk まで混ざる (実測)。混ざった行番号は既存違反を「追加行」に見せる
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            write(root, "note1.md", "古い\n")
            write(root, "note[1].md", f"既存の違反 {SIGIL}8\n")
            commit(root, "base")
            # note1.md の 1 行目を書き換える。glob が当たると added に 1 が混ざり、
            # note[1].md の 1 行目にある既存違反が報告されてしまう
            write(root, "note1.md", "新しい\n")
            write(root, "note[1].md", f"既存の違反 {SIGIL}8\n無害な追記\n")
            git(root, "add", "-A")
            rc, out, err = run(["--check-diff", "--root", str(root)])
        self.assertEqual(rc, 0, err)
        self.assertIn("走査したファイル: 2 個", out)

    def test_gitattributes_suppressing_diff_does_not_exempt_the_file(self):
        # `-diff` 属性が付くと git は binary 扱いで hunk を出さず、追加行集合が空になる。
        # そのファイルの違反が全部免除され、しかも違反 0 件の緑で返る (実測)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            write(root, "doc.md", "1 行目\n")
            write(root, ".gitattributes", "*.md -diff\n")
            commit(root, "base")
            self.stage(root, "doc.md", f"1 行目\n違反 {SIGIL}8\n")
            rc, out, err = run(["--check-diff", "--root", str(root)])
        self.assertEqual(rc, 1)
        self.assertIn("doc.md:2:", err)

    def test_line_boundaries_follow_git_not_python(self):
        # Python の splitlines は \f / 単独の \r / \v も行境界にするが git は \n だけ。
        # ずれると追加行に載った違反が絞り込みから落ち、違反 0 件の緑で返る (実測)
        for name, sep in (("form feed", "\x0c"), ("lone CR", "\r"), ("vertical tab", "\x0b")):
            with self.subTest(separator=name), TemporaryDirectory() as tmp:
                root = Path(tmp)
                init_repo(root, (f"{PREFIX}1_最初の課題",))
                write(root, "doc.md", "1 行目\n")
                commit(root, "base")
                self.stage(root, "doc.md", f"1 行目\n2 行目{sep}違反 {SIGIL}8\n")
                rc, out, err = run(["--check-diff", "--root", str(root)])
                self.assertEqual(rc, 1)
                self.assertIn("doc.md:2:", err)
                self.assertIn("追加行: 1 行", out)

    def test_untracked_issue_directory_does_not_trigger_a_duplicate(self):
        # 番号の重複も走査対象を差分の後側へ揃える。worktree を見ると、まだ index に無い
        # 作りかけの Issue ディレクトリが重複として報告される
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}8_開いている課題",))
            write(root, f"docs/issues/{PREFIX}8_作りかけ/issue.md")
            rc, out, err = run(["--check-diff", "--root", str(root)])
        self.assertEqual(rc, 0, err)

    def test_last_line_of_a_hunk_is_inside_the_added_set(self):
        # 追加行の範囲を 1 行短く取ると、hunk の末尾に置いた違反が静かに落ちる
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            write(root, "doc.md", "1 行目\n")
            commit(root, "base")
            self.stage(root, "doc.md", f"1 行目\n無害な追記\n末尾に違反 {SIGIL}8\n")
            rc, out, err = run(["--check-diff", "--root", str(root)])
        self.assertEqual(rc, 1)
        self.assertIn("doc.md:3:", err)
        self.assertIn("追加行: 2 行", out)

    def test_hunk_marker_inside_an_added_line_is_not_read_as_a_header(self):
        # HUNK_HEADER の行頭固定を外すと、追加行の本文にある `@@ -1 +9 @@` が hunk ヘッダに
        # 見え、実在しない行番号が追加行集合へ入る
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            write(root, "doc.md", f"1 行目\n2 行目の既存違反 {SIGIL}8\n")
            commit(root, "base")
            self.stage(
                root,
                "doc.md",
                f"1 行目\n2 行目の既存違反 {SIGIL}8\n差分の話 @@ -1 +2 @@ を引用する\n",
            )
            rc, out, err = run(["--check-diff", "--root", str(root)])
        self.assertEqual(rc, 0, err)
        self.assertIn("追加行: 1 行", out)

    # --- 走査の要約と終了コード -------------------------------------------------

    def test_empty_diff_is_green_and_still_reports_the_range(self):
        # 配線ミス (base が常に HEAD と一致する等) と正常な空コミットを区別するために、
        # 何を何件見たかを必ず出す。0 件を黙って緑にすると両者が同じ見た目になる
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            rc, out, err = run(["--check-diff", "--root", str(root)])
        self.assertEqual(rc, 0, err)
        self.assertIn("差分 index", out)
        self.assertIn("追加行: 0 行", out)
        self.assertIn("走査したファイル: 0 個", out)
        self.assertIn("違反なし", out)

    def test_unknown_base_ref_is_exit_2(self):
        # 検査不能 (2) を違反 (1) と混ぜない。base が解決できない CI を規約違反に見せない
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            rc, out, err = run(
                ["--check-diff", "--base", "no-such-ref", "--root", str(root)]
            )
        self.assertEqual(rc, 2)
        self.assertIn("git diff", err)

    def test_non_git_root_is_exit_2(self):
        with TemporaryDirectory() as tmp:
            rc, out, err = run(["--check-diff", "--root", tmp])
        self.assertEqual(rc, 2)

    def test_deleted_file_is_not_scanned(self):
        # 削除されたファイルは index から消えるので内容が取れない。入口で落とさないと
        # 「読めずに飛ばした」に化けて、飛ばした理由が要約から読めなくなる
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            write(root, "doomed.md", "消える\n")
            commit(root, "add doomed")
            git(root, "rm", "-q", "doomed.md")
            rc, out, err = run(["--check-diff", "--root", str(root)])
        self.assertEqual(rc, 0, err)
        self.assertIn("走査したファイル: 0 個", out)
        self.assertIn("読めずに飛ばした 0", out)

    def test_stylesheet_is_excluded(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            self.stage(root, "theme.css", "a { color: " + SIGIL + "336699; }\n")
            rc, out, err = run(["--check-diff", "--root", str(root)])
        self.assertEqual(rc, 0, err)
        self.assertIn("拡張子で除外 1", out)

    def test_unreadable_blob_is_exit_2(self):
        # 壊れたリポジトリを「読めずに飛ばした」へ数えると違反 0 件の緑になる。
        # 検査不能は 1 ではなく 2 で返す
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            real = git_out(root, "rev-parse", "HEAD:README.md")
            # 実在しない oid を同じ長さで作る。長さを literal で書くとハッシュ方式に依存する
            fake = ("0" if real[0] != "0" else "1") + real[1:]
            git(root, "update-index", "--add", "--cacheinfo", f"100644,{fake},broken.md")
            rc, out, err = run(["--check-diff", "--root", str(root)])
        self.assertEqual(rc, 2)
        self.assertIn("cat-file", err)

    def test_undecodable_file_is_counted_as_skipped(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            (root / "blob.dat").write_bytes(b"\xff\xfe\x00\x80")
            git(root, "add", "-A")
            rc, out, err = run(["--check-diff", "--root", str(root)])
        self.assertEqual(rc, 0, err)
        self.assertIn("読めずに飛ばした 1", out)


class ArgumentSurface(unittest.TestCase):
    def test_no_mode_is_rejected(self):
        # 入口を指定しない呼び出しが「何もせず緑」にならないこと
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
            issue_id.main([])
        self.assertEqual(ctx.exception.code, 2)

    def test_abbreviated_flag_is_rejected(self):
        # プローブは曖昧でない前方一致にする。--che は --check 系の複数へ前方一致する
        # ので allow_abbrev の既定 (True) でも ambiguous option で
        # SystemExit(2) になり、機構と無関係に緑になる (実測)。--nex は --next にしか
        # 前方一致しないので既定では受理され、typo が静かに採番モードへ落ちる。
        # --root を完全形で添えてあるのは、受理されてしまう変異下でも実リポジトリを
        # 触らせないため
        with TemporaryDirectory() as tmp:
            err = io.StringIO()
            with redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
                issue_id.main(["--nex", "--root", tmp])
        self.assertEqual(ctx.exception.code, 2)

    def test_base_without_check_diff_is_rejected(self):
        # 静かに無視すると「base を指定したのに staged を見ていた」形になり、
        # CI が意図と違う範囲を緑にする
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root, (f"{PREFIX}1_最初の課題",))
            err = io.StringIO()
            with redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
                issue_id.main(["--check", "--base", "main", "--root", str(root)])
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("--base", err.getvalue())

    def test_check_and_check_diff_are_mutually_exclusive(self):
        with TemporaryDirectory() as tmp:
            err = io.StringIO()
            with redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
                issue_id.main(["--check", "--check-diff", "--root", tmp])
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
