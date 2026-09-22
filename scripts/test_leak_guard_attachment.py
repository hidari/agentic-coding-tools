#!/usr/bin/env python3
"""禁止語の検査の取り付けを pin する。検査機構ではなく「呼ばれていること」を見る。

機構そのもののテストは検査スクリプトの隣にある。あちらが全部緑でも、pre-commit から
呼ばれていなければ一度も走らない。commit-msg stage は特に、配線 1 行で黙って skip に
なる形を複数持つ。

取り付け側の literal を持つのはこのファイルなので、検査スクリプトが配布物として
移動しても、追従するのはここと設定ファイルだけで済む。

先例と同じ構成は scripts/test_issue_id_attachment.py。行で読む補助は両者で共有する
(scripts/hook_config_lines.py)。
"""

from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 取り付け側が書く literal はこのパスだけ。pin が探す文字列が実在しないパスへ
# drift すると dead pin になるので、実在も併せて検査する
CHECKER = "plugins/dev-workflow/skills/commit-and-pr-message/scripts/check-leak-guard-denylist.py"

PRE_COMMIT_CONFIG = ROOT / ".pre-commit-config.yaml"

# 各 hook が持ってよいキー。絞り込みの手段は列挙し切れないので、許可する側を pin して
# 知らないキーが増えたら赤にする。commit-msg stage では渡るファイルが message ファイル
# 1 本しかないため、ファイル名やファイル型で絞る指定はどれも集合を空にして skip になる。
# pre-commit stage 側は走査対象を追跡ファイル全体で固定するので同じく絞らない。
TRACKED_HOOK_KEYS = frozenset(
    {"id", "name", "language", "entry", "pass_filenames", "always_run", "verbose"}
)
COMMIT_MSG_HOOK_KEYS = frozenset(
    {"id", "name", "language", "entry", "stages", "always_run", "verbose"}
)


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


