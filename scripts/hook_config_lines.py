"""pre-commit の設定と workflow を「行」として読む補助。

取り付けを pin するテストが共有する。stdlib に YAML パーサが無いため、コメント行を
除いた行を文字列として照合する (照合の規則は _invokes)。YAML 構造としての妥当性までは
見ない。そこは pre-commit 自身と check-yaml hook が担う。

checker を引数に取るのは、取り付けを pin したい検査が複数あるため。呼ぶ側が自分の
literal を持ち、この層はどの検査にも依存しない。

ファイル名が `test_` で始まらないので run-python-tests.py の収集対象にはならない。
振る舞いは、この補助を使う各テストが自分の対象を通して検証する。
"""

from __future__ import annotations

import re
from pathlib import Path

HOOK_START = re.compile(r"^\s*-\s+id:")
HOOK_KEY = re.compile(r"^\s*(?:-\s+)?([A-Za-z_][A-Za-z0-9_-]*):")


def live_lines(path: Path) -> list[str]:
    """コメント行を除いた行。コメントの中の記述を取り付けと誤認しないため。"""
    return [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]


def _invokes(line: str, checker: str, flag: str) -> bool:
    """line が checker を flag 付きで呼んでいるか。

    invocations と hook_block の両方がこの 1 つを使う。述語を別々に書くと、片方だけを
    直したときに 2 つの照合が黙って食い違う。

    flag は split() で照合する。部分文字列だと --check が --check-text の行にも一致し、
    --check の hook を消しても invocations の pin は緑のままで、hook_block は
    --check-text の hook のブロックを返す。

    なおこの照合を部分文字列へ戻す変異は、今の設定では単独で赤にならない。invocations は
    部分文字列でも --check の行を見つけ、hook_block は最初に一致した行を使うので、先に
    書かれた --check の hook のブロックを返す。--check の hook を外した状態と組み合わせて
    初めて差が出る。
    """
    return checker in line and flag in line.split()


def invocations(lines: list[str], checker: str, flag: str) -> list[str]:
    """checker を flag 付きで呼んでいる行。照合の規則は _invokes。"""
    return [line for line in lines if _invokes(line, checker, flag)]


def hook_block(lines: list[str], checker: str, flag: str) -> list[str]:
    """flag で呼んでいる local hook の定義ブロック (次の `- id:` の手前まで)。"""
    hits = [i for i, line in enumerate(lines) if _invokes(line, checker, flag)]
    if not hits:
        return []
    start = hits[0]
    while start > 0 and not HOOK_START.match(lines[start]):
        start -= 1
    end = start + 1
    while end < len(lines) and not HOOK_START.match(lines[end]):
        end += 1
    return lines[start:end]


def hook_keys(block: list[str]) -> set[str]:
    """hook 定義ブロックが持つマッピングのキー。

    入れ子のマッピングも同じ形なので拾う。取りこぼす方向ではなく余計に拾う方向へ
    倒してあるのは、allowlist と突き合わせる用途だから (知らないキーは赤にする)。
    """
    return {m.group(1) for line in block if (m := HOOK_KEY.match(line))}
