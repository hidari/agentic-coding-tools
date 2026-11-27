"""reusable workflow の配線を dict の上で固定する。

job の権限と鍵の置き場がこの仕組みの守りの上限なので、ここが緩むと LLM の job が
Issue に書けたり、書き込みの job が鍵を持ったりする。YAML のパーサを使わずに済むよう、
生成物は JSON の文書として書き、dict と読み戻した結果の一致で生成物の側も固定する。
"""

import itertools
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
    SELECT_RESULTS,
    SELECT_TRIAGE,
    SELECT_UNEXPECTED,
)
from tech_briefing.workflow_def import (  # noqa: E402
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


# --- if の式の小さな評価器 ---
# 配線の if の式を文字列の一致ではなく振る舞いで固定するため、使っている文法だけを解く。
# 解けるのは文字列の literal、文脈の名前、cancelled()、!、==、!=、&&、||、括弧だけで、
# 知らない字句・名前・関数は例外にする (綴りの誤りを黙って偽に倒さないため)。
# 優先順位は ! が最も強く、比較、&&、|| の順 (GitHub の式の docs の演算子の表の記憶による。実物では未確認)

_TOKEN = re.compile(r"\s*(?:(?P<op>&&|\|\||==|!=|!|\(|\))|'(?P<str>[^']*)'|(?P<name>[A-Za-z_][A-Za-z0-9_.-]*))")


def _tokens(expr):
    out, pos = [], 0
    while pos < len(expr.rstrip()):
        m = _TOKEN.match(expr, pos)
        if m is None or m.end() == pos:
            raise ValueError(f"評価器が知らない字句です: {expr[pos:]!r}")
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        pos = m.end()
    return out


# 状態の関数。if の式がこのどれも含まないとき、GitHub は前に暗黙の success() && を付けて評価する
# (docs.github.com の expressions の status check functions の節の記述に基づく。実物の run では確かめていない)
_STATUS_FUNCTIONS = {"success", "failure", "always", "cancelled"}


def _with_implicit_success(tokens):
    calls = {v for (k, v), nxt in zip(tokens, tokens[1:]) if k == "name" and nxt == ("op", "(")}
    if calls & _STATUS_FUNCTIONS:
        return tokens
    return [("name", "success"), ("op", "("), ("op", ")"), ("op", "&&"), ("op", "("), *tokens, ("op", ")")]


def evaluate(expr, context, *, cancelled, success):
    """expr を context (名前から文字列)、取り消しの有無、success() の値で評価し、真偽値を返す。

    success() の値は、job の if なら needs の job がすべて成功したか、step の if なら前の step がすべて
    成功したか。既定値を持たせない (呼ぶ側が毎回決めないと、暗黙の success() の分岐が素通りする)。
    """
    tokens = _with_implicit_success(_tokens(expr))
    i = 0

    def peek():
        return tokens[i] if i < len(tokens) else (None, None)

    def take(expected=None):
        nonlocal i
        token = peek()
        if token[0] is None or (expected is not None and token != ("op", expected)):
            raise ValueError(f"式の形が違います: {expr!r} の {i} 番目")
        i += 1
        return token

    def boolean(value):
        if not isinstance(value, bool):
            raise ValueError(f"真偽値ではない値が条件に使われています: {value!r}")
        return value

    def disjunction():
        value = conjunction()
        while peek() == ("op", "||"):
            take()
            right = conjunction()
            value = boolean(value) or boolean(right)
        return value

    def conjunction():
        value = comparison()
        while peek() == ("op", "&&"):
            take()
            right = comparison()
            value = boolean(value) and boolean(right)
        return value

    def comparison():
        left = unary()
        if peek() in (("op", "=="), ("op", "!=")):
            op = take()[1]
            right = unary()
            return (left == right) if op == "==" else (left != right)
        return left

    def unary():
        if peek() == ("op", "!"):
            take()
            return not boolean(unary())
        return primary()

    def primary():
        kind, value = take()
        if (kind, value) == ("op", "("):
            inner = disjunction()
            take(")")
            return inner
        if kind == "str":
            return value
        if kind == "name":
            if peek() == ("op", "("):
                take()
                take(")")
                if value == "cancelled":
                    return cancelled
                if value == "success":
                    return success
                raise ValueError(f"評価器が知らない関数です: {value}")
            if value not in context:
                raise ValueError(f"評価器が知らない名前です: {value}")
            return context[value]
        raise ValueError(f"式の形が違います: {expr!r}")

    result = disjunction()
    if i != len(tokens):
        raise ValueError(f"式の後ろに余りがあります: {expr!r}")
    return boolean(result)


JOB_RESULTS = ("success", "failure", "cancelled", "skipped")
# post へ進む select の結果 (unexpected と input-too-large は triage を飛ばして定型のコメントにする)
POSTED = {SELECT_TRIAGE, SELECT_UNEXPECTED, SELECT_INPUT_TOO_LARGE}
# select が落ちて outputs が空のときも数える
OUTPUT_VALUES = SELECT_RESULTS + ("",)


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
            self.assertEqual(script_runs, [f"uv run --locked --script tech-briefing/bin/run_{name}.py"], name)

    def test_triage_runs_nothing_but_the_guard_and_its_script(self):
        # LLM の job のシェルの面を、決まった2行に閉じる
        guard = steps("triage")[guard_index(steps("triage"))]["run"]
        runs = [s["run"] for s in steps("triage") if "run" in s]
        self.assertEqual(runs, [guard, "uv run --locked --script tech-briefing/bin/run_triage.py"])


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

    def post_runs(self, select, output, triage, cancelled):
        context = {"needs.select.result": select, "needs.select.outputs.result": output, "needs.triage.result": triage}
        # job の success() は needs の job がすべて成功したとき真。post の if は !cancelled() を持つので
        # 暗黙の success() は付かず、triage が落ちた日も post へ進む
        return evaluate(jobs()["post"]["if"], context, cancelled=cancelled, success=select == "success" and triage == "success")

    def test_post_condition_scenarios(self):
        self.assertEqual(jobs()["post"]["needs"], ["select", "triage"])
        # 読み手が追える代表の行。下の全組み合わせの表と同じ規則の一部を名前付きで置く
        rows = [
            ("報告できた", ("success", SELECT_TRIAGE, "success", False), True),
            ("triage が落ちても失敗を投稿する", ("success", SELECT_TRIAGE, "failure", False), True),
            ("triage を飛ばした unexpected", ("success", SELECT_UNEXPECTED, "skipped", False), True),
            ("triage を飛ばした input-too-large", ("success", SELECT_INPUT_TOO_LARGE, "skipped", False), True),
            ("run の取り消しは尊重する", ("success", SELECT_TRIAGE, "success", True), False),
            ("triage の取り消しは投稿しない", ("success", SELECT_TRIAGE, "cancelled", False), False),
            ("select が赤", ("failure", "config-error", "skipped", False), False),
            ("select が赤で outputs が posted の値", ("failure", SELECT_UNEXPECTED, "skipped", False), False),
            ("処理済み", ("success", "already-processed", "skipped", False), False),
            ("当日の Issue が無い", ("success", "no-issue", "skipped", False), False),
        ]
        for label, args, expected in rows:
            with self.subTest(label):
                self.assertIs(self.post_runs(*args), expected)

    def test_post_condition_table(self):
        # 文字列の一致では同じ振る舞いの書き換えを赤にし、部分文字列では && と || の取り違えや括弧の欠けを
        # 見落とすので、全組み合わせで post へ進む集合を固定する。always() は run の取り消しも無視して
        # 書き込みの job を走らせるので、取り消しの有無も表に入れる
        for select, output, triage, cancelled in itertools.product(JOB_RESULTS, OUTPUT_VALUES, JOB_RESULTS, (False, True)):
            expected = not cancelled and select == "success" and output in POSTED and triage != "cancelled"
            with self.subTest(select=select, output=output, triage=triage, cancelled=cancelled):
                self.assertIs(self.post_runs(select, output, triage, cancelled), expected)

    def test_select_uploads_input_only_for_posted_results(self):
        (upload,) = [s for s in steps("select") if uses_name(s) == UPLOAD]
        # step の if は状態の関数を持たないので、暗黙の success() で前の step の成功も条件になる。
        # 前の step が落ちた run では、outputs が posted の値でも input.json を渡さない
        for output, previous_ok in itertools.product(OUTPUT_VALUES, (True, False)):
            with self.subTest(output=output, previous_ok=previous_ok):
                got = evaluate(upload["if"], {"steps.select.outputs.result": output}, cancelled=False, success=previous_ok)
                self.assertIs(got, previous_ok and output in POSTED)

    def test_evaluator_rejects_unknown_names_and_tokens(self):
        # 対照: 評価器が綴りの誤りや知らない文法を偽に倒さず例外にする
        for expr in ("needs.selct.result == 'success'", "always()", "a == 'x' & b", "contains(x, 'y')", "'x'"):
            with self.subTest(expr):
                with self.assertRaises(ValueError):
                    evaluate(expr, {"a": "x", "b": "y", "x": "z"}, cancelled=False, success=True)
        # 対照: && は || より強く結びつく
        self.assertIs(evaluate("'a' == 'b' && 'a' == 'b' || 'a' == 'a'", {}, cancelled=False, success=True), True)
        self.assertIs(evaluate("'a' == 'a' || 'a' == 'b' && 'a' == 'b'", {}, cancelled=False, success=True), True)
        self.assertIs(evaluate("('a' == 'a' || 'a' == 'b') && 'a' == 'b'", {}, cancelled=False, success=True), False)

    def test_evaluator_adds_implicit_success_only_without_status_function(self):
        # 状態の関数が無い式は success() && (式) として読む。|| で始まる式も括弧ごと包むので success() に掛かる
        self.assertIs(evaluate("'a' == 'a'", {}, cancelled=False, success=False), False)
        self.assertIs(evaluate("'a' == 'b' || 'a' == 'a'", {}, cancelled=False, success=False), False)
        self.assertIs(evaluate("'a' == 'a'", {}, cancelled=False, success=True), True)
        # 状態の関数があれば付けない
        self.assertIs(evaluate("!cancelled() && 'a' == 'a'", {}, cancelled=False, success=False), True)

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


if __name__ == "__main__":
    unittest.main()
