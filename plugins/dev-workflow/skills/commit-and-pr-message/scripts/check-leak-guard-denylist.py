#!/usr/bin/env python3
"""禁止語リストで固有名詞の流入を検査する (漏洩検査の層 2)。

層 1 (形の決まったルール) が捕まえられないのは固有名詞で、それには禁止語リストが要る。
リストを PUBLIC な設定ファイルへ literal で書くとルールファイル自身が露出になるので、
置き場所は環境変数 LEAK_GUARD_DENYLIST で外から指す。

## 分岐 (canonical)

当初は 3 分岐 (未設定 / ファイルあり / ファイル無し) で設計したが、
「ファイルはある」と「比較に使えるエントリが取れる」は別の検査で、後者が 0 でも
前者は通る。配布元で緑のまま何も見ていない形を 30 通り数えたので、判定軸を足してある。
実際の分岐はこの表が canonical。

| 状態                                                      | 終了コード |
|-----------------------------------------------------------|-----------|
| 環境変数が未設定                                           | 0 (skip)  |
| 環境変数が空・空白のみ・前後に空白                         | 2         |
| リストの指し先が通常ファイルでない / 読めない / UTF-8 でない | 2         |
| 実効エントリが 0 件                                        | 2         |
| fold 後に空になるエントリがある                            | 2         |
| エントリが自分自身に一致しない (自己照合の失敗)             | 2         |
| 追跡ファイルが 0 件 / git を呼べない                       | 2         |
| merge conflict 中の index (stage 0 以外がある)             | 2         |
| 追跡ファイルが UTF-8 で読めない (NUL を含まないのに decode 不能) | 2   |
| index が指す object を読めない / cat-file の出力が欠ける    | 2         |
| `--check-text` の対象を読めない                            | 2         |
| 余分な引数を渡された                                       | 2         |
| 上記以外の失敗                                             | 2         |
| 禁止語を検出した                                           | 1         |
| 検出 0 件                                                  | 0         |

2 を 1 と分けるのは、規約違反と「検査を走らせられなかった」を同じ赤にしないため。
未設定を無条件の fail-closed にしないのは、リストを持たない人 (取り付けたリポジトリを
clone した第三者や、このスクリプトを配布物として受け取った利用者) が居るためで、その人と
無関係な理由で常に赤くなる形は採れない。

## 走査面

照合対象は worktree の中身ではなく index の blob にする。コミットされるのは blob で、
worktree は symlink 追従 (リポジトリ外の実体を読み、コミットされるリンク先パス文字列は
一度も見ない)・smudge filter・sparse-checkout の 3 経路で blob と食い違う。起動位置でも
食い違う: `git ls-files` は cwd 相対なので、サブディレクトリから起動すると配下しか
返さず、「走査 1 件 / 違反 0 件」という健全に見える形で緑になる。root は
`rev-parse --show-toplevel` で解決する。

パスも照合対象に含める。追跡ファイルのパスは走査対象と同じ自由テキストで、たとえば
Issue のタイトルをディレクトリ名に含める運用では日本語がそのままパスに入る (配布元では
追跡ファイルの 4 割強が非 ASCII パスだった。実測)。

内容の照合から外れるのは gitlink・上限超えの blob・NUL を含むファイルの 3 つ。UTF-16 は
NUL を持つので 3 つ目に落ちるが、BOM がある形だけはテキストとして読んで走査する。BOM 無しの
UTF-16 は binary に数えたまま残る (BOM 無しから byte order を当てる形は、外すと中身が化けた
まま「成功」するので採らない)。除外の件数は要約に出るので、0 でない値として見える。

## リストの書式

1 行に 1 語。行の前後の空白は落とす。strip 後に `#` で始まる行はコメントで、空行は無視する。
語の中の空白は保つので `Foo Bar Inc` のような形もそのまま書ける。UTF-8 で保存する (BOM は
落とすので Notepad や PowerShell 5 の既定出力でよい)。判定の canonical は parse_entries。

書けないのは「`#` で始まる語」「前後に空白を持つ語」「改行を含む語」の 3 つ。

行指向のプレーンテキストにしてあり、JSON 系へ移さない。理由は 2 つとも出力の設計から来る。
ひとつは出力が出す唯一の座標が行番号だということ (「出力」節) で、formatter が再整形する形式
では要素の物理行が動き、座標が静かに別の行を指すようになる。もうひとつはエスケープで、
quote や backslash の書き損じは「エントリ数は非 0 なのに 1 件も当たらない」形で出る。これは
self_check がわざわざ捕まえようとしている当の形で、エスケープ規則が無ければ起き得ない。

## 照合

大小と表現の揺れは fold() が吸収する。両側へ同一に適用することが要件で、片側だけ違う
関数や余分な正規化を掛けると、同じ literal でも一致しなくなる。何を吸収して何を
書き手の責任に置くかは fold() の docstring が持つ。

## 出力

禁止語そのものを印字しない。出力の宛先は端末だけではなく、tmux や script のログ、
Issue や PR への貼り付け、CI ログ、エージェント経由なら会話ログにも残る。出すのは
座標だけで、座標はリストを持っている人だけがローカルで解決できる。

パス由来の検出でパスを印字すると出力が語そのものになるので、パスが禁止語に一致した
ファイルは位置指標で報告する。組み立てと oid を併記する理由は `_locator` が持つ。検査不能の
メッセージも同じ位置指標を使う。列オフセットや一致長は出さない
(同じ行番号を指す複数行の共通部分文字列から語が計算できるため、1 件で確定させない)。

座標に `#<数字>` の形を使わないのは、同じ bundle の in-repo-issue にある issue-id.py が
`#N` を GitHub の番号空間を指す記法として機械検査で禁じているため (canonical は issue-id.py
の docstring)。意味は別物だが機械検査に区別はできないので、免除を広げるのではなく衝突する
記法を避ける。

どこへ取り付けるかは利用する側が決める。検出した座標を公開されるログへ出すと、対象が
既に公開されている場合に座標の交差から語を復元できるので、取り付け先のログを誰が読めるか
を見て決めること。

## セットアップの確認

環境変数を設定したら、**この検査を起動するのと同じ起動元から** (コミットの hook として
取り付けたならコミットを打つ起動元、エージェントに実行させるならそのエージェントのシェル)、
無害な 1 行だけのファイルを `--check-text` へ通すこと。

`status=checked` が出れば、その起動元へ変数が届いている。`status=skipped` なら届いていない。
`--check` は追跡ファイルを見るので、git リポジトリの外では変数が届いていても rc 2 になる
(実測)。起動する場所で結果が変わるので、確認手順には使わない。

コマンドの前に `LEAK_GUARD_DENYLIST=...` を置かないこと。その形は変数をその場で注入するので
必ず `status=checked` になり、見たい失敗 (変数が届いていない) を原理的に出せない。確かめたい
のはスクリプトが動くことではなく、変数がその起動元に届くことである。

届かない起動元は実在する: `.zshrc` の export は非対話シェル (`zsh -c`) に届かず、`launchctl
getenv` も空を返すので GUI の git クライアントや IDE の VCS 機能が継承する環境にも入らない
(実測)。設定したつもりのまま全ての実行が skip で緑になる形は既知の限界として引き受けており、
この層は自分の取り付けを自分では検査できない。

リストの置き場所をここへ書かない。このファイルは配布物として公開されているので、private な
配線規約を literal で書くと、この検査が防ごうとしている流入を自分で起こす。

中立なパスを既定値として持つ案は採らない。固有名詞を含まないので露出の面では書けるが、入力が
環境変数 1 つという前提で数えた上の分岐表を広げ、シェル側の配線と既定値で同じことを 2 箇所で
実現することになる。上の「届かない起動元」はこの層では塞がらないまま残る、という選択である。
"""

