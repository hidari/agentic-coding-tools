#!/usr/bin/env python3
"""wait-ci-runs.py の仕様。

CI の run を commit の SHA で特定し、登録されるまで引き直し、全部が終わったら conclusion を
読んで判定する入口を pin する。gh はプロセス境界の外にあるので、PATH の先頭に置いた stub で
差し替える。stub は呼ばれた argv を記録し、決めた応答を順に返す (尽きたら最後の応答を返し
続ける)。登録前の `[]` と rc=0 は本物の gh で実測した形で、ここではそれを stub が返す。
本物の gh は `--json` で頼まれた項目しか返さない (実測) ので、stub も run を頼まれた項目へ
削ってから返す。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENTRY = (
    ROOT / "plugins" / "dev-workflow" / "skills" / "pre-merge-quality-gate" / "scripts"
    / "wait-ci-runs.py"
)
SHA = "0123456789abcdef0123456789abcdef01234567"

STUB = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, sys
    state = os.environ["STUB_STATE"]
    with open(os.path.join(state, "argv.log"), "a", encoding="utf-8") as f:
        f.write(json.dumps(sys.argv[1:]) + "\\n")
    with open(os.path.join(state, "responses.json"), encoding="utf-8") as f:
        responses = json.load(f)
    counter = os.path.join(state, "count")
    n = int(open(counter).read()) if os.path.exists(counter) else 0
    open(counter, "w").write(str(n + 1))
    r = responses[min(n, len(responses) - 1)]
    if "runs" in r:
        fields = sys.argv[sys.argv.index("--json") + 1].split(",")
        r["stdout"] = json.dumps([{k: run[k] for k in fields if k in run} for run in r["runs"]])
    sys.stdout.write(r.get("stdout", ""))
    sys.stderr.write(r.get("stderr", ""))
    sys.exit(r.get("rc", 0))
    """
)


def run_entry(name, status, conclusion=""):
    return {"databaseId": abs(hash(name)) % 100000, "name": name, "status": status,
            "conclusion": conclusion}


def listing(*runs):
    return {"rc": 0, "runs": list(runs)}


