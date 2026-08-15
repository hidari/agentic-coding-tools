#!/usr/bin/env python3
"""in-repo Issue の識別子を採番し、GitHub の番号記法との混同を検出する。

in-repo Issue は `docs/issues/<dir>/` というディレクトリなので GitHub の Issue/PR カウンタを
消費しない。にもかかわらず参照を GitHub と同じ番号記法で書くと、同じ番号空間の GitHub
オブジェクトとどちらを指すのか文脈からしか判別できなくなる。GitHub の autolink が既定で
反応するのは数字記法だけなので、識別子を `ISSUE-<N>` にすれば距離ではなく種類で分離できる。
番号がいくつまで伸びても交わらない。

この規約の canonical はこのファイル。SKILL.md も CLAUDE.md も regex も接頭辞も再掲せず、
ファイル名で参照する。

規則は 1 つ、エンジンは 1 つ、入口は 3 つ。

  規則:     数字記法は GitHub 専用。直前が `PR ` (自リポの PR) か `owner/repo` (他リポ) の
            ときだけ許す。in-repo Issue を指す唯一の形は `ISSUE-<N>`
  エンジン: scan_text() 1 本。入口ごとに規則が分岐しない
  入口:     --next (採番) / --check (リポジトリ走査) / --check-text (テキスト 1 本)

終了コードは 0 (合格) / 1 (違反あり) / 2 (検査不能)。2 を 1 と分けるのは
scripts/check-leak-guard-rules.py と同じ理由で、「規約違反」と「検査を走らせられなかった」を
同じ赤にすると git が無い状態がルール違反に見えるため。追跡下のファイルが 1 件も無い場合も
2 にする。違反 0 件で緑にすると「何も見ていない」が「合格」に化ける。

免除の範囲 (広げすぎると静かに全件素通りする):

- コードフェンスとインラインコードの中は免除する (GitHub が autolink しないため)。
  ただしフェンスの閉じ忘れは免除を末尾まで広げる最大の穴なので、閉じ忘れ自体を違反として
  報告する。閉じ忘れを黙って許すと「違反 0 件」の緑に化ける
- 拡張子 `.css` / `.scss` は走査しない。色指定が数字記法と同じ形になり偽陽性になる

既知の限界:

- 未 push の別 clone / worktree での同時採番は解けない。ref 横断の走査は ref に載った分しか
  見えないので、同番号の二重取得は次の push まで検出されない
- 重複判定の鍵は入口ごとに違う。--next は現ツリーと全 ref を混ぜるので、ディレクトリ名から
  接頭辞を除いた部分を同一性の鍵にする (active と closed の間の移動も、移行に伴う接頭辞の
  付与も同じ Issue とみなす)。--check は現ツリーだけを見るのでパスを鍵にし、同じ名前が
  active と closed の両方にある形も重複として報告する
- その帰結として、--next はブランチ間でタイトル部の改名を伴う移動を別の Issue と見て重複を
  報告する (偽陽性側に倒している)。重複と判定されると --next は識別子を出さず exit 1 で
  止まる。つまり新規起票が一切できなくなるので、Issue ディレクトリを rename するときは
  番号もタイトル部も保存し、接頭辞の付与だけに留めること
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

PREFIX = "ISSUE-"
ISSUE_DIR = re.compile(r"^" + re.escape(PREFIX) + r"([0-9]+)_")
GITHUB_REF = re.compile(r"(?<!&)#([0-9]+)\b")
GITHUB_REF_ALLOWED_PREFIX = "PR "
CROSS_REPO_REF = re.compile(r"[0-9A-Za-z._-]+/[0-9A-Za-z._-]+$")

# 後読みで除くのは `&` だけにする。HTML 実体参照 (`&` + 数字 + `;`) を通すためで、
# ここへ英数字を足すと `PR` と数字が空白なしで並ぶ形が無検査になる (実測で確認)。
# 免除は届きすぎる方向へ広がっても違反 0 件の緑にしかならず、出力を見ても気づけない。

# 採番は旧形式 (接頭辞なしの `<N>_<title>`) も数える。移行中は両形式が同じ番号空間を
# 共有するので、新形式だけを見ると既存の番号を再発行する。PREFIX から導出しているので
# canonical は増えない
ANY_ISSUE_DIR = re.compile(r"^(?:" + re.escape(PREFIX) + r")?([0-9]+)_")

ISSUE_ROOT = ("docs", "issues")
CLOSED = "closed"
TEMPLATES = "templates"

SKIP_SUFFIXES = {".css", ".scss"}

# フェンスは N 連バッククォート (3 個以上) で開き、同じ N 個以上で閉じる (CommonMark)。
# 開き行は情報文字列を持てるがバッククォートは含められない。閉じ行はバッククォートだけ。
# 3 連で開いて 4 連で閉じる形は「閉じている」であって閉じ忘れではない
FENCE_LINE = re.compile(r"^ {0,3}(`{3,})([^`]*)$")


class GitError(RuntimeError):
    """git を走らせられなかった。違反 0 件と区別するために送出する。"""


# --- テキスト走査 -------------------------------------------------------------


def _mask_inline_code(line: str) -> str:
    """インラインコードの中身を空白へ潰す。列位置は保つ。

    N 連バッククォートは同じ長さの N 連で閉じる (CommonMark)。閉じないバッククォートは
    コードではなく素のテキストなので、そこから先は潰さない。
    """
    out = list(line)
    i, n = 0, len(line)
    while i < n:
        if line[i] != "`":
            i += 1
            continue
        start = i
        while i < n and line[i] == "`":
            i += 1
        run = i - start
        j, closed = i, None
        while j < n:
            if line[j] != "`":
                j += 1
                continue
            candidate = j
            while j < n and line[j] == "`":
                j += 1
            if j - candidate == run:
                closed = j
                break
        if closed is None:
            break
        for k in range(start, closed):
            out[k] = " "
        i = closed
    return "".join(out)


def _scan_line(line: str, label: str, lineno: int) -> list[str]:
    found = []
    for m in GITHUB_REF.finditer(line):
        before = line[: m.start()]
        # 免除は truthy を要求する。endswith("") は常に真なので、定数を空にする変更が
        # 「全部通す」方向へ静かに広がる。免除は fail closed 側へ倒す
        if GITHUB_REF_ALLOWED_PREFIX and before.endswith(GITHUB_REF_ALLOWED_PREFIX):
            continue
        if CROSS_REPO_REF.search(before):
            continue
        found.append(
            f"{label}:{lineno}: {m.group(0)} は GitHub の番号空間を指す。"
            f"in-repo Issue なら {PREFIX}{m.group(1)} と書く。"
            f"自リポの PR なら '{GITHUB_REF_ALLOWED_PREFIX}' を、"
            "他リポなら 'owner/repo' を直前に置く"
        )
    return found


def scan_text(text: str, label: str) -> list[str]:
    """テキスト 1 本を走査し、違反を 1 件 1 行の文字列で返す。"""
    violations: list[str] = []
    fence_len = 0
    fence_opened_at = 0
    for lineno, line in enumerate(text.splitlines(), 1):
        marker = FENCE_LINE.match(line)
        if fence_len:
            if marker and not marker.group(2).strip() and len(marker.group(1)) >= fence_len:
                fence_len = 0
            continue
        if marker:
            fence_len = len(marker.group(1))
            fence_opened_at = lineno
            continue
        violations.extend(_scan_line(_mask_inline_code(line), label, lineno))
    if fence_len:
        violations.append(
            f"{label}:{fence_opened_at}: コードフェンスが閉じていない "
            f"(バッククォート {fence_len} 個以上で閉じる)。"
            "閉じ忘れは以降の全行を無検査にする"
        )
    return violations


# --- git ---------------------------------------------------------------------


def _git(root: Path, *args: str) -> bytes:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, check=False
        )
    except FileNotFoundError as e:
        raise GitError("git が見つからない") from e
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip() or f"rc={proc.returncode}"
        raise GitError(f"git {' '.join(args)} に失敗した: {detail}")
    return proc.stdout


def resolve_root(explicit: str | None) -> Path:
    if explicit is not None:
        return Path(explicit).resolve()
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, check=False
        )
    except FileNotFoundError as e:
        raise GitError("git が見つからない") from e
    if proc.returncode != 0:
        raise GitError("git リポジトリの root を特定できない (--root で指定する)")
    return Path(proc.stdout.decode("utf-8").strip())


def _refs(root: Path) -> list[str]:
    out = _git(root, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes")
    return [r for r in out.decode("utf-8", "replace").splitlines() if r]


def _ls_tree(root: Path, ref: str) -> list[str]:
    """ref が持つ docs/issues 配下のファイルパスを返す。"""
    # -r: 配下まで再帰する。付けないと docs/issues/closed が tree 1 件として出るだけで
    #     配下が列挙されず、ブランチ内で起票と close を同梱した Issue を取りこぼす (実測)
    # -z: 既定出力は非 ASCII のパスを C クォートする (実測: `"docs/issues/ISSUE-1_\343\201\202"`)。
    #     日本語タイトルのディレクトリが全滅し、しかもエラーではなく短い正常な結果で返る
    try:
        out = _git(root, "ls-tree", "-r", "-z", "--name-only", ref, "/".join(ISSUE_ROOT) + "/")
    except GitError:
        # docs/issues を持たない ref は静かに飛ばす (実測ではこの経路は rc 0 / 空出力だが、
        # 壊れた ref もここへ来るので採番全体を止めない)
        return []
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


def _tracked_files(root: Path) -> list[str]:
    out = _git(root, "ls-files", "-z")
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


# --- Issue ディレクトリ --------------------------------------------------------


def issue_dirs(root: Path) -> list[tuple[str, str]]:
    """現ツリーの Issue ディレクトリを (リポジトリ相対パス, ディレクトリ名) で返す。"""
    base = root.joinpath(*ISSUE_ROOT)
    entries = []
    for parent, skip in ((base, {TEMPLATES, CLOSED}), (base / CLOSED, {TEMPLATES})):
        if not parent.is_dir():
            continue
        for child in sorted(parent.iterdir()):
            if child.is_dir() and child.name not in skip:
                entries.append((child.relative_to(root).as_posix(), child.name))
    return entries


def issue_dir_of(path: str) -> tuple[str, str] | None:
    """docs/issues 配下のファイルパスから (ディレクトリ名, ディレクトリの相対パス) を返す。"""
    parts = path.split("/")
    if tuple(parts[:2]) != ISSUE_ROOT:
        return None
    depth = len(ISSUE_ROOT)
    if len(parts) > depth and parts[depth] == CLOSED:
        depth += 1
    # ls-tree -r が返すのはファイルなので、Issue ディレクトリ配下なら名前の後ろに必ず
    # 1 要素以上ある。無いものは docs/issues 直下のファイルで Issue ディレクトリではない
    if len(parts) <= depth + 1:
        return None
    name = parts[depth]
    if name == TEMPLATES:
        return None
    return name, "/".join(parts[: depth + 1])


def _identity(name: str) -> str:
    """接頭辞の有無を無視したディレクトリの同一性キー。"""
    return name[len(PREFIX) :] if name.startswith(PREFIX) else name


def collect_numbers(root: Path) -> dict[int, dict[str, set[str]]]:
    """番号 -> 同一性キー -> 出所の集合。現ツリーと全 ref を混ぜて集める。"""
    found: dict[int, dict[str, set[str]]] = {}

    def record(name: str, where: str) -> None:
        m = ANY_ISSUE_DIR.match(name)
        if m:
            found.setdefault(int(m.group(1)), {}).setdefault(_identity(name), set()).add(where)

    for rel, name in issue_dirs(root):
        record(name, f"現ツリー: {rel}")
    for ref in _refs(root):
        for path in _ls_tree(root, ref):
            got = issue_dir_of(path)
            if got:
                record(got[0], f"{ref}: {got[1]}")
    return found


def _duplicates(found: dict[int, dict[str, set[str]]]) -> list[str]:
    violations = []
    for number, identities in sorted(found.items()):
        if len(identities) > 1:
            wheres = sorted(w for group in identities.values() for w in group)
            violations.append(
                f"番号 {number} が別々の Issue で使われている: {' | '.join(wheres)}"
            )
    return violations


# --- 入口 ---------------------------------------------------------------------


def _report(violations: list[str]) -> None:
    for v in violations:
        print(f"  [x] {v}", file=sys.stderr)


def run_next(root: Path) -> int:
    found = collect_numbers(root)
    violations = _duplicates(found)
    if violations:
        _report(violations)
        return 1
    print(f"{PREFIX}{(max(found) if found else 0) + 1}")
    return 0


def run_check(root: Path) -> int:
    violations: list[str] = []
    entries = issue_dirs(root)
    for rel, name in entries:
        if not ISSUE_DIR.match(name):
            violations.append(f"{rel}: ディレクトリ名が {PREFIX}<N>_<title> 形式でない")

    by_number: dict[int, list[str]] = {}
    for rel, name in entries:
        m = ANY_ISSUE_DIR.match(name)
        if m:
            by_number.setdefault(int(m.group(1)), []).append(rel)
    for number, rels in sorted(by_number.items()):
        if len(rels) > 1:
            violations.append(f"番号 {number} が重複している: {' | '.join(sorted(rels))}")

    tracked = _tracked_files(root)
    if not tracked:
        # 0 件は「違反なし」ではなく「何も見ていない」。緑にすると走査対象を失った状態が
        # 合格に化ける
        print("[x] 追跡下のファイルが 1 件も無い。走査対象ゼロは合格ではない", file=sys.stderr)
        return 2

    scanned = excluded = unreadable = 0
    for rel in tracked:
        if Path(rel).suffix in SKIP_SUFFIXES:
            excluded += 1
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            unreadable += 1
            continue
        scanned += 1
        violations.extend(scan_text(text, rel))

    _report(violations)
    print(
        f"検査した Issue ディレクトリ: {len(entries)} 個 / 走査したファイル: {scanned} 個 "
        f"(拡張子で除外 {excluded} / 読めずに飛ばした {unreadable})"
    )
    if violations:
        print(f"違反 {len(violations)} 件")
        return 1
    print("違反なし")
    return 0


def run_check_text(source: str) -> int:
    label = "stdin" if source == "-" else source
    if source == "-":
        text = sys.stdin.read()
    else:
        try:
            text = Path(source).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as e:
            print(f"[x] {source} を読めない: {e}", file=sys.stderr)
            return 2
    violations = scan_text(text, label)
    _report(violations)
    print(f"走査した行: {len(text.splitlines())} 行 / 違反 {len(violations)} 件")
    return 1 if violations else 0


def main(argv: list[str] | None = None) -> int:
    # allow_abbrev の既定 (True) は `--che` のような短縮を別モードとして受理する。
    # typo が静かに別の入口へ落ちないよう完全形の明示だけに絞る
    parser = argparse.ArgumentParser(
        description="in-repo Issue の識別子を採番し、GitHub 記法との混同を検査する",
        allow_abbrev=False,
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--next", action="store_true", help="次の識別子を 1 行で印字する")
    mode.add_argument("--check", action="store_true", help="リポジトリを走査する")
    mode.add_argument(
        "--check-text", metavar="PATH", help="テキスト 1 本を走査する (- で標準入力)"
    )
    parser.add_argument(
        "--root", metavar="PATH", help="リポジトリの root (既定: git rev-parse --show-toplevel)"
    )
    args = parser.parse_args(argv)

    try:
        if args.check_text is not None:
            return run_check_text(args.check_text)
        root = resolve_root(args.root)
        return run_next(root) if args.next else run_check(root)
    except GitError as e:
        print(f"[x] {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