from __future__ import annotations

import argparse
import errno as errno_mod
import os
import stat
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import NamedTuple

ENV_VAR = "LEAK_GUARD_DENYLIST"

EXIT_OK = 0
EXIT_VIOLATION = 1
EXIT_UNABLE = 2

# 機械可読な状態語。pre-commit から呼ぶと rc 0 の hook の stdout も stderr も表示されないので
# (実測)、skip したことは verbose: true を付けた hook の出力としてしか見えない。
# 表示されたときに「守っていない」と「見て 0 件だった」が読み分けられる必要がある。
STATUS_SKIPPED = "status=skipped"
STATUS_CHECKED = "status=checked"

# 1 blob あたりの上限。超えたものは内容を読まずに除外する。read_text は「decode できない
# から安全に飛ばす」ように見えて例外が上がる前に全体を読み切っており (実測: 200MB の
# ファイルで peak 615MB)、--check は実行のたびに追跡ファイル全体を走るので (pre-commit へ
# 取り付ければ毎コミット)、動画やフォントが 1 つ入った時点で毎回その倍以上を確保する。
# 開発機のホストには cgroup のような境界が無いことが多く、膨らんだ割り当てが OS 全体を巻き込む。
MAX_BLOB_BYTES = 1024 * 1024

# cat-file --batch へ一度に流すサイズの目安。出力は丸ごと stdout に載るので、
# 追跡ファイルの合計が大きいリポジトリでも常駐量がこの付近で頭打ちになるよう分ける。
BATCH_BYTES = 8 * 1024 * 1024

# 自己照合で使う枠。語を囲む文字列自体は禁止語に一致しない無害な語にする。
CANARY_TEMPLATE = "canary {} canary"

# 空・空白のみ・前後に空白を持つ値を弾いたときの文言。後段の stat / S_ISREG も同じ値を
# 検査不能へ倒すので、終了コードだけではこの分岐が生きているか分からない。原因が読める
# ことがこの分岐の存在理由なので、テストは文言まで見る (定数を共有して drift を防ぐ)。
BLANK_VALUE_HINT = "の値が空・空白のみ・前後に空白を持つ"

_MODE_GITLINK = "160000"

# BOM を見てから UTF-16 と判定する。BOM 無しで decode("utf-16") を試す形は採らない:
# 8 バイトのランダム列の 89%・32 バイトの 61% が「デコードに成功」してテキスト扱いに
# なり (実測。4096 バイトでは 0%)、小さいバイナリほど誤判定する。さらに BOM 無しの
# UTF-16BE は成功したうえで中身が化けるので、失敗がエラーではなく結果として返る。
# BOM は PNG/JPEG/GIF/PDF/ZIP/GZIP/TTF/WOFF2/ELF/Mach-O のどの magic とも衝突しない (実測)
_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")


class Unable(RuntimeError):
    """検査を走らせられなかった。違反 (1) と混ぜないために送出する。"""


