#!/usr/bin/env python3
"""active な Issue の閉じ忘れと、配置と status の不整合と、親子リンクの整合を検出する。

in-repo Issue は「## タスク が全て [x] なら閉じる」という規約で運用されるが、その規約は
in-repo-issue skill の Phase C.3 が散文で持つだけで、守られたかを見る層がどこにも無い。
実装 PR でタスクを埋めてクローズを次の PR へ回すと、その間 Issue は「完了なのに open」に
なる (実測: 過去の main の c33ab70 と d1de3ea がその状態)。

この検査を入れると、その状態でコミットできなくなる。つまりクローズは実装 PR へ同梱する
ことが強制される。main が保護されていて直 push を選べないこのリポジトリではそれが正しいが、
post-merge クローズを既定とするリポジトリとは両立しない。**そのまま配布しないこと。**

不変条件 C (子が全て closed なら親も closed) も同じ取引をする。C を違反 (rc 1) にすると
「最後の子を closed にするコミット」に親の close の同梱が強制され、Phase E.4 が正規の
結末として持つ「提案を拒否 → 何もしない」がこのリポジトリでは選べなくなる (拒否した瞬間に
恒久的な赤になる)。忘れたら赤くなることを取り、拒否の自由を手放す判断で、不変条件 A が
既にしている取引と同型。配布できない理由は A だけでなく C も持つ。

C が潰す運用は E.4 の拒否だけではない。同じ取引で次の 2 つも潰れる。

- 同梱節が示す「親の close を別 PR で行う」経路。子を閉じた PR がその時点で赤くなるので、
  このリポジトリでは分けられない。skill 側の記述は配布先の一般則として正しく、C を持つ
  このリポジトリだけが選べない。祖先チェーンがあると同じ強制が世代を遡って効くので、
  1 コミットで全世代を閉じることになる (E.3 の「世代ごとに提案して聞く」は残らない)
- A.5 の分割を複数コミットへ割る形。子を起票して `parent:` を書き、親の `children:` を
  まだ書いていない状態は「親子リンクが片側にしか無い」で赤くなる。A.5 は分割を 1 コミット
  にする形を既に規定しているので通常は当たらない

記法と母集団の canonical は借用先が持つ。ここでは写さない。

不変条件 C の限界:

- 判定は配置 (closed/ 配下か) だけで行う。frontmatter の status との不整合は不変条件 B の
  担当なので二重に見ない (Phase E が「ディレクトリ位置だけで判定する」と決めているのと同じ)
- 親子の辺は frontmatter のキー名が規約どおりであることに依存する。片側だけが乖離した形は
  「親子リンクが片側にしか無い」で赤くなるが、両側が同時に乖離すると辺が 1 本も立たず、
  0 組で静かに緑になる。ここは残余で ISSUE-38 の射程。スキーマ外のキーを報告する規則を
  ここへ足すと ISSUE-38 の canonical が 2 つになるので、この検査は持たない。
  キー名が規約どおりで値の書式だけが違う形は別で、こちらは注記で名乗って判定を止める
  (unreadable_link_keys)
- 結果の状態しか見ない。D.5 (Phase E を起動する手順) を実行し忘れたこと自体は検出せず、
  親子 Issue が実在して子が全て closed になって初めて赤くなる
- 逆向きの不整合 (親が closed なのに子が active) は見ない。見るようにすると不変条件 A と
  正面衝突する。A は親の箱が全て `[x]` になった時点で「子が active でも親を閉じろ」と要求
  するので、閉じた結果を C が違反にすると、どちらの状態も取れないコミット不能になる。
  この衝突の調停は ISSUE-45 の射程で、そこが決まるまでこの向きは持たない
- 参照先のディレクトリ名が識別子の形でないと、その Issue は索引に載らないので参照は
  「実在しない」と報告される。実在はするので文言は正確ではないが、ディレクトリ名の形式は
  issue-id.py --check が違反として報告し、番号を採れなかったことはこの検査も注記で名乗る
  ので、出力からは追える
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
BORROWED = ("ANY_ISSUE_DIR", "CLOSED", "FENCE_LINE", "GitError", "PREFIX",
            "issue_dirs", "resolve_root")

# 見出しのレベルと空白の揺れを吸収する。厳密に `## タスク` だけを見ると、`### タスク` で
# 書かれた完了済み Issue が「タスク節なし」として素通りする (fixture で実測)
TASK_HEADING = re.compile(r"^#{2,3} *タスク *$")

# 箱として認めるのは半角/全角スペースと x/X だけ。GitHub もこれ以外は箱として描かない
CHECKBOX = re.compile(r"^[ \t]*[-*+] \[([ 　xX])\]")
CHECKED = ("x", "X")


def _key_line(key: str, value: str) -> re.Pattern[str]:
    """frontmatter の 1 行を読む regex を組む。末尾コメントの許し方を 1 箇所へ集める。

    末尾コメントを許すのは、in-repo-issue の frontmatter スキーマが
    `status: open  # open / in_progress / closed` の形で例示しているため。これを写した
    issue.md の status を読めないと、その Issue が不変条件 B の検査から静かに外れる。
    `#` をスペースに続くときだけコメントとして扱うのは YAML の規則に揃えるため。
    """
    return re.compile(r"^" + re.escape(key) + r": *" + value + r"(?: +#.*)? *$")


# frontmatter で読むキーと値の書式。canonical は in-repo-issue SKILL.md の
# 「frontmatter スキーマ」節で、ParityWithTheSchema が節の yaml フェンスの行をここへ
# 実際に流し、キー集合と値の読み取りの両方が一致することを見る。キー集合だけを見る
# parity では、値書式を読めなくする変異がキー集合を変えないので緑のまま通る
FRONTMATTER_READERS = {
    "status": _key_line("status", r"(\S+)"),
    "parent": _key_line("parent", r"(\S+)"),
    "children": _key_line("children", r"\[(.*?)\]"),
}

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


# 行の種別。散文だけを採る呼び出し (_strip_fences_and_comments) と、フェンスの中身が要る
# 呼び出し (テストがスキーマ節の yaml を読む経路) が同じ 1 本の状態機械を通れるようにする
LINE_PROSE = "prose"
LINE_FENCE_MARKER = "fence-marker"
LINE_FENCE = "fence"
LINE_COMMENT = "comment"


def classify_lines(text: str) -> tuple[list[tuple[str, str]], bool]:
    """各行を (行, 種別) へ分類し、フェンスが閉じずに文書末尾へ達したかを返す。

    フェンスの開閉は単純トグルではなく借用元 issue-id.py の scan_text と同じ意味論を使う:
    閉じ行は情報文字列を持たず (`marker.group(2).strip()` が空) かつ開始と同じ長さ以上の
    バッククォートを持つときだけ閉じる (CommonMark)。単純トグルだと、4 連で開いて内側に
    3 連の行がある文書で閉じ判定を誤り (3 連の行を「閉じた」と数えてしまう)、直後に続く
    本物のタスク節がフェンス内へ吸い込まれて消える (実測)。

    種別を捨てずに返すのは、状態機械の写しを増やさないため。散文だけが要る呼び出しは
    _strip_fences_and_comments を通し、フェンスの中身が要る呼び出しは種別で選ぶ。写しを
    持つと片方だけ直したときにずれる。実際、テスト側に置いた 2 つ目の状態機械は HTML
    コメントを見ておらず、コメントの中にフェンスの開き行がある文書で本番と割れていた
    (節の終端になる見出しをフェンス内と誤判定し、節が次の見出しまで伸びた。実測)。

    コメント判定をフェンス判定より先に置くのは、フェンス内の HTML コメント風の行も
    コメントとして扱うため。開きと閉じが同じ行にある `<!-- x -->` はコメント扱いしない
    (その行で閉じているので、以降の行を巻き込まない)。
    """
    fence = notation().FENCE_LINE
    fence_len = 0
    in_comment = False
    classified: list[tuple[str, str]] = []
    for line in text.splitlines():
        if in_comment:
            if "-->" in line:
                in_comment = False
            classified.append((line, LINE_COMMENT))
            continue
        if "<!--" in line and "-->" not in line:
            in_comment = True
            classified.append((line, LINE_COMMENT))
            continue
        marker = fence.match(line)
        if fence_len:
            if marker and not marker.group(2).strip() and len(marker.group(1)) >= fence_len:
                fence_len = 0
                classified.append((line, LINE_FENCE_MARKER))
            else:
                classified.append((line, LINE_FENCE))
            continue
        if marker:
            fence_len = len(marker.group(1))
            classified.append((line, LINE_FENCE_MARKER))
            continue
        classified.append((line, LINE_PROSE))
    return classified, fence_len != 0


def _strip_fences_and_comments(text: str) -> tuple[list[str], bool]:
    """フェンスと HTML コメントの中身を除いた行のリストと、フェンスが閉じずに文書末尾へ
    達したかを返す。

    scan_tasks と collect() が同じ状態機械を共有するのは、二重に持つと片方だけ直したときに
    フェンスの意味論がずれるため。collect() は同じ issue.md に対しこの関数を 1 回だけ呼び、
    返ってきた lines と unclosed の両方を使い回す。
    """
    classified, unclosed = classify_lines(text)
    return [line for line, kind in classified if kind == LINE_PROSE], unclosed


def _scan_lines_for_tasks(lines: list[str]) -> tuple[bool, int, int]:
    """フェンス/コメント除去済みの行から (タスク節の有無, 箱の総数, 未チェック数) を返す。

    scan_tasks と collect() の両方から使う共通本体。箱の数え方を節の内側に限らないのは
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


