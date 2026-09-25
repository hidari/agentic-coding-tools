#!/usr/bin/env python3
"""漏洩検査の取り付けを pin する。検査機構ではなく「呼ばれていること」を見る。

層 2 (禁止語リスト) は Attachment、層 1 (gitleaks の 2 本の config) は Layer1Attachment が見る。
層 2 を subprocess で呼ぶ入口 (check-outgoing-text.py) に取り付けは無く、CI から呼ばないことの
負の pin だけを Attachment が層 2 と一緒に持つ。

機構そのもののテストは検査スクリプトの隣にある。あちらが全部緑でも、pre-commit から
呼ばれていなければ一度も走らない。commit-msg stage は特に、配線 1 行で黙って skip に
なる形を複数持つ。層 1 の対照 (scripts/check-leak-guard-rules.py) も同じで、config が
正しくても pre-commit と CI が別の形で呼べば、その呼び出しの結果は対照と一致しない。

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
# 層 2 を subprocess で呼ぶ入口。取り付けは無いが、CI から呼ばないことの負の pin を層 2 と
# 同じ理由で掛ける (workflow が入口を呼ぶと、層 2 が公開ログへ取り付く)
OUTGOING_CHECK = "plugins/dev-workflow/skills/commit-and-pr-message/scripts/check-outgoing-text.py"

PRE_COMMIT_CONFIG = ROOT / ".pre-commit-config.yaml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

# 層 1 の config。取り付け側 (pre-commit と CI) は 2 本をそれぞれ -c で渡して 1 回ずつ
# 走らせる。1 本へまとめない理由は各 config の冒頭コメントが持つ
GITLEAKS_CONFIGS = (
    "plugins/dev-workflow/skills/commit-and-pr-message/scripts/leak-guard.gitleaks.toml",
    "plugins/dev-workflow/skills/commit-and-pr-message/scripts/leak-guard-default.gitleaks.toml",
)

# 層 2 の各 hook が持ってよいキー。絞り込みの手段は列挙し切れないので、許可する側を pin して
# 知らないキーが増えたら赤にする。commit-msg stage では渡るファイルが message ファイル
# 1 本しかないため、ファイル名やファイル型で絞る指定はどれも集合を空にして skip になる。
# pre-commit stage 側は走査対象を追跡ファイル全体で固定するので同じく絞らない。
TRACKED_HOOK_KEYS = frozenset(
    {"id", "name", "language", "entry", "pass_filenames", "always_run", "verbose"}
)
COMMIT_MSG_HOOK_KEYS = frozenset(
    {"id", "name", "language", "entry", "stages", "always_run", "verbose"}
)
# 層 1 の hook が持ってよいキー。stages を許さないのは、`stages: [manual]` を 1 行足すだけで
# hook がコミット時に走らなくなり、呼び出し行の pin は緑のままになるため
GITLEAKS_HOOK_KEYS = frozenset({"id", "name", "language", "entry", "pass_filenames", "always_run"})

# gitleaks の呼び出しが持ってよいフラグ。範囲を絞るフラグ (gitleaks の help に出る
# --log-opts / --enable-rule / --max-target-megabytes / --baseline-path など) は列挙し切れない
# ので、許可する側を pin して知らないフラグが増えたら赤にする。-c とその値は別に見る。
# CI の --redact は必須にする。PUBLIC リポジトリの Actions ログは誰でも読めるので、
# 外すと検出した値そのものが公開ログへ載る
PRE_COMMIT_GITLEAKS_FLAGS = {
    "required": frozenset({"--staged", "--ignore-gitleaks-allow"}),
    "allowed": frozenset({"--staged", "--ignore-gitleaks-allow", "--redact", "--no-banner"}),
}
CI_GITLEAKS_FLAGS = {
    "required": frozenset({"--ignore-gitleaks-allow", "--redact"}),
    "allowed": frozenset({"--ignore-gitleaks-allow", "--redact", "--no-banner"}),
}

# 全履歴を走査する step とルールを対照で検証する step (RULES_CHECK)、それらを持つ job が
# 持ってよいキー。step の `if:` や job の `if:` / `continue-on-error:` は、走査や検証を
# 飛ばすか失敗を緑に変える
SCAN_STEP_KEYS = frozenset({"name", "run"})
SCAN_JOB_KEYS = frozenset({"name", "runs-on", "steps"})
JOB_START = re.compile(r"^  [A-Za-z0-9_-]+:\s*$")

# 走査する job でルール自身を対照で検証する検査。これが走らないと、ルールが壊れていても
# 全履歴の走査は壊れたルールで緑を出す
RULES_CHECK = "scripts/check-leak-guard-rules.py"


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
effective_stages = _helpers.effective_stages
HOOK_LANGUAGE = _helpers.HOOK_LANGUAGE


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _job_of(lines: list[str], index: int) -> list[str]:
    """index の行を含む workflow の job のブロック (次の job か top-level のキーの手前まで)。

    job は `jobs:` の直下に空白 2 つの字下げで並ぶ前提で読む (ci.yml の書き方)。前提が崩れると
    先頭行が job の見出しにならないので、呼ぶ側がそれを確かめる。
    """
    start = index
    while start > 0 and not JOB_START.match(lines[start]):
        start -= 1
    end = start + 1
    while end < len(lines) and not JOB_START.match(lines[end]) and not re.match(r"^\S", lines[end]):
        end += 1
    return lines[start:end]


def _job_keys(job: list[str]) -> set[str]:
    """job の直下のキー (steps の中身は含めない)。"""
    body = [line for line in job[1:] if line.strip()]
    if not body:
        return set()
    return hook_keys([line for line in body if _indent(line) == _indent(body[0])])


def _steps(job: list[str]) -> list[list[str]]:
    """job の steps を 1 step ずつのブロックに割る。"""
    heads = [i for i, line in enumerate(job) if line.strip() == "steps:"]
    if not heads:
        return []
    items = [i for i in range(heads[0] + 1, len(job)) if job[i].lstrip().startswith("- ")]
    if not items:
        return []
    starts = [i for i in items if _indent(job[i]) == _indent(job[items[0]])]
    return [job[s:e] for s, e in zip(starts, starts[1:] + [len(job)])]


class Attachment(unittest.TestCase):
    def test_checker_path_exists(self):
        # 取り付けを探す文字列が実在しないパスへ drift すると dead pin になる。入口は
        # 負の pin にしか使わないが、名前が実在しなければその pin も同じく dead になる
        for path in (CHECKER, OUTGOING_CHECK):
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).is_file(), f"{path} が無い")

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

    def test_both_hooks_use_the_system_language(self):
        # 値まで pin する理由は hook_config_lines.py の HOOK_LANGUAGE のコメント
        for flag in ("--check", "--check-text"):
            with self.subTest(flag=flag):
                block = hook_block(live_lines(PRE_COMMIT_CONFIG), CHECKER, flag)
                self.assertTrue(block, f"{flag} の hook 定義が見つからない")
                self.assertEqual(
                    hook_values(block, "language"),
                    [HOOK_LANGUAGE],
                    f"{flag} の hook の language が {HOOK_LANGUAGE} でない",
                )

    def test_tracked_file_hook_runs_on_the_pre_commit_stage(self):
        # `stages: [manual]` を 1 行足すと、この hook は commit 時にも
        # `pre-commit run --all-files` にも現れないまま追跡ファイル面が消える。
        # Skipped の表示すら出ないので、出力を見比べても異常に見えない (実測)
        # この hook は stages を宣言しないので top-level の default_stages を継承する
        lines = live_lines(PRE_COMMIT_CONFIG)
        block = hook_block(lines, CHECKER, "--check")
        self.assertTrue(block, "--check の hook 定義が見つからない")
        effective = effective_stages(lines, block)
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
        # この検査 (層 2) と、それを subprocess で呼ぶ入口を CI へ取り付けないことを負の pin
        # として置く。入口の出力は層 2 の座標を含むので、workflow が入口を呼ぶ形でも層 2 が
        # 公開ログへ取り付く。
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
        workflow_dir = ROOT / ".github" / "workflows"
        workflows = sorted(workflow_dir.glob("*.yml")) + sorted(workflow_dir.glob("*.yaml"))
        self.assertTrue(workflows, "workflow が 1 件も無い (negative pin が 0 件で緑になる)")
        for path in (CHECKER, OUTGOING_CHECK):
            name = Path(path).name
            for wf in workflows:
                with self.subTest(script=name, workflow=wf.name):
                    self.assertFalse(
                        [line for line in live_lines(wf) if name in line],
                        f"{wf.name} が {name} を呼んでいる。検出座標が公開ログへ残る",
                    )


class Layer1Attachment(unittest.TestCase):
    """層 1 の 2 本の config が pre-commit (staged 差分) と CI (全履歴) から呼ばれていることと、
    CI の走査する job がルール自身を対照で検証すること。"""

    def test_config_paths_exist(self):
        # 取り付けを探す文字列が実在しないパスへ drift すると dead pin になる
        for config in GITLEAKS_CONFIGS:
            with self.subTest(config=config):
                self.assertTrue((ROOT / config).is_file(), f"{config} が無い")

    def _assert_gitleaks_git(self, command: list[str], config: str, flags: dict, where: str):
        self.assertEqual(command[:2], ["gitleaks", "git"], f"{where} が gitleaks git で始まらない")
        rest = command[2:]
        self.assertEqual(rest.count("-c"), 1, f"{where} の -c が 1 つでない")
        i = rest.index("-c")
        self.assertEqual(rest[i + 1 : i + 2], [config], f"{where} の -c が {config} を指していない")
        used = set(rest[:i] + rest[i + 2 :])
        self.assertFalse(
            sorted(flags["required"] - used), f"{where} に要るフラグが無い"
        )
        self.assertFalse(
            sorted(used - flags["allowed"]),
            f"{where} に未検討のフラグがある。範囲を絞らないことを確かめてから許可集合へ足す",
        )

    def _pre_commit_block(self, config: str) -> list[str]:
        block = hook_block(live_lines(PRE_COMMIT_CONFIG), config, "--staged")
        self.assertTrue(block, f"{config} を --staged で呼ぶ hook が見つからない")
        return block

    def test_pre_commit_scans_the_staged_diff_with_each_config(self):
        # --ignore-gitleaks-allow が無いと、行に gitleaks:allow と書くだけでその行の検出が
        # 消える (実測)。コミットする本人が書ける印で検査が外れる
        for config in GITLEAKS_CONFIGS:
            with self.subTest(config=config):
                block = self._pre_commit_block(config)
                entries = [value.split() for value in hook_values(block, "entry") if value]
                self.assertEqual(len(entries), 1, "hook の entry が 1 行でない")
                self._assert_gitleaks_git(
                    entries[0], config, PRE_COMMIT_GITLEAKS_FLAGS, "pre-commit の gitleaks の entry"
                )

    def test_pre_commit_gitleaks_hooks_always_run(self):
        for config in GITLEAKS_CONFIGS:
            with self.subTest(config=config):
                self.assertTrue(
                    [line for line in self._pre_commit_block(config) if "always_run: true" in line],
                    "gitleaks の hook に always_run: true が無い",
                )

    def test_pre_commit_gitleaks_hooks_do_not_pass_filenames(self):
        # gitleaks git は位置引数をリポジトリのパスとして読む。staged のファイルが 1 本の
        # コミットでそのファイル名が渡ると、git が「ディレクトリへ移れない」で失敗したまま
        # gitleaks は rc 0 の no leaks found を返す (実測: 検出 3 件を持つファイルで緑)。
        # 2 本以上なら引数の数で落ちるので、静かに通るのは 1 本のときだけ
        for config in GITLEAKS_CONFIGS:
            with self.subTest(config=config):
                self.assertTrue(
                    [line for line in self._pre_commit_block(config) if "pass_filenames: false" in line],
                    "gitleaks の hook に pass_filenames: false が無い",
                )

    def test_pre_commit_gitleaks_hooks_use_the_system_language(self):
        # 値まで pin する理由は hook_config_lines.py の HOOK_LANGUAGE のコメント
        for config in GITLEAKS_CONFIGS:
            with self.subTest(config=config):
                self.assertEqual(
                    hook_values(self._pre_commit_block(config), "language"),
                    [HOOK_LANGUAGE],
                    f"gitleaks の hook の language が {HOOK_LANGUAGE} でない",
                )

    def test_pre_commit_gitleaks_hooks_run_on_the_pre_commit_stage(self):
        lines = live_lines(PRE_COMMIT_CONFIG)
        for config in GITLEAKS_CONFIGS:
            with self.subTest(config=config):
                effective = effective_stages(lines, self._pre_commit_block(config))
                self.assertTrue(effective, "gitleaks の hook の stage を決める宣言がどこにも無い")
                for line in effective:
                    self.assertIn(
                        "pre-commit", line, "gitleaks の hook が pre-commit stage から外れている"
                    )

    def test_pre_commit_gitleaks_hooks_have_no_unvetted_keys(self):
        for config in GITLEAKS_CONFIGS:
            with self.subTest(config=config):
                unknown = sorted(hook_keys(self._pre_commit_block(config)) - GITLEAKS_HOOK_KEYS)
                self.assertFalse(
                    unknown,
                    f"gitleaks の hook に未検討のキーがある: {unknown}。"
                    "コミット時に走らなくなる形でないことを確かめてから許可集合へ足す",
                )

    def _ci_scan_job(self, config: str) -> list[str]:
        lines = live_lines(CI_WORKFLOW)
        hits = [i for i, line in enumerate(lines) if invocations([line], config, "--ignore-gitleaks-allow")]
        self.assertTrue(hits, f"ci.yml が {config} を --ignore-gitleaks-allow 付きで呼んでいない")
        job = _job_of(lines, hits[0])
        self.assertTrue(JOB_START.match(job[0]), "走査の step を持つ job の見出しを読めない")
        return job

    def test_ci_scans_the_full_history_with_each_config(self):
        lines = live_lines(CI_WORKFLOW)
        for config in GITLEAKS_CONFIGS:
            with self.subTest(config=config):
                found = invocations(lines, config, "--ignore-gitleaks-allow")
                self.assertTrue(found, f"ci.yml が {config} を --ignore-gitleaks-allow 付きで呼んでいない")
                for line in found:
                    # 1 行だけを渡すので値は高々 1 つ。run の行でなければ空のコマンドになり、
                    # 下の assert が「gitleaks git で始まらない」で落とす
                    command = "".join(hook_values([line], "run")).split()
                    self._assert_gitleaks_git(
                        command, config, CI_GITLEAKS_FLAGS, "ci.yml の gitleaks の run"
                    )

    def test_ci_scan_job_checks_out_the_full_history(self):
        # checkout の既定 (fetch-depth 1) の浅い clone では、全履歴の走査が 1 commits scanned の
        # 緑になる (実測: depth 1 の clone で rc 0 の no leaks found)
        for config in GITLEAKS_CONFIGS:
            with self.subTest(config=config):
                checkouts = [
                    step
                    for step in _steps(self._ci_scan_job(config))
                    if any("uses: actions/checkout@" in line for line in step)
                ]
                self.assertEqual(len(checkouts), 1, "走査する job の checkout が 1 つでない")
                self.assertTrue(
                    [line for line in checkouts[0] if re.match(r"^\s*fetch-depth:\s*0\s*$", line)],
                    "走査する job の checkout に fetch-depth: 0 が無い",
                )

    def test_ci_scan_is_neither_skipped_nor_allowed_to_fail(self):
        for config in GITLEAKS_CONFIGS:
            with self.subTest(config=config):
                job = self._ci_scan_job(config)
                self.assertNotIn(
                    "continue-on-error", hook_keys(job), "走査する job に continue-on-error がある"
                )
                self.assertFalse(
                    sorted(_job_keys(job) - SCAN_JOB_KEYS), "走査する job に未検討のキーがある"
                )
                scans = [step for step in _steps(job) if invocations(step, config, "--ignore-gitleaks-allow")]
                self.assertEqual(len(scans), 1, "走査の step が 1 つでない")
                self.assertFalse(
                    sorted(hook_keys(scans[0]) - SCAN_STEP_KEYS), "走査の step に未検討のキーがある"
                )

    def test_ci_scan_job_verifies_the_rules(self):
        # job の側のキーは test_ci_scan_is_neither_skipped_nor_allowed_to_fail が見る。
        # 引数を許さないのは、--update-manifest が manifest との照合を飛ばして 0 を返すため
        for config in GITLEAKS_CONFIGS:
            with self.subTest(config=config):
                checks = [
                    step
                    for step in _steps(self._ci_scan_job(config))
                    if any(RULES_CHECK in value.split() for value in hook_values(step, "run"))
                ]
                self.assertEqual(len(checks), 1, "走査する job に対照検査の step が 1 つでない")
                self.assertEqual(
                    [value.split() for value in hook_values(checks[0], "run") if value],
                    [["python3", RULES_CHECK]],
                    "対照検査の呼び出しが python3 での引数なしの 1 行でない",
                )
                self.assertFalse(
                    sorted(hook_keys(checks[0]) - SCAN_STEP_KEYS),
                    "対照検査の step に未検討のキーがある",
                )


if __name__ == "__main__":
    unittest.main()