class Entry(NamedTuple):
    """禁止語 1 件。

    lineno はリストファイルの行番号 (パース後の index ではない)。コメント行・空行・
    重複除去・sort のどれでも index はファイルの行番号からずれ、運用者はその番号を
    頼りにリストを開くので、ずれると別のエントリを消す。

    raw は fold 前の語。自己照合で本文へ埋めるのに使う。fold 済みの語を埋めると
    「本文側に fold が掛かっていない」実装を検出できない。
    """

    lineno: int
    folded: str
    raw: str


class Finding(NamedTuple):
    """検出 1 件。語は持たない。

    index はパスを印字できないときの位置指標だが、**走査した index に対する序数**であって
    運用者が後から引く `git ls-files` の序数とは限らない。git は `commit -a` のとき
    `.git/index.lock`、`commit -- <pathspec>` のとき `.git/next-index-<pid>.lock` を hook へ
    渡し (実測)、そこは実 index とエントリ集合が違う。どちらの形かの判定は
    resolve_index_kind が持つ。序数だけを頼りにすると別のファイルが指され、語が無いので
    誤検出と判断される。
    そのため oid を併記する: `git ls-files -s | grep <oid>` はどの index からでも引ける。
    """

    index: int
    path: str
    oid: str
    lineno: int  # 本文の行番号。パス由来なら 0
    entry_lineno: int
    is_path: bool


class Report(NamedTuple):
    findings: list[Finding]
    tracked: int
    scanned: int
    binary: int
    oversize: int
    gitlinks: int


# --- 正規化と照合 --------------------------------------------------------------


def fold(text: str) -> str:
    """照合の前にリスト側と本文側へ同一に掛ける正規化。

    NFKC → category Cf 除去 → casefold → NFKC の 4 段。前後の NFKC は別のものを守る。
    当初どちらも「casefold が NFKC 正規形へ戻さない code point のため」と書いていたが、
    どちらを外しても既存のテストが赤くならなかったので測り直した結果がこれ。

    先頭の NFKC は冪等性を守る。外すと fold(fold(x)) != fold(x) になる code point が
    BMP に現れる (実測: U+037A, U+03D2-U+03D4, U+03F2 ほか)。照合の結果そのものは
    変えないので、冪等性を見る対照が無いと外しても気づけない。

    末尾の NFKC は照合の結果を変える。Cf 除去がゼロ幅文字を落とすと、それまで隣接して
    いなかった base と結合文字が隣り合うので、そこで合成をやり直す必要がある (実測:
    'ｶ' + ZWSP + 'ﾞ' は末尾 NFKC が無いと 'ガ' に一致しない。'か' + ZWSP + U+3099 と
    'jose' + SHY + U+0301 も同じ)。ゼロ幅文字は Web ページや PDF からのペーストで入るので、
    この組み合わせは難読化ではなく事故として現実に起きる。

    casefold を lower より優先するのは、両側へ同一に掛ける限り差は一致範囲が広がる側
    (ß→ss, ſ→s, 最終シグマ) にしか出ず、fail-closed へ倒れるため。

    吸収するもの: 大小、NFC/NFD、全角/半角の英数とカナ、U+3000、ゼロ幅文字と SHY
    (category Cf)。どれも IME・Finder・Web や PDF からのペーストという「運用者の事故」で
    入る表現差で、この検査の脅威モデルはそこにある。

    吸収しないもの: ダッシュの異体 (U+2010/2013/2014/2212/30FC は NFKC が畳まない)、
    ラテンのアクセント (José/Jose)、ローマ字とかなの表記揺れ、行を跨いだ語、意図的な
    難読化。綴りの異体は書き手がリストへ列挙する側に置く。畳み表を手で持つと表 1 つごとに
    pin が要るうえ、'ー' はかな文字なので畳むとかな側と衝突する。この線引きは
    test_check_leak_guard_denylist.py の Fold が両側から挟んで pin している。
    """
    s = unicodedata.normalize("NFKC", text)
    s = "".join(c for c in s if unicodedata.category(c) != "Cf")
    return unicodedata.normalize("NFKC", s.casefold())


def split_lines(text: str) -> list[str]:
    """`\\n` だけで行を割る。

    str.splitlines() は `\\v` `\\f` `\\x1c`-`\\x1e` NEL `U+2028` `U+2029` と単独の `\\r` も
    行境界にするが、git・grep・エディタは `\\n` だけを境界にする。語を出力しない設計では
    行番号が唯一の手がかりなので、ずれると「示された行を開いても何も無い」状態になり、
    運用者は誤検出と判断してその語をリストから外す。露出を防ぐ検査が防御を外させる。
    先例は issue-id.py の _split_lines。
    """
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def scan_text(text: str, entries: list[Entry]) -> list[tuple[int, int]]:
    """(本文の行番号, エントリのリスト行番号) を返す。"""
    found = []
    for lineno, line in enumerate(split_lines(text), 1):
        folded = fold(line)
        found.extend((lineno, e.lineno) for e in entries if e.folded in folded)
    return found


def scan_path(path: str, entries: list[Entry]) -> list[int]:
    """パス文字列に一致したエントリのリスト行番号を返す。"""
    folded = fold(path)
    return [e.lineno for e in entries if e.folded in folded]


# --- 禁止語リスト --------------------------------------------------------------


