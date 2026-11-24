# /// script
# requires-python = ">=3.11"
# dependencies = ["anthropic==0.79.0", "PyYAML==6.0.3"]
# ///
"""triage の job の入口。select の input.json と呼び出し側の設定から要求を組み、Messages API とのループを回す。

書くもの: 提出された報告 (report.json) か失敗の種類だけの JSON の文字列 (failure.json) のどちらか一方と、
道具の呼び出しの一覧と usage (tool-log.json)。想定の外の例外も internal-error として failure.json に残す。
GITHUB_TOKEN は渡されない (release notes は認証なしで引く)。
"""

import os
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

TB_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TB_ROOT))

from tech_briefing import jobio  # noqa: E402
from tech_briefing.config import load_config  # noqa: E402
from tech_briefing.constants import FAIL_INTERNAL_ERROR  # noqa: E402
from tech_briefing.tools import RESULT_LIMIT, ToolBox  # noqa: E402
from tech_briefing.triage import LIMITS, SYSTEM_PROMPT, Outcome, build_params, run_triage  # noqa: E402
from tech_briefing.workflow_def import INPUT_FILE  # noqa: E402

REPORT_FILE = "report.json"
FAILURE_FILE = "failure.json"
TOOL_LOG_FILE = "tool-log.json"


def _triage(env, deps) -> Outcome:
    out = Path(env["TB_OUT_DIR"])
    caller = Path(env["TB_CALLER_DIR"])
    user_input = jobio.read_json(out / INPUT_FILE)
    raw = jobio.load_yaml_under(caller, env["TB_CONFIG_PATH"], deps.load_yaml)
    # select が同じ設定を検証済みなので、ここでの誤りは想定の外 (internal-error) として上げる
    config = load_config(raw)
    criteria = jobio.read_under(caller, config.criteria)
    box = ToolBox(caller, config, deps.fetcher, RESULT_LIMIT)
    params = build_params(SYSTEM_PROMPT, criteria, user_input, box)
    return run_triage(deps.messages, params, box, LIMITS)


def main(env, deps) -> int:
    out = Path(env["TB_OUT_DIR"])
    try:
        outcome = _triage(env, deps)
    except Exception:
        traceback.print_exc()
        jobio.write_json(out / FAILURE_FILE, FAIL_INTERNAL_ERROR)
        print(f"triage の結果: トリアージできなかった: {FAIL_INTERNAL_ERROR}")
        return 1
    jobio.write_json(out / TOOL_LOG_FILE, {"tool_log": outcome.tool_log, "usage": outcome.usage})
    usage = ", ".join(f"{k}={v}" for k, v in outcome.usage.items())
    jobio.append_summary(env, f"triage の usage: {usage}")
    if outcome.report is not None:
        jobio.write_json(out / REPORT_FILE, outcome.report)
        print(f"triage の結果: 報告を受け取りました ({len(outcome.report['items'])} 項目)")
        return 0
    jobio.write_json(out / FAILURE_FILE, outcome.failure)
    print(f"triage の結果: トリアージできなかった: {outcome.failure}")
    return 1


def _real_deps() -> SimpleNamespace:
    from adapters.anthropic_client import SdkMessages
    from adapters.fetcher import UrllibFetcher
    from adapters.yaml_loader import load

    return SimpleNamespace(messages=SdkMessages(), fetcher=UrllibFetcher(), load_yaml=load)


if __name__ == "__main__":
    sys.exit(main(os.environ, _real_deps()))