class Attachment(unittest.TestCase):
    def test_checker_path_exists(self):
        # 取り付けを探す文字列が実在しないパスへ drift すると dead pin になる
        self.assertTrue((ROOT / CHECKER).is_file(), f"{CHECKER} が無い")

    def test_pre_commit_runs_the_tracked_file_check(self):
        self.assertTrue(
            invocations(live_lines(PRE_COMMIT_CONFIG), CHECKER, "--check"),
            "pre-commit が --check を呼んでいない",
        )

    def test_pre_commit_runs_the_commit_message_check(self):
        self.assertTrue(
            invocations(live_lines(PRE_COMMIT_CONFIG), CHECKER, "--check-text"),
            "pre-commit が --check-text を呼んでいない",
        )

    def test_commit_message_hook_is_bound_to_the_commit_msg_stage(self):
        block = hook_block(live_lines(PRE_COMMIT_CONFIG), CHECKER, "--check-text")
        self.assertTrue(block, "--check-text の hook 定義が見つからない")
        self.assertTrue(
            [line for line in block if line.lstrip().startswith("stages:") and "commit-msg" in line],
            "--check-text の hook が commit-msg stage に紐付いていない",
        )

    def test_both_hooks_always_run(self):
        # commit-msg stage では渡るファイルが message ファイル 1 本しかないので、
        # ファイル名で絞る指定は「絞る」ではなく「常に skip」になる。pre-commit stage 側も
        # 走査対象を追跡ファイル全体で固定するために絞らない
        for flag in ("--check", "--check-text"):
            with self.subTest(flag=flag):
                block = hook_block(live_lines(PRE_COMMIT_CONFIG), CHECKER, flag)
                self.assertTrue(block, f"{flag} の hook 定義が見つからない")
                self.assertTrue(
                    [line for line in block if "always_run: true" in line],
                    f"{flag} の hook に always_run: true が無い",
                )

    def test_both_hooks_are_verbose(self):
        # pre-commit は rc 0 の hook の出力を捨てるので、verbose: true が無いと
        # 「設定し忘れて skip した」と「検査して 0 件だった」が端末上で同じ 1 行になる
        for flag in ("--check", "--check-text"):
            with self.subTest(flag=flag):
                block = hook_block(live_lines(PRE_COMMIT_CONFIG), CHECKER, flag)
                self.assertTrue(block, f"{flag} の hook 定義が見つからない")
                self.assertTrue(
                    [line for line in block if "verbose: true" in line],
                    f"{flag} の hook に verbose: true が無い。skip 通知が誰の目にも入らない",
                )

    def test_tracked_file_hook_runs_on_the_pre_commit_stage(self):
        # `stages: [manual]` を 1 行足すと、この hook は commit 時にも
        # `pre-commit run --all-files` にも現れないまま追跡ファイル面が消える。
        # Skipped の表示すら出ないので、出力を見比べても異常に見えない (実測)
        # この hook は stages を宣言しないので top-level の default_stages を継承する。
        # hook ブロック内だけを見る形は、宣言が無いとループが一度も回らず空虚に緑になり、
        # top-level の 1 行を [manual] へ変えるだけで全 stage から消えても捕まらない (実測)
        lines = live_lines(PRE_COMMIT_CONFIG)
        block = hook_block(lines, CHECKER, "--check")
        self.assertTrue(block, "--check の hook 定義が見つからない")
        own = [line for line in block if line.lstrip().startswith("stages:")]
        effective = own or [line for line in lines if re.match(r"^default_stages:", line)]
        self.assertTrue(effective, "--check の stage を決める宣言がどこにも無い")
        for line in effective:
            self.assertIn(
                "pre-commit", line, "--check の hook が pre-commit stage から外れている"
            )

    def test_tracked_file_hook_does_not_pass_filenames(self):
        # 落ちると always_run のまま追跡パスが引数で渡り、argparse のエラーが argv を
        # そのまま印字する。汚染パスの置き換えが隠すはずのパス (= 語そのもの) が
        # Failed ブロックへ並ぶ (実測)。スクリプト側の parse_known_args と 2 層で塞ぐ
        block = hook_block(live_lines(PRE_COMMIT_CONFIG), CHECKER, "--check")
        self.assertTrue(block, "--check の hook 定義が見つからない")
        self.assertTrue(
            [line for line in block if "pass_filenames: false" in line],
            "--check の hook に pass_filenames: false が無い",
        )

    def test_hook_blocks_have_no_unvetted_keys(self):
        # 個別の narrowing キーを列挙して禁じる形は採らない。絞り込みの手段は列挙し切れず、
        # pre-commit が新しいキーを足せば列挙の外から同じ穴が開く。許可する側を pin して、
        # 知らないキーが増えたら赤にする (先例は scripts/test_issue_id_attachment.py)
        for flag, allowed in (
            ("--check", TRACKED_HOOK_KEYS),
            ("--check-text", COMMIT_MSG_HOOK_KEYS),
        ):
            with self.subTest(flag=flag):
                block = hook_block(live_lines(PRE_COMMIT_CONFIG), CHECKER, flag)
                self.assertTrue(block, f"{flag} の hook 定義が見つからない")
                unknown = sorted(hook_keys(block) - allowed)
                self.assertFalse(
                    unknown,
                    f"{flag} の hook に未検討のキーがある: {unknown}。"
                    "silent skip を招かないことを確かめてから許可集合へ足す",
                )

    def test_ci_does_not_run_this_check(self):
        # この検査を CI へ取り付けないことを負の pin として置く。
        #
        # PUBLIC リポジトリの Actions ログは誰でも読める。検出座標を公開ログへ出すと、
        # 対象のコミットは push 済みなので座標の交差から語を復元できる。取り付けない
        # 決定は散文だけでは守れないので、ここで赤にする形にしてある。
        #
        # 判断そのものは取り付ける側のものなので、検査スクリプト自身は危険の一般形 (公開される
        # ログへ座標を出すと語を復元できる) だけを書き、このリポジトリで取り付けない判断はここが
        # 持つ。公開されないログしか持たない環境では、取り付けても同じ危険は生じない。
        #
        # 照合はフルパスではなくファイル名で行う。フルパスを書かない呼び方 (working-directory で
        # skill のディレクトリへ入って相対パスで呼ぶ、ディレクトリを変数に入れて呼ぶ、インストール
        # 先のパスで呼ぶ) も射程に入れるため。workflow が呼ぶスクリプトの中から間接に呼ぶ形は
        # 射程の外。正の pin がフルパスのままなのは、pre-commit が呼ぶのが正しいファイルで
        # あることまで見るため。
        #
        # 1 ファイルだけを見ると、別の workflow ファイルから呼ぶ取り付けが射程の外で
        # 素通りする。走査した件数が 0 でないことも併せて見る (0 件で緑になる形を作らない)。
        name = Path(CHECKER).name
        workflow_dir = ROOT / ".github" / "workflows"
        workflows = sorted(workflow_dir.glob("*.yml")) + sorted(workflow_dir.glob("*.yaml"))
        self.assertTrue(workflows, "workflow が 1 件も無い (negative pin が 0 件で緑になる)")
        for wf in workflows:
            with self.subTest(workflow=wf.name):
                self.assertFalse(
                    [line for line in live_lines(wf) if name in line],
                    f"{wf.name} がこの検査を呼んでいる。検出座標が公開ログへ残る",
                )


if __name__ == "__main__":
    unittest.main()
