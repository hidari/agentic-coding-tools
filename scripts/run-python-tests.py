#!/usr/bin/env python3
"""リポジトリ内の Python テストを stdlib の unittest で実行し、テスト ID 集合の manifest と照合する。

pytest を使わないのは、CI が runner の python3 をそのまま使う方針で、
setup-python や pip install を足すと pin すべき依存が増えるため。
検証対象の winvm.py 自身も `dependencies = []` である。

pin するのは「実行されたテスト ID の集合」であって件数ではない。件数の下限は
canonical (テストソース) の再掲になって drift し、下限を割らない範囲の痩せを
原理的に捕捉できない。集合なら消えた側も未記録側も ID を名指しで赤にでき、
同数入れ替え (1 件消して 1 件足す) も対で捕まる。manifest
(scripts/python-tests-manifest.txt) は --update-manifest だけが書ける snapshot で、
テストの増減・改名は manifest の diff として PR に現れる (gen-readme.py --check と
同じ構図)。赤の run では更新を拒否する。壊れた収集や skip を baseline へ
焼き込ませないため。

テストは検証対象モジュールと同じディレクトリに置かれ、そこを import path として
起動する必要があるので、ファイルごとに cwd を切り替えて実行する。

skip と expectedFailure は manifest では捕まらないので実行側で別途赤にする。
skip されたテストも suite (= 列挙) に載り、expectedFailure は wasSuccessful() が
成功に数える (実測: 4 件中 skip 1 + xfail 1 でも `Ran 4 tests` / OK / exit 0)。
どちらも件数にも exit code にも現れない「実行していないのに緑」の形。

終了コードは 0 (manifest と一致で全緑) / 1 (違反: テスト失敗・消えた・未記録・
skip・expectedFailure) / 2 (検査不能: manifest 不在・引数不正)。分離の先例は
scripts/check-leak-guard-rules.py。引数不正の 2 は argparse の既定挙動で、typo が
照合モードへの静かなフォールバックにならない。

限界 (自己ホスト盲点): runner 自身の取り付けは、この runner が回す
scripts/test_run_python_tests.py が検証する構造のため、次の 3 形は in-band では
原理的に検出できない。
  1. 両取り付け (pre-commit hook + CI job) の同時撤去
  2. 末尾の `if __name__ == "__main__"` ブロックの除去 (起動しても何もせず exit 0)
  3. main 末尾の集計を `return 0` に潰す変更 (赤を印字しつつ緑で終わる)
CI へ第 2 の取り付けを足せば塞がるが、取り付け literal の二重管理と引き換えに
なるため採らず、レビューを防衛層とする (ユーザー判断)。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_NAME = "python-tests-manifest.txt"
UPDATE_CMD = "python3 scripts/run-python-tests.py --update-manifest"

# check-package-shape.py の集合に `.cache` を足したもの。rglob はドット始まりの
# ディレクトリも node_modules も辿るので (実測)、除外しないと skill が持つ venv の
# site-packages のテストまで収集して実行する。`.cache` は隔離実験やスクラッチの
# ツリーが置かれる場所で、実リポの rglob が拾う test_*.py の大半はここに当たる。
# 収集すると実験のコピーが本物の検査を汚す。
SKIP_DIRS = {".git", ".venv", ".cache", "node_modules", "__pycache__"}

# 先頭に再生成コマンドを書くのは、利用者が文面から原因へ辿り着けるようにするため
# (gen-readme.py --check で「文面から原因に辿り着けない」事故の実績がある)
MANIFEST_HEADER = f"""\
# {UPDATE_CMD} が生成する。手で編集しない。
# 実行されたテスト ID の集合を pin し、消えた側 (痩せ) も未記録側 (増加) も赤にする。
# テストを増減・改名したら上のコマンドで再生成し、diff ごとコミットする。
# 衝突したら手でマージせず再生成する。
"""

# 子プロセスは同一ロードの suite を「列挙してから実行」する。列挙した集合 =
# 実行した集合が構造的に保証され、別ロードで両者が食い違う余地を残さないため。
# 起動は `-m unittest` ではなく `-c`。ID の列挙にこのコードが要るためだが、
# スクリプトファイルとして起動すると sys.path[0] がスクリプトの親ディレクトリに
# なり、テストの import に失敗して _FailedTest 化する (実測)。`-c` の子は
# sys.path[0] が '' (= cwd) なので -m と同じ import 前提のまま使える (実測)。
BOOTSTRAP = """\
import json, sys, unittest

name, out_path = sys.argv[1], sys.argv[2]
suite = unittest.TestLoader().loadTestsFromName(name)


def flatten(s):
    for t in s:
        if isinstance(t, unittest.TestSuite):
            yield from flatten(t)
        else:
            yield t


tests = list(flatten(suite))
result = unittest.TextTestRunner(verbosity=1).run(suite)
with open(out_path, "w", encoding="utf-8") as f:
    json.dump({
        "ids": [t.id() for t in tests],
        "skipped": [t.id() for t, reason in result.skipped],
        "expected_failures": [t.id() for t, err in result.expectedFailures],
    }, f)
