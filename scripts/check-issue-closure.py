#!/usr/bin/env python3
"""active な Issue の閉じ忘れと、配置と status の不整合を検出する。

in-repo Issue は「## タスク が全て [x] なら閉じる」という規約で運用されるが、その規約は
in-repo-issue skill の Phase C.3 が散文で持つだけで、守られたかを見る層がどこにも無い。
実装 PR でタスクを埋めてクローズを次の PR へ回すと、その間 Issue は「完了なのに open」に
なる (実測: 過去の main の c33ab70 と d1de3ea がその状態)。

この検査を入れると、その状態でコミットできなくなる。つまりクローズは実装 PR へ同梱する
ことが強制される。main が保護されていて直 push を選べないこのリポジトリではそれが正しいが、
post-merge クローズを既定とするリポジトリとは両立しない。**そのまま配布しないこと。**

記法と母集団の canonical は借用先が持つ。ここでは写さない。
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent

# 母集団と記法の canonical。写さずに借りる (同じ形を scripts/check-related-refs.py が採る)
NOTATION_SOURCE = "plugins/dev-workflow/skills/in-repo-issue/scripts/issue-id.py"

# 借りる名前。rename が静かな素通りにならないよう実在を検査する
BORROWED = ("FENCE_LINE", "GitError", "issue_dirs", "resolve_root")

CLOSED_SEGMENT = "closed"

# 見出しのレベルと空白の揺れを吸収する。厳密に `## タスク` だけを見ると、`### タスク` で
# 書かれた完了済み Issue が「タスク節なし」として素通りする (fixture で実測)
TASK_HEADING = re.compile(r"^#{2,3} *タスク *$")

# 箱として認めるのは半角/全角スペースと x/X だけ。GitHub もこれ以外は箱として描かない
CHECKBOX = re.compile(r"^[ \t]*[-*+] \[([ 　xX])\]")
CHECKED = ("x", "X")

STATUS_LINE = re.compile(r"^status: *(\S+) *$")

_notation = None


class CheckError(Exception):
    """検査を走らせられない。規約違反 (rc 1) と分けて rc 2 で返す。"""


def notation():
    global _notation
    if _notation is not None:
        return _notation
    path = ROOT / NOTATION_SOURCE
    spec = importlib.util.spec_from_file_location("issue_id_notation", path)
    if spec is None or spec.loader is None:
        raise CheckError(f"{NOTATION_SOURCE} を読み込めない (ファイルが無い)")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (OSError, SyntaxError, ValueError) as e:
        raise CheckError(f"{NOTATION_SOURCE} を読み込めない: {e}") from e
    missing = [name for name in BORROWED if not hasattr(module, name)]
    if missing:
        raise CheckError(
            f"{NOTATION_SOURCE} に {', '.join(missing)} が無い。"
            "canonical が動いたので、この検査の借用先を追随させること"
        )
    _notation = module
    return module


def _strip_fences_and_comments(text: str) -> tuple[list[str], bool]:
    """フェンスと HTML コメントの中身を除いた行のリストと、フェンスが閉じずに文書末尾へ
    達したかを返す。

    フェンスの開閉は単純トグルではなく借用元 issue-id.py の scan_text と同じ意味論を使う:
    閉じ行は情報文字列を持たず (`marker.group(2).strip()` が空) かつ開始と同じ長さ以上の
    バッククォートを持つときだけ閉じる (CommonMark)。単純トグルだと、4 連で開いて内側に
    3 連の行がある文書で閉じ判定を誤り (3 連の行を「閉じた」と数えてしまう)、直後に続く
    本物のタスク節がフェンス内へ吸い込まれて消える (実測)。

    scan_tasks と collect() が同じ状態機械を共有するのは、二重に持つと片方だけ直したときに
    フェンスの意味論がずれるため。collect() は同じ issue.md に対しこの関数を 1 回だけ呼び、
    返ってきた lines と unclosed の両方を使い回す (D3: 以前は has_unclosed_fence(text) と
    scan_tasks(text) が独立にこの関数を呼んでおり、同じテキストを 2 回パースしていた)。
    コメント判定をフェンス判定より先に置く順序は元の実装のまま変えていない (フェンス内の
    HTML コメント風の行も comment として扱われる、という既存の挙動を維持する)。
    """
    fence = notation().FENCE_LINE
    fence_len = 0
    in_comment = False
    kept: list[str] = []
    for line in text.splitlines():
        if in_comment:
            if "-->" in line:
                in_comment = False
            continue
        if "<!--" in line and "-->" not in line:
            in_comment = True
            continue
        marker = fence.match(line)
        if fence_len:
            if marker and not marker.group(2).strip() and len(marker.group(1)) >= fence_len:
                fence_len = 0
            continue
        if marker:
            fence_len = len(marker.group(1))
            continue
        kept.append(line)
    return kept, fence_len != 0


def _scan_lines_for_tasks(lines: list[str]) -> tuple[bool, int, int]:
    """フェンス/コメント除去済みの行から (タスク節の有無, 箱の総数, 未チェック数) を返す。

    scan_tasks と collect() の両方から使う共通本体 (D3)。箱の数え方を節の内側に限らないのは
    C.3 に揃えるため。節の外へ囮を置くだけで免除される形を作らない。
    """
    has_heading = False
    total = 0
    unchecked = 0
    for line in lines:
        if TASK_HEADING.match(line):
            has_heading = True
            continue
        m = CHECKBOX.match(line)
        if m:
            total += 1
            if m.group(1) not in CHECKED:
                unchecked += 1
    return has_heading, total, unchecked


def scan_tasks(text: str) -> tuple[bool, int, int]:
    """(タスク節の有無, 箱の総数, 未チェック数) を返す。テストが直接叩く公開関数。"""
    lines, _ = _strip_fences_and_comments(text)
    return _scan_lines_for_tasks(lines)


def read_status(text: str) -> str | None:
    """frontmatter の status を返す。読めなければ None。

    frontmatter は先頭の `---` で開いて次の `---` で閉じる。閉じる前だけを見るのは、
    本文中に status: と書かれた行を拾わないため。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            return None
        m = STATUS_LINE.match(line)
        if m:
            return m.group(1)
    return None


