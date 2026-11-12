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
- --next は採番が数えられない名前 (旧形式の <N>_ にも当たらないもの) のディレクトリが現ツリーか
  どれか 1 つの ref にあるだけで、識別子を出さず exit 1 で止まる。Issue ではない補助ディレクトリ
  (未追跡や隠しディレクトリを含む) も同じく止める。名前も置き場も規約で決まっていて動かせない
  ものは、DECLARATION で宣言すれば止まらない (宣言の置き場と形は DECLARATION、宣言できない
  名前は _declaration_problem が持つ)。宣言は名前で外すので、宣言した名前の中に番号を
  持ちうるディレクトリがあれば止める (どれを止めるかは _may_hide_number が持つ)。
  ref にしか無い名前は --check が見ないので、--check が緑でも --next は止まりうる。
  remote-tracking ref は手元で消しても次の fetch で戻るので、上流のブランチで改名するか、
  上流で消えたものを prune するまで止まり続ける
- 宣言は --next と --check が作業ツリーから、--check-diff が差分の後側 (index か HEAD の
  tree) から読む。--check-diff は走査の内容を後側に揃えているので、宣言だけを作業ツリーから
  読むと、stage していない宣言の編集が増分の判定を変える
- --check は追跡下のファイルに加え、Issue ディレクトリ配下の未追跡で ignore されていない
  ファイルも読む。pre-commit は未追跡のファイルを stash しないので、違反のある書きかけの
  issue.md を git add せずに置いているあいだは、無関係なコミットでも hook が赤くなる。
  起票した直後の issue.md を読まずに緑を返す方 (実測) を避けるために引き受けている
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
import json
import os
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

# Issue ではないディレクトリの名前をリポジトリの側で足す宣言。リポジトリ直下に置き、
# {"non_issue_dirs": ["<名前>", ...]} の形で書く。docs/issues 直下と closed/ 直下の両方で
# templates/ と同じ扱いになる。規約で名前の決まった補助ディレクトリ (雛形の置き場や資料の
# 保管庫) は、形では Issue をまとめたディレクトリと見分けられないので名前で宣言させる。
# 宣言は作業ツリーのものを全 ref の走査に当てる。ref ごとに読むと、補助ディレクトリを持つ
# 全ブランチが宣言を取り込むまで採番が止まったままになる
DECLARATION = ".issue-id.json"
DECLARATION_KEY = "non_issue_dirs"

# 宣言できない名前の形。数字で始まる名前は番号を持ちうる (2-b は番号 2 の Issue でありうる)
# ので、外すとその番号を再発行する。採番が数える形 (ANY_ISSUE_DIR) もここに含まれる
NUMBER_LEAD = re.compile(r"^(?:" + re.escape(PREFIX) + r")?[0-9]")

# --next が走査する ref の名前空間。--next が止まったときの直し方がどちらかで変わる
LOCAL_REFS = "refs/heads/"
REMOTE_REFS = "refs/remotes/"

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


class UncheckableError(RuntimeError):
    """検査を走らせられなかった。違反 0 件 (rc 0) とも違反あり (rc 1) とも区別して rc 2 にする。

    入口と借用する検査はこの型だけを捕まえる。派生を足しても捕まえる側を直さずに済む
    """


class GitError(UncheckableError):
    """git を走らせられなかった。"""