sys.exit(0 if result.wasSuccessful() else 1)
"""


def discover(root: Path) -> list[Path]:
    # ディレクトリの列挙 (旧 SEARCH_DIRS) ではなく root 全体を走査して除外を列挙
    # する。列挙方式は「列挙に無いディレクトリは無検査」という穴をディレクトリが
    # 増えるたびに再発させる (scripts/ が無検査だった Issue #8 の周辺そのもの)。
    # 除外判定は root 相対の parts で行う。絶対パスの parts で見ると、リポジトリ
    # 自体が SKIP_DIRS の名前を含む場所 (例: ~/.venv 配下) へ checkout されたとき
    # 全件が除外される (実測)。
    found = sorted(root.rglob("test_*.py"))
    return [p for p in found if not SKIP_DIRS & set(p.relative_to(root).parts)]


def run_one(path: Path) -> tuple[bool, list[str], str]:
    """(緑か, 列挙されたテスト ID, 要約) を返す。"""
    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / "result.json"
        # bytecode cache は run ごとの一時側へ隔離する。同サイズ・同秒の書き換え
        # (fixture の同名長改名や変異注入の復元) は pyc の有効性判定 (mtime 秒 +
        # サイズ) を素通りし、stale な bytecode が変更前のテストを実行して緑を返す
        # (実測: 同名長のテスト改名が manifest と一致してしまった)。検査対象の
        # ツリーへ __pycache__ を残さない効果もある。
        proc = subprocess.run(
            [sys.executable, "-c", BOOTSTRAP, path.stem, str(out_path)],
            cwd=path.parent,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPYCACHEPREFIX": str(Path(tmp) / "pycache")},
        )
        if not out_path.exists():
            # BOOTSTRAP が結果を書く前に死んだ (構文エラー等)。列挙が取れないので
            # manifest 照合に載せられず、ここで赤にする
            return False, [], f"列挙結果が出力されなかった\n{(proc.stdout + proc.stderr).rstrip()}"
        data = json.loads(out_path.read_text(encoding="utf-8"))
    ids: list[str] = data["ids"]
    if proc.returncode != 0:
        return False, ids, f"失敗 ({len(ids)} 件列挙)\n{(proc.stdout + proc.stderr).rstrip()}"
    if not ids:
        return False, ids, "0 件しか実行されていない (検査対象ゼロは合格ではない)"
    problems = [f"skip: {test_id}" for test_id in data["skipped"]]
    problems += [f"expectedFailure: {test_id}" for test_id in data["expected_failures"]]
    if problems:
        # どちらも「実行していないのに緑」の形で、suite には載るので manifest では
        # 捕まらない (docstring の実測)。ID を名指しで赤にする
        detail = "\n".join(f"  {p}" for p in problems)
        return False, ids, f"実行されなかったテストがある\n{detail}"
    return True, ids, f"{len(ids)} 件 pass"


def parse_manifest(text: str) -> set[str]:
    lines = (line.strip() for line in text.splitlines())
    return {line for line in lines if line and not line.startswith("#")}


def main(argv: list[str] | None = None, *, root: Path = ROOT) -> int:
    # allow_abbrev の既定 (True) は --update や --u を --update-manifest の短縮として
    # 受理し、manifest を無言で書き換える (実測)。書き込みに至るフラグは完全形の
    # 明示だけに絞り、短縮は typo と同じ exit 2 へ倒す
    parser = argparse.ArgumentParser(
        description="Python テストを実行し、テスト ID 集合を manifest と照合する",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--update-manifest",
        action="store_true",
        help="全テストが緑のとき、実行されたテスト ID の集合で manifest を書き直す",
    )
    args = parser.parse_args(argv)
    manifest_path = root / "scripts" / MANIFEST_NAME

    targets = discover(root)
    if not targets:
        print("[x] test_*.py が 1 つも見つからない。検査対象ゼロは合格ではない")
        return 1

    failed = 0
    actual: set[str] = set()
    for path in targets:
        ok, ids, summary = run_one(path)
        rel = path.relative_to(root).as_posix()
        actual |= {f"{rel}::{test_id}" for test_id in ids}
        print(f"{'[+]' if ok else '[x]'} {rel}: {summary}")
        if not ok:
            failed += 1

    if args.update_manifest:
        if failed:
            # 赤のまま書くと、壊れた収集や skip がそのまま baseline に焼き込まれ、
            # 次回から「痩せた集合との一致」が緑になる。laundering を構造的に塞ぐ
            print(f"[x] {failed} ファイルが赤のままなので manifest は更新しない")
            return 1
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            MANIFEST_HEADER + "".join(f"{line}\n" for line in sorted(actual)),
            encoding="utf-8",
        )
        print(f"[+] manifest を更新した ({len(actual)} 件)。diff を確認してコミットすること")
        return 0

    if not manifest_path.exists():
        # 照合基準が無いのは違反ではなく検査不能。テストの赤 (1) と混ぜると
        # manifest の置き忘れがテストの失敗に見える
        print(f"[x] manifest ({MANIFEST_NAME}) が無く照合できない。`{UPDATE_CMD}` で生成してコミットする")
        return 2

    expected = parse_manifest(manifest_path.read_text(encoding="utf-8"))
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    for test_id in missing:
        print(f"  [x] 消えたテスト: {test_id}")
    for test_id in unexpected:
        print(f"  [x] 未記録のテスト: {test_id}")
    if missing or unexpected:
        print(
            f"[x] manifest と実行結果が一致しない (消えた {len(missing)} / 未記録 {len(unexpected)})。"
            f"意図した増減なら `{UPDATE_CMD}` を実行して diff をコミットする"
        )
        failed += 1

    if failed:
        print(f"検査した Python テスト: {len(targets)} ファイル / 問題 {failed} 件")
        return 1
    print(
        f"検査した Python テスト: {len(targets)} ファイル / テスト {len(actual)} 件 "
        "(manifest と一致) / 違反なし"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
