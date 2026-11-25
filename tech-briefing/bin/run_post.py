# /// script
# requires-python = ">=3.11"
# dependencies = []
# [tool.uv]
# exclude-newer = "2026-11-10T00:00:00Z"
# ///
"""post の job の入口。select と triage の artifact から本文を作り、当日の Issue に印付きのコメントを1件書く。

投稿先の番号は TB_SELECT_DIR の input.json からだけ取る。TB_TRIAGE_DIR (LLM の job が書いた artifact) からは
report.json と failure.json だけを名指しで読み、そこの input.json は開かない (LLM の job が投稿先を
差し替えられないため)。本文と終了コードは render.plan_post、投稿の直前の再確認は render.should_post が決める。
TB_DRY_RUN がちょうど "false" のときだけ投稿し、それ以外は本文を step summary に出す (誤って投稿しない側に倒す)。
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

TB_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TB_ROOT))

from tech_briefing import jobio  # noqa: E402
from tech_briefing.render import plan_post, should_post  # noqa: E402
from tech_briefing.workflow_def import FAILURE_FILE, INPUT_FILE, REPORT_FILE  # noqa: E402


def _target(select_dir: Path) -> int | None:
    data = jobio.read_json_or_none(select_dir / INPUT_FILE)
    number = data.get("issue_number") if isinstance(data, dict) else None
    if isinstance(number, int) and not isinstance(number, bool) and number > 0:
        return number
    return None


def main(env, deps) -> int:
    number = _target(Path(env["TB_SELECT_DIR"]))
    if number is None:
        print("post: select の input.json から投稿先の Issue を決められません", file=sys.stderr)
        return 1
    triage_dir = Path(env["TB_TRIAGE_DIR"])
    report = jobio.read_json_or_none(triage_dir / REPORT_FILE)
    failure = jobio.read_json_or_none(triage_dir / FAILURE_FILE)
    # 形の合わない artifact は無いものとして扱い、plan_post が internal-error の本文にする
    report = report if isinstance(report, dict) else None
    failure = failure if isinstance(failure, str) else None
    body, code = plan_post(env["TB_SELECT_RESULT"], report, failure)

    if not should_post(deps.github, number):
        print(f"post: Issue {number} には既に印付きのコメントがあるので投稿しません")
        return 0
    if env["TB_DRY_RUN"] != "false":
        jobio.append_summary(env, body)
        print(f"post: dry-run のため投稿せず、本文を step summary に出しました (Issue {number}, 終了コード {code})")
        return code
    deps.github.post_comment(number, body)
    print(f"post: Issue {number} に投稿しました (終了コード {code})")
    return code


def _real_deps() -> SimpleNamespace:
    from adapters.github_api import GitHubRest

    return SimpleNamespace(github=GitHubRest(os.environ["GITHUB_TOKEN"], os.environ["TB_REPOSITORY"]))


if __name__ == "__main__":
    sys.exit(main(os.environ, _real_deps()))