class DeclarationError(UncheckableError):
    """Issue ではないディレクトリの宣言を読めない。

    違反ではなく検査不能として止める。黙って無視すると宣言したつもりの名前で止まり続け、
    黙って受けると番号を隠す名前まで外してしまう
    """


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
        # 列挙は root を起点に docs/issues を相対で引くので、サブディレクトリを root にすると
        # 配下しか見ず、重複した識別子や「0 個 / 違反なし」を rc 0 で返した (実測)。
        # top-level へ読み替える形は、入れ子の docs/issues を指す呼び出しを同じ形で黙って
        # 壊した (実測) ので、top-level 以外は止める。判定はパスを比べずに show-prefix が
        # 空かどうかで見る。パスの比較は大小を区別しない FS で正しい root を拒否した (実測)。
        # show-prefix は作業ツリーの外 (.git の中、bare) でも空を返し、.git を root にした
        # --check が「0 個 / 違反なし」の rc 0 になった (実測) ので、同じ呼び出しで
        # is-inside-work-tree も聞く
        out = _git(Path(explicit), "rev-parse", "--is-inside-work-tree", "--show-prefix")
        inside, _, prefix = out.decode("utf-8").partition("\n")
        prefix = prefix.removesuffix("\n")
        if inside != "true":
            raise GitError(
                f"--root はリポジトリの top-level を指すこと: {explicit} は作業ツリーの外にある"
            )
        if prefix:
            raise GitError(
                f"--root はリポジトリの top-level を指すこと: {explicit} は top-level の下の {prefix} にある"
            )
        return Path(explicit).resolve()
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, check=False
        )
    except FileNotFoundError as e:
        raise GitError("git が見つからない") from e
    if proc.returncode != 0:
        raise GitError("git リポジトリの root を特定できない (--root で指定する)")
    # strip() は名前の末尾の空白まで落とし、存在しないパスになる (実測)。rstrip("\n") も
    # 名前の末尾の改行を落とす。落とすのは git が足す改行 1 つだけ
    return Path(proc.stdout.decode("utf-8").removesuffix("\n"))


def _refs(root: Path) -> list[str]:
    out = _git(root, "for-each-ref", "--format=%(refname)", LOCAL_REFS, REMOTE_REFS)
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


def _untracked_issue_files(root: Path, non_issue: frozenset[str]) -> list[str]:
    """Issue ディレクトリ配下の、未追跡で ignore されていないファイルを返す。

    issue_dirs() は filesystem を見るので起票直後のディレクトリを数えるが、本文の走査が
    追跡下だけだと、その issue.md を読まずに緑を返した (実測)。母集団を Issue ディレクトリに
    限るのは、それ以外の未追跡は作業中のスクラッチで、赤くする理由が無いため。
    intent-to-add (git add -N) のファイルは index にあるので --others に出ず、追跡下の側と
    重ならない (実測)
    """
    out = _git(
        root, "ls-files", "-z", "--others", "--exclude-standard", "--", "/".join(ISSUE_ROOT) + "/"
    )
    return [
        p
        for p in out.decode("utf-8", "replace").split("\0")
        if p and issue_dir_of(p, non_issue)
    ]


