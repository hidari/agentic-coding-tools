#!/usr/bin/env python3
"""共通の reusable workflow を tech_briefing.workflow_def から生成する。

--check は生成物とファイルの一致を見て、ずれていれば exit 1 (CI と pre-commit 用)。
--write はファイルを書き直す。生成物を手で編集してはならない。
リポジトリの root はこのファイルの置き場から決めるので、cwd に依らない。
"""

import argparse
import os
import sys

TB_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(TB_ROOT)
TARGET = os.path.join(REPO_ROOT, ".github", "workflows", "tech-briefing-triage.yml")

sys.path.insert(0, TB_ROOT)

from tech_briefing.workflow_def import render, workflow  # noqa: E402


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)

    text = render(workflow())
    rel = os.path.relpath(TARGET, REPO_ROOT)
    if args.write:
        os.makedirs(os.path.dirname(TARGET), exist_ok=True)
        with open(TARGET, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return 0
    try:
        with open(TARGET, encoding="utf-8", newline="") as f:
            current = f.read()
    except FileNotFoundError:
        print(f"{rel} がありません。--write で生成してください", file=sys.stderr)
        return 1
    if current != text:
        print(f"{rel} が生成結果と一致しません。--write で生成し直してください", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
