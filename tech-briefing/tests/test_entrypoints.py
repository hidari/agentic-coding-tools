"""入口のスクリプト (bin/run_*.py) を Fake で通しに回す。

スクリプトは importlib でファイルから読み、`main(env, deps)` に Fake と一時ディレクトリを渡す。
`__name__` が `__main__` にならないので、実物のアダプタを組み立てる部分は走らない。env は workflow の
dict のその step の env の鍵と runner が渡す2つの鍵だけを持たせ、宣言していない名前を読めば落とす。
artifact は workflow と同じ置き方 (select の出力 → triage の TB_OUT_DIR、post の2つのディレクトリ) で受け渡す。
"""

import builtins
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from fakes import FakeFetcher, FakeGitHub, FakeMessages  # noqa: E402
from tech_briefing import jobio  # noqa: E402
from tech_briefing.constants import BOT_LOGIN, MARKER  # noqa: E402
from tech_briefing.protocols import CommentRef, IssueRef  # noqa: E402
from tech_briefing.render import render_failure, render_report, render_too_large, render_unexpected  # noqa: E402
from tech_briefing.selection import MAX_INPUT_CHARS  # noqa: E402
from tech_briefing.workflow_def import INPUT_FILE, WORKFLOW_FILE, workflow  # noqa: E402

TB = Path(__file__).resolve().parent.parent
RUNNER_KEYS = {"GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY"}
AUTHOR = "briefer"
CALLER_REPO = "acme/app"
MECH_REPO = "mech/shared"
SHA = "a" * 40
NOW = datetime(2026, 11, 24, 0, 0, tzinfo=timezone.utc)
TITLE = "[tech-briefing] 2026-11-24"
CALLER_WORKFLOW = ".github/workflows/briefing.yml"
CONFIG = {
    "criteria": "docs/criteria.md",
    "official_sources": ["docs.example.org"],
    "files": [{"glob": "package.json", "search": True}],
}
REPORT = {
    "items": [{"title": "lodash の更新", "classification": "C", "findings": "情報のみ", "sources": ["docs.example.org"]}],
    "unreachable_sources": [],
    "notes": "",
}
CRITERIA_TEXT = "利用側の基準の目印"
USAGE = {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}


def caller_workflow(mech_repo=MECH_REPO):
    return {
        "on": {"workflow_dispatch": {}},
        "permissions": {},
        "jobs": {
            "triage": {
                "uses": f"{mech_repo}/{WORKFLOW_FILE}@{SHA}",
                "permissions": {"contents": "read", "issues": "write"},
                "secrets": {"ANTHROPIC_API_KEY": "x"},
                "with": {"config-path": "tb.yml"},
            }
        },
    }


def submit_response(report=REPORT):
    block = {"type": "tool_use", "id": "t1", "name": "submit_report", "input": report}
    return {"content": [block], "stop_reason": "tool_use", "usage": dict(USAGE)}


def end_turn_response():
    return {"content": [{"type": "text", "text": "終わり"}], "stop_reason": "end_turn", "usage": dict(USAGE)}


def issue(number=7, author=AUTHOR, title=TITLE, created="2026-11-24T00:00:00Z", body="本文"):
    return IssueRef(number, title, author, created, body)


class StrictEnv(dict):
    """宣言した鍵だけを持つ env。get でも、無い鍵を読めば落とす (黙って既定値に倒れないように)。"""

    def get(self, key, default=None):
        return self[key]


def step_env_keys(job: str) -> set[str]:
    step = next(s for s in workflow()["jobs"][job]["steps"] if s.get("id") == job)
    return set(step["env"]) | RUNNER_KEYS