def parse_entries(raw: bytes) -> list[Entry]:
    """バイト列から禁止語を読む。

    utf-8-sig で読むのは BOM を落とすため。Windows の Notepad や PowerShell 5 の
    Out-File が既定で付けるもので、`encoding='utf-8'` で読むと 1 行目だけが永久に
    当たらない。1 行目がコメントなら `\\ufeff#` が startswith('#') を外れてエントリへ
    昇格し、以降の「リスト内の位置」が全部 1 ずれる (実測)。

    行の strip はコメント判定より先に行う。CRLF の `\\r` もここで落ちる。strip しない
    実装では、末尾に空白が 1 つ付いたエントリが本文中の同じ語に一致しなくなる (実測)。
    """
    text = raw.decode("utf-8-sig")
    entries = []
    for lineno, line in enumerate(text.split("\n"), 1):
        s = line.strip()
        # 本文側とリスト側では同じ「\n だけを境界にする」規約の帰結が違う。本文側は
        # 座標がずれるだけだが、リスト側はエントリが黙って結合して比較対象から消える。
        # CR のみのファイルは全体が 1 エントリになり、entries 非 0 / self_check 成功 /
        # 検出 0 件という最も健全に見える形で緑を返す (実測)。self_check の canary は
        # parse 後の結合済み raw から作るので、この形を原理的に見られない
        if s and len(s.splitlines()) > 1:
            raise Unable(
                f"禁止語リストの {lineno} 行目が \\n 以外の行境界文字を含む。"
                "エディタ上の行と一致せず、エントリが黙って結合する"
            )
        if not s or s.startswith("#"):
            continue
        entries.append(Entry(lineno, fold(s), s))
    return entries


def load_entries(path: Path) -> list[Entry]:
    """リストを読んで検証する。使えない形はすべて Unable。

    「存在する」と「比較に使えるエントリが 1 件以上取れる」を別の検査にしている。
    後者が 0 でも走査は完走して 0 件の緑を返し、走査件数が十分大きいので要約からは
    何も比較していないことが読めない (実測: コメント行だけのファイルも空ファイルも
    エントリ 0 件で完走した)。
    """
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise Unable(
            f"{ENV_VAR} の指す先を読めない (errno={e.errno})。パスは印字しない"
        ) from None
    try:
        entries = parse_entries(raw)
    except UnicodeDecodeError:
        # errors='replace' で読むとエントリ数は数えられるのに 1 件も当たらない
        # (UTF-16 保存で実測)。件数の要約まで正常に見えるので最も危険な形
        raise Unable(f"{ENV_VAR} の指す先が UTF-8 で読めない") from None

    # fold 後に空になる行は全ファイル全行に一致する。エディタでは空行に見えるので
    # (U+200B だけの行など)、原因のエントリを特定できない騒がしい赤になる
    empty = [e.lineno for e in entries if not e.folded]
    if empty:
        raise Unable(
            f"禁止語リストの {', '.join(map(str, empty))} 行目が fold 後に空になる "
            "(見えない文字だけの行)。全行に一致するので検査を止める"
        )
    if not entries:
        raise Unable(
            "禁止語リストに実効エントリが 1 件も無い。"
            "0 件での全走査は「違反なし」ではなく「何も比較していない」"
        )
    return entries


def canary_text(entry: Entry) -> str:
    """自己照合で走査する合成テキスト。fold 前の語を埋める。"""
    return CANARY_TEMPLATE.format(entry.raw)


def self_check(entries: list[Entry]) -> list[Entry]:
    """リストに書いた語をそのまま本文へ書いたら検出されることを確かめる。

    「エントリ数は非 0 なのに 1 件も当たらない」形はエントリ数の要約まで正常に見えるので、
    出力からは読めない。BOM・NFD・末尾空白・fold の片側適用がどれもこの形で出る。
    照合の前に毎回走らせて、静かな緑を騒がしい赤へ変える。

    射程は「parse を通過したエントリが自分自身に一致するか」まで。canary は parse 後の
    raw から作るので、parse の段階で結合・消失したエントリは原理的に見えない。その面は
    parse_entries の行境界の検査が持つ。
    """
    return [e for e in entries if not scan_text(canary_text(e), [e])]


def resolve_denylist(env) -> Path | None:
    """環境変数から置き場所を解決する。未設定なら None (skip)。

    値そのものは決して印字しない。リストは PUBLIC に書けないから外に置くので、その
    置き場所のパス自体がユーザー名 (層 1 の対象) と私的プロジェクト名 (層 2 の対象) を
    含みがちで、「リストが無い」という最もありふれた設定ミスのたびに露出する。
    """
    raw = env.get(ENV_VAR)
    if raw is None:
        return None
    stripped = raw.strip()
    if not stripped or stripped != raw:
        # 空文字列は `.env` の値なし行・`export VAR=$UNSET_VAR`・値を取るラッパの失敗で
        # 日常的に生じる。`if not env.get(VAR)` 型は未設定と同じ skip へ落とし、
        # `Path('')` は PosixPath('.') になって exists() が True を返す (どちらも実測)。
        # 末尾改行は `$(cat ...)` 由来。表示に空白と改行が見えないので原因が読めない
        raise Unable(f"{ENV_VAR} {BLANK_VALUE_HINT}。値は印字しない")
    path = Path(raw)
    try:
        st = path.stat()  # symlink は辿る。壊れた symlink はここで ENOENT
    except OSError as e:
        raise Unable(
            f"{ENV_VAR} の指す先を stat できない (errno={e.errno})。パスは印字しない"
        ) from None
    if not stat.S_ISREG(st.st_mode):
        # ディレクトリとディレクトリへの symlink は exists() が True を返すので、
        # 存在で分岐すると「ファイルあり」へ進んで read が IsADirectoryError で落ちる。
        # 未捕捉なら rc 1 になり、設計上の 1 は「違反あり」なので検査不能が違反に化ける
        raise Unable(
            f"{ENV_VAR} の指す先が通常ファイルではない (errno={errno_mod.EISDIR} 相当)。"
            "パスは印字しない"
        )
    return path