def _diff_records(root: Path, rng: str, *filters: str) -> list[tuple[str, str | None, str]]:
    """差分のレコードを (種別, 旧パス, 新パス) で返す。旧パスは rename / copy のときだけ入る。

    -M を明示するのは rename 検出を diff.renames の設定から切り離すためで、既定に任せると
    マシンの設定で検査結果が変わる (実測: 設定を off にしても -M があれば R100 が出る)。
    既存の Issue ディレクトリを違反名へ rename すると R になって --diff-filter=A から漏れる。
    --diff-filter=AR にしたのはその症状への対処で、-M が原因 (rename 検出が設定に依存する
    こと) への対処にあたる。

    旧パスが要るのは「名前が変わったのか、位置だけ変わったのか」を分けるため。Issue を
    docs/issues 直下から closed/ へ移す操作は名前を変えないので、名前検査の対象にできない。

    -z を使うのは _ls_tree と同じ理由で、既定出力は非 ASCII を含むパスを C クォートする
    (実測)。-z の --name-status は種別とパスを NUL で区切って並べ、R と C だけ
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


def _declaration_problem(name: object) -> str | None:
    """宣言できない名前なら理由を返す。"""
    if not isinstance(name, str) or not name:
        return "空でない文字列で書く"
    if name in (".", "..") or "/" in name or "\\" in name:
        return "ディレクトリ 1 段の名前で書く (パス区切りや . / .. は使えない)"
    if name == CLOSED:
        return f"{CLOSED} は閉じた Issue の置き場で、中の Issue を数える"
    if NUMBER_LEAD.match(name):
        return "数字で始まる名前は番号を持ちうるので外せない"
    return None


def load_non_issue_dirs(root: Path) -> frozenset[str]:
    """作業ツリーの宣言から Issue ではないディレクトリの名前を返す。"""
    path = root / DECLARATION
    if not path.exists():
        return parse_non_issue_dirs(None)
    try:
        data = path.read_bytes()
    except OSError as e:
        raise DeclarationError(f"{DECLARATION} を読めない: {e}") from e
    return parse_non_issue_dirs(data)


def parse_non_issue_dirs(data: bytes | None) -> frozenset[str]:
    """宣言の本文から、templates/ に宣言の分を足した名前の集合を返す。None は宣言なし。

    本文はバイト列で受ける。作業ツリーと blob の両方の読み出し元が、UTF-8 の検証をここへ寄せる
    """
    if data is None:
        return frozenset({TEMPLATES})
    try:
        data = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise DeclarationError(f"{DECLARATION} を読めない: {e}") from e
    # 知らないキーを黙って通すと、綴りを間違えた宣言が「宣言なし」として静かに効かなくなる
    if not isinstance(data, dict) or set(data) != {DECLARATION_KEY}:
        raise DeclarationError(
            f"{DECLARATION} は {DECLARATION_KEY} だけを持つオブジェクトで書く"
        )
    names = data[DECLARATION_KEY]
    if not isinstance(names, list):
        raise DeclarationError(f"{DECLARATION} の {DECLARATION_KEY} は名前の配列で書く")
    for name in names:
        problem = _declaration_problem(name)
        if problem:
            raise DeclarationError(
                f"{DECLARATION} の {DECLARATION_KEY} に宣言できない名前: {name!r} ({problem})"
            )
    return frozenset({TEMPLATES, *names})


def _issue_parents(root: Path) -> list[tuple[Path, set[str]]]:
    """Issue ディレクトリを直下に持つ場所と、そこで Issue として数えない名前。"""
    base = root.joinpath(*ISSUE_ROOT)
    return [(base, {CLOSED}), (base / CLOSED, set())]


def issue_dirs(root: Path, non_issue: frozenset[str]) -> list[tuple[str, str]]:
    """現ツリーの Issue ディレクトリを (リポジトリ相対パス, ディレクトリ名) で返す。"""
    entries = []
    for parent, skip in _issue_parents(root):
        if not parent.is_dir():
            continue
        skip = skip | non_issue
        for child in sorted(parent.iterdir()):
            if child.is_dir() and child.name not in skip:
                entries.append((child.relative_to(root).as_posix(), child.name))
    return entries


def _top_level(path: str) -> tuple[list[str], int] | None:
    """docs/issues 配下のファイルパスを要素に割り、直下のディレクトリの位置を返す。

    closed/ の中のファイルなら closed/ 直下の位置を返す。ディレクトリの配下に無いファイルは None
    """
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
    return parts, depth


def issue_dir_of(path: str, non_issue: frozenset[str]) -> tuple[str, str] | None:
    """docs/issues 配下のファイルパスから (ディレクトリ名, ディレクトリの相対パス) を返す。"""
    got = _top_level(path)
    if got is None:
        return None
    parts, depth = got
    if parts[depth] in non_issue:
        return None
    return parts[depth], "/".join(parts[: depth + 1])


def _may_hide_number(name: str, holds_issue_file: bool) -> bool:
    """Issue ではないディレクトリの中で、番号を持つ Issue でありうるディレクトリか。

    外したディレクトリの中に Issue が残っていると、その番号を数えずに再発行する。
    採番が数える形は中身を問わず止める。数字で始まるだけの名前 (2-b) は、直下に
    ISSUE_FILE を持つときだけ止める。持たないもの (年で分けた archive/2026/ など) まで
    止めると、規約で決まった保管庫を宣言しても起票できないままになる
    """
    return bool(ANY_ISSUE_DIR.match(name)) or (holds_issue_file and bool(NUMBER_LEAD.match(name)))


def hidden_issue_dir_of(path: str, non_issue: frozenset[str]) -> str | None:
    """Issue ではないディレクトリの中で、番号を持ちうるディレクトリの相対パスを返す。

    最後の要素はファイルなので名前としては見ない。最も外側のものを返す。現ツリーも ref も
    index もパスの列にしてここへ通すので、判定はここだけが持つ。ISSUE_FILE の有無も
    パスの文字列の完全一致で見るので、大小を区別しない FS でも読み出し元ごとに割れない
    """
    got = _top_level(path)
    if got is None:
        return None
    parts, depth = got
    if parts[depth] not in non_issue:
        return None
    last_dir = len(parts) - 2
    for i in range(depth + 1, last_dir + 1):
        if _may_hide_number(parts[i], i == last_dir and parts[-1] == ISSUE_FILE):
            return "/".join(parts[: i + 1])
    return None


def hidden_issue_dirs(root: Path, non_issue: frozenset[str]) -> list[str]:
    """現ツリーで hidden_issue_dir_of に当たるディレクトリを返す。

    filesystem を見るのは issue_dirs() と同じ理由で、未追跡のまま置いたものも数えるため。
    ファイルを 1 つも持たないディレクトリは拾わないが、Issue を持たないので番号も隠さない
    """
    paths = []
    for parent, _skip in _issue_parents(root):
        for name in non_issue:
            for dirpath, _dirnames, files in os.walk(parent / name):
                paths.extend((Path(dirpath) / f).relative_to(root).as_posix() for f in files)
    return sorted({hidden_issue_dir_of(p, non_issue) for p in paths} - {None})


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


def _issue_dirs_from_paths(
    paths: list[str], non_issue: frozenset[str]
) -> list[tuple[str, str]]:
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
        got = issue_dir_of(path, non_issue)
        if got:
            found[got[1]] = got[0]
    return sorted(found.items())


def _identity(name: str) -> str:
    """接頭辞の有無を無視したディレクトリの同一性キー。"""
    return name[len(PREFIX) :] if name.startswith(PREFIX) else name


class Numbering(NamedTuple):
    """--next が現ツリーと全 ref から集めたもの。"""

    # 番号 -> 同一性キー -> 出所の集合
    found: dict[int, dict[str, set[str]]]
    # ANY_ISSUE_DIR に当たらないディレクトリ: 相対パス -> 出所 (WORKING_TREE か ref 名) の集合。
    # 番号を読めないので found に入れられない。パスで束ねるのは、同じディレクトリが ref の数だけ
    # 別の行に出て読めなくなるため
    unrecognized: dict[str, set[str]]
    # Issue ではないディレクトリの中で番号を持つディレクトリ: 相対パス -> 出所の集合
    hidden: dict[str, set[str]]


WORKING_TREE = "現ツリー"


def collect_numbers(root: Path, non_issue: frozenset[str]) -> Numbering:
    """現ツリーと全 ref を混ぜて Issue ディレクトリの番号を集める。"""
    found: dict[int, dict[str, set[str]]] = {}
    unrecognized: dict[str, set[str]] = {}
    hidden: dict[str, set[str]] = {}

    def record(name: str, source: str, rel: str) -> None:
        m = ANY_ISSUE_DIR.match(name)
        if m:
            where = f"{source}: {rel}"
            found.setdefault(int(m.group(1)), {}).setdefault(_identity(name), set()).add(where)
        else:
            unrecognized.setdefault(rel, set()).add(source)

    for rel, name in issue_dirs(root, non_issue):
        record(name, WORKING_TREE, rel)
    for rel in hidden_issue_dirs(root, non_issue):
        hidden.setdefault(rel, set()).add(WORKING_TREE)
    for ref in _refs(root):
        for path in _ls_tree(root, ref):
            got = issue_dir_of(path, non_issue)
            if got:
                record(got[0], ref, got[1])
            rel = hidden_issue_dir_of(path, non_issue)
            if rel:
                hidden.setdefault(rel, set()).add(ref)
    return Numbering(found, unrecognized, hidden)


def _with_sources(rel: str, sources: set[str]) -> str:
    """同じディレクトリが複数の出所にあるときは、出所を並べた 1 行にまとめる。"""
    return f"{rel} ({', '.join(sorted(sources, key=lambda s: (s != WORKING_TREE, s)))})"


HIDDEN_MESSAGE = "Issue ではないディレクトリの中に番号を持つディレクトリがある"


def _unrecognized_report(
    unrecognized: dict[str, set[str]], hidden: dict[str, set[str]]
) -> tuple[list[str], list[str]]:
    """番号を数えられないディレクトリの違反と、利用者が直し方を選ぶためのヒントを返す。

    数えられない名前と、Issue ではないディレクトリの中で番号を持ちうるものの 2 種を扱う。
    どちらも出所が ref なら作業ツリーでは直せないので、ref の直し方は両方の出所から決める。

    直し方は規約の形だけを示す。旧形式も数えはするが、旧形式へ改名すると --check で赤くなる。
    外へ移す直し方を Issue を含まないものに限るのは、closed/<年>/ のような Issue のまとめを
    外へ移すと、中の Issue の番号を rc 0 で再発行するため (実測)。git mv は空になった親を
    残し、それがまた止める原因になる (実測)。
    ref が出所のものは作業ツリーの改名では解けない。remote-tracking ref は手元で消しても次の
    fetch で戻り、消した間は載っている Issue の番号を再発行しうるので、上流で直す道だけを示す
    """
    violations = [
        f"{HIDDEN_MESSAGE}: {_with_sources(rel, sources)}" for rel, sources in sorted(hidden.items())
    ]
    violations.extend(
        f"採番が数えられない Issue ディレクトリ: {_with_sources(rel, sources)}"
        for rel, sources in sorted(unrecognized.items())
    )
    if not violations:
        return [], []
    hints = []
    if unrecognized:
        hints += [
            f"Issue なら {PREFIX}<N>_<title> へ改名する",
            f"Issue をまとめたディレクトリなら、中の Issue を docs/issues/ 直下か {CLOSED}/ 直下へ"
            "移し、空になったディレクトリを消す (git mv は空になった親を残す)",
            "Issue を含まないものだけを docs/issues/ の外へ移す"
            f" (直下で例外になるのは {TEMPLATES}/ と {CLOSED}/ と、宣言した名前だけ)",
            f"Issue を含まず、名前も置き場も規約で決まっているものは、リポジトリ直下の {DECLARATION}"
            f" の {DECLARATION_KEY} で宣言する (宣言は作業ツリーのものを全 ref に当てる)",
        ]
    if hidden:
        hints.append(
            f"Issue ではないディレクトリの中の Issue は、docs/issues/ 直下か {CLOSED}/ 直下へ移す"
            f" ({PREFIX}<N>_<title> の名前で)"
        )
    sources = {s for group in (*unrecognized.values(), *hidden.values()) for s in group}
    if any(s.startswith(LOCAL_REFS) for s in sources):
        hints.append(
            f"出所が {LOCAL_REFS} のものは作業ツリーの改名では解けない。"
            "そのブランチで直して (改名・移動) コミットするか、不要なブランチを消す"
        )
    if any(s.startswith(REMOTE_REFS) for s in sources):
        hints.append(
            f"出所が {REMOTE_REFS} のものは手元で消しても次の fetch で戻る。"
            "上流のブランチで直して (改名・移動) push する (別の clone で直したなら手元で git fetch)"
            " か、上流で消してから git fetch --prune"
        )
    return violations, hints


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
    found, unrecognized, hidden = collect_numbers(root, load_non_issue_dirs(root))
    violations = _duplicates(found)
    # 数えられない名前を黙って飛ばすと、2-b のように番号を持つディレクトリがあっても同じ番号を
    # rc 0 で再発行する (実測)。旧形式の <N>_ は数えられるので止めない (ISSUE_DIR で判定すると
    # 移行前のリポジトリで採番が全部止まる)
    unrecognized_violations, hints = _unrecognized_report(unrecognized, hidden)
    violations.extend(unrecognized_violations)
    if violations:
        _report(violations)
        for hint in hints:
            print(f"      {hint}", file=sys.stderr)
        return 1
    print(f"{PREFIX}{(max(found) if found else 0) + 1}")
    return 0


def run_check(root: Path) -> int:
    non_issue = load_non_issue_dirs(root)
    entries = issue_dirs(root, non_issue)
    violations = _dir_name_violations(entries)
    violations.extend(_number_conflicts(entries))
    violations.extend(f"{HIDDEN_MESSAGE}: {rel}" for rel in hidden_issue_dirs(root, non_issue))

    tracked = _tracked_files(root)
    if not tracked:
        # 0 件は「違反なし」ではなく「何も見ていない」。緑にすると走査対象を失った状態が
        # 合格に化ける
        print("[x] 追跡下のファイルが 1 件も無い。走査対象ゼロは合格ではない", file=sys.stderr)
        return 2

    scanned = excluded = unreadable = 0
    for rel in tracked + _untracked_issue_files(root, non_issue):
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
    # 宣言も差分の後側から読む。作業ツリーから読むと、stage していない宣言の編集が増分の
    # 判定を変え、宣言を含めずにコミットした内容を手元だけが緑にする
    declared = blobs.get(DECLARATION)
    non_issue = parse_non_issue_dirs(
        None if declared is None else _git(root, "cat-file", "blob", declared[1])
    )

    # ディレクトリ名は、新しく現れた名前だけを見る。起点を issue.md に限るのは、配下の
    # 任意ファイルを起点にすると旧記法のディレクトリを触るたび赤くなるため。
    # rename で位置だけが変わったもの (docs/issues 直下から closed/ への移動) を外すのは
    # 名前が 1 文字も変わっていないためで、ここを分けないと旧記法の Issue を閉じるという
    # 日常操作のたびに赤くなり、増分の ratchet が成立しない (実測)
    new_dirs: list[tuple[str, str]] = []
    for _kind, old, path in _diff_records(root, rng, "--diff-filter=AR"):
        got = issue_dir_of(path, non_issue)
        if got is None:
            continue
        name, rel = got
        if path != f"{rel}/{ISSUE_FILE}":
            continue
        if old is not None:
            was = issue_dir_of(old, non_issue)
            if was is not None and was[0] == name:
                continue
        new_dirs.append((rel, name))

    violations = _dir_name_violations(new_dirs)
    violations.extend(_number_conflicts(_issue_dirs_from_paths(list(blobs), non_issue)))
    # 番号の重複と同じく、絞っても逃げられる先が無いので後側の全体を見る
    violations.extend(
        f"{HIDDEN_MESSAGE}: {rel}"
        for rel in sorted({hidden_issue_dir_of(p, non_issue) for p in blobs} - {None})
    )

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


class _StoreOnceNonEmpty(argparse.Action):
    """値を 1 つ取る option の、2 回目と空の値を usage error (exit 2) にする。

    argparse の既定の store は後勝ちで、`--check-text A --check-text B` は A を黙って捨てて
    B だけを見る (違反を含む A の後ろに違反の無い B を足すと exit 0 になった。実測)。
    `--base` と `--root` の繰り返しも、意図と違う範囲やリポジトリを緑にする。空の値も黙って
    別の意味になる。`--base=` は範囲が `...HEAD` になって追加行 0 行の緑、`--root=` は
    Path("") が cwd になる (どちらも実測)。1 回目かどうかを既定値の None との比較で見るので、
    default を持つ option には使えない。
    """

    def __call__(self, parser, namespace, values, option_string=None):
        if not values:
            parser.error(f"{option_string} に空の値は渡せない")
        if getattr(namespace, self.dest) is not None:
            parser.error(f"{option_string} が 2 回以上ある。1 回だけ渡すこと")
        setattr(namespace, self.dest, values)


def main(argv: list[str] | None = None) -> int:
    # allow_abbrev の既定 (True) は `--nex` のような一意な前方一致を `--next` の短縮として
    # 受理する。typo が静かに別の入口へ落ちないよう完全形の明示だけに絞る
    parser = argparse.ArgumentParser(
        description="in-repo Issue の識別子を採番し、GitHub 記法との混同を検査する",
        allow_abbrev=False,
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--next", action="store_true", help="次の識別子を 1 行で印字する")
    mode.add_argument("--check", action="store_true", help="リポジトリ全体を走査する")
    mode.add_argument("--check-diff", action="store_true", help="差分だけを走査する")
    mode.add_argument(
        "--check-text",
        metavar="PATH",
        action=_StoreOnceNonEmpty,
        help="テキスト 1 本を走査する (- で標準入力)",
    )
    parser.add_argument(
        "--base",
        metavar="REF",
        action=_StoreOnceNonEmpty,
        help="--check-diff の基準 ref (既定: index と HEAD の差分)",
    )
    parser.add_argument(
        "--root",
        metavar="PATH",
        action=_StoreOnceNonEmpty,
        help="リポジトリの top-level。サブディレクトリは拒否する (既定: カレントディレクトリの top-level)",
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
    except UncheckableError as e:
        print(f"[x] {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
