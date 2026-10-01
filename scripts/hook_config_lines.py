"""pre-commit の設定と workflow を「行」として読む補助。

取り付けを pin するテストが共有する。stdlib に YAML パーサが無いため、コメント行を
除いた行を文字列として照合する (照合の規則は _invokes)。YAML 構造としての妥当性までは
見ない。そこは pre-commit 自身と check-yaml hook が担う。

引数 checker は呼び出しの行を特定する文字列 (検査スクリプトのパスや gitleaks の config の
パス) で、取り付けを pin したい対象が複数あるため引数に取る。対象ごとの literal は呼ぶ側が
持ち、この層はどの対象にも依存しない。この層に置く取り付けの規則も、どの対象にも共通する
もの (HOOK_LANGUAGE、COMMIT_MSG_HOOK_KEYS と effective_stages) に限る。

ファイル名が `test_` で始まらないので run-python-tests.py の収集対象にはならない。
振る舞いは、この補助を使う各テストが自分の対象を通して検証する。
"""

from __future__ import annotations

import re
from pathlib import Path

HOOK_START = re.compile(r"^\s*-\s+id:")
HOOK_KEY = re.compile(r"^\s*(?:-\s+)?([A-Za-z_][A-Za-z0-9_-]*):")

# 検査を呼ぶ hook の language は値まで pin する。キーの許可集合は値を見ないので、
# `language: pygrep` へ 1 語変えても他の pin は緑のままになる (変異注入で確認)。pygrep は
# entry を正規表現として渡されたファイルを照合するだけで、entry に書いたコマンドを起動しない。
# pass_filenames: false の hook では照合するファイルも渡らず、常に rc 0 になる
# (pre-commit 4.6.2 の languages/pygrep.py を読んで確認)
HOOK_LANGUAGE = "system"

# commit-msg stage の hook が持ってよいキー。個別の narrowing キーを列挙して禁じる形は
# 採らない。この stage では渡るファイルが message ファイル 1 本しかないため、ファイル名や
# ファイル型で絞る指定はどれも集合を空にし、絞り込みではなく skip になる (実測: files /
# exclude / types / exclude_types のいずれでも "(no files to check)Skipped" の rc 0)。
# 手段はこの 4 つに限らず、pre-commit が新しいキーを足せば列挙の外から同じ穴が開く。
# 許可する側を pin して、知らないキーが増えたら赤にする。hook ごとに要るキー (verbose など) は
# 呼ぶ側がこの集合に足す。
COMMIT_MSG_HOOK_KEYS = frozenset({"id", "name", "language", "entry", "stages", "always_run"})


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


def effective_stages(lines: list[str], block: list[str]) -> list[str]:
    """hook が走る stage を決める行。

    hook 自身が stages を宣言しなければ top-level の default_stages を継ぐ。hook ブロック内
    だけを見る形は、宣言が無いとループが一度も回らず空虚に緑になり、top-level の 1 行を
    [manual] へ変えるだけで全 stage から消えても捕まらない (実測)
    """
    own = [line for line in block if line.lstrip().startswith("stages:")]
    return own or [line for line in lines if re.match(r"^default_stages:", line)]


def hook_keys(block: list[str]) -> set[str]:
    """hook 定義ブロックが持つマッピングのキー。

    入れ子のマッピングも同じ形なので拾う。取りこぼす方向ではなく余計に拾う方向へ
    倒してあるのは、allowlist と突き合わせる用途だから (知らないキーは赤にする)。
    """
    return {m.group(1) for line in block if (m := HOOK_KEY.match(line))}


def hook_values(block: list[str], key: str) -> list[str]:
    """hook 定義ブロックで key が持つ値を、書かれた順に返す。

    キーの許可集合は値を見ないので、値まで pin したいときに使う。`key:` の後ろを前後の
    空白だけ除いて返し、引用符や行末コメントは解釈しない。YAML として等価な別表記は別の値に
    なるので、完全一致を要求する用途ではそのずれは赤に倒れる。
    """
    return [
        line[m.end() :].strip()
        for line in block
        if (m := HOOK_KEY.match(line)) and m.group(1) == key
    ]
