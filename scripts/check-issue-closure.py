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

    scan_tasks と has_unclosed_fence が同じ状態機械を共有するのは、二重に持つと片方だけ
    直したときにフェンスの意味論がずれるため。コメント判定をフェンス判定より先に置く
    順序は元の実装のまま変えていない (フェンス内の HTML コメント風の行も comment として
    扱われる、という既存の挙動を維持する)。
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


def scan_tasks(text: str) -> tuple[bool, int, int]:
    """(タスク節の有無, 箱の総数, 未チェック数) を返す。

    箱の数え方を節の内側に限らないのは C.3 に揃えるため。節の外へ囮を置くだけで
    免除される形を作らない。
    """
    lines, _ = _strip_fences_and_comments(text)
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


def has_unclosed_fence(text: str) -> bool:
    """コードフェンスが閉じずに文書末尾へ達しているか。

    閉じ忘れは以降の全行を無検査にする。ここでは検査対象を安全に走査できないという
    シグナルとして使うだけで、違反 (rc 1) としては報告しない。閉じ忘れ自体は
    issue-id.py --check が全追跡ファイルに対して既に検出・報告しており
    (KIND_UNCLOSED_FENCE)、pre-commit / CI の両方に取り付け済みなので、ここで違反にすると
    同じ規則の 2 つ目の canonical になる。呼び出し側は「走査できなかった」件数へ寄せること。
    """
    _, unclosed = _strip_fences_and_comments(text)
    return unclosed


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


def collect(root: Path) -> tuple[list[str], list[str], int, int, int, int]:
    """(違反, 走査できなかった注記, active 件数, closed 件数,
    issue.md が無いディレクトリ数, 走査できなかった件数) を返す。

    「走査できなかった」に寄せるのは 2 つ: issue.md が読めない (エンコーディング不正など)
    と、コードフェンスが閉じておらず内容を安全に走査できない場合。どちらも違反 (rc 1) には
    しない。読めないファイルを Python の既定 (UnicodeDecodeError で未捕捉のまま伝播) に
    任せると rc が 1 になり、「読めない」が「違反」を名乗ってしまう。閉じ忘れフェンスは
    issue-id.py --check が既に検出・報告しているので、ここで違反にすると同じ規則の
    2 つ目の canonical になる (借用元 issue-id.py の _blob_text / run_check も同じ経路を
    unreadable として数え、違反にはしない)。
    どちらも沈黙させないのは、母集団が縮んだことを出力から見えるようにするため。
    """
    n = notation()
    dirs = n.issue_dirs(root)
    if not dirs:
        raise CheckError("Issue ディレクトリが 1 件も無い。走査対象ゼロは合格ではない")
    violations: list[str] = []
    unscanned_notes: list[str] = []
    active = closed = missing = unscanned = 0
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
            unscanned += 1
            unscanned_notes.append(f"{rel_dir}: 読めないので走査できなかった ({e})")
            continue
        if is_closed:
            continue
        if has_unclosed_fence(text):
            unscanned += 1
            unscanned_notes.append(
                f"{rel_dir}: コードフェンスが閉じていないので走査できなかった "
                "(閉じ忘れ自体は issue-id.py --check が別途報告する)"
            )
            continue
        has_heading, total, unchecked = scan_tasks(text)
        if has_heading and total >= 1 and unchecked == 0:
            violations.append(
                f"{rel_dir}: タスクが全て消化済みなのに active に居る "
                f"(箱 {total} 個 / 未チェック 0)。クローズを同じ PR へ同梱する"
            )
    return violations, unscanned_notes, active, closed, missing, unscanned


def build_parser() -> argparse.ArgumentParser:
    # 短縮形が別モードへ静かに落ちるのを防ぐ (既存 3 本と同じ理由)
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--root", default=None, help="リポジトリのルート (既定: git が返す)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = resolve_root(args.root)
        violations, unscanned_notes, active, closed, missing, unscanned = collect(root)
    except CheckError as e:
        print(f"[x] {e}", file=sys.stderr)
        return 2
    for line in violations:
        print(f"  [x] {line}", file=sys.stderr)
    # 走査できなかった注記は違反と別記号にする。同じ [x] で並べると rc に効かない件数まで
    # 「違反」に見え、読み手が rc 0 の理由を誤解する
    for line in unscanned_notes:
        print(f"  [-] {line}", file=sys.stderr)
    print(
        f"検査した Issue: active {active} 個 / closed {closed} 個"
        f" / issue.md が無い {missing} 個 / 走査できなかった {unscanned} 個"
        f" / 違反 {len(violations)} 件"
    )
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