def resolve_root(explicit: str | None) -> Path:
    """借用先で root を解決する。git を走らせられないのも検査不能なので CheckError へ寄せる。"""
    module = notation()
    try:
        return module.resolve_root(explicit)
    except module.GitError as e:
        raise CheckError(str(e)) from e


def issue_md_path(root: Path, rel_dir: str) -> Path | None:
    """Issue ディレクトリから issue.md を返す。名前の大小は無視する。

    git の pathspec はファイル名を大小区別して照合するが macOS の既定は
    core.ignorecase=true で、追跡名は作成時の綴りが記録される。`Issue.md` で作られた
    Issue は開発機では普通に開けるのに pathspec からは落ちる (fixture で実測)。
    """
    directory = root / rel_dir
    if not directory.is_dir():
        return None
    for child in sorted(directory.iterdir()):
        if child.is_file() and child.name.lower() == "issue.md":
            return child
    return None


class CollectResult(NamedTuple):
    """collect() の戻り値 (D1)。

    active / closed / missing / unreadable は同じ int 型が並ぶので、位置だけのタプルだと
    取り違えても TypeError にならず件数が静かに入れ替わる。フィールド名を必須にする
    NamedTuple へ寄せることで取り違えを起こしにくくする。借用元 issue-id.py の
    Violation(NamedTuple) と同じ形。

    unscanned (走査できなかった件数) を持たないのは、unscanned_notes と常に同値な
    冗長な状態だったため (D2)。呼び出し側は len(unscanned_notes) を使う。
    """

    violations: list[str]
    unscanned_notes: list[str]
    active: int
    closed: int
    missing: int
    unreadable: int