def load_script(name: str):
    path = TB / "bin" / f"run_{name}.py"
    spec = importlib.util.spec_from_file_location(f"tb_entry_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.caller = self.tmp / "caller"
        self.write(self.caller / "tb.yml", json.dumps(CONFIG))
        self.write(self.caller / CALLER_WORKFLOW, json.dumps(caller_workflow()))
        self.write(self.caller / "docs" / "criteria.md", CRITERIA_TEXT)
        self.write(self.caller / "package.json", '{"dependencies": {"lodash": "4.17.21"}}\n')
        self.select_out = self.tmp / "select-out"
        self.triage_out = self.tmp / "triage-out"
        self.post_select = self.tmp / "post" / "select"
        self.post_triage = self.tmp / "post" / "triage"
        self.summary = self.tmp / "summary.md"
        self.output = self.tmp / "output.txt"

    def write(self, path: Path, text: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def call(self, name: str, env, deps) -> int:
        # スクリプトの1行の要約 (stdout) と内部のログ (stderr) を受け、テストの出力に混ぜない
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(self.stdout), contextlib.redirect_stderr(self.stderr):
            return load_script(name).main(env, deps)

    def env(self, job: str, **values) -> StrictEnv:
        values = {"GITHUB_OUTPUT": str(self.output), "GITHUB_STEP_SUMMARY": str(self.summary), **values}
        # テストの env は workflow が渡す鍵と一致させる。workflow の側が変われば、ここで先に落ちる
        self.assertEqual(set(values), step_env_keys(job))
        return StrictEnv(values)

    # --- 各 job ---

    def run_select(self, github, config_path="tb.yml", workflow_ref=None, author=AUTHOR, mech_repo=MECH_REPO):
        env = self.env(
            "select",
            GITHUB_TOKEN="unused",
            TB_REPOSITORY=CALLER_REPO,
            TB_OUT_DIR=str(self.select_out),
            TB_CONFIG_PATH=config_path,
            TB_AUTHOR=author,
            TB_CALLER_DIR=str(self.caller),
            TB_CALLER_WORKFLOW_REF=workflow_ref or f"{CALLER_REPO}/{CALLER_WORKFLOW}@refs/heads/main",
            TB_WORKFLOW_REPOSITORY=mech_repo,
            TB_WORKFLOW_SHA=SHA,
        )
        deps = SimpleNamespace(github=github, load_yaml=json.loads, now=lambda: NOW)
        return self.call("select", env, deps)

    def run_triage(self, messages):
        # download-artifact は select の artifact を TB_OUT_DIR に展開する
        self.triage_out.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.select_out / INPUT_FILE, self.triage_out / INPUT_FILE)
        env = self.env(
            "triage",
            ANTHROPIC_API_KEY="unused",
            TB_REPOSITORY=CALLER_REPO,
            TB_OUT_DIR=str(self.triage_out),
            TB_CONFIG_PATH="tb.yml",
            TB_CALLER_DIR=str(self.caller),
        )
        self.messages = FakeMessages(messages)
        deps = SimpleNamespace(messages=self.messages, fetcher=FakeFetcher(), load_yaml=json.loads)
        return self.call("triage", env, deps)

    def stage_post(self, with_triage=True):
        # select の artifact と triage の artifact を別のディレクトリに受ける (workflow_def の POST_*_DIR)
        self.post_select.mkdir(parents=True, exist_ok=True)
        if (self.select_out / INPUT_FILE).exists():
            shutil.copy(self.select_out / INPUT_FILE, self.post_select / INPUT_FILE)
        if with_triage:
            self.post_triage.mkdir(parents=True, exist_ok=True)
            for name in ("report.json", "failure.json", "tool-log.json"):
                if (self.triage_out / name).exists():
                    shutil.copy(self.triage_out / name, self.post_triage / name)

    def run_post(self, github, select_result, dry_run="false", triage_result="success"):
        env = self.env(
            "post",
            GITHUB_TOKEN="unused",
            TB_REPOSITORY=CALLER_REPO,
            TB_OUT_DIR=str(self.tmp / "post"),
            TB_SELECT_DIR=str(self.post_select),
            TB_TRIAGE_DIR=str(self.post_triage),
            TB_DRY_RUN=dry_run,
            TB_AUTHOR=AUTHOR,
            TB_SELECT_RESULT=select_result,
            TB_TRIAGE_RESULT=triage_result,
        )
        return self.call("post", env, SimpleNamespace(github=github))

    # --- 読み取り ---

    def outputs(self) -> str:
        return self.output.read_text(encoding="utf-8") if self.output.exists() else ""

    def summary_text(self) -> str:
        return self.summary.read_text(encoding="utf-8") if self.summary.exists() else ""

    def read_json(self, path: Path):
        return json.loads(path.read_text(encoding="utf-8"))


class Import(unittest.TestCase):
    def test_loading_scripts_does_not_import_runtime_deps(self):
        watched = ("anthropic", "yaml", "adapters")
        before = {m for m in sys.modules if m.split(".")[0] in watched}
        for name in ("select", "triage", "post"):
            with self.subTest(name):
                module = load_script(name)
                self.assertTrue(callable(module.main))
        after = {m for m in sys.modules if m.split(".")[0] in watched}
        self.assertEqual(after - before, set())


class Select(Harness):
    def test_triage_writes_input_and_result(self):
        rc = self.run_select(FakeGitHub([issue()], {7: [CommentRef(1, AUTHOR, "コメント", "2026-11-24T00:01:00Z")]}))
        self.assertEqual(rc, 0)
        self.assertEqual(self.outputs(), "result=triage\n")
        self.assertEqual(len(self.stdout.getvalue().splitlines()), 1)
        data = self.read_json(self.select_out / INPUT_FILE)
        self.assertEqual(data["issue_number"], 7)
        self.assertEqual([c["body"] for c in data["comments"]], ["コメント"])
        self.assertIn("triage", self.summary_text())

    def test_unexpected_and_too_large_write_target_number(self):
        cases = {
            "unexpected": FakeGitHub([issue(7), issue(8, created="2026-11-24T01:00:00Z")], {8: []}),
            "input-too-large": FakeGitHub([issue(7, body="x" * (MAX_INPUT_CHARS + 1))], {7: []}),
        }
        for result, github in cases.items():
            with self.subTest(result):
                self.setUp()
                self.assertEqual(self.run_select(github), 0)
                self.assertEqual(self.outputs(), f"result={result}\n")
                expected = 8 if result == "unexpected" else 7
                self.assertEqual(self.read_json(self.select_out / INPUT_FILE), {"issue_number": expected})

    def test_results_without_post_write_no_input(self):
        cases = {
            "no-issue": (FakeGitHub([]), 0),
            "wrong-author": (FakeGitHub([issue(author="someone")]), 1),
            "title-mismatch": (FakeGitHub([issue(title=TITLE + " (2)")]), 1),
            "already-processed": (FakeGitHub([issue()], {7: [CommentRef(1, BOT_LOGIN, MARKER, "2026-11-24T00:01:00Z")]}), 0),
        }
        for result, (github, rc) in cases.items():
            with self.subTest(result):
                self.setUp()
                self.assertEqual(self.run_select(github), rc)
                self.assertEqual(self.outputs(), f"result={result}\n")
                self.assertFalse((self.select_out / INPUT_FILE).exists())
                self.assertIn(result, self.summary_text())

    def test_config_errors_are_red_and_do_not_touch_github(self):
        cases = {
            "missing config": {"config_path": "missing.yml"},
            "config outside caller": {"config_path": "../tb.yml"},
            "absolute config": {"config_path": str(self.caller / "tb.yml")},
            "empty config path": {"config_path": ""},
            "workflow of another repo": {"workflow_ref": f"other/app/{CALLER_WORKFLOW}@refs/heads/main"},
            "workflow outside workflows dir": {"workflow_ref": f"{CALLER_REPO}/tb.yml@refs/heads/main"},
            "workflow ref without ref": {"workflow_ref": f"{CALLER_REPO}/{CALLER_WORKFLOW}"},
            "calls another mechanism": {"mech_repo": "evil/shared"},
            "empty author": {"author": ""},
        }
        for label, kwargs in cases.items():
            with self.subTest(label):
                self.setUp()
                github = FakeGitHub([issue()])
                self.assertEqual(self.run_select(github, **kwargs), 1)
                self.assertEqual(self.outputs(), "result=config-error\n")
                self.assertFalse((self.select_out / INPUT_FILE).exists())
                self.assertEqual(github.calls, [])

    def test_unparseable_config_is_config_error(self):
        self.write(self.caller / "tb.yml", "{not json")
        self.assertEqual(self.run_select(FakeGitHub([issue()])), 1)
        self.assertEqual(self.outputs(), "result=config-error\n")

    def test_control_valid_inputs_reach_github(self):
        # 対照: 上の config-error は入力の誤りで起きていて、配線の誤りで常に起きているのではない
        github = FakeGitHub([])
        self.assertEqual(self.run_select(github), 0)
        self.assertEqual(github.calls, [("open_issues",)])


class Triage(Harness):
    def select_triage(self):
        self.assertEqual(self.run_select(FakeGitHub([issue()], {7: []})), 0)

    def test_report_writes_report_and_tool_log(self):
        self.select_triage()
        self.assertEqual(self.run_triage([submit_response()]), 0)
        self.assertEqual(self.read_json(self.triage_out / "report.json"), REPORT)
        self.assertFalse((self.triage_out / "failure.json").exists())
        log = self.read_json(self.triage_out / "tool-log.json")
        self.assertEqual(log["tool_log"], [{"kind": "client", "name": "submit_report", "input": REPORT}])
        self.assertEqual(log["usage"]["input_tokens"], 10)
        self.assertIn("input_tokens", self.summary_text())
        # 入力は select が書いた input.json から組み立てる
        sent = self.messages.requests[0]["messages"][0]["content"][0]["text"]
        self.assertIn('"issue_number": 7', sent)
        self.assertIn(CRITERIA_TEXT, json.dumps(self.messages.requests[0]["system"], ensure_ascii=False))

    def test_failure_writes_kind_and_tool_log(self):
        self.select_triage()
        self.assertEqual(self.run_triage([end_turn_response()]), 1)
        self.assertEqual(self.read_json(self.triage_out / "failure.json"), "no-report")
        self.assertFalse((self.triage_out / "report.json").exists())
        self.assertTrue((self.triage_out / "tool-log.json").exists())
        self.assertIn("input_tokens", self.summary_text())

    def test_unexpected_error_is_internal_error(self):
        self.select_triage()
        (self.caller / "docs" / "criteria.md").unlink()
        self.assertEqual(self.run_triage([submit_response()]), 1)
        self.assertEqual(self.read_json(self.triage_out / "failure.json"), "internal-error")
        self.assertEqual(self.messages.requests, [])
        self.assertIn("Traceback", self.stderr.getvalue())

    def test_criteria_outside_caller_is_internal_error(self):
        self.select_triage()
        self.write(self.tmp / "secret.md", "外の文書")
        self.write(self.caller / "tb.yml", json.dumps(dict(CONFIG, criteria="../secret.md")))
        self.assertEqual(self.run_triage([submit_response()]), 1)
        self.assertEqual(self.read_json(self.triage_out / "failure.json"), "internal-error")
        self.assertEqual(self.messages.requests, [])


class Post(Harness):
    def chain(self, responses):
        github = FakeGitHub([issue()], {7: []})
        self.assertEqual(self.run_select(github), 0)
        self.run_triage(responses)
        self.stage_post()
        return github

    def test_chain_posts_report_to_select_target(self):
        github = self.chain([submit_response()])
        self.assertEqual(self.run_post(github, "triage"), 0)
        self.assertEqual(github.posted, [(7, render_report(REPORT))])

    def test_chain_posts_failure_and_is_red(self):
        github = self.chain([end_turn_response()])
        self.assertEqual(self.run_post(github, "triage", triage_result="failure"), 1)
        self.assertEqual(github.posted, [(7, render_failure("no-report"))])

    def test_target_comes_only_from_select_dir(self):
        github = self.chain([submit_response()])
        # LLM の job が書ける側に、別の番号の input.json を置く
        (self.post_triage / INPUT_FILE).write_text(json.dumps({"issue_number": 99}), encoding="utf-8")
        opened = []
        real_open = builtins.open

        def recording_open(file, *args, **kwargs):
            opened.append(Path(file).resolve())
            return real_open(file, *args, **kwargs)

        with mock.patch.object(builtins, "open", recording_open):
            self.assertEqual(self.run_post(github, "triage"), 0)
        self.assertEqual([n for n, _ in github.posted], [7])
        self.assertNotIn((self.post_triage / INPUT_FILE).resolve(), opened)
        # 対照: 記録は働いていて、select の側の input.json は開いている
        self.assertIn((self.post_select / INPUT_FILE).resolve(), opened)

    def test_dry_run_unless_exactly_false(self):
        for value in ("true", "", "False", "0", "no"):
            with self.subTest(value):
                self.setUp()
                github = self.chain([submit_response()])
                self.assertEqual(self.run_post(github, "triage", dry_run=value), 0)
                self.assertEqual(github.posted, [])
                self.assertIn(render_report(REPORT), self.summary_text())

    def test_already_marked_is_not_posted_again(self):
        github = self.chain([submit_response()])
        github._comments[7] = [CommentRef(1, BOT_LOGIN, MARKER + "\n先の報告", "2026-11-24T00:05:00Z")]
        self.assertEqual(self.run_post(github, "triage"), 0)
        self.assertEqual(github.posted, [])

    def test_missing_triage_artifact_is_internal_error(self):
        github = FakeGitHub([issue()], {7: []})
        self.assertEqual(self.run_select(github), 0)
        self.stage_post(with_triage=False)
        self.assertEqual(self.run_post(github, "triage", triage_result="failure"), 1)
        self.assertEqual(github.posted, [(7, render_failure("internal-error"))])

    def test_malformed_triage_artifacts_are_internal_error(self):
        cases = {
            "report not json": {"report.json": "{"},
            "report not object": {"report.json": "[]"},
            "failure not string": {"failure.json": '{"kind": "no-report"}'},
        }
        for label, files in cases.items():
            with self.subTest(label):
                self.setUp()
                github = FakeGitHub([issue()], {7: []})
                self.assertEqual(self.run_select(github), 0)
                self.stage_post(with_triage=False)
                self.post_triage.mkdir(parents=True)
                for name, text in files.items():
                    (self.post_triage / name).write_text(text, encoding="utf-8")
                self.assertEqual(self.run_post(github, "triage"), 1)
                self.assertEqual(github.posted, [(7, render_failure("internal-error"))])

    def test_unexpected_and_too_large_are_posted_and_red(self):
        cases = {
            "unexpected": (FakeGitHub([issue(7), issue(8, created="2026-11-24T01:00:00Z")], {8: []}), 8, render_unexpected()),
            "input-too-large": (FakeGitHub([issue(7, body="x" * (MAX_INPUT_CHARS + 1))], {7: []}), 7, render_too_large()),
        }
        for result, (github, number, body) in cases.items():
            with self.subTest(result):
                self.setUp()
                self.assertEqual(self.run_select(github), 0)
                self.stage_post(with_triage=False)
                self.assertEqual(self.run_post(github, result, triage_result="skipped"), 1)
                self.assertEqual(github.posted, [(number, body)])

    def test_missing_or_bad_select_input_posts_nothing(self):
        cases = {"missing": None, "not json": "{", "no number": "{}", "bool number": '{"issue_number": true}', "zero": '{"issue_number": 0}'}
        for label, text in cases.items():
            with self.subTest(label):
                self.setUp()
                self.post_select.mkdir(parents=True)
                if text is not None:
                    (self.post_select / INPUT_FILE).write_text(text, encoding="utf-8")
                github = FakeGitHub([issue()], {7: []})
                self.assertEqual(self.run_post(github, "triage"), 1)
                self.assertNotEqual(self.stderr.getvalue(), "")
                self.assertEqual(github.calls, [])


class JobIo(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_write_json_keeps_lone_surrogate_as_escape(self):
        lone = chr(0xDC80)
        path = self.tmp / "a" / "x.json"
        jobio.write_json(path, {"v": f"日本{lone}"})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"v": f"日本{lone}"})
        self.assertIn("日本", path.read_text(encoding="utf-8"))

    def test_write_output_rejects_newline(self):
        env = {"GITHUB_OUTPUT": str(self.tmp / "out")}
        with self.assertRaises(ValueError):
            jobio.write_output(env, "result", "a\nb=c")

    def test_caller_workflow_path(self):
        cases = {
            f"{CALLER_REPO}/{CALLER_WORKFLOW}@refs/heads/main": CALLER_WORKFLOW,
            f"{CALLER_REPO}/.github/workflows/../../tb.yml@refs/heads/main": None,
            f"{CALLER_REPO}x/{CALLER_WORKFLOW}@refs/heads/main": None,
            "": None,
        }
        for ref, expected in cases.items():
            with self.subTest(ref):
                self.assertEqual(jobio.caller_workflow_path(ref, CALLER_REPO), expected)

    def test_read_under_rejects_symlink_out(self):
        root = self.tmp / "root"
        root.mkdir()
        (self.tmp / "outside.txt").write_text("外", encoding="utf-8")
        (root / "link.txt").symlink_to(self.tmp / "outside.txt")
        with self.assertRaises(ValueError):
            jobio.read_under(root, "link.txt")


if __name__ == "__main__":
    unittest.main()