# --- git ----------------------------------------------------------------------


def _git(root: Path, *args: str, stdin: bytes | None = None) -> bytes:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            input=stdin,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        raise Unable("git が見つからない") from None
    if proc.returncode != 0:
        # stderr を貼らない。git のエラーはパスを含むことがあり、この検査の出力は
        # 端末以外にも残る。原因の特定には rc と引数で足りる
        raise Unable(f"git {' '.join(args)} に失敗した (rc={proc.returncode})")
    return proc.stdout


def resolve_root(start: Path) -> Path:
    """走査の起点となるリポジトリ root。

    `git ls-files` は cwd 相対なので、サブディレクトリから起動すると配下しか返さない。
    0 件ではなく部分欠落なので「追跡 0 件なら止める」ガードを素通りし、
    「走査 1 件 / 読めずに飛ばした 0 件」という完全に健全な形で緑になる (実測)。
    """
    out = _git(start, "rev-parse", "--show-toplevel")
    return Path(out.decode("utf-8").strip())


def resolve_index_kind(root: Path, env) -> str:
    """走査した index が既定のものか一時のものかを返す。

    変数の有無では分けられない。git は as-is の `git commit` でも hook へ
    `GIT_INDEX_FILE=.git/index` を渡す (実測 git 2.55.0。`-a` は `.git/index.lock`、
    `commit -- <pathspec>` は `.git/next-index-<pid>.lock`)。有無で判定すると hook 経由の
    全コミットが temporary になり、本当に一時 index で序数がずれた回と区別が付かない。
    手がかりとして機能させるには値を既定の index と突き合わせる必要がある。

    対照に `rev-parse --git-path index` は使えない。`GIT_INDEX_FILE` を尊重して上書き後の
    値を返すので、常に一致して全部 default になる。`--absolute-git-dir` は index の
    指し先に影響されないので使える。
    """
    raw = env.get("GIT_INDEX_FILE")
    if not raw:
        return "default"
    out = _git(root, "rev-parse", "--absolute-git-dir")
    git_dir = Path(out.decode("utf-8").strip())
    # 相対値は hook の cwd 基準。git は hook を worktree の top-level で起動する。
    # 絶対値のときは `/` の右辺が絶対パスなら左辺を捨てる pathlib の規則で root が落ちる
    scanned = os.path.realpath(root / raw)
    default = os.path.realpath(git_dir / "index")
    return "default" if scanned == default else "temporary"


def _ls_files(root: Path) -> list[tuple[str, str, str]]:
    """(mode, oid, path) の一覧。

    -z を使うのは、既定出力が非 ASCII パスを C クォートするため (実測:
    `"docs/issues/ISSUE-1_\\343\\201\\202"`)。クォートされた名前は照合にも open にも
    使えず、しかもエラーではなく短い正常な結果で返る。配布元では追跡ファイルの 4 割強が
    非 ASCII パスで、-z を外すとその分が走査面から落ちた (実測)。
    """
    out = _git(root, "ls-files", "-s", "-z")
    entries = []
    for record in out.decode("utf-8", "replace").split("\0"):
        if not record:
            continue
        meta, sep, path = record.partition("\t")
        fields = meta.split()
        # 読めないレコードを静かに飛ばさない。走査面が痩せる向きの失敗なので、
        # 「違反なし」ではなく「何を見たか分からない」として止める
        if not sep or len(fields) < 3:
            raise Unable("git ls-files の出力を解釈できない (走査面を確定できない)")
        mode, oid, stage = fields[0], fields[1], fields[2]
        # stage 0 以外は merge conflict 中の index。同じパスが 3 回出るので件数も
        # 照合結果も実態とずれる。競合の解決前は検査の前提が崩れているので止める
        if stage != "0":
            raise Unable(
                "merge conflict 中の index では走査面を確定できない (競合を解決してから実行する)"
            )
        entries.append((mode, oid, path))
    return entries


def _blob_sizes(root: Path, oids: list[str]) -> list[int]:
    """--batch-check で内容を読まずにサイズだけ取る。"""
    if not oids:
        return []
    stdin = "".join(f"{oid}\n" for oid in oids).encode()
    out = _git(root, "cat-file", "--batch-check", stdin=stdin)
    sizes = []
    for line in out.decode("utf-8", "replace").splitlines():
        fields = line.split()
        if len(fields) < 3:
            # `<oid> missing`。index が指す oid を読めないのはリポジトリの破損で、
            # 握って「飛ばした」に数えると壊れたリポジトリが違反 0 件の緑になる
            raise Unable("index が指す object を読めない (リポジトリの破損)")
        sizes.append(int(fields[2]))
    if len(sizes) != len(oids):
        raise Unable("cat-file --batch-check の行数が要求と一致しない")
    return sizes


