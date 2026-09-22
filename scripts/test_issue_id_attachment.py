#!/usr/bin/env python3
"""issue-id.py の取り付けを pin する。検査機構ではなく「呼ばれていること」を見る。

検査機構そのもののテストは skill 側の test_issue_id.py が持つ。あちらが緑でも
pre-commit と CI から呼ばれていなければ一度も走らないので、取り付けは別に pin する。

先例は scripts/test_run_python_tests.py の Attachment。あちらは run-python-tests.py の
取り付けを、その run-python-tests.py 自身に走らされて検証するため、両取り付けを同時に
外すとこのテスト自身が走らず検出できないという自己ホスト盲点を持つ (runner の docstring と
ISSUE-13)。こちらは検証対象 (issue-id.py) と実行者 (run-python-tests.py) が別なので、
issue-id.py の両取り付けを同時に外しても runner は走り続け、ここが赤くなる (実測)。

設定を行で読む補助と、その読み方の限界 (YAML として解釈しない) は
scripts/hook_config_lines.py が持つ。漏洩検査の取り付けを pin する
scripts/test_leak_guard_attachment.py と共有している。
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 取り付け側が書く literal はこのパスだけ。pin が探す文字列が実在しないパスへ
# drift すると dead pin になるので、実在も併せて検査する
CHECKER = "plugins/dev-workflow/skills/in-repo-issue/scripts/issue-id.py"

PRE_COMMIT_CONFIG = ROOT / ".pre-commit-config.yaml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

# commit-msg stage の hook が持ってよいキー。個別の narrowing キーを列挙して禁じる形は
# 採らない。この stage では渡るファイルが message ファイル 1 本しかないため、ファイル名や
# ファイル型で絞る指定はどれも集合を空にし、絞り込みではなく skip になる (実測: files /
# exclude / types / exclude_types のいずれでも "(no files to check)Skipped" の rc 0)。
# 手段はこの 4 つに限らず、pre-commit が新しいキーを足せば列挙の外から同じ穴が開く。
# 許可する側を pin して、知らないキーが増えたら赤にする。
COMMIT_MSG_HOOK_KEYS = frozenset({"id", "name", "language", "entry", "stages", "always_run"})

# hook の language は値まで pin する。キーの許可集合は値を見ないので、`language: pygrep` へ
# 1 語変えても他の pin は緑のままになる (変異注入で確認)。pygrep は entry を正規表現として
# 渡されたファイルを照合するだけで、issue-id.py を起動しない (pre-commit 4.6.2 の
# languages/pygrep.py を読んで確認)
HOOK_LANGUAGE = "system"


def _load_helpers():
    """行で読む補助を読む。素の import はリポジトリ root から回すと解決できない。"""
    spec = importlib.util.spec_from_file_location(
        "hook_config_lines", Path(__file__).resolve().parent / "hook_config_lines.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_helpers = _load_helpers()
live_lines = _helpers.live_lines
invocations = _helpers.invocations
hook_block = _helpers.hook_block
hook_keys = _helpers.hook_keys
hook_values = _helpers.hook_values


class Attachment(unittest.TestCase):
    def test_checker_path_exists(self):
        self.assertTrue(
            (ROOT / CHECKER).is_file(),
            f"{CHECKER} が無い。取り付けを探す文字列が実在しないパスになっている",
        )

    def test_pre_commit_runs_the_repository_check(self):
        self.assertTrue(
            invocations(live_lines(PRE_COMMIT_CONFIG), CHECKER, "--check"),
            "pre-commit が issue-id.py --check を呼んでいない",
        )

    def test_pre_commit_runs_the_commit_message_check(self):
        self.assertTrue(
            invocations(live_lines(PRE_COMMIT_CONFIG), CHECKER, "--check-text"),
            "pre-commit が issue-id.py --check-text を呼んでいない",
        )

    def test_commit_message_check_is_bound_to_the_commit_msg_stage(self):
        block = hook_block(live_lines(PRE_COMMIT_CONFIG), CHECKER, "--check-text")
        self.assertTrue(block, "--check-text の hook 定義が見つからない")
        self.assertTrue(
            [line for line in block if line.lstrip().startswith("stages:") and "commit-msg" in line],
            "--check-text の hook が commit-msg stage に紐付いていない",
        )

    def test_both_hooks_use_the_system_language(self):
        # 値まで pin する理由は HOOK_LANGUAGE のコメント
        for flag in ("--check", "--check-text"):
            with self.subTest(flag=flag):
                block = hook_block(live_lines(PRE_COMMIT_CONFIG), CHECKER, flag)
                self.assertTrue(block, f"{flag} の hook 定義が見つからない")
                self.assertEqual(
                    hook_values(block, "language"),
                    [HOOK_LANGUAGE],
                    f"{flag} の hook の language が {HOOK_LANGUAGE} でない",
                )

    def test_commit_msg_hook_type_is_installed_by_default(self):
        # stage の宣言だけでは `pre-commit install` が commit-msg の hook を置かず、
        # 設定にあるのに一度も発火しない形になる
        self.assertTrue(
            [
                line
                for line in live_lines(PRE_COMMIT_CONFIG)
                if line.startswith("default_install_hook_types:") and "commit-msg" in line
            ],
            "default_install_hook_types に commit-msg が無く、hook が install されない",
        )

    def test_repository_check_is_not_narrowed(self):
        # 走査対象を追跡ファイル全体で固定するための対。実際に走ることを保証している
        # のは always_run: true の方で (実測: 絞り込みを足しても always_run があれば
        # 走る)、files: / exclude: を置かないのはその宣言。always_run だけが落ちると
        # 残った絞り込みが効き始め、走らなかったこと自体が出力に現れなくなる
        block = hook_block(live_lines(PRE_COMMIT_CONFIG), CHECKER, "--check")
        self.assertTrue(block, "--check の hook 定義が見つからない")
        self.assertFalse(
            [line for line in block if line.lstrip().startswith(("files:", "exclude:"))],
            "--check の hook が files: / exclude: で絞られている",
        )
        self.assertTrue(
            [line for line in block if "always_run: true" in line],
            "--check の hook に always_run: true が無い",
        )

    def test_commit_message_check_is_not_narrowed(self):
        # commit-msg stage では渡るファイルが message ファイル 1 本しかないので、
        # ファイル名で絞る指定は「絞る」ではなく「常に skip」になる。実測: この hook へ
        # files: を足しても既存の pin は全て緑のままで、裸の数字記法を含むメッセージの
        # コミットが rc 0 で成功した。設定にあるのに一度も発火しない形で、
        # test_commit_msg_hook_type_is_installed_by_default が防いでいる形と同型
        block = hook_block(live_lines(PRE_COMMIT_CONFIG), CHECKER, "--check-text")
        self.assertTrue(block, "--check-text の hook 定義が見つからない")
        unknown = sorted(hook_keys(block) - COMMIT_MSG_HOOK_KEYS)
        self.assertFalse(
            unknown,
            f"--check-text の hook に未検討のキーがある: {unknown}。"
            "commit-msg stage で skip を招かないことを確かめてから "
            "COMMIT_MSG_HOOK_KEYS へ足す",
        )

    def test_commit_message_check_survives_filename_filtering(self):
        # 上の allowlist が見るのはこの hook のブロックだけなので、設定の top-level に
        # 置いたフィルタは射程の外にある (実測: top-level の exclude / files でも
        # 同じ silent skip が起きる)。always_run: true があると空集合でも hook が起動し、
        # 引数ゼロの argparse エラー (exit 2) で落ちる (実測)。静かな skip を
        # 騒がしい失敗へ変える堰なので、キーの有無ではなく値まで見る
        block = hook_block(live_lines(PRE_COMMIT_CONFIG), CHECKER, "--check-text")
        self.assertTrue(block, "--check-text の hook 定義が見つからない")
        self.assertTrue(
            [line for line in block if "always_run: true" in line],
            "--check-text の hook に always_run: true が無い。"
            "ファイル名フィルタが silent skip になる",
        )

    def test_ci_runs_the_repository_check(self):
        self.assertTrue(
            invocations(live_lines(CI_WORKFLOW), CHECKER, "--check"),
            "ci.yml が issue-id.py --check を呼んでいない",
        )


if __name__ == "__main__":
    unittest.main()
