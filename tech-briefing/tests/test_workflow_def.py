"""reusable workflow の配線を dict の上で固定する。

job の権限と鍵の置き場がこの仕組みの守りの上限なので、ここが緩むと LLM の job が
Issue に書けたり、書き込みの job が鍵を持ったりする。YAML のパーサを使わずに済むよう、
生成物は JSON の文書として書き、dict と読み戻した結果の一致で生成物の側も固定する。
"""

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG_ROOT = os.path.join(HERE, "..")
sys.path.insert(0, PKG_ROOT)

from tech_briefing.constants import (  # noqa: E402
    SELECT_INPUT_TOO_LARGE,
    SELECT_TRIAGE,
    SELECT_UNEXPECTED,
)
from tech_briefing.workflow_def import (  # noqa: E402
    FAILURE_FILE,
    REPORT_FILE,
    TOOL_LOG_FILE,
    TRIAGE_FILES,
    UV_VERSION,
    render,
    workflow,
)

REPO_ROOT = os.path.abspath(os.path.join(PKG_ROOT, ".."))
GENERATED = os.path.join(REPO_ROOT, ".github", "workflows", "tech-briefing-triage.yml")
GENERATOR = os.path.join(PKG_ROOT, "bin", "gen_workflow.py")

EXPR = "${{"
MECH_REPO = "${{ job.workflow_repository }}"
MECH_SHA = "${{ job.workflow_sha }}"
CHECKOUT = "actions/checkout"
UPLOAD = "actions/upload-artifact"
DOWNLOAD = "actions/download-artifact"
SETUP_UV = "astral-sh/setup-uv"
OUT = "${{ runner.temp }}/tech-briefing"
SHA_RE = re.compile(r"[0-9a-f]{40}")


def jobs():
    return workflow()["jobs"]


def steps(job):
    return jobs()[job]["steps"]


def uses_name(step):
    # `uses:` の値は `<action>@<sha>`。記法の検査が `@` の連なりに反応するので、
    # テストの中にはその形のリテラルを書かず、値を分けて比べる
    return step.get("uses", "").split("@", 1)[0]


def is_mechanism_checkout(step):
    return uses_name(step) == CHECKOUT and step.get("with", {}).get("repository") == MECH_REPO


def guard_index(job_steps):
    """共通の仕組みの repository と SHA の両方を env で受け、空なら止める step の位置。"""
    for i, s in enumerate(job_steps):
        env = s.get("env", {})
        values = set(env.values())
        if MECH_REPO in values and MECH_SHA in values and "run" in s:
            run = s["run"]
            names = [k for k, v in env.items() if v in (MECH_REPO, MECH_SHA)]
            if all(f'-z "${n}"' in run for n in names) and "exit 1" in run:
                return i
    return None


class Permissions(unittest.TestCase):
    def test_jobs_are_select_triage_post(self):
        self.assertEqual(list(jobs()), ["select", "triage", "post"])

    def test_top_level_permissions_are_empty(self):
        self.assertEqual(workflow()["permissions"], {})

    def test_job_permission_table(self):
        self.assertEqual(
            {name: job["permissions"] for name, job in jobs().items()},
            {
                "select": {"contents": "read", "issues": "read"},
                "triage": {"contents": "read"},
                "post": {"contents": "read", "issues": "write"},
            },
        )