def _iter_blobs(root: Path, oids: list[str], sizes: list[int]):
    """--batch で内容を取り、読めたものから 1 件ずつ返す。

    list へ貯めて返す形は採らない。それだと BATCH_BYTES の分割が抑えるのは cat-file
    1 回の stdout だけで、常駐量は走査対象の合計サイズに比例する (開発機のホストには
    cgroup のような境界が無いことが多い)。

    逐次返しても 1 チャンクぶんにはならない。_read_chunk が stdout 全体を持ったまま
    そこから切り出した bytes のリストも作るので、常駐はチャンクの約 2 倍で頭打ちになる
    (実測: 合計 35.2 MiB を BATCH 8 MiB で読むと peak 17.6 MiB。list へ貯める版は
    39.6 MiB で合計に比例する)。頭打ちになることが要件で、倍率は要件ではない。

    上限の強制もここへ置く。呼び出し側の選別だけに頼ると、選別を外す変更が「読まずに
    除外する」という保証を黙って落とす。ここなら blob を読む関数自身が上限を知っている。
    """
    chunk: list[str] = []
    total = 0
    for oid, size in zip(oids, sizes):
        if size > MAX_BLOB_BYTES:
            # 序数はこの関数からは分からないので oid だけを出す。捏造した序数を
            # 位置指標の形で出すと、運用者が引ける鍵に見えて別のファイルを指す
            raise Unable(f"上限を超える blob を読もうとした (oid {oid[:12]})")
        if chunk and total + size > BATCH_BYTES:
            yield from _read_chunk(root, chunk)
            chunk, total = [], 0
        chunk.append(oid)
        total += size
    if chunk:
        yield from _read_chunk(root, chunk)


def _read_chunk(root: Path, oids: list[str]) -> list[bytes]:
    stdin = "".join(f"{oid}\n" for oid in oids).encode()
    out = _git(root, "cat-file", "--batch", stdin=stdin)
    bodies = []
    pos = 0
    for _ in oids:
        nl = out.find(b"\n", pos)
        if nl < 0:
            raise Unable("cat-file --batch の出力が途中で切れている")
        fields = out[pos:nl].decode("utf-8", "replace").split()
        if len(fields) < 3:
            raise Unable("index が指す object を読めない (リポジトリの破損)")
        size = int(fields[2])
        # 宣言されたサイズぶんのバイトが実際にあることを確かめる。スライスは範囲外でも
        # 例外を出さず短い bytes を返すので、出力が途中で切れると「短い内容を走査して
        # 違反なし」に化ける。ここから先のレコード境界も全部ずれる
        if nl + 1 + size > len(out):
            raise Unable("cat-file --batch の出力が宣言されたサイズに足りない")
        bodies.append(out[nl + 1 : nl + 1 + size])
        pos = nl + 1 + size + 1  # レコード末尾の改行
    if len(bodies) != len(oids):
        raise Unable("cat-file --batch が要求した数の blob を返さなかった")
    return bodies


def scan_tracked(start: Path, entries: list[Entry]) -> Report:
    """追跡ファイルのパスと blob を走査する。"""
    root = resolve_root(start)
    tracked = _ls_files(root)
    if not tracked:
        # 0 件は「違反なし」ではなく「何も見ていない」
        raise Unable("追跡下のファイルが 1 件も無い。走査対象ゼロは合格ではない")

    findings: list[Finding] = []

    # パスは全エントリを見る。gitlink も自分のパスは持つ
    for index, (_mode, oid, path) in enumerate(tracked, 1):
        findings.extend(
            Finding(index, path, oid, 0, entry_lineno, True)
            for entry_lineno in scan_path(path, entries)
        )

    # 内容は blob から読む。gitlink は別リポジトリの commit を指すので読めない。
    # symlink は除外しない: blob の中身がリンク先パス文字列で、それ自体が走査面に要る
    readable = [(i, m, o, p) for i, (m, o, p) in enumerate(tracked, 1) if m != _MODE_GITLINK]
    gitlinks = len(tracked) - len(readable)
    sizes = _blob_sizes(root, [o for _, _, o, _ in readable])

    wanted = [(i, p, o, s) for (i, _m, o, p), s in zip(readable, sizes) if s <= MAX_BLOB_BYTES]
    oversize = len(readable) - len(wanted)
    blobs = _iter_blobs(root, [o for _, _, o, _ in wanted], [s for _, _, _, s in wanted])

    scanned = binary = consumed = 0
    for (index, path, oid, _size), body in zip(wanted, blobs):
        consumed += 1
        if body.startswith(_UTF16_BOMS):
            # UTF-16 のテキストは ASCII 域の文字ごとに NUL を持つので、NUL による binary
            # 判定を先に置くと丸ごと未走査になる。Windows のエディタが書く形なので、
            # 非 ASCII の禁止語を運ぶ経路としては CP932 と同程度に現実的
            try:
                text = body.decode("utf-16")
            except UnicodeDecodeError:
                raise Unable(
                    f"UTF-16 の BOM を持つのに読めない追跡ファイルがある ({_locator(index, oid)})"
                ) from None
            scanned += 1
            findings.extend(
                Finding(index, path, oid, lineno, entry_lineno, False)
                for lineno, entry_lineno in scan_text(text, entries)
            )
            continue
        if b"\0" in body:
            binary += 1
            continue
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            # 非 ASCII の禁止語を運ぶ可能性が最も高い形式 (CP932 のテキスト) が、そのまま
            # 最も検査されない形式になる。unreadable に数えて緑を返すとバイナリを飛ばした
            # ときと同じ数え方になり、要約を読んでも異常に見えない
            raise Unable(
                f"UTF-8 で読めない追跡ファイルがある ({_locator(index, oid)})。"
                "テキストなら UTF-8 へ直し、バイナリなら NUL を含む形で保存する"
            ) from None
        scanned += 1
        findings.extend(
            Finding(index, path, oid, lineno, entry_lineno, False)
            for lineno, entry_lineno in scan_text(text, entries)
        )

    # zip は短い方で黙って止まるので、長さのずれは「一部を走査しただけの緑」になる。
    # 逐次消費では len() が取れないので数え、余った側も next() で見る (多い向きのずれも
    # zip に飲まれる)
    if consumed != len(wanted) or next(blobs, None) is not None:
        raise Unable("読み出した blob の数が走査対象と一致しない")

    return Report(findings, len(tracked), scanned, binary, oversize, gitlinks)


