#!/usr/bin/env python3
"""check-session-url.py の検出と、その取り付けを検証する。

fixture は 1 行の切れ端ではなく、実際のコミットメッセージと同じ構造上の位置に置く
(subject、空行、本文、末尾の trailer。squash の本文では箇条の間に trailer が並ぶ)。
切れ端だけだと、行の切り分けや scissors 行の扱いを壊す変異が別経路で緑のまま残る。

ExitCodes は checker を subprocess で起動して終了コードを pin する。関数を直接呼ぶテスト
だけだと、検出ありで `return 0` にする 1 行の変異が全緑のまま生き残る
(scripts/test_check_related_refs.py の docstring が同じ形を実測で踏んでいる)。

Attachment はリポジトリの設定を読むので、隔離コピーで検出の変異を注入するときは
Detection と ExitCodes だけを指名して回す。
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

CHECKER = "scripts/check-session-url.py"
PRE_COMMIT_CONFIG = ROOT / ".pre-commit-config.yaml"

# セッションの ID は架空の値にする。実在の ID を fixture に書くと、それ自体が公開面になる
SESSION_ID = "session_0FICTIONAL0000000000"
SESSION_URL = "https://claude.ai/code/" + SESSION_ID


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# 取り付けの pin が探す CHECKER と同じパスから読む。CHECKER が実在しないパスへ drift すると、
# ここの読み込みが落ちてファイルごと赤になる
find_session_urls = _load("check_session_url", ROOT / CHECKER).find_session_urls


class Detection(unittest.TestCase):
    def test_trailer_at_the_end_of_a_commit_message(self):
        message = (
            "fix(scripts): 検査の取りこぼしを直す\n"
            "\n"
            "本文の段落。\n"
            "\n"
            f"Claude-Session: {SESSION_URL}\n"
        )
        self.assertEqual(find_session_urls(message), [(5, "trailer")])

    def test_trailers_in_the_middle_of_a_squash_body(self):
        # squash の本文ではブランチの各コミットのメッセージが箇条で並び、trailer は本文の
        # 途中に出る。git の trailer の解釈はこれを trailer と見ない
        message = (
            "feat: 何かを足す (PR #1)\n"
            "\n"
            "* feat: 1 つ目\n"
            "\n"
            f"Claude-Session: {SESSION_URL}\n"
            "\n"
            "* fix: 2 つ目\n"
            "\n"
            f"Claude-Session: {SESSION_URL}\n"
            "\n"
            "* docs: 3 つ目\n"
        )
        self.assertEqual(find_session_urls(message), [(5, "trailer"), (9, "trailer")])

    def test_bare_url_inside_a_paragraph(self):
        message = (
            "docs: 記録を足す\n"
            "\n"
            f"作業の経緯は {SESSION_URL} を参照。\n"
        )
        self.assertEqual(find_session_urls(message), [(3, "url")])

    def test_url_without_a_scheme(self):
        message = f"docs: 記録を足す\n\n経緯は claude.ai/code/{SESSION_ID} にある。\n"
        self.assertEqual(find_session_urls(message), [(3, "url")])

    def test_trailer_key_is_case_insensitive(self):
        # git の trailer のキーは大小を区別しない
        message = f"chore: x\n\nclaude-session: {SESSION_URL}\n"
        self.assertEqual(find_session_urls(message), [(3, "trailer")])

    def test_indented_trailer_is_still_caught_by_its_url(self):
        # 行頭の空白は trailer の形から外すが、値の URL は URL の形で当たる
        message = f"chore: x\n\n  Claude-Session: {SESSION_URL}\n"
        self.assertEqual(find_session_urls(message), [(3, "url")])

    def test_lines_above_the_scissors_are_still_checked(self):
        # scissors 行で切るのは「以降」だけ。切る位置がずれて全体を見なくなる変異の対
        message = (
            "chore: x\n"
            "\n"
            f"Claude-Session: {SESSION_URL}\n"
            "# ------------------------ >8 ------------------------\n"
            "+ diff の行\n"
        )
        self.assertEqual(find_session_urls(message), [(3, "trailer")])


class Allowed(unittest.TestCase):
    def test_clean_message(self):
        self.assertEqual(find_session_urls("fix: 直す\n\n本文。\n"), [])

    def test_key_explained_in_the_middle_of_a_line(self):
        message = (
            "docs: 規則を書く\n"
            "\n"
            "`Claude-Session:` の trailer を付けない。Claude-Session: の行は検査が止める。\n"
        )
        self.assertEqual(find_session_urls(message), [])

    def test_placeholders(self):
        message = (
            "docs: 形を書く\n"
            "\n"
            "Claude-Session: <URL>\n"
            "値は https://claude.ai/code/<id> の形で、session_ の後ろに英数字が続く。\n"
            # session_ までは実物と同じ形で、英数字が続くかだけが違う。続く文字の条件を外す
            # 変異の対
            "https://claude.ai/code/session_<id> のように書く。\n"
        )
        self.assertEqual(find_session_urls(message), [])

    def test_lines_below_the_scissors_are_not_checked(self):
        # git commit -v は scissors 行より後に diff を足す。このテストの fixture のような
        # 検出すべき例を含む diff をコミットすると、検査が自分の diff に当たる
        message = (
            "test: fixture を足す\n"
            "\n"
            "本文。\n"
            "# ------------------------ >8 ------------------------\n"
            "# Do not modify or remove the line above.\n"
            f"+Claude-Session: {SESSION_URL}\n"
            f"+    \"作業の経緯は {SESSION_URL} を参照。\"\n"
        )
        self.assertEqual(find_session_urls(message), [])


class ExitCodes(unittest.TestCase):
    def run_checker(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(ROOT / CHECKER), *args],
            capture_output=True,
            text=True,
        )

    def write(self, directory: str, data: bytes) -> str:
        path = Path(directory) / "COMMIT_EDITMSG"
        path.write_bytes(data)
        return str(path)

    def test_finding_is_rc_1_without_echoing_the_url(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(
                directory, f"chore: x\n\nClaude-Session: {SESSION_URL}\n".encode()
            )
            result = self.run_checker(path)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("[x] line 3: trailer", result.stderr)
        self.assertIn("走査した行: 3 行 / 検出 1 件", result.stdout)
        # 端末やログへ値を写さない。出すのは座標と種別だけ
        self.assertNotIn(SESSION_ID, result.stdout + result.stderr)

    def test_clean_message_is_rc_0_and_reports_what_it_scanned(self):
        # 緑のときも何行見たかを出す。scissors より後は数えない
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(
                directory,
                "fix: 直す\n\n本文。\n# ------------------------ >8 ------------------------\n+x\n".encode(),
            )
            result = self.run_checker(path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("走査した行: 3 行 / 検出 0 件", result.stdout)

    def test_unreadable_inputs_are_rc_2(self):
        with tempfile.TemporaryDirectory() as directory:
            undecodable = self.write(directory, b"chore: x\n\n\xff\xfe\n")
            cases = {
                "no-args": (),
                "two-args": (undecodable, undecodable),
                "missing": (str(Path(directory) / "absent"),),
                "undecodable": (undecodable,),
            }
            for name, args in cases.items():
                with self.subTest(case=name):
                    self.assertEqual(self.run_checker(*args).returncode, 2)


class Attachment(unittest.TestCase):
    """取り付けを pin する。読み方の補助と限界は scripts/hook_config_lines.py が持つ。"""

    @classmethod
    def setUpClass(cls):
        helpers = _load("hook_config_lines", HERE / "hook_config_lines.py")
        lines = helpers.live_lines(PRE_COMMIT_CONFIG)
        # entry にフラグが無いので、パス自体を空白区切りの 1 語として照合する
        cls.block = helpers.hook_block(lines, CHECKER, CHECKER)
        cls.helpers = helpers

    def test_hook_is_bound_to_the_commit_msg_stage(self):
        self.assertTrue(self.block, f"{CHECKER} を呼ぶ hook が見つからない")
        self.assertEqual(self.helpers.hook_values(self.block, "stages"), ["[commit-msg]"])

    def test_hook_runs_without_a_matching_filename(self):
        # always_run が無いと、この stage では "(no files to check)Skipped" の rc 0 になる
        self.assertEqual(self.helpers.hook_values(self.block, "always_run"), ["true"])

    def test_hook_uses_the_system_language(self):
        self.assertEqual(
            self.helpers.hook_values(self.block, "language"), [self.helpers.HOOK_LANGUAGE]
        )

    def test_hook_has_no_unexamined_keys(self):
        unknown = sorted(self.helpers.hook_keys(self.block) - self.helpers.COMMIT_MSG_HOOK_KEYS)
        self.assertFalse(unknown, f"未検討のキーがある: {unknown}")


if __name__ == "__main__":
    unittest.main()
