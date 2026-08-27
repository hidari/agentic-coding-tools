#!/usr/bin/env python3
"""in-repo Issue の識別子を採番し、GitHub の数字記法との混同を検出する。

in-repo Issue は `docs/issues/<dir>/` というディレクトリなので GitHub の Issue/PR カウンタを
消費しない。にもかかわらず参照を GitHub と同じ数字記法で書くと、同じ番号空間の GitHub
オブジェクトとどちらを指すのか文脈からしか判別できなくなる。GitHub の autolink が既定で
反応するのは数字記法だけなので、識別子を `ISSUE-<N>` にすれば距離ではなく種類で分離できる。
番号がいくつまで伸びても交わらない。

この規約の canonical はこのファイル。SKILL.md も CLAUDE.md も regex も接頭辞も再掲せず、
ファイル名で参照する。

規則は 1 つ、エンジンは 1 つ、入口は 4 つ。

  規則:     数字記法は GitHub 専用。直前が `PR ` (自リポの PR) か `owner/repo` (他リポ) の
            ときだけ許す。in-repo Issue を指す唯一の形は `ISSUE-<N>`
  エンジン: scan_text() 1 本。入口ごとに規則が分岐しない
  入口:     --next (採番) / --check (リポジトリ全走査) / --check-diff (差分だけ) /
            --check-text (テキスト 1 本)

--check-diff は見る範囲だけを差分へ絞る。規則もエンジンも --check と共有する。既存の違反を
どこにも記録せずに免除するので、増分が見落とした違反はそのまま恒久的な baseline になる。
これは設計上の選択で、検査の取り付けと既存ディレクトリの一括 rename を切り離すために
引き受けている。範囲を絞る対象は「ディレクトリ名の形式」と「数字記法」だけで、番号の重複は
絞っても逃げられる先が無いので全体を見たままにする。

終了コードは 0 (合格) / 1 (違反あり) / 2 (検査不能)。2 を 1 と分けるのは
scripts/check-leak-guard-rules.py と同じ理由で、「規約違反」と「検査を走らせられなかった」を
同じ赤にすると git が無い状態がルール違反に見えるため。--check は追跡下のファイルが 1 件も
無い場合も 2 にする。違反 0 件で緑にすると「何も見ていない」が「合格」に化ける。
--check-diff の空差分は 0 で返す。差分が無いことは正常な状態で、走査の要約が空を明示する。

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
  active と closed の両方にある形も重複として報告する。--check-diff は差分の後側 (index か
  HEAD の tree) のパスを鍵にするので、未追跡の Issue ディレクトリを数に入れない
- その帰結として、--next はブランチ間でタイトル部の改名を伴う移動を別の Issue と見て重複を
  報告する (偽陽性側に倒している)。重複と判定されると --next は識別子を出さず exit 1 で
  止まる。つまり新規起票が一切できなくなるので、Issue ディレクトリを rename するときは
  番号もタイトル部も保存し、接頭辞の付与だけに留めること
- --check-diff の免除は記録に残らないので、件数を監査できない。--check が全走査の backstop
  になる。取り付ける側は増分だけに頼らないこと
- --check-diff は rename の回転やスワップを rename として見ない。git の rename 検出は片側に
  しか無いパスどうしをマッチさせるので、中間のパスが両側に存在する形 (A から B への移動と
  同時に B から C への移動) は削除・変更・追加として出る (実測)。移動しただけの既存違反が
  「追加行」として報告される。偽陽性の方向なので見落としにはならない。Issue ディレクトリの
  番号を入れ替える操作は --next の限界からも避けるべきなので、この形は想定していない
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

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

# 増分モードでディレクトリ名の検査を起こす唯一のファイル。配下の任意ファイルを起点にすると、
# 旧記法のディレクトリを触るたびに赤くなり、増分の ratchet が成立しない
ISSUE_FILE = "issue.md"

# gitlink (submodule) の mode。差分には hunk 付きで出るが blob を持たないので内容が取れない
GITLINK_MODE = "160000"

SKIP_SUFFIXES = {".css", ".scss"}

# フェンスは N 連バッククォート (3 個以上) で開き、同じ N 個以上で閉じる (CommonMark)。
# 開き行は情報文字列を持てるがバッククォートは含められない。閉じ行はバッククォートだけ。
# 3 連で開いて 4 連で閉じる形は「閉じている」であって閉じ忘れではない
FENCE_LINE = re.compile(r"^ {0,3}(`{3,})([^`]*)$")

# `@@ -<旧開始>,<旧行数> +<新開始>,<新行数> @@` の新側だけを読む。行数は 1 のとき省略される
HUNK_HEADER = re.compile(r"^@@ -[0-9]+(?:,[0-9]+)? \+([0-9]+)(?:,([0-9]+))? @@")

KIND_BARE_REF = "bare-ref"
KIND_UNCLOSED_FENCE = "unclosed-fence"


class Violation(NamedTuple):
    """走査で見つけた違反 1 件。

    message は位置を含む完成した表示文字列で、lineno と kind は増分モードが絞り込みに使う。
    行番号を 2 つの形で持つが、両方を 1 箇所で組むので食い違わない。message を parse して
    行番号を取り出す実装だと、label にコロンを含むパスで静かに壊れる。

    kind を持つのは、閉じ忘れフェンスだけを行番号の絞り込みから外すため。メッセージの文言で
    見分ける実装は、文言を変えた瞬間に絞り込みが黙って全通しへ倒れる。
    """

    lineno: int
    kind: str
    message: str


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


def _split_lines(text: str) -> list[str]:
    """`\n` だけで行を割る。

    str.splitlines() は `\v` `\f` `\x1c`-`\x1e` NEL `U+2028` `U+2029` と単独の `\r` も行境界に
    するが、git の diff が行区切りにするのは `\n` だけである (実測: `b\fSEE` を含む追加は
    hunk では 1 行、splitlines では 2 行になる)。ずれると増分モードで違反の行番号が追加行集合
    から外れ、違反 0 件の緑で返る。--check の行番号も git と食い違うので、両方ここへ寄せる。

    末尾の改行が作る空要素は落とす。落とさないと最終行の次に存在しない行が 1 つ増える
    """
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _scan_line(line: str, label: str, lineno: int) -> list[Violation]:
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
            Violation(
                lineno,
                KIND_BARE_REF,
                f"{label}:{lineno}: {m.group(0)} は GitHub の番号空間を指す。"
                f"in-repo Issue なら {PREFIX}{m.group(1)} と書く。"
                f"自リポの PR なら '{GITHUB_REF_ALLOWED_PREFIX}' を、"
                "他リポなら 'owner/repo' を直前に置く",
            )
        )
    return found


def scan_text(text: str, label: str) -> list[Violation]:
    """テキスト 1 本を走査し、違反を返す。"""
    violations: list[Violation] = []
    fence_len = 0
    fence_opened_at = 0
    for lineno, line in enumerate(_split_lines(text), 1):
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
            Violation(
                fence_opened_at,
                KIND_UNCLOSED_FENCE,
                f"{label}:{fence_opened_at}: コードフェンスが閉じていない "
                f"(バッククォート {fence_len} 個以上で閉じる)。"
                "閉じ忘れは以降の全行を無検査にする",
            )
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


def _diff_records(root: Path, rng: str, *filters: str) -> list[tuple[str, str | None, str]]:
    """差分のレコードを (種別, 旧パス, 新パス) で返す。旧パスは rename / copy のときだけ入る。

    -M を明示するのは rename 検出を diff.renames の設定から切り離すためで、既定に任せると
    マシンの設定で検査結果が変わる (実測: 設定を off にしても -M があれば R100 が出る)。
    罠 3 で --diff-filter=AR にしたのは症状への対処で、-M が原因への対処にあたる。

    旧パスが要るのは「名前が変わったのか、位置だけ変わったのか」を分けるため。Issue を
    docs/issues 直下から closed/ へ移す操作は名前を変えないので、名前検査の対象にできない。

    -z を使うのは _ls_tree と同じ理由で、既定出力は非 ASCII を含むパスを C クォートする
    (実測)。-z の --name-status は 種別とパスを NUL で区切って並べ、R と C だけ
    種別・旧パス・新パスの 3 要素になる (実測)
    """
    out = _git(root, "diff", rng, "-M", *filters, "-z", "--name-status")
    fields = [f for f in out.decode("utf-8", "replace").split("\0") if f]
    records: list[tuple[str, str | None, str]] = []
    i = 0
    # 末尾が 1 要素だけ残った場合は落とす。R / C なのに 2 要素しか残っていない形は 2 要素
    # レコードとして読むが、git が成功して返す出力ではこの形にならない (_git は非 0 で
    # GitError を送出するので、途中で切れた出力はそもそもここへ来ない)
    while i + 1 < len(fields):
        kind = fields[i][:1]
        if kind in ("R", "C") and i + 2 < len(fields):
            records.append((kind, fields[i + 1], fields[i + 2]))
            i += 3
        else:
            records.append((kind, None, fields[i + 1]))
            i += 2
    return records


def _added_lines(
    root: Path, rng: str, path: str, old_path: str | None = None
) -> set[int]:
    """path の差分で後側に増えた行番号を返す。

    pathspec は :(literal) で渡す。既定の pathspec は `*` `?` `[` を glob として解釈するので、
    それらを含むパスが別のファイルに当たるか 1 件も当たらない。

    rename のときは旧パスも渡す。新パスだけに絞ると git は rename の対応付けを失い
    `new file mode` として全行を追加行で返す (実測)。Issue を closed/ へ移す操作がこれに当たり、
    本文の既存違反が丸ごと「追加行」として再浮上する。旧パスを添えると、内容が同じ移動は
    hunk 0 件、内容を変えた移動は変えた行だけになる。

    --text を付けるのは、gitattributes で `-diff` が付いたファイルを git が binary として扱い
    hunk を 1 件も出さないため (実測)。hunk が空だと追加行集合も空になり、そのファイルの違反が
    全部免除される。結果は違反 0 件の緑で返るので出力を見ても気づけない。ここへ来るのは
    _blob_text が UTF-8 として読めたファイルだけなので、--text が本物の binary を展開しない
    """
    specs = [f":(literal){path}"]
    if old_path is not None:
        specs.append(f":(literal){old_path}")
    out = _git(root, "diff", rng, "-M", "-U0", "--text", "--", *specs)
    added: set[int] = set()
    for raw in out.decode("utf-8", "replace").splitlines():
        m = HUNK_HEADER.match(raw)
        if not m:
            continue
        start = int(m.group(1))
        count = 1 if m.group(2) is None else int(m.group(2))
        added.update(range(start, start + count))
    return added


def _parse_entries(out: bytes, oid_index: int) -> dict[str, tuple[str, str]]:
    """`<mode> ...<TAB><path>` の NUL 区切りレコードを パス -> (mode, oid) で読む。

    ls-files -s は `<mode> <oid> <stage>`、ls-tree は `<mode> <type> <oid>` と oid の位置が
    違うだけで、他は同じ形をしている
    """
    entries: dict[str, tuple[str, str]] = {}
    for record in out.decode("utf-8", "replace").split("\0"):
        if not record:
            continue
        meta, sep, path = record.partition("\t")
        fields = meta.split()
        if not sep or len(fields) <= oid_index:
            continue
        entries[path] = (fields[0], fields[oid_index])
    return entries


def _index_entries(root: Path) -> dict[str, tuple[str, str]]:
    return _parse_entries(_git(root, "ls-files", "-s", "-z"), oid_index=1)


def _tree_entries(root: Path, ref: str) -> dict[str, tuple[str, str]]:
    return _parse_entries(_git(root, "ls-tree", "-r", "-z", ref), oid_index=2)


def _blob_text(root: Path, oid: str) -> str | None:
    """blob の中身を返す。UTF-8 で読めなければ None。

    cat-file の失敗は握らない。gitlink を mode で除いたあとの oid は index / tree が持って
    いるものなので、失敗はリポジトリの破損を意味する。握って「読めずに飛ばした」に数えると
    壊れたリポジトリが違反 0 件の緑になる。実在しない oid は update-index で index へ入り、
    パス列挙にも差分にも出るので、この経路は到達不能ではない (実測)
    """
    try:
        return _git(root, "cat-file", "blob", oid).decode("utf-8")
    except UnicodeDecodeError:
        return None


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


def _dir_name_violations(entries: list[tuple[str, str]]) -> list[str]:
    """ディレクトリ名が識別子の形式でないものを報告する。

    渡す集合は入口ごとに違う。--check は現ツリーの全 Issue、--check-diff は新しく現れた名前
    だけを渡す。増分が絞るのは対象の集合であって規則ではないので、判定と文言はここで共有する
    """
    return [
        f"{rel}: ディレクトリ名が {PREFIX}<N>_<title> 形式でない"
        for rel, name in entries
        if not ISSUE_DIR.match(name)
    ]


def _number_conflicts(entries: list[tuple[str, str]]) -> list[str]:
    """同じ番号を持つ Issue ディレクトリを報告する。

    絞っても逃げられる先が無いので、増分モードでも全体を見たまま共有する
    """
    by_number: dict[int, list[str]] = {}
    for rel, name in entries:
        m = ANY_ISSUE_DIR.match(name)
        if m:
            by_number.setdefault(int(m.group(1)), []).append(rel)
    return [
        f"番号 {number} が重複している: {' | '.join(sorted(rels))}"
        for number, rels in sorted(by_number.items())
        if len(rels) > 1
    ]


def _issue_dirs_from_paths(paths: list[str]) -> list[tuple[str, str]]:
    """追跡下のパス一覧から Issue ディレクトリを (相対パス, 名前) で返す。

    issue_dirs() と同じ形を返すのでタプルの順序もそちらに合わせてある (issue_dir_of は
    (名前, 相対パス) の順なので、単複の対応に見えて順序が逆になる点に注意)。

    issue_dirs() は filesystem を見るので、部分 stage のときに index と食い違う。増分モードは
    走査対象を差分の後側へ統一するのでこちらを使う。追跡されていない Issue ディレクトリ (git が
    追跡しない空ディレクトリを含む) が issue_dirs() との差になる。--check 側は backstop なので
    追跡前のものも見る。この非対称は意図したもので、両側にテストを置いてある
    """
    found: dict[str, str] = {}
    for path in paths:
        got = issue_dir_of(path)
        if got:
            found[got[1]] = got[0]
    return sorted(found.items())


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


def _finish(violations: list[str], summary: str) -> int:
    """違反と走査の要約を出して終了コードを返す。

    「違反 0 件を緑にする」はこの機構が最も守りたい判定なので、入口ごとに書かない。
    2 箇所にあると片方だけが緑へずれても、機械検査は何も言わない
    """
    _report(violations)
    print(summary)
    if violations:
        print(f"違反 {len(violations)} 件")
        return 1
    print("違反なし")
    return 0


def run_next(root: Path) -> int:
    found = collect_numbers(root)
    violations = _duplicates(found)
    if violations:
        _report(violations)
        return 1
    print(f"{PREFIX}{(max(found) if found else 0) + 1}")
    return 0


def run_check(root: Path) -> int:
    entries = issue_dirs(root)
    violations = _dir_name_violations(entries)
    violations.extend(_number_conflicts(entries))

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
        violations.extend(v.message for v in scan_text(text, rel))

    return _finish(
        violations,
        f"検査した Issue ディレクトリ: {len(entries)} 個 / 走査したファイル: {scanned} 個 "
        f"(拡張子で除外 {excluded} / 読めずに飛ばした {unreadable})",
    )


def run_check_diff(root: Path, base: str | None) -> int:
    if base is None:
        rng, label = "--cached", "index"
        blobs = _index_entries(root)
    else:
        # three-dot にする。two-dot だと base 側にしか無いコミットが「削除」として差分へ
        # 混ざる (実測)。見たいのは分岐点から現在までの変更なので merge-base 基準が正しい
        rng = label = f"{base}...HEAD"
        blobs = _tree_entries(root, "HEAD")

    # ディレクトリ名は、新しく現れた名前だけを見る。起点を issue.md に限るのは、配下の
    # 任意ファイルを起点にすると旧記法のディレクトリを触るたび赤くなるため。
    # rename で位置だけが変わったもの (docs/issues 直下から closed/ への移動) を外すのは
    # 名前が 1 文字も変わっていないためで、ここを分けないと旧記法の Issue を閉じるという
    # 日常操作のたびに赤くなり、増分の ratchet が成立しない (実測)
    new_dirs: list[tuple[str, str]] = []
    for _kind, old, path in _diff_records(root, rng, "--diff-filter=AR"):
        got = issue_dir_of(path)
        if got is None:
            continue
        name, rel = got
        if path != f"{rel}/{ISSUE_FILE}":
            continue
        if old is not None:
            was = issue_dir_of(old)
            if was is not None and was[0] == name:
                continue
        new_dirs.append((rel, name))

    violations = _dir_name_violations(new_dirs)
    violations.extend(_number_conflicts(_issue_dirs_from_paths(list(blobs))))

    scanned = excluded = unreadable = submodules = 0
    added_total = 0
    # d (小文字) は「削除以外」。削除されたファイルは index からも消えるので内容が取れず、
    # 入口で落とさないと「読めずに飛ばした」に化けて理由が要約から読めなくなる
    for _kind, old, path in _diff_records(root, rng, "--diff-filter=d"):
        if Path(path).suffix in SKIP_SUFFIXES:
            excluded += 1
            continue
        entry = blobs.get(path)
        if entry is None:
            # 差分に出たパスが index / tree に無い状態。--diff-filter=d で削除を除いてある
            # ので通常は起きないが、起きたときに黙って数を合わせず要約へ出す
            unreadable += 1
            continue
        mode, oid = entry
        if mode == GITLINK_MODE:
            submodules += 1
            continue
        # 走査するのは差分の後側の内容。worktree を走査すると部分 stage で行番号がずれ、
        # コミットされる違反が絞り込みから落ちる (逆向きの偽陽性も同じ根から出る)
        text = _blob_text(root, oid)
        if text is None:
            unreadable += 1
            continue
        added = _added_lines(root, rng, path, old)
        added_total += len(added)
        scanned += 1
        for v in scan_text(text, path):
            # 閉じ忘れは行番号で絞らない。base のフェンスが開いたままだと、追加行の違反は
            # 「フェンス内」として消え、閉じ忘れ自体は旧行番号に付くので絞り込みでも消える。
            # 両方消えると完全な緑になる (実測)。閉じ忘れが出た増分は検証不能とみなす
            if v.kind == KIND_UNCLOSED_FENCE or v.lineno in added:
                violations.append(v.message)

    # 何を何件見たかを必ず出す。これが無いと配線ミス (base が常に HEAD と一致する等) と
    # 正常な空コミットが同じ見た目になる
    return _finish(
        violations,
        f"差分 {label}: 追加行: {added_total} 行 / 走査したファイル: {scanned} 個 / "
        f"検査した Issue ディレクトリ: {len(new_dirs)} 個 "
        f"(拡張子で除外 {excluded} / 読めずに飛ばした {unreadable} / submodule {submodules})",
    )


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
    _report([v.message for v in violations])
    print(f"走査した行: {len(_split_lines(text))} 行 / 違反 {len(violations)} 件")
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
    mode.add_argument("--check", action="store_true", help="リポジトリ全体を走査する")
    mode.add_argument("--check-diff", action="store_true", help="差分だけを走査する")
    mode.add_argument(
        "--check-text", metavar="PATH", help="テキスト 1 本を走査する (- で標準入力)"
    )
    parser.add_argument(
        "--base", metavar="REF", help="--check-diff の基準 ref (既定: index と HEAD の差分)"
    )
    parser.add_argument(
        "--root", metavar="PATH", help="リポジトリの root (既定: git rev-parse --show-toplevel)"
    )
    args = parser.parse_args(argv)
    if args.base is not None and not args.check_diff:
        # 静かに無視すると「base を指定したのに index を見ていた」形になり、意図と違う範囲で
        # 緑が出る。範囲の取り違えは出力を見ても気づけない
        parser.error("--base は --check-diff と一緒にしか使えない")

    try:
        if args.check_text is not None:
            return run_check_text(args.check_text)
        root = resolve_root(args.root)
        if args.next:
            return run_next(root)
        if args.check_diff:
            return run_check_diff(root, args.base)
        return run_check(root)
    except GitError as e:
        print(f"[x] {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