class WaitCiRuns(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = Path(self._tmp.name)
        self.bindir = self.state / "bin"
        self.bindir.mkdir()
        stub = self.bindir / "gh"
        stub.write_text(STUB, encoding="utf-8")
        stub.chmod(0o755)

    def run_with(self, responses, *args, path=None):
        (self.state / "responses.json").write_text(json.dumps(responses), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GH_", "GITHUB_"))}
        env["STUB_STATE"] = str(self.state)
        env["PATH"] = path if path is not None else f"{self.bindir}{os.pathsep}{env.get('PATH', '')}"
        # 待ちの上限を短く置くのは、待ち方を壊したときに subprocess の timeout ではなく
        # rc のアサーションで赤くするため。テストが上限を渡すときは後ろの args が勝つ
        proc = subprocess.run(
            [sys.executable, str(ENTRY), "--register-timeout", "5", "--complete-timeout", "5",
             *args, "--interval", "0"],
            capture_output=True, text=True, env=env, timeout=60,
        )
        return proc.returncode, proc.stdout, proc.stderr

    def calls(self):
        log = self.state / "argv.log"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    # --- SHA の形 ---------------------------------------------------------------

    def test_short_sha_is_rejected_without_calling_gh(self):
        # 短縮した SHA を gh run list --commit に渡すと `[]` と rc=0 が返り、
        # 「まだ登録されていない」と区別できない (実測)
        rc, out, err = self.run_with([listing()], SHA[:7], "--register-timeout", "0")
        self.assertEqual(rc, 2, out + err)
        self.assertIn("40 桁", err)
        self.assertEqual(self.calls(), [])

    def test_non_hex_sha_is_rejected(self):
        rc, out, err = self.run_with([listing()], "z" * 40, "--register-timeout", "0")
        self.assertEqual(rc, 2, out + err)
        self.assertEqual(self.calls(), [])

    def test_full_sha_is_passed_to_gh_as_commit_filter(self):
        rc, out, err = self.run_with([listing(run_entry("CI", "completed", "success"))], SHA)
        self.assertEqual(rc, 0, out + err)
        first = self.calls()[0]
        self.assertEqual(first[:2], ["run", "list"])
        self.assertEqual(first[first.index("--commit") + 1], SHA)
        # gh の --limit の既定は 20 で、それより多い run は黙って落ちる
        self.assertEqual(first[first.index("--limit") + 1], "100")
        self.assertLessEqual(
            {"databaseId", "name", "status", "conclusion"},
            set(first[first.index("--json") + 1].split(",")),
        )

    # --- 登録を待つ -------------------------------------------------------------

    def test_empty_listing_is_retried_until_runs_appear(self):
        rc, out, err = self.run_with(
            [listing(), listing(), listing(run_entry("CI", "completed", "success"))], SHA
        )
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(self.calls()), 3)

    def test_runs_never_registered_is_exit_2(self):
        rc, out, err = self.run_with([listing()], SHA, "--register-timeout", "0")
        self.assertEqual(rc, 2, out + err)
        self.assertIn("登録されない", err)

    # --- 終わるのを待つ ---------------------------------------------------------

    def test_in_progress_run_is_polled_until_completed(self):
        rc, out, err = self.run_with(
            [
                listing(run_entry("CI", "in_progress")),
                listing(run_entry("CI", "completed", "success")),
            ],
            SHA,
        )
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(self.calls()), 2)

    def test_waits_until_every_run_has_completed(self):
        # 1 本が終わっても、残りが終わるまでは判定しない。未完了の run の conclusion は空
        rc, out, err = self.run_with(
            [
                listing(run_entry("CI", "completed", "success"), run_entry("Deploy", "in_progress")),
                listing(run_entry("CI", "completed", "success"),
                        run_entry("Deploy", "completed", "success")),
            ],
            SHA,
        )
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(self.calls()), 2)

    def test_run_never_completing_is_exit_2(self):
        rc, out, err = self.run_with(
            [listing(run_entry("CI", "queued"))], SHA, "--complete-timeout", "0"
        )
        self.assertEqual(rc, 2, out + err)
        self.assertIn("終わらない", err)

    # --- conclusion の判定 -------------------------------------------------------

    def test_success_is_exit_0(self):
        # 行の形は release skill の手順が読む (run ごとの行の末尾の run-id を次のコマンドへ渡す)
        run = run_entry("CI", "completed", "success")
        rc, out, err = self.run_with([listing(run)], SHA)
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(
            out.splitlines(),
            [f"success CI {run['databaseId']}", "run 1 件のうち通らなかったもの 0 件"],
        )

    def test_failure_is_exit_1(self):
        rc, out, err = self.run_with([listing(run_entry("CI", "completed", "failure"))], SHA)
        self.assertEqual(rc, 1, out + err)
        self.assertIn("failure CI", out)

    def test_skipped_and_neutral_pass(self):
        # skipped と neutral は「実行して落ちた」ではないので通す
        for conclusion in ("skipped", "neutral"):
            with self.subTest(conclusion=conclusion):
                rc, out, err = self.run_with(
                    [listing(run_entry("CI", "completed", conclusion))], SHA
                )
                self.assertEqual(rc, 0, out + err)

    def test_cancelled_and_other_conclusions_fail(self):
        # cancelled は検証を終えていないので通さない。知らない値も通さない
        for conclusion in ("cancelled", "timed_out", "action_required", "startup_failure", ""):
            with self.subTest(conclusion=conclusion):
                rc, out, err = self.run_with(
                    [listing(run_entry("CI", "completed", conclusion))], SHA
                )
                self.assertEqual(rc, 1, out + err)

    def test_one_failing_run_among_several_fails(self):
        rc, out, err = self.run_with(
            [listing(run_entry("CI", "completed", "success"),
                     run_entry("Deploy", "completed", "failure"))],
            SHA,
        )
        self.assertEqual(rc, 1, out + err)
        self.assertIn("success CI", out)
        self.assertIn("failure Deploy", out)

    # --- gh を走らせられない ----------------------------------------------------

    def test_gh_failure_is_exit_2(self):
        rc, out, err = self.run_with([{"rc": 1, "stderr": "HTTP 401"}], SHA)
        self.assertEqual(rc, 2, out + err)
        self.assertIn("HTTP 401", err)

    def test_unparseable_output_is_exit_2(self):
        rc, out, err = self.run_with([{"rc": 0, "stdout": "not json"}], SHA)
        self.assertEqual(rc, 2, out + err)

    def test_missing_gh_is_exit_2(self):
        empty = self.state / "empty"
        empty.mkdir()
        rc, out, err = self.run_with([listing()], SHA, path=str(empty))
        self.assertEqual(rc, 2, out + err)
        self.assertIn("gh", err)


if __name__ == "__main__":
    unittest.main()