# --- 入口 ----------------------------------------------------------------------


def _locator(index: int, oid: str) -> str:
    """パスの代わりに出す位置指標。

    序数は走査した index に対するもので、運用者が後から引く `git ls-files` とずれるので
    oid を併記する (理由は Finding の docstring)。組み立てを 1 箇所に集約するのは、
    位置指標を出す場所が増えたときに片方だけ oid を落とす形を避けるため。実際に
    検査不能メッセージの側が序数だけを出しており、一時 index では別のファイルを指していた。
    """
    return f"tracked file {index} (oid {oid[:12]})"


def _render(finding: Finding, tainted: set[str]) -> str:
    """検出 1 件を座標だけの行にする。

    パスが禁止語に一致したファイルは、パスを印字すると出力が語そのものになるので
    位置指標へ置き換える。oid は blob のハッシュで、blob 自体はコミットされて
    公開されるものなので、これを出しても露出は増えない。
    """
    if finding.path in tainted:
        label = _locator(finding.index, finding.oid)
    else:
        label = finding.path
    if finding.is_path:
        return f"{label}: denylist line {finding.entry_lineno}"
    return f"{label}:{finding.lineno}: denylist line {finding.entry_lineno}"


def run_check(start: Path, entries: list[Entry], env) -> int:
    report = scan_tracked(start, entries)
    tainted = {f.path for f in report.findings if f.is_path}
    for finding in report.findings:
        print(f"  [x] {_render(finding, tainted)}", file=sys.stderr)
    # どの index を走査したかを出す。git は commit -a / commit -- <pathspec> のとき hook へ
    # 一時 index を渡すので、そこでの序数は運用者が後から引く `git ls-files` とずれる (実測)。
    # ずれたことに気づける手がかりが要る。判定規則は resolve_index_kind が持つ
    index_kind = resolve_index_kind(start, env)
    print(
        f"{STATUS_CHECKED} tracked={report.tracked} scanned={report.scanned} "
        f"entries={len(entries)} binary={report.binary} oversize={report.oversize} "
        f"gitlinks={report.gitlinks} index={index_kind} findings={len(report.findings)}"
    )
    print(
        f"追跡 {report.tracked} 件のパスと {report.scanned} 件の内容を"
        f"禁止語 {len(entries)} 件と照合した"
    )
    if report.findings:
        print(
            f"[x] 検出 {len(report.findings)} 件。"
            "座標が指す行と、禁止語リストの該当行を突き合わせること"
        )
        return EXIT_VIOLATION
    return EXIT_OK


def run_check_text(source: str, entries: list[Entry]) -> int:
    """テキスト 1 本を走査する入口。渡されたファイルの全文を、何も剥がさずに走査する。

    コミットメッセージはその用途の 1 つで、commit-msg hook として取り付けたときに渡るのは
    git の cleanup より前の message ファイル全文になる。コメント行も `git commit -v` が
    末尾へ足す diff も含まれるが、`#` 行の除去も scissors 行以降の切り落としも行わない。
    既定の cleanup は編集経由が strip、-m / -F は whitespace で、同じ本文でも `#` 行の
    運命が経路で逆になる (実測: 同一の `# ...` 行がエディタ経由では commit object から
    消え、-F では残った)。同梱先の commit-and-pr-message の手順は本文を -F で渡すので、
    日常の経路がまさに残る側にあたる。git の cleanup 規則を再実装すると二重管理になり、
    誤検出を嫌って剥がす向きの変更がそのまま fail-open へ倒れる。同じ bundle の
    issue-id.py の --check-text も、同じ面で誤検出を引き受けている。

    既知の限界: commit-msg hook として取り付けても、この入口が発火しない経路がある
    (すべて実測)。`git cherry-pick` と `git revert` は元のメッセージを持つ新しいコミットを
    作るが hook は 1 度も発火せず、revert が自動生成する件名は元の件名を丸ごと含む。
    `git rebase` の再生も発火しない (発火するのは reword で編集した回だけ)。`--no-verify` も
    当然通らない。GitHub 上の squash merge は PR タイトルと本文が main の恒久コミット
    メッセージになるが、ローカル hook は原理的に走らない。もう一方の入口 (追跡ファイル) は
    コミットメッセージを走査面に持たず、gitleaks もメッセージを見ないので backstop が無い。

    PR タイトルと本文をこの入口へ手で通す手順は spec の実装順序 5 が扱う (未実装)。
    """
    path = Path(source)
    try:
        # read_text ではなく bytes から decode する。read_text の universal newlines は
        # 単独の \r を \n に置き換えてから渡すので、split_lines が \n だけを境界にしていても
        # \r を持つ本文では行番号が 1 つ進む (実測)。同じ本文を層 1 (gitleaks。\n だけで
        # 数える) と一緒に通す入口では、層ごとに座標がずれて片方が別の行を指す
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise Unable(
            f"走査対象のテキストを読めない ({type(e).__name__})。パスは印字しない"
        ) from None
    hits = scan_text(text, entries)
    # source を印字しない。1 起動で走査するのは 1 ファイルなので読む側は対象を知っており、
    # 座標は行番号で足りる。印字すると 2 つの経路で漏れる: (1) message ファイルのパスが
    # 禁止語を含む場合、source は照合対象でないのでその語がそのまま出力になる。
    # (2) linked worktree からのコミットでは git が commit-msg hook へ
    # `<main>/.git/worktrees/<name>/COMMIT_EDITMSG` という絶対パスを渡す (main worktree では
    # 相対の `.git/COMMIT_EDITMSG`)。絶対パスの先頭はホームディレクトリを含むので、層 1 が
    # 守っているユーザー名が Failed ブロックへ出る (実測)。
    for lineno, entry_lineno in hits:
        print(f"  [x] line {lineno}: denylist line {entry_lineno}", file=sys.stderr)
    print(
        f"{STATUS_CHECKED} lines={len(split_lines(text))} "
        f"entries={len(entries)} findings={len(hits)}"
    )
    if hits:
        print(f"[x] 検出 {len(hits)} 件。禁止語リストの該当行と突き合わせること")
        return EXIT_VIOLATION
    return EXIT_OK


