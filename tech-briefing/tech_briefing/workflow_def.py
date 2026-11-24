"""共通の reusable workflow (select → triage → post) の定義と、その書き出し。

`.github/workflows/tech-briefing-triage.yml` はここから生成し、手で書かない。配線のテストは
この dict に当てるので、YAML のパーサなしで権限と鍵の置き場を固定できる。書き出しは JSON の
文書にする。YAML 1.2 は JSON を含むので GitHub はそのまま読め、YAML 1.1 のパーサが `on` を
真偽値に化かす形も JSON の文字列の鍵なら起きない。JSON は注釈を持てないので、action の版と
選んだ理由はこのファイルの定数の横に置く。
"""

import json

from tech_briefing.constants import (
    SELECT_INPUT_TOO_LARGE,
    SELECT_TRIAGE,
    SELECT_UNEXPECTED,
)

# action は 40 桁の commit SHA で固定する (タグは付け替えられるため)。2026-11-24 に GitHub の API で
# 引いた値で、どのタグも commit を直に指していた
# checkout は .github/workflows/ci.yml と同じ SHA を使う (同じ action を2つの版で持たない。テストが一致を見る)
CHECKOUT = "actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0"  # v7
UPLOAD_ARTIFACT = "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9"  # v7.0.2
DOWNLOAD_ARTIFACT = "actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333"  # v8.0.2
# v10.4.0 は 2026-11-24 の時点で公開から4日しか経っていないので、あえて1つ前を使う
SETUP_UV = "astral-sh/setup-uv@68559e99cc807c1eb7e9238fd43a15bba460eec2"  # v10.3.3
# action の SHA が固定するのは導入の手順だけで、入る uv の版は別に決まる。版を渡さないと
# その日の最新が入るので明示する。0.13.4 は開発機の Homebrew の版 (2026-10-20 のビルド)
UV_VERSION = "0.13.4"

# 共通の仕組みと呼び出し側のリポジトリを別の場所へ checkout する。入口のスクリプトは
# 共通の仕組みの側を cwd にして起動するので、呼び出し側の uv.toml や pyproject.toml を
# uv が拾わない
SHARED_DIR = "tech-briefing-shared"
CALLER_DIR = "caller"

# job の間の受け渡し。job の outputs は文字列なので、外から来た本文は artifact で渡す
SELECT_ARTIFACT = "tech-briefing-select"
TRIAGE_ARTIFACT = "tech-briefing-triage"
OUT_DIR = "${{ runner.temp }}/tech-briefing"
INPUT_FILE = "input.json"
TRIAGE_FILES = ("report.json", "failure.json", "tool-log.json")
# 中身は Issue の本文と報告で、Issue そのものより長く残す理由が無い
RETENTION_DAYS = 7

# select の結果のうち post へ進むもの。unexpected と input-too-large は triage を飛ばして
# post が定型のコメントにする
POSTED_RESULTS = (SELECT_TRIAGE, SELECT_UNEXPECTED, SELECT_INPUT_TOO_LARGE)

MECH_REPO_EXPR = "${{ job.workflow_repository }}"
MECH_SHA_EXPR = "${{ job.workflow_sha }}"


def _script(name: str) -> str:
    return f"uv run --script tech-briefing/bin/run_{name}.py"


def _guard_step() -> dict:
    # job.workflow_repository と job.workflow_sha は reusable workflow が自分のソースを
    # checkout するための値 (docs.github.com の contexts、2026-11-24確認)。GitHub Enterprise Server
    # などで空になると、checkout は既定の呼び出し側のリポジトリを黙って取り、呼び出し側の
    # コードを共通の仕組みとして走らせてしまうので、checkout の前に止める
    return {
        "name": "共通の仕組みの repository と SHA が届いているか確かめる",
        "env": {"TB_WORKFLOW_REPOSITORY": MECH_REPO_EXPR, "TB_WORKFLOW_SHA": MECH_SHA_EXPR},
        "run": (
            'if [ -z "$TB_WORKFLOW_REPOSITORY" ] || [ -z "$TB_WORKFLOW_SHA" ]; then '
            'echo "::error::job.workflow_repository か job.workflow_sha が空です"; exit 1; fi'
        ),
    }


