#!/usr/bin/env python3
"""issue-id.py の取り付けを pin する。検査機構ではなく「呼ばれていること」を見る。

検査機構そのもののテストは skill 側の test_issue_id.py が持つ。あちらが緑でも
pre-commit と CI から呼ばれていなければ一度も走らないので、取り付けは別に pin する。

先例は scripts/test_run_python_tests.py の Attachment。あちらは run-python-tests.py の
取り付けを、その run-python-tests.py 自身に走らされて検証するため、両取り付けを同時に
外すとこのテスト自身が走らず検出できないという自己ホスト盲点を持つ (runner の docstring と
ISSUE-13)。こちらは検証対象 (issue-id.py) と実行者 (run-python-tests.py) が別なので、
issue-id.py の両取り付けを同時に外しても runner は走り続け、ここが赤くなる (実測)。

stdlib に YAML パーサが無いため、コメント行を除いた行の部分文字列で見る。YAML 構造
としての妥当性までは見ない。そこは pre-commit 自身と check-yaml hook が担う。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 取り付け側が書く literal はこのパスだけ。pin が探す文字列が実在しないパスへ
# drift すると dead pin になるので、実在も併せて検査する
CHECKER = "plugins/dev-workflow/skills/in-repo-issue/scripts/issue-id.py"

PRE_COMMIT_CONFIG = ROOT / ".pre-commit-config.yaml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

HOOK_START = re.compile(r"^\s*-\s+id:")


def live_lines(path: Path) -> list[str]:
    """コメント行を除いた行。コメントの中の記述を取り付けと誤認しないため。"""
    return [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]


def invocations(lines: list[str], flag: str) -> list[str]:
    """checker を flag 付きで呼んでいる行。

    flag は split() で照合する。部分文字列だと --check が --check-text にも
    一致し、片方の hook を消しても両方の pin が緑のままになる。
    """
    return [line for line in lines if CHECKER in line and flag in line.split()]


def hook_block(lines: list[str], flag: str) -> list[str]:
    """flag で呼んでいる local hook の定義ブロック (次の `- id:` の手前まで)。"""
    hits = [i for i, line in enumerate(lines) if CHECKER in line and flag in line.split()]
    if not hits:
        return []
    start = hits[0]
    while start > 0 and not HOOK_START.match(lines[start]):
        start -= 1
    end = start + 1
    while end < len(lines) and not HOOK_START.match(lines[end]):
        end += 1
    return lines[start:end]


class Attachment(unittest.TestCase):
    def test_checker_path_exists(self):
        self.assertTrue(
            (ROOT / CHECKER).is_file(),
            f"{CHECKER} が無い。取り付けを探す文字列が実在しないパスになっている",
        )

    def test_pre_commit_runs_the_repository_check(self):
        self.assertTrue(
            invocations(live_lines(PRE_COMMIT_CONFIG), "--check"),
            "pre-commit が issue-id.py --check を呼んでいない",
        )

    def test_pre_commit_runs_the_commit_message_check(self):
        self.assertTrue(
            invocations(live_lines(PRE_COMMIT_CONFIG), "--check-text"),
            "pre-commit が issue-id.py --check-text を呼んでいない",
        )

    def test_commit_message_check_is_bound_to_the_commit_msg_stage(self):
        block = hook_block(live_lines(PRE_COMMIT_CONFIG), "--check-text")
        self.assertTrue(block, "--check-text の hook 定義が見つからない")
        self.assertTrue(
            [line for line in block if line.lstrip().startswith("stages:") and "commit-msg" in line],
            "--check-text の hook が commit-msg stage に紐付いていない",
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
        # files: で絞ると docs/issues を触らないコミットで走らなくなり、
        # 走らなかったこと自体が出力に現れない
        block = hook_block(live_lines(PRE_COMMIT_CONFIG), "--check")
        self.assertTrue(block, "--check の hook 定義が見つからない")
        self.assertFalse(
            [line for line in block if line.lstrip().startswith(("files:", "exclude:"))],
            "--check の hook が files: / exclude: で絞られている",
        )
        self.assertTrue(
            [line for line in block if "always_run: true" in line],
            "--check の hook に always_run: true が無い",
        )

    def test_ci_runs_the_repository_check(self):
        self.assertTrue(
            invocations(live_lines(CI_WORKFLOW), "--check"),
            "ci.yml が issue-id.py --check を呼んでいない",
        )


if __name__ == "__main__":
    unittest.main()
