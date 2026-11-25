# /// script
# requires-python = ">=3.11"
# dependencies = ["PyYAML==6.0.3"]
# [tool.uv]
# exclude-newer = "2026-11-10T00:00:00Z"
# ///
"""select の job の入口。設定と呼び出しの workflow を読み、当日の Issue を判定して input.json を書く。

判定は tech_briefing.selection が持ち、ここは実物のアダプタと job の入出力をつなぐだけにする。
post へ進む結果 (triage / unexpected / input-too-large) では input.json を書く。triage では入力の全体、
ほかの2つでは投稿先の番号だけを持つ。結果の値は GITHUB_OUTPUT の result= と step summary に出す。
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

TB_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TB_ROOT))

from tech_briefing import jobio  # noqa: E402
from tech_briefing.selection import MAX_INPUT_CHARS, is_red, run_select  # noqa: E402
from tech_briefing.workflow_def import INPUT_FILE, POSTED_RESULTS, WORKFLOW_FILE  # noqa: E402


def main(env, deps) -> int:
    caller = Path(env["TB_CALLER_DIR"])
    raw_config = jobio.load_yaml_under(caller, env["TB_CONFIG_PATH"], deps.load_yaml)
    workflow_path = jobio.caller_workflow_path(env["TB_CALLER_WORKFLOW_REF"], env["TB_REPOSITORY"])
    caller_workflow = jobio.load_yaml_under(caller, workflow_path, deps.load_yaml)
    reusable = f"{env['TB_WORKFLOW_REPOSITORY']}/{WORKFLOW_FILE}"

    decision = run_select(
        raw_config,
        caller_workflow,
        reusable,
        env["TB_WORKFLOW_SHA"],
        lambda rel: jobio.read_criteria_or_none(caller, rel),
        deps.github,
        env["TB_AUTHOR"],
        deps.now(),
        MAX_INPUT_CHARS,
    )
    if decision.result in POSTED_RESULTS:
        data = decision.input if decision.input is not None else {"issue_number": decision.issue.number}
        jobio.write_json(Path(env["TB_OUT_DIR"]) / INPUT_FILE, data)
    jobio.write_output(env, "result", decision.result)
    jobio.append_summary(env, f"select の結果: {decision.summary}")
    print(f"select の結果: {decision.summary}")
    return 1 if is_red(decision.result) else 0


def _real_deps() -> SimpleNamespace:
    from adapters.github_api import GitHubRest
    from adapters.yaml_loader import load

    return SimpleNamespace(
        github=GitHubRest(os.environ["GITHUB_TOKEN"], os.environ["TB_REPOSITORY"]),
        load_yaml=load,
        now=lambda: datetime.now(timezone.utc),
    )


if __name__ == "__main__":
    sys.exit(main(os.environ, _real_deps()))
