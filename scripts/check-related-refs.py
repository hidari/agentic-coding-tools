#!/usr/bin/env python3
"""`## 関連` 節の Issue 参照が識別子だけで書かれ、その識別子が実在するかを検査する。

Issue をクローズすると `docs/issues/closed/` へ移り、パスの深さが 1 段変わる。相対リンクは
この深さに依存するので、移動のたびに両方向の書き換えが要る。リンクを張らなければ移動を
残したまま書き換えが 0 になる。

規約そのものの canonical は `dev-workflow:in-repo-issue` skill、決定に至る 3 案の比較と
設計は配布先リポジトリ dotfiles の Issue 43 とその spec が持つ。ここは検査だけを持つ。

## 記法は借りる、写さない

識別子とディレクトリ名の記法は同じリポジトリの
`plugins/dev-workflow/skills/in-repo-issue/scripts/issue-id.py` が canonical なので、
写さずに importlib で読み込んで借りる。dotfiles 側の同等の検査は apm の deploy 先が
追跡下に無いため写しを持たざるを得ず、記法が変わると検査側だけが取り残されて
「違反 0 件」で沈黙する。上流にはその制約が無い。

借用名は BORROWED が持ち、起動時に実在を検査する。rename されたときに素通りではなく
exit 2 (検査不能) で落ちるようにするため。借用先が private 名 (`_` 始まり) を含むのは
承知のうえで、失敗が静かにならない側へ倒してある。

数字記法 (`#<N>`) の判定は借用した `GITHUB_REF` と 2 つの免除をそのまま使う。ここを
書き直すと後読みの規則が 2 つ目の canonical になる (実際、後読みを自前で厳しく書いた版は
`<owner>/<repo>#<N>` をパターン段階で落としており、免除の分岐へ一度も到達していなかった)。
`PR ` と `<owner>/<repo>` の免除を数字記法だけに効かせるのも借用先に合わせたもので、
`ISSUE-<N>` 形まで広げると `PR ISSUE-<N>` のような形が免除される。

番号の同一性も借用先に合わせて整数で持つ。文字列のままにすると `ISSUE-07` が `ISSUE-7` を
指せなくなる (実測: 借用先は `int()` を鍵にしており、写した側だけが正規化を落としていた)。

## 何を検査するか

1. `issue.md` が `## 関連` 節を持つか。見出しは h2 の完全一致で見るので、`##` と語の間の
   余分な空白・末尾の `#` 列・setext 形では節が開かない。開かなければ配下は無検査になるので、
   「節が無いこと」自体を違反として報告する (実測: 追跡下の `issue.md` は全て持つ)
2. 節に書かれた識別子のうち、リポジトリ名の前置が無いものが自リポジトリに実在するか。
   実在しないのは参照先が消えたか綴りが違うかのどちらかで、どちらも読む側が辿れない
3. 節にローカルリンクが残っていないか。既存のリンクは LINK_BASELINE に記録して除外し、
   実態と突き合わせる。増えた側だけでなく減った側も報告する。減ったのに baseline を
   残すと次の 1 本が無検査で入る
4. `<リポジトリ名> 側:` のようにリポジトリ名でまとめて括る段落見出しが無いか。見出しは
   行単位の走査から見えないので、配下の識別子が前置を失って自リポジトリへ静かに解決される

baseline の鍵は Issue ディレクトリ名にする。パスを鍵にすると、クローズの `git mv` で
「baseline に記録があるのに見つからない」と「リンクが増えた」の 2 件が同時に出る。後者は
1 本も増えていないので診断が誤りで、読んだ人はリンクを外しにいく (実測)。移動で変わらない
同一性は識別子の側で、パスはクローズ手順が意図的に変えるもの。

## 他リポジトリ参照は実在を検査しない

そのリポジトリが手元にあるとは限らない。手元にあるかどうかで結果が変わる検査は、CI と
開発機で違う答えを返す。前置の判定は FOREIGN_REPOS の集合で行い、集合に無い名前は前置と
認めず報告される側へ倒す。「ハイフンを含むトークンを前置とみなす」ような近似へ寄せない
こと。免除が広がっても結果は違反 0 件の緑にしかならず、出力からは気づけない
(近似を測って外した実測は dotfiles の 43-spec が持つ)。

照合は ASCII のトークン境界で行う。部分文字列で見ると `my-dotfiles-backup` のような
無関係の語が前置として通る。

前置は同じ行のそれより後ろに在る識別子すべてに効く。1 つの前置で他リポの識別子を続けて
書く形があるため。行をまたいでは効かない。

## 終了コード

0 (合格) / 1 (違反あり) / 2 (検査不能)。2 を 1 と分ける理由は
scripts/check-leak-guard-rules.py と同じで、「規約違反」と「検査を走らせられなかった」を
同じ赤にすると環境の不備がルール違反に見えるため。対象ファイルが 1 件も無い場合と、
対象ファイルを decode できない場合も 2 にする。違反 0 件で緑にすると「何も見ていない」が
「合格」に化ける。

## 既知の限界

- 識別子の抽出は行単位の regex で Markdown パーサではない。`Issue 43 件` のような数量
  表現も識別子として読む。射程を `## 関連` 節に限っているのはこのため (節に現れる識別子は
  参照だと言い切れる)。それでも実在しない番号の数量表現は誤検出になる
- コードフェンスの閉じ忘れは、以降の全行をこの検査から隠す。閉じ忘れ自体を違反として
  報告するのは issue-id.py の scan_text で、あちらは追跡ファイル全体という上位集合を
  同じエンジンで走査する。ここで二重に持つと canonical が 2 つになるので持たない
  (依存していることの pin は test_check_related_refs.py が持つ)
- フェンスの判定はバッククォート形だけで、チルダ形 (`~~~`) はプローズとして読む。
  チルダフェンスの中に h2 見出しがあると `## 関連` 節がそこで閉じ、以降が無検査になる
  (隔離コピーで実測)。直すときは借用先の FENCE_LINE を広げるだけでは足りず、開いた
  フェンスの文字種を覚える必要があるので、借用先と prose_lines の両方の状態機械へ手が
  要る。正規表現だけ広げるとフェンスの入れ子と閉じ忘れ検出が壊れる (これも実測)
- 節の中の setext 見出し (`見出し` の下に `---`) は節を閉じない。以降が `## 関連` の中身
  として読まれるので、偽陽性の側に倒れる (実測)
- ローカルリンクの中に書かれた識別子は読まない。その行はリンク側の検査が本数として
  報告するため。外部 URL とアンカーを指すリンクは本数に数えないので、そちらはリンク
  テキストの側から識別子を読む
- インラインコードを潰してからリンクを潰すので、リンクのターゲットにバッククォートを
  含む形 (`[a](b ` + 「バッククォート」 + `c) ISSUE-1`) はコードスパンと誤読され、後ろの
  識別子が消える。作為的な形なので直していない
- 走査対象は index ではなく作業ツリーの中身で、実在判定はファイルシステムを見る。
  違反を stage してから作業ツリーだけ直すと hook は緑になり、未追跡の Issue ディレクトリも
  実在として通る (どちらも実測)。CI は commit されたツリーを読むので第 2 層になる。
  issue-id.py の `--check` と共通の性質
- baseline を増やす側は機械が止めない。リンクを 1 本足して同じコミットで LINK_BASELINE を
  1 増やすと実態と一致するので緑で通る。単調非増加はレビューが守る約束であって検査が
  守っている状態ではない (埋める作業は ISSUE-28 が持つ)
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 記法の canonical。写さずに借りる (理由はモジュール docstring)
NOTATION_SOURCE = "plugins/dev-workflow/skills/in-repo-issue/scripts/issue-id.py"

# 借りる名前。private 名を含むので、rename が静かな素通りにならないよう実在を検査する
BORROWED = (
    "GitError",
    "ANY_ISSUE_DIR",
    "GITHUB_REF",
    "CROSS_REPO_REF",
    "GITHUB_REF_ALLOWED_PREFIX",
    "PREFIX",
    "FENCE_LINE",
    "_mask_inline_code",
    "_git",
    "issue_dirs",
    "issue_dir_of",
    "resolve_root",
)

ISSUE_ROOT_POSIX = "docs/issues"

# 走査対象。git の pathspec なので `*` は `/` も跨ぐ (実測: closed/ 配下も返る)
TARGET_PATHSPEC = f"{ISSUE_ROOT_POSIX}/*.md"

# 節を必ず持つべきファイル。補助資料 (`<ID>-spec.md` 等) には要求しない
ISSUE_FILENAME = "issue.md"

RELATED_HEADING = "## 関連"

# 他リポジトリの Issue を指すときに識別子へ前置する名前。ここに無い名前は前置と認めず
# 自リポジトリで解決するので、参照するリポジトリが増えたらここへ足す
FOREIGN_REPOS: tuple[str, ...] = ("dotfiles",)

# 節の境界を決める見出し。h3 以下は境界にならず節の本文として走査する (識別子も括りの
# 見出しも h3 の中に書けるため)。段数を捨てる条件と対で読ませないよう、拾う側を h2 までに絞る
HEADING = re.compile(r"^#{1,2}\s")

# `<リポジトリ名> 側:` の形。配下の識別子が前置を失う形なので、見出しそのものを報告する。
# リポジトリ名が既知かどうかは条件に入れない。未知の名前で括られた見出しこそ配下が
# 自リポジトリで解決される側なので、既知に限ると危ない方だけが報告されなくなる。
# 語間の空白を許すのは、単一トークン前提だと `<リポジトリ名> リポジトリ側:` が素通りし、
# この見出しが防ごうとした失敗が 2 語で再現するため (実測)。
# 全角コロンはコードポイントで書く。字面で置くと半角と見分けが付かず、片方を落とす
# 変更が目視レビューを通り抜ける (落ちた側は違反 0 件の緑になるので出力にも出ない)
SCOPE_HEADING = re.compile(r"^\S(?:.*\S)?\s*側[:：]\s*$")

# リンク記法。ターゲットは山括弧で囲う形と素の形の両方が実在する。囲う形を先に試すのは、
# 中身に括弧を含めても止まらないようにするため
LINK = re.compile(r"\[([^\]]*)\]\(\s*(<[^>]*>|[^()]*)\)")

# 参照形式のリンク (`[text][ref]` と `[text][]`)。描画も移動で切れる性質もインライン形と
# 同じなので同じ 1 本として数える。数えないと規約を丸ごと迂回できる (実測)
REFERENCE_LINK = re.compile(r"\[[^\]]*\]\[[^\]]*\]")

# スキーム付きのターゲットは外部リンク。`http:` `https:` `mailto:` などをまとめて受ける
EXTERNAL_TARGET = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")

# `## 関連` 節に残っているローカルリンクの本数。鍵は Issue ディレクトリ名で、クローズの
# `git mv` で変わらない。既存のリンクは一括変換しない (リンクテキストがディレクトリ名と
# 一致していない実例が ISSUE-24 の実測にあり、機械変換するとその不一致を識別子側へ持ち込む)。
# リンクを外したらここも減らす。減らし忘れは「baseline が実態と合っていない」として
# 報告される。増やす側を機械が止めていないことは docstring の「既知の限界」を参照。
LINK_BASELINE: dict[str, int] = {
    "ISSUE-10_SSH 断時に prlctl exec を直接叩くときの注意が既存文書から辿れない": 2,
    "ISSUE-11_resolve-ip が APIPA をそのまま返すとき利用者に何も知らせない": 2,
    "ISSUE-12_scripts の検査スクリプトが自分自身のテストを持たない": 2,
    "ISSUE-13_両取り付けの同時撤去を機構で検出できない": 2,
    "ISSUE-14_in-repo Issue の識別子が GitHub の番号空間と衝突する": 7,
    "ISSUE-23_露出スイープで判定を保留した 5 箇所が残っている": 2,
    "ISSUE-24_Issue 間の相対リンクの書式が 3 通り混在している": 2,
    "ISSUE-4_VM の pwsh probe が偽陽性で exec と health が実機で動かない": 2,
    "ISSUE-8_run-python-tests.py の件数ガードが実質 1 件で機能していない": 1,
    "ISSUE-9_doctor が APIPA アドレスを健全と判定する": 3,
}

NO_SECTION_MESSAGE = (
    f"`{RELATED_HEADING}` 節が無い。見出しは h2 の完全一致で見るので、`##` と語の間の"
    "余分な空白・末尾の `#` 列・setext 形では節が開かず、配下が無検査になる"
)

SCOPE_MESSAGE = (
    "リポジトリ名でまとめて括る見出しは、配下の識別子が前置を失う。行単位の走査から"
    "見出しは見えないので、番号が自リポジトリにも在ると別の Issue へ静かに解決される。"
    "見出しをやめ、識別子ごとに同じ行へ前置すること"
)

MISSING_MESSAGE = (
    f"この識別子に対応する Issue が {ISSUE_ROOT_POSIX} に無い。参照先が消えたか綴りが違う。"
    "他リポジトリを指すならリポジトリ名を同じ行の識別子より前へ置くこと "
    f"(既知: {', '.join(FOREIGN_REPOS)})"
)

LINK_OVER_MESSAGE = (
    f"`{RELATED_HEADING}` 節ではリンクを張らず識別子だけを書くこと。"
    "リンクは Issue の移動で切れるが、識別子は切れない"
)

LINK_UNDER_MESSAGE = (
    "リンクが減ったので LINK_BASELINE も同じ値へ下げること。"
    "残したままだと次の 1 本が無検査で入る"
)

LINK_ORPHAN_MESSAGE = (
    f"baseline に記録があるが `{RELATED_HEADING}` 節を持つ追跡ファイルとして見つからない。"
    "参照が消えたなら LINK_BASELINE から消すこと"
)


class CheckError(RuntimeError):
    """検査を走らせられなかった。違反 0 件と区別するために送出する。"""


# --- 記法の借用 ---------------------------------------------------------------

_notation = None


def notation():
    """issue-id.py を読み込んで返す。記法の canonical は向こうにある。"""
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
            "記法の canonical が動いたので、この検査の借用先を追随させること"
        )
    _notation = module
    return module


def resolve_root(explicit: str | None) -> Path:
    """借用先で root を解決する。git を走らせられないのも検査不能なので CheckError へ寄せる。"""
    module = notation()
    try:
        return module.resolve_root(explicit)
    except module.GitError as e:
        raise CheckError(str(e)) from e


def word_pattern(prefix: str) -> re.Pattern[str]:
    """`ISSUE-<N>` と `Issue <N>` を受けるパターンを作る。

    境界に `\\b` を使わない。日本語は Python の `\\w` に入るので、`詳細はISSUE-1` のように
    かな漢字の直後へ続けて書かれると境界が成立せず、識別子が黙って読まれなくなる (実測)。
    ASCII の後読みなら書き方に依らず読める。

    `Issue <N>` を受けるのは、識別子が裸の数字である記法のリポジトリを指す参照が実在する
    ため。自リポジトリの記法の canonical は借用先で、こちらは prefix をそこから受け取る。
    """
    return re.compile(r"(?<![0-9A-Za-z])(?:" + re.escape(prefix) + r"|Issue\s+)([0-9]+)")


def _repo_pattern(repo: str) -> re.Pattern[str]:
    """リポジトリ名を ASCII のトークン境界で照合するパターン。"""
    return re.compile(r"(?<![0-9A-Za-z_-])" + re.escape(repo) + r"(?![0-9A-Za-z_-])")


FOREIGN_REPO_PATTERNS = tuple(_repo_pattern(repo) for repo in FOREIGN_REPOS)


# --- テキスト走査 -------------------------------------------------------------


def prose_lines(text: str):
    """コードフェンスの外の行を順に返す。

    閉じ忘れたフェンスは以降を隠すが、閉じ忘れ自体は issue-id.py の scan_text が
    追跡ファイル全体で報告する (docstring の「既知の限界」)。
    """
    fence_len = 0
    fence = notation().FENCE_LINE
    for line in text.splitlines():
        marker = fence.match(line)
        if fence_len:
            if marker and not marker.group(2).strip() and len(marker.group(1)) >= fence_len:
                fence_len = 0
            continue
        if marker:
            fence_len = len(marker.group(1))
            continue
        yield line


def related_lines(text: str) -> list[str] | None:
    """`## 関連` 節の本文行を返す。節が無ければ None。

    空の節と節が無い場合を分けるために None を使う。前者は書き手が意図して空にした形で、
    後者は見出しが規約どおりでないか節そのものが落ちている形。
    """
    lines: list[str] = []
    inside = found = False
    for line in prose_lines(text):
        if HEADING.match(line):
            inside = line.strip() == RELATED_HEADING
            found = found or inside
            continue
        if inside and line.strip():
            lines.append(line)
    return lines if found else None


def _is_local_link(match: re.Match[str]) -> bool:
    """リンクがリポジトリ内を指すか。外部 URL と純アンカーは除く。

    判定を 1 箇所に閉じるのは、綴りが 2 つあると外部リンクの扱いを変えたときに片方だけが
    直るため。
    """
    target = match.group(2).strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    return bool(target) and not target.startswith("#") and not EXTERNAL_TARGET.match(target)


def strip_links(line: str) -> str:
    """リンク記法を同じ長さの空白へ潰す。列位置は保つ。

    識別子を読む前にこれを通すのは、角括弧が `PR ` の直前判定を壊すため
    (`PR [#588](...)` の形)。潰さないとリンクテキスト内の番号も識別子として読まれる。
    """
    return LINK.sub(lambda m: " " * len(m.group(0)), line)


def _hash_spans(body: str) -> list[tuple[int, str, str]]:
    """数字記法の識別子を (開始位置, 書かれた形, 番号) で返す。

    パターンも 2 つの免除も借用先の `_scan_line` と同じものを同じ順で使う。免除が
    truthy を要求するのも借用先に合わせている (定数を空にする変更が「全部通す」方向へ
    静かに広がるため)。
    """
    module = notation()
    found = []
    for match in module.GITHUB_REF.finditer(body):
        before = body[: match.start()]
        allowed = module.GITHUB_REF_ALLOWED_PREFIX
        if allowed and before.endswith(allowed):
            continue
        if module.CROSS_REPO_REF.search(before):
            continue
        found.append((match.start(), match.group(0), match.group(1)))
    return found


def identifier_spans(body: str) -> list[tuple[int, str, str]]:
    """行から自リポジトリの Issue 識別子を (開始位置, 書かれた形, 番号) で返す。

    数字記法と語形は排他なので (`Issue #<N>` は数字記法の枝が前置ごと消費する)、
    1 つの出現が 2 回数えられることはない。
    """
    pattern = word_pattern(notation().PREFIX)
    spans = [(m.start(), m.group(0), m.group(1)) for m in pattern.finditer(body)]
    spans.extend(_hash_spans(body))
    return sorted(spans)


def identifier_matches(masked: str) -> list[tuple[str, str]]:
    """インラインコードを潰した行から識別子を (書かれた形, 番号) で返す。

    潰す責務を呼び出し側へ出してあるのは、識別子側とリンク側で潰しの規則が一致することを
    コメントの約束ではなく構造で保証するため。

    リポジトリ名を前置した他リポジトリ参照は除く。判定は識別子より前のテキストだけを
    見るので、同じ行の後続の識別子は通常どおり読まれる。
    """
    body = strip_links(masked)
    found = []
    for start, written, number in identifier_spans(body):
        before = body[:start]
        if any(pattern.search(before) for pattern in FOREIGN_REPO_PATTERNS):
            continue
        found.append((written, number))
    return found


def foreign_link_identifiers(masked: str) -> list[tuple[str, str]]:
    """外部 URL / アンカーを指すリンクのテキストから識別子を返す。

    ローカルリンクのテキストは読まない。そちらは本数として報告されるので、テキストからも
    読むと同じ 1 本が 2 回報告される。外部を指すリンクは本数に数えないため、読まないと
    テキストの中の識別子がどちらの機構からも見えなくなる (実測)。
    """
    found: list[tuple[str, str]] = []
    for match in LINK.finditer(masked):
        if not _is_local_link(match):
            found.extend(identifier_matches(match.group(1)))
    return found


def section_refs(lines: list[str]) -> tuple[list[str], list[tuple[str, str]], int]:
    """節を 1 度だけ走り (括りの見出し, 識別子, リンク本数) を返す。

    検査とサマリが別々に走ると、片方だけを直したときに出力と Finding が食い違う。
    """
    headings: list[str] = []
    identifiers: list[tuple[str, str]] = []
    links = 0
    mask = notation()._mask_inline_code
    for line in lines:
        if SCOPE_HEADING.match(line.strip()):
            headings.append(line.strip())
            continue
        masked = mask(line)
        links += sum(1 for m in LINK.finditer(masked) if _is_local_link(m))
        links += len(REFERENCE_LINK.findall(masked))
        identifiers.extend(identifier_matches(masked))
        identifiers.extend(foreign_link_identifiers(masked))
    return headings, identifiers, links


# --- リポジトリ走査 -----------------------------------------------------------


def target_files(root: Path) -> list[str]:
    """走査対象の Issue ドキュメントをリポジトリ相対パスで返す。"""
    module = notation()
    try:
        out = module._git(root, "ls-files", "-z", TARGET_PATHSPEC)
    except module.GitError as e:
        raise CheckError(str(e)) from e
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


def baseline_key(rel: str) -> str:
    """baseline の鍵。Issue ディレクトリ名で、クローズの `git mv` で変わらない。

    Issue ディレクトリの外にあるファイル (テンプレート等) はパスをそのまま鍵にする。
    """
    got = notation().issue_dir_of(rel)
    return got[0] if got else rel


def existing_numbers(root: Path) -> set[int]:
    """実在する Issue ディレクトリの番号。active と closed の両方を含む。

    整数で持つのは借用先が `int()` を鍵にしているため。文字列のままだと `ISSUE-07` が
    `ISSUE-7` を指せない。
    """
    module = notation()
    numbers = set()
    for _, name in module.issue_dirs(root):
        match = module.ANY_ISSUE_DIR.match(name)
        if match:
            numbers.add(int(match.group(1)))
    return numbers


def check_link_baseline(
    links_by_key: dict[str, int], sources: dict[str, str], allowed: dict[str, int]
) -> list[tuple[str, str, str]]:
    """リンク本数を baseline と突き合わせる。増えた側も減った側も報告する。"""
    findings: list[tuple[str, str, str]] = []
    for key, count in sorted(allowed.items()):
        if key not in links_by_key:
            findings.append((key, f"baseline {count}", LINK_ORPHAN_MESSAGE))
    for key, count in sorted(links_by_key.items()):
        limit = allowed.get(key, 0)
        where = sources.get(key, key)
        if count > limit:
            findings.append((where, f"{count} > {limit}", LINK_OVER_MESSAGE))
        elif count < limit:
            findings.append((where, f"{count} < {limit}", LINK_UNDER_MESSAGE))
    return findings


def check(
    root: Path, baseline: dict[str, int] | None = None
) -> tuple[list[tuple[str, str, str]], dict[str, int]]:
    """検査を実行し (違反, サマリの数値) を返す。"""
    allowed = LINK_BASELINE if baseline is None else baseline
    files = target_files(root)
    if not files:
        # 0 件は「違反なし」ではなく「何も見ていない」
        raise CheckError(
            f"{TARGET_PATHSPEC} に一致する追跡ファイルが 1 件も無い。走査対象ゼロは合格ではない"
        )

    numbers = existing_numbers(root)
    findings: list[tuple[str, str, str]] = []
    links_by_key: dict[str, int] = {}
    sources: dict[str, str] = {}
    issue_files = identifiers = 0

    for rel in sorted(files):
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as e:
            # 走査対象そのものが読めないので、skip ではなく検査不能へ倒す。skip にすると
            # 不正なバイト 1 個でファイル全体が無検査のまま緑になる (実測)
            raise CheckError(f"{rel} を読めない: {e}") from e
        is_issue = Path(rel).name == ISSUE_FILENAME
        lines = related_lines(text)
        if lines is None:
            if is_issue:
                issue_files += 1
                findings.append((rel, RELATED_HEADING, NO_SECTION_MESSAGE))
            continue
        if is_issue:
            issue_files += 1
        headings, found, count = section_refs(lines)
        identifiers += len(found)
        key = baseline_key(rel)
        # 同じ Issue ディレクトリに `## 関連` 節を持つファイルが複数あっても数を落とさない
        links_by_key[key] = links_by_key.get(key, 0) + count
        sources.setdefault(key, rel)
        findings.extend((rel, heading, SCOPE_MESSAGE) for heading in headings)
        findings.extend(
            (rel, written, MISSING_MESSAGE)
            for written, number in found
            if int(number) not in numbers
        )

    findings.extend(check_link_baseline(links_by_key, sources, allowed))
    # 節数とリンク総数は links_by_key から導く。別のアキュムレータで数えると、登録条件を
    # 触ったときにサマリだけが静かにずれる。サマリは「何を何件見たか」の唯一の出力
    summary = {
        "issue_dirs": len(numbers),
        "files": len(files),
        "issue_files": issue_files,
        "sections": len(links_by_key),
        "identifiers": identifiers,
        "links": sum(links_by_key.values()),
    }
    return findings, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="`## 関連` 節の Issue 参照が識別子だけで書かれ、実在するかを検査する",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--root", metavar="PATH", help="リポジトリの root (既定: git rev-parse --show-toplevel)"
    )
    args = parser.parse_args(argv)

    try:
        findings, summary = check(resolve_root(args.root))
    except CheckError as e:
        print(f"[x] {e}", file=sys.stderr)
        return 2

    for where, detail, message in findings:
        print(f"  [x] {where}: {detail} — {message}", file=sys.stderr)
    print(
        f"実在する Issue: {summary['issue_dirs']} 個 / 走査したファイル: {summary['files']} 個 "
        f"(うち {ISSUE_FILENAME} {summary['issue_files']} 個) / {RELATED_HEADING} 節: "
        f"{summary['sections']} 節 / 識別子: {summary['identifiers']} 件 / "
        f"残リンク: {summary['links']} 本"
    )
    if findings:
        print(f"違反 {len(findings)} 件")
        return 1
    print("違反なし")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