class Secrets(unittest.TestCase):
    def test_api_key_is_required_on_workflow_call(self):
        self.assertIs(workflow()["on"]["workflow_call"]["secrets"]["ANTHROPIC_API_KEY"]["required"], True)

    def test_only_triage_references_any_secret(self):
        # 鍵を triage 以外のどこにも書かない。with: や if: に書いた場合も拾うため、
        # triage を除いた文書全体を文字列にして探す。`secrets['X']` や `toJSON(secrets)` の形でも
        # 引けるので、`secrets.` ではなく語そのものを探す
        doc = workflow()
        others = {k: v for k, v in doc.items() if k not in ("jobs", "on")}
        others["jobs"] = {k: v for k, v in doc["jobs"].items() if k != "triage"}
        self.assertNotIn("secrets", json.dumps(others))
        # triage でも鍵1つだけを1か所で引く。secrets.GITHUB_TOKEN などを足すと赤になる
        triage = json.dumps(doc["jobs"]["triage"])
        self.assertEqual(triage.count("secrets"), 1)
        self.assertIn("secrets.ANTHROPIC_API_KEY", triage)

    def test_triage_receives_key_only_in_its_run_step_env(self):
        holders = [s for s in steps("triage") if "secrets" in json.dumps(s)]
        self.assertEqual(len(holders), 1)
        self.assertEqual(holders[0]["env"]["ANTHROPIC_API_KEY"], "${{ secrets.ANTHROPIC_API_KEY }}")
        self.assertIn("run_triage.py", holders[0]["run"])

    def test_triage_has_environment(self):
        self.assertEqual(jobs()["triage"]["environment"], "tech-briefing")
        self.assertNotIn("environment", jobs()["select"])
        self.assertNotIn("environment", jobs()["post"])

    def test_triage_does_not_see_github_token(self):
        # triage は読み取りだけの job で、GitHub の API を呼ばない。token を env に渡すと
        # LLM の道具の実装から使える経路が増える。`github['token']` の形でも引けるので、
        # 式の形ではなく語そのものを大小を区別せずに探す
        self.assertNotIn("token", json.dumps(jobs()["triage"]).lower())

    def test_no_secrets_inherit(self):
        self.assertNotIn("inherit", json.dumps(workflow()))


class RunSteps(unittest.TestCase):
    def test_no_expression_inside_run(self):
        for name, job in jobs().items():
            for s in job["steps"]:
                if "run" in s:
                    self.assertNotIn(EXPR, s["run"], f"{name}: {s.get('name')}")

    def test_no_custom_shell(self):
        for job in jobs().values():
            for s in job["steps"]:
                self.assertNotIn("shell", s)

    def test_each_job_runs_its_fixed_script(self):
        for name in ("select", "triage", "post"):
            script_runs = [s["run"] for s in steps(name) if "run" in s and "uv run" in s["run"]]
            self.assertEqual(script_runs, [f"uv run --script tech-briefing/bin/run_{name}.py"], name)

    def test_triage_runs_nothing_but_the_guard_and_its_script(self):
        # LLM の job のシェルの面を、決まった2行に閉じる
        guard = steps("triage")[guard_index(steps("triage"))]["run"]
        runs = [s["run"] for s in steps("triage") if "run" in s]
        self.assertEqual(runs, [guard, "uv run --script tech-briefing/bin/run_triage.py"])


class Uses(unittest.TestCase):
    def test_every_uses_is_pinned_by_full_sha(self):
        seen = 0
        for job in jobs().values():
            for s in job["steps"]:
                if "uses" in s:
                    seen += 1
                    _, _, ref = s["uses"].partition("@")
                    self.assertTrue(SHA_RE.fullmatch(ref), s["uses"])
        # 0 件で緑にならないよう、checkout と artifact と uv の分があることを見る
        self.assertGreater(seen, 0)

    def test_checkout_sha_matches_ci(self):
        # 同じ action を2つの版で持たない。ci.yml の固定を canonical にする
        with open(os.path.join(REPO_ROOT, ".github", "workflows", "ci.yml"), encoding="utf-8") as f:
            ci = f.read()
        ci_refs = set(re.findall(r"actions/checkout.([0-9a-f]{40})", ci))
        self.assertEqual(len(ci_refs), 1)
        for job in jobs().values():
            for s in job["steps"]:
                if uses_name(s) == CHECKOUT:
                    self.assertEqual(s["uses"].partition("@")[2], next(iter(ci_refs)))

    def test_setup_uv_pins_version_and_disables_cache(self):
        # cache は鍵を持つ job でもほかの run の中身を復元するので切る
        for name in ("select", "triage", "post"):
            uv = [s for s in steps(name) if uses_name(s) == SETUP_UV]
            self.assertEqual(len(uv), 1, name)
            self.assertEqual(uv[0]["with"], {"version": UV_VERSION, "enable-cache": False}, name)

    def test_every_checkout_drops_credentials(self):
        checkouts = [s for job in jobs().values() for s in job["steps"] if uses_name(s) == CHECKOUT]
        self.assertGreater(len(checkouts), 0)
        for s in checkouts:
            self.assertIs(s["with"]["persist-credentials"], False)


