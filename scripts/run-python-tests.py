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

2 は 3.12 未満の処理系のための堰である。3.12 以降の unittest は 0 件収集で
非 0 を返す (3.14.6 で exit 5 を実測) が、このスクリプトは子を `sys.executable`
つまり自分を起動した python3 で走らせるので、どのバージョンで走るかは環境次第。
リポジトリは python のバージョンを pin していない。冗長に見えるからと外すと、
3.11 の環境で「収集に失敗しているのに緑」に戻る。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEARCH_DIRS = ("skills", "plugins")

# check-package-shape.py と同じ集合。rglob はドット始まりのディレクトリも
# node_modules も辿るので (実測)、除外しないと skill が持つ venv の
# site-packages に入っているテストまで収集して実行する。
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__"}

RAN_RE = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)


def discover() -> list[Path]:
    found: list[Path] = []
    for d in SEARCH_DIRS:
        found.extend(sorted((ROOT / d).rglob("test_*.py")))
    return [p for p in found if not SKIP_DIRS & set(p.parts)]


def run_one(path: Path) -> tuple[bool, str]:
    """(緑か, 要約) を返す。"""
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", path.stem],
        cwd=path.parent,
        capture_output=True,
        text=True,
    )
    # unittest は要約を stderr に書く。stdout を先に見るとテスト自身の出力に
    # 同じ形の行があったとき本物より先に一致する。
    m = RAN_RE.search(proc.stderr) or RAN_RE.search(proc.stdout)
    count = int(m.group(1)) if m else 0
    if proc.returncode != 0:
        return False, f"失敗 ({count} 件実行)\n{(proc.stdout + proc.stderr).rstrip()}"
    if count == 0:
        return False, "0 件しか実行されていない (検査対象ゼロは合格ではない)"
    return True, f"{count} 件 pass"


def main() -> int:
    targets = discover()
    if not targets:
        print(
            f"[x] テストファイルが 1 つも見つからない ({'/'.join(SEARCH_DIRS)} 配下の "
            "test_*.py)。検査対象ゼロは合格ではない"
        )
        return 1

    failed = 0
    for path in targets:
        ok, summary = run_one(path)
        print(f"{'[+]' if ok else '[x]'} {path.relative_to(ROOT)}: {summary}")
        if not ok:
            failed += 1

    if failed:
        print(f"検査した Python テスト: {len(targets)} ファイル / 失敗 {failed} ファイル")
        return 1
    print(f"検査した Python テスト: {len(targets)} ファイル / 違反なし")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