def collect(root: Path) -> CollectResult:
    """Issue ディレクトリを歩き、不変条件 A (完了漏れ) と不変条件 B (配置と status の
    整合) を検査する。

    「走査できなかった」(unscanned_notes) に寄せるのは 2 つ: issue.md が読めない
    (エンコーディング不正など) と、コードフェンスが閉じておらず内容を安全に走査できない
    場合。どちらも違反 (rc 1) にはしない。読めないファイルを Python の既定
    (UnicodeDecodeError で未捕捉のまま伝播) に任せると rc が 1 になり、「読めない」が
    「違反」を名乗ってしまう。閉じ忘れフェンスは issue-id.py --check が既に検出・報告して
    いるので、ここで違反にすると同じ規則の 2 つ目の canonical になる (借用元 issue-id.py
    の _blob_text / run_check も同じ経路を unreadable として数え、違反にはしない)。

    status (frontmatter) が読めない場合は unreadable へ別カウントする。タスクの完了漏れ
    (本文) と status の読めなさ (frontmatter) は原因も直し方も別なので、同じ
    unscanned_notes へ混ぜない。スペックの要求どおり「読めなかった」を合格へ倒さず件数へ
    出す。

    どれも沈黙させないのは、母集団が縮んだことを出力から見えるようにするため。
    """
    n = notation()
    dirs = n.issue_dirs(root)
    if not dirs:
        raise CheckError("Issue ディレクトリが 1 件も無い。走査対象ゼロは合格ではない")
    violations: list[str] = []
    unscanned_notes: list[str] = []
    active = closed = missing = unreadable = 0
    for rel_dir, _ in dirs:
        is_closed = CLOSED_SEGMENT in Path(rel_dir).parts
        if is_closed:
            closed += 1
        else:
            active += 1
        path = issue_md_path(root, rel_dir)
        if path is None:
            missing += 1
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as e:
            unscanned_notes.append(f"{rel_dir}: 読めないので走査できなかった ({e})")
            continue
        # 不変条件 B: 配置 (closed/ に居るか) と frontmatter の status が食い違わないこと。
        # フェンスの状態に関わらず frontmatter は先頭にあるので、フェンス走査より前に
        # status を見る。closed 側もここで検査するので、後続の "is_closed: continue" より
        # 前に置く
        status = read_status(text)
        if status is None:
            unreadable += 1
        elif is_closed and status != "closed":
            violations.append(
                f"{rel_dir}: closed/ に居るのに status が {status}。"
                "git mv だけして frontmatter を書き換えていない"
            )
        elif not is_closed and status == "closed":
            violations.append(
                f"{rel_dir}: status が closed なのに active に居る。"
                "closed/ へ移していない"
            )
        if is_closed:
            continue
        # D3: フェンス除去は _strip_fences_and_comments を 1 回呼ぶだけにする。以前は
        # has_unclosed_fence(text) と scan_tasks(text) が同じ issue.md に対し独立に
        # 呼んでおり、同じテキストを 2 回パースしていた
        lines, unclosed = _strip_fences_and_comments(text)
        if unclosed:
            # 閉じ忘れは以降の全行を無検査にする。検査対象を安全に走査できないという
            # シグナルとして使うだけで、違反 (rc 1) としては報告しない。閉じ忘れ自体は
            # issue-id.py --check が全追跡ファイルに対して既に検出・報告しており
            # (KIND_UNCLOSED_FENCE)、pre-commit / CI の両方に取り付け済みなので、ここで
            # 違反にすると同じ規則の 2 つ目の canonical になる
            unscanned_notes.append(
                f"{rel_dir}: コードフェンスが閉じていないので走査できなかった "
                "(閉じ忘れ自体は issue-id.py --check が別途報告する)"
            )
            continue
        has_heading, total, unchecked = _scan_lines_for_tasks(lines)
        if has_heading and total >= 1 and unchecked == 0:
            violations.append(
                f"{rel_dir}: タスクが全て消化済みなのに active に居る "
                f"(箱 {total} 個 / 未チェック 0)。クローズを同じ PR へ同梱する"
            )
    return CollectResult(violations, unscanned_notes, active, closed, missing, unreadable)


def build_parser() -> argparse.ArgumentParser:
    # 短縮形が別モードへ静かに落ちるのを防ぐ (既存 3 本と同じ理由)
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--root", default=None, help="リポジトリのルート (既定: git が返す)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = resolve_root(args.root)
        result = collect(root)
    except CheckError as e:
        print(f"[x] {e}", file=sys.stderr)
        return 2
    for line in result.violations:
        print(f"  [x] {line}", file=sys.stderr)
    # 走査できなかった注記は違反と別記号にする。同じ [x] で並べると rc に効かない件数まで
    # 「違反」に見え、読み手が rc 0 の理由を誤解する
    for line in result.unscanned_notes:
        print(f"  [-] {line}", file=sys.stderr)
    # unscanned (走査できなかった件数) は unscanned_notes の長さそのもの (D2)。ここで
    # len() を経由するのが唯一の消費点で、状態として別に持たない
    print(
        f"検査した Issue: active {result.active} 個 / closed {result.closed} 個"
        f" / issue.md が無い {result.missing} 個"
        f" / status が読めない {result.unreadable} 個"
        f" / 走査できなかった {len(result.unscanned_notes)} 個"
        f" / 違反 {len(result.violations)} 件"
    )
    return 1 if result.violations else 0


if __name__ == "__main__":
    sys.exit(main())