class Artifacts(unittest.TestCase):
    def upload_path(self, job):
        ups = [s for s in steps(job) if uses_name(s) == UPLOAD]
        self.assertEqual(len(ups), 1, job)
        return ups[0]["with"]["path"]

    def test_select_uploads_only_input(self):
        self.assertEqual(self.upload_path("select"), f"{OUT}/input.json")

    def test_triage_uploads_exactly_its_three_files(self):
        # LLM の job が post の入力 (input.json) を上書きできないよう、渡すファイルを閉じる
        # 名前は入口のスクリプトも同じ定数から取る (書く名前・upload する名前・読む名前の一致は test_entrypoints が見る)
        self.assertEqual(TRIAGE_FILES, (REPORT_FILE, FAILURE_FILE, TOOL_LOG_FILE))
        self.assertEqual(TRIAGE_FILES, ("report.json", "failure.json", "tool-log.json"))
        self.assertEqual(self.upload_path("triage"), "\n".join(f"{OUT}/{f}" for f in TRIAGE_FILES))

    def test_post_extracts_artifacts_into_separate_dirs(self):
        downloads = {s["with"]["name"]: s["with"]["path"] for s in steps("post") if uses_name(s) == DOWNLOAD}
        self.assertEqual(
            downloads,
            {"tech-briefing-select": f"{OUT}/select", "tech-briefing-triage": f"{OUT}/triage"},
        )
        env = next(s for s in steps("post") if "run" in s and "uv run" in s["run"])["env"]
        self.assertEqual(env["TB_SELECT_DIR"], f"{OUT}/select")
        self.assertEqual(env["TB_TRIAGE_DIR"], f"{OUT}/triage")


class MechanismCheckout(unittest.TestCase):
    def test_every_job_checks_out_mechanism_after_guard(self):
        for name in ("select", "triage", "post"):
            job_steps = steps(name)
            mech = [i for i, s in enumerate(job_steps) if is_mechanism_checkout(s)]
            self.assertEqual(len(mech), 1, name)
            self.assertEqual(job_steps[mech[0]]["with"]["ref"], MECH_SHA, name)
            g = guard_index(job_steps)
            self.assertIsNotNone(g, name)
            self.assertLess(g, mech[0], name)

    def test_caller_checkout_only_in_select_and_triage(self):
        for name, expected in (("select", 1), ("triage", 1), ("post", 0)):
            caller = [
                s for s in steps(name) if uses_name(s) == CHECKOUT and "repository" not in s.get("with", {})
            ]
            self.assertEqual(len(caller), expected, name)
            for s in caller:
                mech_path = next(x for x in steps(name) if is_mechanism_checkout(x))["with"]["path"]
                self.assertNotEqual(s["with"]["path"], mech_path)

    def test_scripts_run_inside_mechanism_checkout(self):
        for name in ("select", "triage", "post"):
            mech_path = next(s for s in steps(name) if is_mechanism_checkout(s))["with"]["path"]
            for s in steps(name):
                if "run" in s and "uv run" in s["run"]:
                    self.assertEqual(s["working-directory"], mech_path, name)


