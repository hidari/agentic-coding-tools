#!/usr/bin/env python3
"""commit の SHA で CI の run を特定し、登録されるまで待ってから、全部が終わったら判定する。

使い方: wait-ci-runs.py <40 桁の SHA> [--register-timeout 秒] [--complete-timeout 秒] [--interval 秒]

終了コードは 0 (全部の run が通った) / 1 (通らなかった run がある) / 2 (判定できない)。
2 は「CI が落ちた」ではなく「CI を見られなかった」で、SHA の形が違う、gh を走らせられない、
出力を読めない、時間内に run が登録されない、時間内に終わらない、のどれか。

この形にしている理由 (どれも実測):

- PR の checks を watch する形 (gh の pr checks サブコマンドに --watch) は、push の直後で
  新しい head の check が 0 件のとき、待たずに「no checks reported」と rc=1 で終わることがある。登録前は `gh run list --commit <SHA>` も
  `[]` と rc=0 を返すので、空を「無い」と読まずに引き直す
- 短縮した SHA を --commit に渡しても `[]` と rc=0 が返り、登録前と区別できない。だから
  40 桁の 16 進だけを受け付ける
- 判定は `gh run watch --exit-status` の rc ではなく、終わった run の conclusion を読み直して
  決める。success と、実行して落ちたのではない skipped / neutral を通し、cancelled を含む
  それ以外 (知らない値も) は通さない

既知の限界:

- 見るのは GitHub Actions の run だけで、外部の status (commit status API) は見ない
- 最初の run が見えた後に遅れて登録された workflow の run は、見ている間に一覧へ出なければ
  数えない。1 つの SHA に workflow が複数あるリポジトリでは取りこぼしうる
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time

SHA_FORM = re.compile(r"[0-9a-f]{40}")
PASSING = {"success", "skipped", "neutral"}
FIELDS = "databaseId,name,status,conclusion"


class CannotJudge(Exception):
    """判定できない。rc 2 で終わる。"""


def list_runs(sha: str) -> list[dict]:
    try:
        proc = subprocess.run(
            ["gh", "run", "list", "--commit", sha, "--json", FIELDS, "--limit", "100"],
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError as e:
        raise CannotJudge("gh が見つからない") from e
    if proc.returncode != 0:
        raise CannotJudge(f"gh run list に失敗した (rc={proc.returncode}): {proc.stderr.strip()}")
    try:
        runs = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise CannotJudge(f"gh run list の出力を JSON として読めない: {e}") from e
    if not isinstance(runs, list):
        raise CannotJudge("gh run list の出力が一覧ではない")
    return runs


def wait(sha: str, register_timeout: float, complete_timeout: float, interval: float) -> list[dict]:
    start = time.monotonic()
    registered_at = None
    while True:
        runs = list_runs(sha)
        now = time.monotonic()
        if not runs:
            if now - start >= register_timeout:
                raise CannotJudge(f"{register_timeout:g} 秒待っても run が登録されない: {sha}")
        else:
            if registered_at is None:
                registered_at = now
            if all(r.get("status") == "completed" for r in runs):
                return runs
            if now - registered_at >= complete_timeout:
                raise CannotJudge(f"{complete_timeout:g} 秒待っても run が終わらない: {sha}")
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sha")
    parser.add_argument("--register-timeout", type=float, default=300)
    parser.add_argument("--complete-timeout", type=float, default=3600)
    parser.add_argument("--interval", type=float, default=5)
    args = parser.parse_args(argv)

    try:
        if not SHA_FORM.fullmatch(args.sha):
            raise CannotJudge(f"SHA は 40 桁の 16 進 (小文字) で渡すこと: {args.sha}")
        runs = wait(args.sha, args.register_timeout, args.complete_timeout, args.interval)
    except CannotJudge as e:
        print(f"[x] {e}", file=sys.stderr)
        return 2

    failed = 0
    for r in runs:
        conclusion = r.get("conclusion") or ""
        if conclusion not in PASSING:
            failed += 1
        print(f"{conclusion or '(なし)'} {r.get('name', '')} {r.get('databaseId', '')}")
    print(f"run {len(runs)} 件のうち通らなかったもの {failed} 件")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