def _shared_checkout() -> dict:
    return {
        "name": "共通の仕組みを呼ばれた SHA で checkout する",
        "uses": CHECKOUT,
        "with": {
            "repository": MECH_REPO_EXPR,
            "ref": MECH_SHA_EXPR,
            "path": SHARED_DIR,
            "persist-credentials": False,
        },
    }


def _caller_checkout() -> dict:
    return {
        "name": "呼び出し側のリポジトリを checkout する",
        "uses": CHECKOUT,
        "with": {"path": CALLER_DIR, "persist-credentials": False},
    }


def _setup_uv() -> dict:
    # cache は鍵を持つ job でも復元されるので、ほかの run が書いた中身を読ませない
    return {
        "name": "uv を入れる",
        "uses": SETUP_UV,
        "with": {"version": UV_VERSION, "enable-cache": False},
    }


def _run(name: str, env: dict) -> dict:
    # 値は env だけで渡す。run の中に式を書くと、展開された値がシェルの構文として解釈される
    return {
        "name": f"{name} を走らせる",
        "id": name,
        "working-directory": SHARED_DIR,
        "env": env,
        "run": _script(name),
    }


def _common_env() -> dict:
    return {
        "TB_REPOSITORY": "${{ github.repository }}",
        "TB_OUT_DIR": OUT_DIR,
    }


def _select_job() -> dict:
    env = _common_env() | {
        "GITHUB_TOKEN": "${{ github.token }}",
        "TB_CONFIG_PATH": "${{ inputs.config-path }}",
        "TB_AUTHOR": "${{ vars.TECH_BRIEFING_AUTHOR }}",
        "TB_CALLER_DIR": "${{ github.workspace }}/" + CALLER_DIR,
        # 呼び出し側の検査が、呼び出しの workflow のファイルと固定した SHA を突き合わせるため
        "TB_CALLER_WORKFLOW_REF": "${{ github.workflow_ref }}",
        "TB_WORKFLOW_REPOSITORY": MECH_REPO_EXPR,
        "TB_WORKFLOW_SHA": MECH_SHA_EXPR,
    }
    posted = " || ".join(f"steps.select.outputs.result == '{r}'" for r in POSTED_RESULTS)
    return {
        "runs-on": "${{ inputs.runner }}",
        "timeout-minutes": 10,
        "permissions": {"contents": "read", "issues": "read"},
        "outputs": {"result": "${{ steps.select.outputs.result }}"},
        "steps": [
            _guard_step(),
            _shared_checkout(),
            _caller_checkout(),
            _setup_uv(),
            _run("select", env),
            {
                "name": "入力を渡す",
                "if": posted,
                "uses": UPLOAD_ARTIFACT,
                "with": {
                    "name": SELECT_ARTIFACT,
                    "path": f"{OUT_DIR}/{INPUT_FILE}",
                    "if-no-files-found": "error",
                    "retention-days": RETENTION_DAYS,
                },
            },
        ],
    }