def is_completed(has_heading: bool, total: int, unchecked: int) -> bool:
    """走査結果から「この Issue を完了とみなすか」を返す。

    判定式をここへ名前で置くのは、collect() のインラインの条件式とテスト側の写しに分裂して
    いたため (実測: collect() の条件から箱 0 個のガードを外しても、テストが同じ式を自前で
    持っていたので 359 件が全て緑のまま通った)。規則のコピーが 2 つあると、片方だけ壊れた
    ことをどの検査も見ない。呼ぶ側は collect() とテストの両方で、必ずこの関数を通す。

    箱が 0 個のときに完了とみなさないのは、チェックリストが空の Issue が自動 close の対象外
    だから。「未チェックが 0」は箱が 1 つも無いときにも成り立つので、その形を除かないと
    `## タスク` の見出しだけ書いて中身が空の Issue が「全て消化済み」に化ける
    (メッセージも「箱 0 個 / 未チェック 0」という矛盾した文面になる)。 in-repo-issue skill の
    C.3 が持つ `boxes == 0` 分岐と同じ判断で、ParityWithPhaseC3 が両者の一致を検証する。
    """
    return has_heading and total >= 1 and unchecked == 0


def frontmatter(text: str) -> list[str] | None:
    """frontmatter の行を返す。開いていない / 閉じていないなら None。

    frontmatter は先頭の `---` で開いて次の `---` で閉じる。閉じる前だけを見るのと、
    閉じ `---` が最後まで現れない文書を諦めるのは同じ目的で、どちらも本文中に同じキーが
    書かれた行を frontmatter の値として拾わないため。

    None は「切り出せなかった」を意味する。その issue.md からは status も parent も
    children も読めないので、呼び出し側は「キーが無い」と区別すること。前者は宣言が丸ごと
    読めていないので辺の対称性を判定できないが、後者は判定できる。

    切り出しは呼び出し側が 1 回だけ行い、返った行を read_status と read_links で使い回す。
    キーごとに切り出し直すと、frontmatter の境界規則を触ったときに「どの読み取りがどの
    規則で読んだか」を追う先が増える (同じ規律を _strip_fences_and_comments が持つ)。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    body = lines[1:]
    for i, line in enumerate(body):
        if line.strip() == "---":
            return body[:i]
    return None


def _read_key(lines: list[str], key: str) -> str | None:
    """切り出し済みの frontmatter から 1 キーの値を返す。無ければ None。"""
    pattern = FRONTMATTER_READERS[key]
    for line in lines:
        m = pattern.match(line)
        if m:
            return m.group(1)
    return None


def read_status(lines: list[str] | None) -> str | None:
    """frontmatter の status を返す。切り出せていないか status が無ければ None。"""
    return None if lines is None else _read_key(lines, "status")


def split_children(value: str) -> list[str]:
    """`children:` の値 (角括弧の中身) を識別子のリストへ割る。

    空要素を落とすのは、`children: []` が「子が 0 件」であって「空文字の子が 1 件」では
    ないため。落とさないと、その空文字が「識別子の形でない」違反として報告される。
    """
    return [item.strip() for item in value.split(",") if item.strip()]


# 親子関係を張るキー。status は不変条件 B が使うだけで辺には関わらないので分けてある
LINK_KEYS = ("parent", "children")


def read_links(lines: list[str] | None) -> tuple[str | None, list[str]]:
    """frontmatter の parent と children を返す。切り出せていなければ (None, [])。"""
    if lines is None:
        return None, []
    children = _read_key(lines, "children")
    return _read_key(lines, "parent"), split_children(children) if children else []


def unreadable_link_keys(lines: list[str] | None) -> list[str]:
    """キー行はあるのに値を読めなかった親子キーを返す。

    「キーが無い」と「キーはあるが値の書式が違う」を区別するために要る。区別しないと、
    標準的な YAML のブロックリスト形 (`children:` の下に `- ISSUE-2` を並べる形) が
    「子が 0 件」と同じ観測値になる。子が `parent` を書いていなければ違反 0 件の静かな緑に、
    書いていれば規約どおりのキー名で書いてある親が「children が欠けている」と違反を
    名乗らされる (どちらも実測)。

    見るのはキー行の有無だけで、値の書式は FRONTMATTER_READERS が canonical のまま。
    接頭辞をキー名から導くのは、ここが 2 つ目の書式仕様へ育たないようにするため。
    """
    if lines is None:
        return []
    return [
        key for key in LINK_KEYS
        if _read_key(lines, key) is None
        and any(line.startswith(key + ":") for line in lines)
    ]


def issue_number(value: str) -> int | None:
    """frontmatter に書かれた識別子を番号へ正規化する。識別子の形でなければ None。

    番号を int で持つのは、文字列のままだと `children: [ISSUE-07]` がディレクトリ
    `ISSUE-7_*` を指せなくなるため (借用元 issue-id.py も番号の同一性を int で持っており、
    写した側だけが正規化を落とすと参照が静かに解決しなくなる)。

    接頭辞は借用先の PREFIX から組む。literal を書くと記法の canonical が 2 つになる。
    ディレクトリ名側の ANY_ISSUE_DIR と違って接頭辞を省略できないのは、frontmatter の値には
    `_<title>` のような後続が無く、裸の数字を許すと `parent: 2026` のような散文がそのまま
    番号へ解決してしまうため。
    """
    m = re.match(r"^" + re.escape(notation().PREFIX) + r"([0-9]+)$", value)
    return int(m.group(1)) if m else None


def dir_number(name: str) -> int | None:
    """Issue ディレクトリ名から番号を返す。形式でなければ None。

    こちらは接頭辞なしの旧形式も採る借用先の ANY_ISSUE_DIR をそのまま使う。移行中の
    リポジトリで旧形式のディレクトリが親子リンクの母集団から静かに落ちないため。
    """
    m = notation().ANY_ISSUE_DIR.match(name)
    return int(m.group(1)) if m else None


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
    """collect() の戻り値。

    active / closed は同じ int 型が並ぶので、位置だけのタプルだと取り違えても TypeError に
    ならず件数が静かに入れ替わる。フィールド名を必須にする NamedTuple へ寄せることで
    取り違えを起こしにくくする。借用元 issue-id.py の Violation(NamedTuple) と同じ形。

    完全には検査できなかった 5 種 (issue.md が無い / status が読めない / 安全に走査できない /
    ディレクトリ名から番号が採れない / 親子キーの値を読めない) を件数ではなく注記のリストで
    持つのは、件数を別に持つと注記と常に同値な冗長な状態になり、片方だけ更新する変更が入る
    余地ができるため。呼び出し側は len() を使う。注記が rel_dir を持つのは、件数だけでは
    どの Issue が壊れているのか出力から特定できないため。

    5 種を分けるのは、止まる検査の範囲がそれぞれ違うため。全部止まるのは issue.md が無い
    場合と読めない場合だけで、残りは部分的に止まる (フェンス閉じ忘れは不変条件 A だけを
    止め、B と C は走る。番号が採れない場合は C だけを止める。親子キーの値を読めない場合は
    C の辺の解決だけを止める)。同じリストへ混ぜると、要約行の件数が実態より広い宣言になる。

    links と judged も同じ理由で件数ではなくデータで持つ。judged を links と別に持つのは、
    辺があっても判定しない親がいるため (親が closed / 宣言を完全には把握できていない)。
    どちらも不変条件 C が見た母集団と判定を検査と同じ 1 パスから出すためのフィールドで、
    別の grep で数え直すと印字された数と検査された数が食い違いうる。
    """

    violations: list[str]
    unscanned_notes: list[str]
    missing_notes: list[str]
    unreadable_notes: list[str]
    unnumbered_notes: list[str]
    unreadable_links_notes: list[str]
    links: list[tuple[int, int]]
    judged: list[int]
    active: int
    closed: int


def prefer_active(
    previous: tuple[str, bool] | None, candidate: tuple[str, bool]
) -> tuple[str, bool]:
    """同じ番号のディレクトリが複数あるときに、どちらを索引へ残すかを決める。

    値は (リポジトリ相対パス, closed/ 配下か)。active 側へ倒すのは Phase E が
    `-maxdepth 2` で active 側だけを引くのと同じ向きで、まだ居る子を見落として親へ
    close 提案を出す形を作らないため。重複そのものは issue-id.py --check が違反として
    報告するので、ここでは倒し方だけを決める。

    collect() から呼ぶだけなら `previous is None` で足りる。借用先の issue_dirs が active
    を先に返すためで、これは呼び出し先の走査順に依存した無害さでしかない。順序が変わった
    ときに静かに壊れないよう、ここでは登場順に依存しない形で決める。テストは collect()
    経由では逆順を作れないので、この関数を直に両順序で叩く。
    """
    if previous is None:
        return candidate
    return candidate if previous[1] and not candidate[1] else previous


class _Declared(NamedTuple):
    """frontmatter に書かれた親子リンクの片側 1 本。

    どちら側の宣言かを bool ではなく key ("parent" / "children") で持つのは、違反メッセージ
    がそのままキー名を名乗れるようにするため。読み手が直す先はキーそのものなので、真偽値へ
    畳んでから文言へ戻すと対応が 2 箇所に分かれる。
    """

    rel_dir: str
    key: str
    value: str
    number: int


def _resolve_links(
    declared: list[_Declared],
    placed: dict[int, tuple[str, bool]],
    unread: set[int],
) -> tuple[list[str], list[tuple[int, int]], list[int]]:
    """宣言された片側の辺から、違反と辺の母集団と、実際に判定した親を返す。

    判定した親を別に返すのは、母集団と判定が同じ数だとは限らないため。親が closed だった
    り、宣言を完全には把握できていなかったりすると、辺はあるのに判定しない。同じ数で
    名乗ると「見たが違反が無かった」と「そもそも見ていない」が区別できなくなる。

    両面 (`parent` と `children`) から集めた辺の union を母集団にする。intersection に
    すると片側が欠けた辺が母集団から静かに落ち、「見落としが 1 本ある」が「そもそも辺が
    無い」に化ける。片側にしか無いことは別の違反として報告する。

    読めなかった Issue が絡む辺を対称性の検査から外すのは、その Issue の `parent` を
    読めていないだけで欠けているとは限らないため。報告すると「読めなかった」が
    「規約違反」を名乗る。母集団からは外さない (親側の宣言は実在するので数える)。

    自己参照は辺にせず違反として報告する。辺にすると親が自分自身の子になり、親は active
    なので「子が全て closed」が必ず偽になって、同じ親が持つ本物の子が全て closed でも
    不変条件 C が沈黙する。しかも両側へ書くと対称性の検査も通るので rc 0 で注記も出ない
    (実測)。片側だけ書いた状態は「片側にしか無い」で赤くなるため、その指摘どおりに
    もう片側を足すと無音へ遷移する。検査自身の修正指示が検査を殺す形になっていた。
    """
    violations: list[str] = []
    from_child: set[tuple[int, int]] = set()
    from_parent: set[tuple[int, int]] = set()
    # `children` の要素を 1 つでも解決できなかった親。読めなかった親と同じ理由で判定へ
    # 進まない。読めた分だけで「子は全て closed」と断定すると、解決できなかった参照が
    # active な子の打ち間違いだったときに誤った指示を出す。しかも参照の違反と親の違反が
    # 同じ出力に並ぶので、上から直す人ほど踏む
    partial: set[int] = set()
    for rel_dir, key, value, number in declared:
        target = issue_number(value)
        if target is None or target not in placed:
            # 何を指すつもりだったのか分からない。active な子の打ち間違いでありうるので、
            # この親の children はもう完全には把握できていない
            reason = (f"{key} の値 {value} が識別子の形でない" if target is None
                      else f"{key} が指す {value} が実在しない")
            violations.append(f"{rel_dir}: {reason}")
            if key == "children":
                partial.add(number)
            continue
        if target == number:
            # 自己参照は指す先が確定しているので、落としても children の把握は欠けない。
            # partial へ入れず判定は続ける (入れると、この 1 行のせいで本物の子が全て
            # closed でも不変条件 C が沈黙する)
            violations.append(f"{rel_dir}: {key} が自分自身を指している")
            continue
        if key == "parent":
            from_child.add((target, number))
        else:
            from_parent.add((number, target))
    for parent, child in sorted(from_child ^ from_parent):
        if parent in unread or child in unread:
            continue
        missing = "親側の children" if (parent, child) in from_child else "子側の parent"
        violations.append(
            f"{placed[parent][0]} と {placed[child][0]}: "
            f"親子リンクが片側にしか無い ({missing} が欠けている)"
        )
    links = sorted(from_child | from_parent)
    children_of: dict[int, list[int]] = {}
    for parent, child in links:
        children_of.setdefault(parent, []).append(child)
    judged: list[int] = []
    for parent, children in sorted(children_of.items()):
        # 親が既に closed なら提案先が無い。Phase E が PARENT_PATH を -maxdepth 2 で引いて
        # closed の親を解決しないのと同じ向きで、既に closed の親へ close 提案を出さない
        if placed[parent][1]:
            continue
        # 宣言を完全には把握できていない親は判定しない。子側から立った辺だけの部分集合で
        # 「子は全て closed」と断定すると、把握できていない宣言に居る active な子を
        # 見落とす。母集団からは外さず判定だけを止める (把握できなさは注記が名乗る)
        if parent in unread or parent in partial:
            continue
        judged.append(parent)
        if all(placed[child][1] for child in children):
            violations.append(
                f"{placed[parent][0]}: 子 Issue が全て closed なのに active に居る "
                f"(子 {len(children)} 件)。親伝播のクローズを同じ PR へ同梱する"
            )
    return violations, links, judged


def collect(root: Path) -> CollectResult:
    """Issue ディレクトリを歩き、不変条件 A (完了漏れ) と不変条件 B (配置と status の
    整合) と不変条件 C (親子リンクの整合) を検査する。

    「走査できなかった」(unscanned_notes) に寄せるのは 2 つ: issue.md が読めない
    (エンコーディング不正など) と、コードフェンスが閉じておらず内容を安全に走査できない
    場合。どちらも違反 (rc 1) にはしない。読めないファイルを Python の既定
    (UnicodeDecodeError で未捕捉のまま伝播) に任せると rc が 1 になり、「読めない」が
    「違反」を名乗ってしまう。閉じ忘れフェンスは issue-id.py --check が既に検出・報告して
    いるので、ここで違反にすると同じ規則の 2 つ目の canonical になる (借用元 issue-id.py
    の _blob_text / run_check も同じ経路を unreadable として数え、違反にはしない)。

    status (frontmatter) が読めない場合は unreadable_notes へ別に寄せる。タスクの完了漏れ
    (本文) と status の読めなさ (frontmatter) は原因も直し方も別なので、同じ
    unscanned_notes へ混ぜない。スペックの要求どおり「読めなかった」を合格へ倒さず件数へ
    出す。

    どれも沈黙させないのは、母集団が縮んだことを出力から見えるようにするため。

    不変条件 B の限界: active 側は status が "closed" と一致するかだけを見るので、
    `clsoed` のような綴り間違いは active に居たまま素通りする (closed/ 側は "closed" 以外を
    全て違反にするので綴り間違いも捕まる)。

    不変条件 C は辺の解決を _resolve_links へ分けてある。ディレクトリの登録 (placed) を
    issue.md を読む前に行うのは、issue.md が無いディレクトリを「実在しない」と誤報しない
    ため。配置はファイルが読めなくてもパスから分かるので、読めなかった Issue も母集団には
    残す。止めるのは判定の側で、その Issue が絡む辺の対称性と、その Issue が親であるときの
    C の判定の 2 つ (子であるときは配置だけで判定できるので止めない)。

    ディレクトリ名から番号が採れない場合は unnumbered_notes へ寄せ、走査できなかった件数へは
    混ぜない。その Issue は不変条件 A と B の走査を通っており、解決できないのは親子リンクだけ
    なので、混ぜると要約行の宣言が実態より広くなる。
    """
    n = notation()
    dirs = n.issue_dirs(root)
    if not dirs:
        raise CheckError("Issue ディレクトリが 1 件も無い。走査対象ゼロは合格ではない")
    violations: list[str] = []
    unscanned_notes: list[str] = []
    missing_notes: list[str] = []
    unreadable_notes: list[str] = []
    unnumbered_notes: list[str] = []
    unreadable_links_notes: list[str] = []
    placed: dict[int, tuple[str, bool]] = {}
    declared: list[_Declared] = []
    unread: set[int] = set()
    active = closed = 0
    for rel_dir, name in dirs:
        # closed/ の名前も借用先から採る。写しを持つと、借用先が rename したときに
        # issue_dirs が返すディレクトリと探す名前がずれ、全 Issue が active 扱いになる。
        # 不変条件 B は全 closed Issue に対して赤く鳴るので気づけるが、不変条件 C の親判定
        # だけは「子が全て closed」が成立しなくなって静かに消える
        is_closed = n.CLOSED in Path(rel_dir).parts
        if is_closed:
            closed += 1
        else:
            active += 1
        number = dir_number(name)
        if number is None:
            # 番号が採れないと辺を張れない。ディレクトリ名の形式そのものは
            # issue-id.py --check が違反として報告するので、ここでは重ねて違反にせず
            # 母集団が縮んだことだけを注記で見えるようにする
            unnumbered_notes.append(
                f"{rel_dir}: ディレクトリ名から番号が採れないので親子リンクを解決できなかった "
                "(名前の形式自体は issue-id.py --check が別途報告する)"
            )
        else:
            placed[number] = prefer_active(placed.get(number), (rel_dir, is_closed))
        path = issue_md_path(root, rel_dir)
        if path is None:
            missing_notes.append(f"{rel_dir}: issue.md が無いので走査できなかった")
            if number is not None:
                unread.add(number)
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as e:
            unscanned_notes.append(f"{rel_dir}: 読めないので走査できなかった ({e})")
            if number is not None:
                unread.add(number)
            continue
        # frontmatter の切り出しは 1 回だけ行い、status と親子リンクの両方で使い回す
        front = frontmatter(text)
        if number is not None:
            # 宣言を完全には把握できていない Issue は辺の対称性の検査から外す。欠けている
            # のか読めていないだけなのかを区別できないので、外さないと「読めなかった」が
            # 「規約違反」を名乗る。frontmatter ごと切り出せない形と、キー行はあるのに値を
            # 読めない形の両方が同じ扱いになる
            unreadable = unreadable_link_keys(front)
            if front is None:
                unread.add(number)
            elif unreadable:
                unread.add(number)
                unreadable_links_notes.append(
                    f"{rel_dir}: {' と '.join(unreadable)} の値を読めなかったので"
                    "親子リンクを解決できなかった"
                )
            parent_value, children_values = read_links(front)
            if parent_value is not None:
                declared.append(_Declared(rel_dir, "parent", parent_value, number))
            for value in children_values:
                declared.append(_Declared(rel_dir, "children", value, number))
        # 不変条件 B: 配置 (closed/ に居るか) と frontmatter の status が食い違わないこと。
        # フェンスの状態に関わらず frontmatter は先頭にあるので、フェンス走査より前に
        # status を見る。closed 側もここで検査するので、後続の "is_closed: continue" より
        # 前に置く
        status = read_status(front)
        if status is None:
            unreadable_notes.append(f"{rel_dir}: frontmatter の status が読めなかった")
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
        if is_completed(has_heading, total, unchecked):
            violations.append(
                f"{rel_dir}: タスクが全て消化済みなのに active に居る "
                f"(箱 {total} 個 / 未チェック 0)。クローズを同じ PR へ同梱する"
            )
    link_violations, links, judged = _resolve_links(declared, placed, unread)
    return CollectResult(
        violations=violations + link_violations,
        unscanned_notes=unscanned_notes,
        missing_notes=missing_notes,
        unreadable_notes=unreadable_notes,
        unnumbered_notes=unnumbered_notes,
        unreadable_links_notes=unreadable_links_notes,
        links=links,
        judged=judged,
        active=active,
        closed=closed,
    )


def build_parser() -> argparse.ArgumentParser:
    # 短縮形が別モードへ静かに落ちるのを防ぐ (既存 3 本と同じ理由)
    # docstring 全文ではなく 1 行目だけを渡す。全文は設計判断と限界の記録で 40 行を超え、
    # argparse の既定フォーマッタが箇条書きを 1 段落へ潰して `**` を生のまま吐く (実測:
    # --help が 32 行)。隣接 3 本はいずれも 1 行を渡している。literal を書かず 1 行目を
    # 採るのは、書くと docstring の 1 行目と二重管理になるため
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0], allow_abbrev=False)
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
    # 検査できなかった注記は違反と別記号にする。同じ [x] で並べると rc に効かない件数まで
    # 「違反」に見え、読み手が rc 0 の理由を誤解する。5 種を同じ [-] で並べるのは、どれも
    # 「この Issue はどこかを検査できていない」という同じ意味を持つため。何が検査できて
    # いないかは注記の本文が名乗る
    for line in (*result.missing_notes, *result.unreadable_notes, *result.unscanned_notes,
                 *result.unnumbered_notes, *result.unreadable_links_notes):
        print(f"  [-] {line}", file=sys.stderr)
    # active / closed が母集団で、括弧の中はその部分集合。「うち」で括るのは、並べて書くと
    # 互いに排他な状態が 7 つあるように読めるため。括弧の中の項目どうしも排他ではない
    # (閉じ忘れフェンスを持つ issue.md は status も読めないことがあり、番号が採れないことは
    # 内容の読めなさと独立に起きる)。issue.md が無い側だけは continue でそこから先へ進まない
    # ので、内容に由来する 2 つとは排他になる
    print(
        f"検査した Issue: active {result.active} 個 / closed {result.closed} 個"
        f" (うち issue.md が無い {len(result.missing_notes)} 個"
        f" / status が読めない {len(result.unreadable_notes)} 個"
        f" / 走査できなかった {len(result.unscanned_notes)} 個"
        f" / 番号が採れない {len(result.unnumbered_notes)} 個"
        f" / 値を読めない {len(result.unreadable_links_notes)} 個)"
        f"。違反 {len(result.violations)} 件"
    )
    # 不変条件 C が見た母集団を別行で名乗る。0 組のときだけ「何も見ていない」と言うのは、
    # このリポジトリには親子 Issue が 1 件も無く C が空虚に緑を返すため。名乗りが無いと
    # その緑を「規約が守られていた証拠」として引用できてしまう。件数は検査と同じ 1 パス
    # から出しているので、印字された 0 と検査された 0 は食い違えない。
    #
    # 「C 全体」ではなく「C の親判定」と名乗るのは、辺が 1 本も立たない実行でも C は違反を
    # 出しうるため (参照が実在しない / 識別子の形でない / 自分自身を指す)。C 全体が何も
    # 見ていないと名乗ると、その違反が同じ出力に並んだまま宣言が実態より広くなる
    parents = len({parent for parent, _ in result.links})
    vacuous = "" if result.judged else " (不変条件 C の親判定はこの実行で何も見ていない)"
    print(f"親子リンク: {len(result.links)} 組 / 親 {parents} 個"
          f" / 判定した親 {len(result.judged)} 個{vacuous}")
    return 1 if result.violations else 0


if __name__ == "__main__":
    sys.exit(main())
