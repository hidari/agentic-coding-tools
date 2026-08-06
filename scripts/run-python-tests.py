#!/usr/bin/env python3
"""リポジトリ内の Python テストを stdlib の unittest で実行する。

pytest を使わないのは、CI が runner の python3 をそのまま使う方針で、
setup-python や pip install を足すと pin すべき依存が増えるため。
検証対象の winvm.py 自身も `dependencies = []` である。

テストは検証対象モジュールと同じディレクトリに置かれ、そこを import path として
起動する必要があるので、ファイルごとに cwd を切り替えて実行する。

「0 件実行」を緑と読まないための堰を 2 つ置く:
  1. テストファイルが 1 つも見つからなければ失敗させる
  2. 各ファイルで実行件数が 0 件なら失敗させる

2 は Python 3.12 未満のための堰である。3.12 以降の unittest は 0 件収集で
非 0 (実測: 3.14.6 で exit 5) を返すので冗長に見えるが、winvm.py は
requires-python = ">=3.11" を宣言しており 3.11 では 0 件でも exit 0 になる。
冗長だからと外すと、古い処理系で「収集に失敗しているのに緑」に戻る。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEARCH_DIRS = ("skills", "plugins")
RAN_RE = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)


def discover() -> list[Path]:
    found: list[Path] = []
    for d in SEARCH_DIRS:
        found.extend(sorted((ROOT / d).rglob("test_*.py")))
    return [p for p in found if "__pycache__" not in p.parts]


def run_one(path: Path) -> tuple[bool, str]:
    """(緑か, 要約) を返す。"""
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", path.stem],
        cwd=path.parent,
        capture_output=True,
        text=True,
    )
    output = proc.stdout + proc.stderr
    m = RAN_RE.search(output)
    count = int(m.group(1)) if m else 0
    if proc.returncode != 0:
        return False, f"失敗 ({count} 件実行)\n{output.rstrip()}"
    if count == 0:
        return False, "0 件しか実行されていない (収集に失敗している可能性)"
    return True, f"{count} 件 pass"


def main() -> int:
    targets = discover()
    if not targets:
        print(f"[x] テストファイルが 1 つも見つからない ({'/'.join(SEARCH_DIRS)} 配下の test_*.py)")
        return 1

    failed = 0
    for path in targets:
        ok, summary = run_one(path)
        rel = path.relative_to(ROOT)
        print(f"{'[o]' if ok else '[x]'} {rel}: {summary}")
        if not ok:
            failed += 1

    print(f"\nPython テスト {len(targets)} ファイル / 失敗 {failed} ファイル")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