def _triage_job() -> dict:
    env = _common_env() | {
        "ANTHROPIC_API_KEY": "${{ secrets.ANTHROPIC_API_KEY }}",
        "TB_CONFIG_PATH": "${{ inputs.config-path }}",
        "TB_CALLER_DIR": "${{ github.workspace }}/" + CALLER_DIR,
    }
    return {
        "needs": ["select"],
        "if": f"needs.select.outputs.result == '{SELECT_TRIAGE}'",
        "runs-on": "${{ inputs.runner }}",
        "timeout-minutes": 30,
        "permissions": {"contents": "read"},
        # 鍵は environment の側に置く。呼び出しの job は environment を持てない (docs.github.com、2026-11-24確認)
        "environment": "tech-briefing",
        "steps": [
            _guard_step(),
            _shared_checkout(),
            _caller_checkout(),
            _setup_uv(),
            {
                "name": "入力を受け取る",
                "uses": DOWNLOAD_ARTIFACT,
                "with": {"name": SELECT_ARTIFACT, "path": OUT_DIR},
            },
            _run("triage", env),
            {
                # 失敗の種類 (failure.json) はスクリプトが赤で終わっても残す。取り消しのときは残さない
                "name": "報告を渡す",
                "if": "!cancelled()",
                "uses": UPLOAD_ARTIFACT,
                "with": {
                    "name": TRIAGE_ARTIFACT,
                    "path": "\n".join(f"{OUT_DIR}/{f}" for f in TRIAGE_FILES),
                    "if-no-files-found": "ignore",
                    "retention-days": RETENTION_DAYS,
                },
            },
        ],
    }


def _post_job() -> dict:
    env = _common_env() | {
        "GITHUB_TOKEN": "${{ github.token }}",
        "TB_DRY_RUN": "${{ inputs.dry-run }}",
        "TB_AUTHOR": "${{ vars.TECH_BRIEFING_AUTHOR }}",
        "TB_SELECT_RESULT": "${{ needs.select.outputs.result }}",
        "TB_TRIAGE_RESULT": "${{ needs.triage.result }}",
    }
    posted = " || ".join(f"needs.select.outputs.result == '{r}'" for r in POSTED_RESULTS)
    return {
        "needs": ["select", "triage"],
        # triage が赤でも post は走り、failure.json か internal-error の定型を投稿する
        "if": (
            f"always() && needs.select.result == 'success' && ({posted}) "
            "&& needs.triage.result != 'cancelled'"
        ),
        "runs-on": "${{ inputs.runner }}",
        "timeout-minutes": 10,
        "permissions": {"contents": "read", "issues": "write"},
        "steps": [
            _guard_step(),
            _shared_checkout(),
            _setup_uv(),
            {
                "name": "入力を受け取る",
                "uses": DOWNLOAD_ARTIFACT,
                "with": {"name": SELECT_ARTIFACT, "path": OUT_DIR},
            },
            {
                # triage を飛ばした結果では artifact が無く、triage が落ちた日も無いことがある。
                # 無いことは run_post が internal-error として扱うので、ここでは止めない
                "name": "報告を受け取る",
                "if": "needs.triage.result != 'skipped'",
                "continue-on-error": True,
                "uses": DOWNLOAD_ARTIFACT,
                "with": {"name": TRIAGE_ARTIFACT, "path": OUT_DIR},
            },
            _run("post", env),
        ],
    }


def workflow() -> dict:
    return {
        "name": "tech-briefing-triage",
        "on": {
            "workflow_call": {
                "inputs": {
                    "config-path": {
                        "description": "設定のファイルのパス (呼び出し側のリポジトリの root から)",
                        "type": "string",
                        "required": True,
                    },
                    "dry-run": {
                        "description": "真なら投稿せず、本文を step summary に出す",
                        "type": "boolean",
                        "default": True,
                    },
                    "runner": {
                        "description": "runs-on のラベル",
                        "type": "string",
                        "default": "ubuntu-24.04",
                    },
                },
                "secrets": {"ANTHROPIC_API_KEY": {"required": True}},
            }
        },
        # 既定の権限を全部落とし、job ごとに必要なものだけを足す
        "permissions": {},
        # post の直前の再確認と合わせて、同じリポジトリの run が並んで二重に投稿するのを防ぐ。
        # 取り消すと投稿の途中で止まりうるので、後の run は待たせる
        "concurrency": {
            "group": "${{ github.repository }}-tech-briefing-triage",
            "cancel-in-progress": False,
        },
        "jobs": {
            "select": _select_job(),
            "triage": _triage_job(),
            "post": _post_job(),
        },
    }


def render(doc: dict) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