def _tolerate_unencodable_stdout() -> None:
    """stdout の符号化で表せない文字を backslashreplace で \\x.. や \\u.... の形にして書く。

    stdout の符号化が日本語を書けない (PYTHONIOENCODING=ascii、cp1252 のコンソール) と、既定の
    strict では status=checked を出したあとの日本語の行で UnicodeEncodeError になり、rc 2 で
    終わる (実測)。rc 1 と status=checked の組を検出として読む呼び出し元では、検出が「検査
    不能」に化ける。UTF-8 のバイト列を直接書く案と比べ、cp932 の Windows では日本語がそのまま
    出て、表せない符号化でも ASCII の目印は壊れない。stderr は Python の既定で backslashreplace
    なので触らない。テストが StringIO へ差し替えた stdout には reconfigure が無いので呼ばない。
    """
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(errors="backslashreplace")


def main(argv: list[str] | None = None, *, env=None) -> int:
    # 最初の書き込みより前に置く。argparse の --help は parse_known_args の中で日本語の説明を
    # stdout へ書くので、その後ろに置くと ASCII の stdout で traceback が stderr へ出て
    # rc 1 になる (実測)
    _tolerate_unencodable_stdout()
    env = os.environ if env is None else env
    # allow_abbrev の既定 (True) は `--che` を別モードの短縮として受理する。
    # typo が静かに別の入口へ落ちないよう完全形の明示だけに絞る (先例 issue-id.py)
    parser = argparse.ArgumentParser(
        description="禁止語リストで固有名詞の流入を検査する",
        allow_abbrev=False,
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="追跡ファイルを走査する")
    mode.add_argument("--check-text", metavar="PATH", help="テキスト 1 本を走査する")
    # parse_args ではなく parse_known_args を使う。argparse の
    # `error: unrecognized arguments: <argv 全部>` は余分な引数をそのまま stderr へ出すので、
    # hook から `pass_filenames: false` が落ちて追跡パスが引数で渡ると、汚染パスの置き換えが
    # 隠すはずのパス (= 語そのもの) が Failed ブロックへ並ぶ (実測)。配線に依存しない防御に
    # するため、件数だけを報告してここで止める。
    args, extra = parser.parse_known_args(argv)
    if extra:
        print(f"[x] 余分な引数が {len(extra)} 件ある。引数は印字しない", file=sys.stderr)
        return EXIT_UNABLE

    try:
        path = resolve_denylist(env)
        if path is None:
            print(f"{STATUS_SKIPPED} reason=env-unset")
            print(f"禁止語ガードを skip した ({ENV_VAR} が未設定)")
            return EXIT_OK
        entries = load_entries(path)
        failed = self_check(entries)
        if failed:
            raise Unable(
                "禁止語リストの "
                f"{', '.join(str(e.lineno) for e in failed)} 行目が自己照合に失敗した。"
                "そのエントリは本文に同じ語があっても検出できない"
            )
        if args.check_text is not None:
            return run_check_text(args.check_text, entries)
        return run_check(Path.cwd(), entries, env)
    except Unable as e:
        print(f"[x] {e}", file=sys.stderr)
        return EXIT_UNABLE
    except Exception as e:  # noqa: BLE001
        # traceback を出力経路から閉じる。例外の str と traceback はリストのパスも
        # 語も載せることがあり (FileNotFoundError・KeyError・ValueError で実測)、
        # pre-commit は Failed ブロックへ hook の stdout+stderr を切り詰めずに出す
        print(f"[x] 予期しない失敗: {type(e).__name__}", file=sys.stderr)
        return EXIT_UNABLE


if __name__ == "__main__":
    raise SystemExit(main())