class Flow(unittest.TestCase):
    def test_select_exposes_result_from_a_step(self):
        out = jobs()["select"]["outputs"]["result"]
        m = re.fullmatch(r"\$\{\{ steps\.([\w-]+)\.outputs\.result \}\}", out)
        self.assertIsNotNone(m)
        self.assertIn(m.group(1), [s.get("id") for s in steps("select")])

    def test_triage_runs_only_on_triage_result(self):
        self.assertEqual(jobs()["triage"]["needs"], ["select"])
        self.assertEqual(jobs()["triage"]["if"], f"needs.select.outputs.result == '{SELECT_TRIAGE}'")

    def test_post_condition(self):
        post = jobs()["post"]
        self.assertEqual(post["needs"], ["select", "triage"])
        # 部分文字列の有無では && と || の取り違えや括弧の欠けが見えないので、全体を比べる。
        # always() は run の取り消しも無視して書き込みの job を走らせるので、!cancelled() で
        # 前の job の失敗だけを越える
        posted = " || ".join(
            f"needs.select.outputs.result == '{r}'"
            for r in (SELECT_TRIAGE, SELECT_UNEXPECTED, SELECT_INPUT_TOO_LARGE)
        )
        self.assertEqual(
            post["if"],
            f"!cancelled() && needs.select.result == 'success' && ({posted}) "
            "&& needs.triage.result != 'cancelled'",
        )

    def test_concurrency_is_per_caller_and_not_cancelling(self):
        # run ごとに変わる値が混ざると run が並ぶので、group 全体を比べる
        self.assertEqual(
            workflow()["concurrency"],
            {"group": "${{ github.repository }}-tech-briefing-triage", "cancel-in-progress": False},
        )

    def test_every_job_has_positive_timeout(self):
        for name, job in jobs().items():
            t = job.get("timeout-minutes")
            self.assertIs(type(t), int, name)
            self.assertGreater(t, 0, name)

    def test_workflow_call_is_the_only_trigger(self):
        on = workflow()["on"]
        self.assertEqual(list(on), ["workflow_call"])
        inputs = on["workflow_call"]["inputs"]
        self.assertEqual(sorted(inputs), ["config-path", "dry-run", "runner"])
        self.assertEqual(inputs["config-path"]["type"], "string")
        self.assertIs(inputs["config-path"]["required"], True)
        self.assertEqual(inputs["dry-run"]["type"], "boolean")
        self.assertIs(inputs["dry-run"]["default"], True)
        for job in jobs().values():
            self.assertEqual(job["runs-on"], "${{ inputs.runner }}")


class Generated(unittest.TestCase):
    def test_generated_file_matches_workflow(self):
        with open(GENERATED, encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(json.loads(text), workflow())
        self.assertEqual(text, render(workflow()))

    def test_render_keeps_on_as_string_key(self):
        self.assertTrue(render(workflow()).startswith("{\n"))
        self.assertIn('"on": {', render(workflow()))
        self.assertTrue(render(workflow()).endswith("}\n"))


class GeneratorCli(unittest.TestCase):
    """bin/ を import しないため、複製した木の上でスクリプトを別プロセスで動かす。

    生成器はリポジトリの root を自分の置き場から決めるので、複製の木の生成物だけを書き換え、
    本物の生成物には触らない。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        tb = os.path.join(self.tmp, "tech-briefing")
        shutil.copytree(os.path.join(PKG_ROOT, "tech_briefing"), os.path.join(tb, "tech_briefing"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        os.makedirs(os.path.join(tb, "bin"))
        shutil.copy(GENERATOR, os.path.join(tb, "bin", "gen_workflow.py"))
        self.script = os.path.join(tb, "bin", "gen_workflow.py")
        self.out = os.path.join(self.tmp, ".github", "workflows", "tech-briefing-triage.yml")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_gen(self, *args):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run([sys.executable, self.script, *args], capture_output=True, text=True, env=env)

    def test_check_fails_when_file_missing(self):
        self.assertEqual(self.run_gen("--check").returncode, 1)

    def test_write_then_check_then_one_char_drift(self):
        self.assertEqual(self.run_gen("--write").returncode, 0)
        with open(self.out, encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(text, render(workflow()))
        self.assertEqual(self.run_gen("--check").returncode, 0)
        i = text.index("issues")
        with open(self.out, "w", encoding="utf-8") as f:
            f.write(text[:i] + "J" + text[i + 1:])
        r = self.run_gen("--check")
        self.assertEqual(r.returncode, 1)
        self.assertNotEqual(r.stderr, "")

    def test_requires_exactly_one_mode(self):
        self.assertNotEqual(self.run_gen().returncode, 0)
        self.assertNotEqual(self.run_gen("--check", "--write").returncode, 0)


class BodyDoesNotImportAdapters(unittest.TestCase):
    def test_no_adapter_or_bin_import(self):
        pkg = os.path.join(PKG_ROOT, "tech_briefing")
        files = sorted(f for f in os.listdir(pkg) if f.endswith(".py"))
        self.assertIn("workflow_def.py", files)
        for name in files:
            with open(os.path.join(pkg, name), encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    mods = [node.module]
                for m in mods:
                    self.assertNotIn(m.split(".")[0], ("adapters", "bin", "anthropic", "yaml"), name)


if __name__ == "__main__":
    unittest.main()
