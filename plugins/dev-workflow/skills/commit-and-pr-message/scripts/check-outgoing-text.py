#!/usr/bin/env python3
"""公開される本文を git / gh へ渡す前に、漏洩検査の 2 層を 1 本で通す入口。

引数に渡されたファイル (PR 本文、コミット本文、リリースノート、1 行のタイトルを書いた
ファイルなど) を、層 1 (同じディレクトリにある gitleaks の config 2 本。形の決まった漏洩) と
層 2 (同じディレクトリの check-leak-guard-denylist.py。禁止語リスト) の両方へ当て、結果を
1 つの終了コードへ畳む。標準ライブラリだけで書き、Python 3.9 で動く形に保つ (annotations の
future import を持ち、tomllib を使わない)。

    check-outgoing-text.py [--target-private] <file> [<file> ...]

argparse は使わない。usage を stderr へ出し、--help で rc 0 を返すので、「stderr に何も
出さない」「引数が無いときは止まる」と両立しない。先頭が --target-private ならそれを
取り除き、層 2 を not-applicable にして起動しない (層 1 は送り先を問わず当てる)。残りは
全てファイルとして扱う。付けるかどうかは呼ぶ側の判定で、入口は送り先を検証しない。
PRIVATE と誤って判定すると層 2 が外れる。

## 状態と理由の語彙

層ごとに状態を 1 つ持つ。

    checked         その層の検査が全ての入力で完走した (検出の有無は別に数える)
    skipped         その層を走らせる前提が無い
    unable          走らせたが、完了したとは言えない
    not-applicable  --target-private のときの層 2 だけが取る

checked 以外には reason= を添える。

    no-input             引数にファイルが 1 つも無い (両層)
    input-unreadable     通常ファイルでない (symlink は辿った先で見る) か、読めない (両層)
    input-empty          0 byte (両層)
    input-undecodable    UTF-8 として読めない、または NUL を含む (両層)
    input-too-large      canary を足した payload が MAX_PAYLOAD_BYTES を超える (両層。理由は層 1 の節)
    gitleaks-not-found   渡された env の PATH に gitleaks が無い (層 1 の skipped)
    gitleaks-failed      gitleaks を起動できない、または rc が 0 でない (層 1)
    report-unreadable    gitleaks のレポートを読めない、行番号が流した範囲の外 (層 1)
    canary-not-detected  canary の検出が揃わない (層 1)
    env-unset            層 2 が status=skipped を出した (層 2 の skipped)
    denylist-unable      層 2 の出力を判定できない (層 2)
    target-private       --target-private (層 2 の not-applicable)

入力の前段 (no-input と input-*) は両層より前に確かめ、1 つでも当たれば not-applicable で
ない層を全部その理由の unable にし、gitleaks も層 2 も起動しない。0 byte を通すと、
書き忘れたファイルが「検査済みの 0 件」になる。層の状態は入力間で最も悪いもの
(unable > skipped > checked) を採り、理由は最初にその状態になった入力のもの。

## 終了コードと result

上から順に、最初に当たる行で決める (decide)。

    1 finding   どれかの層に検出がある
    2 unable    どれかの層が unable
    3 skipped   どれかの層が skipped
    0 ok        両層とも checked (層 2 は not-applicable でもよい) で検出 0 件

検出を状態より優先するのは、完走しなかった層があっても、既に見つかった検出を先に直せる
ようにするため。どちらも渡す前に止まる向きで、順序で fail-open にはならない。

## 出力

stdout だけに出し、stderr には何も出さない。

    layer1 status=<state> [reason=<reason>] findings=<n>
    layer2 status=<state> [reason=<reason>] findings=<n>
      [x] file <n> line <m>: layer1 <rule-id>
      [x] file <n> line <m>: layer2 denylist line <k>
    result=<ok|finding|unable|skipped>
    <要約 1 行>

file は引数の順番 (1 始まり)、line は入力の行番号 (\\n だけを境界に数える)。要約は結果
だけを述べ、結果を受けてどうするかは呼ぶ側の手順が持つ。想定外の例外のときは error=<型名>、
result=unable、要約の 3 行だけを出す。出力は最後まで貯めてから出すので、途中まで出た状態行が
残らない。未捕捉の例外は Python の既定で rc 1 になって「検出あり」に化け、traceback にパスが
載るので、最上位で必ず受ける。書き出しもその内側に置き、書き出せなかったときは何も出さずに
rc 2 を返す。stdout の符号化で表せない文字は \\x.. や \\u.... の形にして書く (理由は emit)。

出さないもの: 禁止語、入力のパス、一致した文字列、gitleaks と層 2 の生の出力。出力の
宛先は端末だけでなく、会話ログや Issue への貼り付けにも及ぶ。

## 層 1 (gitleaks)

渡された env の PATH だけで gitleaks を探し (shutil.which)、見つかったパスを絶対パスにして
起動する。名前で引き直すと、解決と起動が別の実体を指しうる (PATH に version manager の shim と
実体の両方がある環境で実測)。PATH に相対の要素があると which は相対パスを返し、入口は cwd を
一時ディレクトリへ移して起動するので、相対のままでは起動できない (gitleaks-failed になった。
実測)。絶対パスへは which が見たのと同じ cwd を基準に直す。env に PATH が無ければ見つからない
扱いにする。which に None を渡すと実行元の PATH へ戻るので、空文字列を渡す。

入力ごと・config ごとに 1 回、gitleaks stdin で流す。流すバイト列は実行ごとの一時ディレクトリ
に書いた普通のファイルにし、それを開いて stdin に渡す。パスを引数で渡さず stdin にするのは、
ファイル名・拡張子・symlink・一時領域のパスがどれも gitleaks に渡らないため (開いたファイルを
fd 0 として渡しても名前は渡らないので、普通のファイルでも成り立つ)。それぞれに要る対策
(固定名での写し、未知のファイル名の照合、パスによる既定の除外への備え) が消える。
stdin のレポートは File が空で、StartLine は流したバイト列の行番号になる (gitleaks 8.30.1
で実測)。

流すのは、前の canary、入力のバイト列、(改行で終わらなければ改行)、後ろの canary。canary は
ルール ID の昇順に 1 行ずつ並べ、custom の config にはルールごとに 1 行、既定の config には
既定ルール 1 本 (github-pat) に当たる行を置く。値は実行時に組み立てる (このファイル自身が
層 1 に捕まらないため)。前と後ろの canary が両方とも期待どおり検出されて初めて checked に
する。これで見えるのは、gitleaks が先頭と末尾を読み、行番号が揃っていること、ルールが有効な
こと。揃わないとき (canary-not-detected) に考えられる原因:

- 中身による読み飛ばし。gitleaks は先頭バイトで「バイナリ」と判定した入力を、rc 0 と空の
  レポートのまま読み飛ばす (PDF・MZ・RTF の magic、128 バイト目の DICM、UTF-16 で実測。
  網羅ではない)。前に canary を置くと先頭の magic による読み飛ばしは消えるが、足したあとも
  条件が成り立つ形では canary ごと消える
- ルールの欠落。custom の config からルールが消えた、regex が壊れた、既定の config から
  useDefault が落ちた。custom の config に useDefault が戻った場合も、既定 config の全体除外
  (?i)^true|false|null$ が custom ルールの検出にも効くので (実測)、false を含む canary
  (user-path と email-address) が消える
- gitleaks の版の違い。読み方や行番号の振り方が変わる版では止まり続ける。検証した版は
  8.30.1

途中の連続性は canary では見えない。gitleaks 8.30.1 は入力を区切り (fragment) ごとに独立に
走査する。stdin は bufio.Reader 越しに defaultBufferSize (100 * 1_000 バイト) のバッファへ
read し (sources/file.go の fileFragments。Go の bufio は内部のバッファが空で要求が大きいとき、
下の reader から直接 1 回 read する)、続けて readUntilSafeBoundary (sources/common.go) が空行
(改行 2 つの連続) を maxPeekSize (25 * 1_000 バイト) 先まで探して区切りを延ばす。見つからなければ
行の途中で切り、切れ目が一致の途中に来るとその検出は消える。このとき前後の canary は揃い行番号も
合うので、checked のまま件数だけが減る。pipe の read は書き手の進み具合で返る量が揺れるので、
defaultBufferSize 未満でも区切られる (空行の無い約 97KB の入力を pipe で 30 回流すと、2 度の
計測で 8 回と 10 回、検出が 1 件落ちた。普通のファイルでは 30 回とも落ちなかった)。普通のファイル
の read は要求した量を一度に返すので、1 回の read と maxPeekSize の延長に収まる間は区切られない
(1 行 13 バイトで空行の無い入力を普通のファイルで流すと、約 124KB までは 0 件、約 126KB で 1 件、
約 1.7MB で 30 件落ちた。約 1.7MB でも 20 行ごとに空行を入れると 0 件)。そこで stdin を普通の
ファイルにし、2 本の config のうち大きい方の payload が 1 回の read に収まる MAX_PAYLOAD_BYTES
以下の入力だけを流す。超える入力は前段で input-too-large にする。

canary の行の余分な検出は捨てる (上流が既定ルールを足しただけでは止まらないように)。
どの canary の行も gitleaks:allow の印を持つ。--ignore-gitleaks-allow が呼び出しから落ちると
印を持つ行の検出が消えるので、canary が消えて露見する (実測。印は本文を書く本人が書ける)。

フラグ (gitleaks 8.30.1 で実測):

    -c <config>              config を明示する。明示すると cwd の .gitleaks.toml は効かない
    --ignore-gitleaks-allow  上記
    --report-format json     JSON で出す
    --report-path -          レポートを stdout へ。検出 0 件は []
    --redact                 レポートの Match と Secret を伏せる。入口は行番号とルール ID しか
                             読まないが、レポートが一致した文字列を持たない形にしておく
    --no-banner / --no-color 判定に使わない出力を減らす
    --exit-code 0            検出があっても rc 0 にし、rc が 0 でないことを「走らせられ
                             なかった」に限る。config を読めないとき gitleaks は rc 1 を返し、
                             stderr に config の絶対パスをそのまま出す

cwd は実行ごとの一時ディレクトリの下に作る空のディレクトリにし、payload のファイルもその外に
置く (gitleaks が cwd から何を読むかを数え上げずに済むように、cwd には何も置かない)。stdin
モードは -i に何を渡しても cwd の .gitleaksignore を読み (実測)、その fingerprint は
:<ルール>:<行> の形なので、呼び出し元の免除ファイルが入力の検出や canary を消しうる。stdout と
stderr は捕捉し、どちらも流さない。成功した run の stderr は走査量と件数の 2 行だけだが、失敗
した run の stderr は上記のとおりパスを持つ。

## 層 2 (禁止語リスト)

入力ごとに [sys.executable, check-leak-guard-denylist.py, --check-text=<path>] を、受けた
env をそのまま渡して起動する。値を = でつないだ 1 引数にするのは、- で始まる相対パスを 2 引数で
渡すと層 2 の argparse がオプションとして読んで落ちるため (denylist-unable になった。実測)。
判定は stdout の ASCII の目印 (status=skipped / status=checked) と rc と、stderr の座標
(line N: denylist line M) だけで行う。stdout と stderr は UTF-8 として読めないバイトを置換して
読む (ja-JP の Windows は非対話出力を CP932 で書く)。

    status=skipped                          skipped (env-unset)
    status=checked、rc 0、座標なし          checked
    status=checked、rc 1、座標 1 件以上     checked、検出あり
    それ以外                                unable (denylist-unable)

rc だけで判定すると、起動に失敗した Python の rc 1 が「検出あり」に、出力を出さずに rc 0 で
終わる壊れ方が「検査済み」に化ける。目印の値は層 2 の STATUS_SKIPPED / STATUS_CHECKED と
同じで、canonical は層 2 の側。ここで層 2 を import しないのは、隣が無いときに import 時の
traceback (パス入り・rc 1) で落ちる経路を作らないため。

## 既知の限界

- 層 2 が見るのはリストに載った語だけで、リストに無い語と、組み合わせで対象を特定する
  書き方は通る。層 1 が見る形は 2 本の config が持つ。渡す前に自分で読むこと
- 起動元に環境変数が届かない環境では、層 2 が skipped (env-unset) になる
- canary が揃うことは gitleaks の版に依存する
- canary を足した payload が MAX_PAYLOAD_BYTES を超える入力は input-too-large で止まる。検査
  するには空行 (改行だけの行) の位置で分けて渡し、行の途中や、鍵のブロックのような複数行に
  またがる値の途中では分けない。切れ目をまたぐ一致はどちらの層からも消える (層 2 は行の中、
  層 1 は渡した 1 ファイルの中でしか照合しない。鍵のブロックを行の境目で分けても層 1 の検出が
  消えた。実測)。空行は gitleaks 自身も安全な区切りとして扱う (層 1 の節)
- --target-private の判定は呼ぶ側が行い、入口は検証しない
- 入口は実行時に config を読まない。ルールの集合と canary の対応は、配布元の対照検査が見る
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Mapping, NamedTuple

EXIT_OK, EXIT_FINDING, EXIT_UNABLE, EXIT_SKIPPED = 0, 1, 2, 3
RESULT_BY_EXIT = {
    EXIT_OK: "ok",
    EXIT_FINDING: "finding",
    EXIT_UNABLE: "unable",
    EXIT_SKIPPED: "skipped",
}
STATE_CHECKED, STATE_SKIPPED, STATE_UNABLE = "checked", "skipped", "unable"
STATE_NOT_APPLICABLE = "not-applicable"
TARGET_PRIVATE = "--target-private"

HERE = Path(__file__).resolve().parent
DENYLIST_SCRIPT = HERE / "check-leak-guard-denylist.py"
CUSTOM_RULES = HERE / "leak-guard.gitleaks.toml"
DEFAULT_RULES = HERE / "leak-guard-default.gitleaks.toml"

REASON_NO_INPUT = "no-input"
REASON_INPUT_UNREADABLE = "input-unreadable"
REASON_INPUT_EMPTY = "input-empty"
REASON_INPUT_UNDECODABLE = "input-undecodable"
REASON_INPUT_TOO_LARGE = "input-too-large"
REASON_GITLEAKS_NOT_FOUND = "gitleaks-not-found"
REASON_GITLEAKS_FAILED = "gitleaks-failed"
REASON_REPORT_UNREADABLE = "report-unreadable"
REASON_CANARY_NOT_DETECTED = "canary-not-detected"
REASON_ENV_UNSET = "env-unset"
REASON_DENYLIST_UNABLE = "denylist-unable"
REASON_TARGET_PRIVATE = "target-private"

# 層 2 の目印。値の canonical は層 2 の STATUS_SKIPPED / STATUS_CHECKED (import しない理由は
# docstring)
DENYLIST_STATUS_SKIPPED = "status=skipped"
DENYLIST_STATUS_CHECKED = "status=checked"
_DENYLIST_HIT = re.compile(r"^\s*\[x\] line (\d+): denylist line (\d+)\s*$", re.MULTILINE)

ALLOW_MARK = "gitleaks:allow"

# 前後の canary を足した payload の上限。gitleaks 8.30.1 が stdin を 1 回で read する大きさ
# (sources/file.go の defaultBufferSize) に合わせる。理由は docstring の層 1
MAX_PAYLOAD_BYTES = 100 * 1_000

# 要約は結果だけを述べる固定の文にする。件数は状態行が持ち、要約に語や座標を混ぜない。
# not-applicable の層 2 も完了に数えるので、OK の文は「両層」と言わない
SUMMARY_BY_EXIT = {
    EXIT_OK: "検査を完了し、検出は 0 件",
    EXIT_FINDING: "検出がある",
    EXIT_UNABLE: "検査を完了できなかった",
    EXIT_SKIPPED: "未検査の層がある",
}
SUMMARY_UNEXPECTED = "想定外の失敗で検査を完了できなかった"

# 状態の悪い順。入力間で最も悪いものを層の状態にする
_RANK = {STATE_CHECKED: 0, STATE_SKIPPED: 1, STATE_UNABLE: 2}


class Hit(NamedTuple):
    file_no: int  # 引数の順番 (1 始まり)
    line: int  # 入力の行番号 (前の canary の行を除いた番号)
    layer: int  # 1 or 2
    label: str  # 層 1 はルール ID、層 2 は "denylist line <n>"


class Layer(NamedTuple):
    state: str
    reason: str  # checked のときは ""
    hits: list[Hit]


class ReportError(ValueError):
    """gitleaks のレポートを読めない、または行番号が流した範囲の外にある。"""


class CanaryMissing(ValueError):
    """前か後ろの canary の検出が揃わない。"""


class InputError(ValueError):
    """入力の前段で止まった。reason に理由の語を持つ。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Layer1Error(RuntimeError):
    """層 1 を完走できなかった。reason に理由の語を持つ。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# --- canary ---------------------------------------------------------------------


def custom_canary() -> dict[str, str]:
    """custom ルール ID -> canary の行。

    値は連結で組み立てる。literal で書くと、このファイル自身が層 1 に捕まる。user-path と
    email-address の値が false を含むのは、custom の config に既定 config の全体除外が戻った
    ときに消えて露見させるため (理由は docstring)。UUID は 16 進なので true / false / null を
    含みようがない。
    """
    mark = " " + ALLOW_MARK
    return {
        "user-path": "/Users/" + "false-canary" + mark,
        "vm-uuid": "-".join(("c0ffee11", "2222", "4333", "8444", "555566667777")) + mark,
        "email-address": "false-canary" + "@" + "leak-canary.internal" + mark,
    }


def default_canary() -> dict[str, str]:
    """既定ルール ID -> canary の行。

    github-pat は ghp_ + 英数字 36 文字 (gitleaks 8.30.1 で実測)。値に true / false / null を
    含めない。既定 config の全体除外に掛かると、既定ルールが生きていても canary が消える。
    """
    return {"github-pat": "ghp" + "_" + "c4n4ryC4N4RY" * 3 + " " + ALLOW_MARK}


# --- 純粋関数 ---------------------------------------------------------------------


def decide(layer1: Layer, layer2: Layer) -> int:
    """終了コード。優先順は docstring の表。not-applicable は checked と同じに扱う。"""
    if layer1.hits or layer2.hits:
        return EXIT_FINDING
    states = {layer1.state, layer2.state}
    if STATE_UNABLE in states:
        return EXIT_UNABLE
    if STATE_SKIPPED in states:
        return EXIT_SKIPPED
    if states <= {STATE_CHECKED, STATE_NOT_APPLICABLE}:
        return EXIT_OK
    # 知らない状態を ok に落とさない。最上位で受けて unable になる
    raise ValueError("unknown layer state")


def parse_report(stdout: bytes) -> list[tuple[int, str]]:
    """gitleaks の JSON レポートから (StartLine, RuleID) を取る。読めなければ ReportError。

    空の stdout も ReportError にする。検出 0 件のレポートは [] であって空ではない (実測)
    ので、空はレポートが作られなかった形。
    """
    try:
        data = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ReportError("レポートを JSON として読めない") from None
    if not isinstance(data, list):
        raise ReportError("レポートが配列でない")
    found = []
    for finding in data:
        if not isinstance(finding, dict):
            raise ReportError("レポートの要素がオブジェクトでない")
        line, rule = finding.get("StartLine"), finding.get("RuleID")
        if not isinstance(line, int) or isinstance(line, bool) or not isinstance(rule, str):
            raise ReportError("StartLine か RuleID が無い")
        found.append((line, rule))
    return found


def locate(
    report: list[tuple[int, str]], rules: list[str], input_lines: int
) -> list[tuple[int, str]]:
    """canary を照合し、間の検出を入力の行番号へ戻す。

    rules は canary の行の並び (K 本)。前の canary の i 行目と後ろの canary の i 行目
    (K + input_lines + i) に rules[i] の検出が揃わなければ CanaryMissing。1..2K+input_lines の
    外の行があれば ReportError。canary の行の余分な検出は捨てる。
    """
    k = len(rules)
    total = 2 * k + input_lines
    expected = {(i + 1, rule) for i, rule in enumerate(rules)}
    expected |= {(k + input_lines + i + 1, rule) for i, rule in enumerate(rules)}
    on_canary = set()
    between = []
    for line, rule in report:
        if not 1 <= line <= total:
            raise ReportError("行番号が流した範囲の外にある")
        if line <= k or line > k + input_lines:
            on_canary.add((line, rule))
        else:
            between.append((line - k, rule))
    if not expected <= on_canary:
        raise CanaryMissing("canary の検出が揃わない")
    return sorted(between)


def build_payload(canary: dict[str, str], data: bytes) -> bytes:
    """gitleaks へ流すバイト列。前の canary、入力、(改行で終わらなければ改行)、後ろの canary。

    走査と上限の判定の両方がこれを使う。大きさを別の式で数えると、組み立てと判定がずれうる。
    """
    block = "".join(canary[rule] + "\n" for rule in sorted(canary)).encode("utf-8")
    return block + data + (b"" if data.endswith(b"\n") else b"\n") + block


def judge_denylist(rc: int, stdout: str, stderr: str) -> tuple[str, str, list[tuple[int, int]]]:
    """層 2 の出力を (state, reason, 座標) に読む。判定表は docstring。"""
    lines = stdout.splitlines()
    if any(line.startswith(DENYLIST_STATUS_SKIPPED) for line in lines):
        return STATE_SKIPPED, REASON_ENV_UNSET, []
    if not any(line.startswith(DENYLIST_STATUS_CHECKED) for line in lines):
        return STATE_UNABLE, REASON_DENYLIST_UNABLE, []
    coords = [(int(line), int(entry)) for line, entry in _DENYLIST_HIT.findall(stderr)]
    if rc == 0 and not coords:
        return STATE_CHECKED, "", []
    if rc == 1 and coords:
        return STATE_CHECKED, "", coords
    # rc と座標の有無が食い違う形 (rc 1 で座標なし、rc 0 で座標あり) は判定しない
    return STATE_UNABLE, REASON_DENYLIST_UNABLE, []


def combine(results: list[Layer]) -> Layer:
    """入力ごとの結果を 1 つに畳む。状態は最悪、理由は最初にその状態になった入力のもの。"""
    state = max((r.state for r in results), key=_RANK.__getitem__)
    reason = next(r.reason for r in results if r.state == state)
    return Layer(state, reason, [hit for r in results for hit in r.hits])


# --- 入力の前段 -------------------------------------------------------------------


def read_input(path: str, canaries: list[dict[str, str]]) -> bytes:
    """入力を読む。前段のどれかに当たれば、その理由の InputError。

    当たる形は、通常ファイルでない・読めない・0 byte・UTF-8 でない・NUL を含む・canary を
    足した payload が上限を超える (canaries のうち payload が大きくなる方で見る)。
    """
    target = Path(path)
    try:
        # stat は symlink を辿る。辿った先が通常ファイルでなければ読まない
        st = target.stat()
        if not stat.S_ISREG(st.st_mode):
            raise InputError(REASON_INPUT_UNREADABLE)
        # 大きさだけで上限を超えるものは読む前に止める。取り違えて渡した大きなファイルを全部
        # 読むと、UTF-8 として読める中身 (ログなど) では下の判定に届くまでに大きさの 3 倍を
        # メモリに載せる (20MB で tracemalloc の peak を実測。NUL を含む中身は 1 倍)。payload
        # は入力より必ず長いので、ここで止めるものは下の判定でも止まる。正確な判定は下の
        # build_payload による判定が持つ
        if st.st_size > MAX_PAYLOAD_BYTES:
            raise InputError(REASON_INPUT_TOO_LARGE)
        data = target.read_bytes()
    except OSError:
        raise InputError(REASON_INPUT_UNREADABLE) from None
    if not data:
        raise InputError(REASON_INPUT_EMPTY)
    if b"\0" in data:
        raise InputError(REASON_INPUT_UNDECODABLE)
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        raise InputError(REASON_INPUT_UNDECODABLE) from None
    if max(len(build_payload(canary, data)) for canary in canaries) > MAX_PAYLOAD_BYTES:
        raise InputError(REASON_INPUT_TOO_LARGE)
    return data


# --- 層 1 ---------------------------------------------------------------------------


def gitleaks_argv(gitleaks: str, config: Path) -> list[str]:
    """フラグを渡す理由は docstring。"""
    return [
        gitleaks,
        "stdin",
        "-c",
        str(config),
        "--ignore-gitleaks-allow",
        "--report-format",
        "json",
        "--report-path",
        "-",
        "--redact",
        "--no-banner",
        "--no-color",
        "--exit-code",
        "0",
    ]


def resolve_gitleaks(env: Mapping[str, str]) -> str | None:
    """渡された env の PATH だけで gitleaks を探し、絶対パスで返す。無ければ None。理由は docstring。"""
    found = shutil.which("gitleaks", path=env.get("PATH", ""))
    return None if found is None else os.path.abspath(found)


def scan_stdin(
    gitleaks: str, config: Path, canary: dict[str, str], data: bytes, env: Mapping[str, str]
) -> list[tuple[int, str]]:
    """入力 1 本を config 1 本で走査し、(入力の行番号, ルール ID) を返す。完走できなければ Layer1Error。"""
    rules = sorted(canary)
    # stdin は普通のファイルにし、cwd は何も置かない空のディレクトリにする (理由は docstring の
    # 層 1。pipe だと区切りが行の途中に来うる、cwd の .gitleaksignore が効く)。一時ディレクトリは
    # mkdtemp が作成者だけに開く権限で作るので、写した本文は他のユーザーから読めない
    with tempfile.TemporaryDirectory() as box:
        cwd = os.path.join(box, "cwd")
        os.mkdir(cwd)
        source = os.path.join(box, "payload")
        with open(source, "wb") as sink:
            sink.write(build_payload(canary, data))
        with open(source, "rb") as stdin:
            try:
                proc = subprocess.run(
                    gitleaks_argv(gitleaks, config),
                    stdin=stdin,
                    capture_output=True,
                    cwd=cwd,
                    env=env,
                    check=False,
                )
            except OSError:
                raise Layer1Error(REASON_GITLEAKS_FAILED) from None
    if proc.returncode != 0:
        raise Layer1Error(REASON_GITLEAKS_FAILED)
    try:
        report = parse_report(proc.stdout)
    except ReportError:
        raise Layer1Error(REASON_REPORT_UNREADABLE) from None
    input_lines = data.count(b"\n") + (0 if data.endswith(b"\n") else 1)
    try:
        return locate(report, rules, input_lines)
    except CanaryMissing:
        raise Layer1Error(REASON_CANARY_NOT_DETECTED) from None
    except ReportError:
        raise Layer1Error(REASON_REPORT_UNREADABLE) from None


def run_layer1(
    inputs: list[bytes], env: Mapping[str, str], configs: list[tuple[Path, dict[str, str]]]
) -> Layer:
    """configs は (config, その canary) の並び。"""
    gitleaks = resolve_gitleaks(env)
    if gitleaks is None:
        return Layer(STATE_SKIPPED, REASON_GITLEAKS_NOT_FOUND, [])
    results = []
    for file_no, data in enumerate(inputs, 1):
        hits: list[Hit] = []
        try:
            for config, canary in configs:
                hits.extend(
                    Hit(file_no, line, 1, rule)
                    for line, rule in scan_stdin(gitleaks, config, canary, data, env)
                )
        except Layer1Error as e:
            results.append(Layer(STATE_UNABLE, e.reason, hits))
            continue
        results.append(Layer(STATE_CHECKED, "", hits))
    return combine(results)


# --- 層 2 ---------------------------------------------------------------------------


def check_denylist(file_no: int, path: str, env: Mapping[str, str], script: Path) -> Layer:
    try:
        proc = subprocess.run(
            # 値を = でつなぐ理由は docstring の層 2 (- で始まる相対パス)
            [sys.executable, str(script), f"--check-text={path}"],
            capture_output=True,
            env=env,
            check=False,
        )
    except OSError:
        return Layer(STATE_UNABLE, REASON_DENYLIST_UNABLE, [])
    state, reason, coords = judge_denylist(
        proc.returncode,
        proc.stdout.decode("utf-8", "replace"),
        proc.stderr.decode("utf-8", "replace"),
    )
    return Layer(state, reason, [Hit(file_no, line, 2, f"denylist line {entry}") for line, entry in coords])


def run_layer2(paths: list[str], env: Mapping[str, str], script: Path) -> Layer:
    return combine([check_denylist(no, path, env, script) for no, path in enumerate(paths, 1)])


# --- 入口 ---------------------------------------------------------------------------


def check(
    args: list[str],
    env: Mapping[str, str],
    custom_rules: Path,
    default_rules: Path,
    denylist_script: Path,
) -> tuple[Layer, Layer]:
    private = bool(args) and args[0] == TARGET_PRIVATE
    paths = args[1:] if private else args
    layer2_off = Layer(STATE_NOT_APPLICABLE, REASON_TARGET_PRIVATE, [])
    configs = [(custom_rules, custom_canary()), (default_rules, default_canary())]
    try:
        if not paths:
            raise InputError(REASON_NO_INPUT)
        inputs = [read_input(path, [canary for _, canary in configs]) for path in paths]
    except InputError as e:
        halted = Layer(STATE_UNABLE, e.reason, [])
        return halted, (layer2_off if private else halted)
    layer1 = run_layer1(inputs, env, configs)
    layer2 = layer2_off if private else run_layer2(paths, env, denylist_script)
    return layer1, layer2


def render(layer1: Layer, layer2: Layer, rc: int) -> list[str]:
    lines = []
    for name, layer in (("layer1", layer1), ("layer2", layer2)):
        reason = f" reason={layer.reason}" if layer.reason else ""
        lines.append(f"{name} status={layer.state}{reason} findings={len(layer.hits)}")
    for hit in sorted(layer1.hits + layer2.hits):
        lines.append(f"  [x] file {hit.file_no} line {hit.line}: layer{hit.layer} {hit.label}")
    lines.append(f"result={RESULT_BY_EXIT[rc]}")
    lines.append(SUMMARY_BY_EXIT[rc])
    return lines


def emit(lines: list[str]) -> None:
    """出力を stdout へ書く。

    stdout の符号化で表せない文字は backslashreplace で \\x.. や \\u.... の形にする。stdout の符号化が
    日本語を書けない (PYTHONIOENCODING=ascii、cp1252 のコンソール) と、既定の strict では要約の
    行で UnicodeEncodeError になる (実測)。UTF-8 のバイト列を直接書く案と比べ、cp932 の Windows
    では日本語がそのまま出て、表せない符号化でも ASCII の目印は壊れない。テストが StringIO へ
    差し替えた stdout には reconfigure が無いので呼ばない。
    """
    stream = sys.stdout
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(errors="backslashreplace")
    stream.write("\n".join(lines) + "\n")
    stream.flush()


def discard_stdout() -> None:
    """書き出しに失敗したあと、fd 1 を /dev/null へ付け替えて残りを捨てさせる。

    Python は終了時に stdout を flush し直す。読み手が閉じた pipe では、そこでもう一度
    BrokenPipeError になって stderr へ「Exception ignored」を出し、rc が 120 になる (実測)。
    付け替えは Python の signal モジュールの文書が SIGPIPE の注意書きで示す形。fd 1 を閉じて
    起動したときは sys.stdout が None で、終了時に flush するものが無い。ここで失敗しても
    呼び出し元の rc を変えないよう、例外は外へ出さない。
    """
    stream = sys.stdout
    if stream is None:
        return
    try:
        os.dup2(os.open(os.devnull, os.O_WRONLY), stream.fileno())
    except Exception:  # noqa: BLE001
        pass


def main(
    argv: list[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    custom_rules: Path = CUSTOM_RULES,
    default_rules: Path = DEFAULT_RULES,
    denylist_script: Path = DENYLIST_SCRIPT,
) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    environ = os.environ if env is None else env
    try:
        layer1, layer2 = check(args, environ, custom_rules, default_rules, denylist_script)
        rc = decide(layer1, layer2)
        lines = render(layer1, layer2, rc)
    except Exception as e:  # noqa: BLE001
        # 型名だけを出す。例外の str と traceback はパスを載せることがある (docstring)
        rc = EXIT_UNABLE
        lines = [f"error={type(e).__name__}", f"result={RESULT_BY_EXIT[rc]}", SUMMARY_UNEXPECTED]
    try:
        emit(lines)
    except Exception:  # noqa: BLE001
        # 書き出しに失敗した (fd 1 を閉じて起動すると sys.stdout は None、読み手が閉じた pipe
        # では BrokenPipeError)。出力を読めない呼び出し元へ ok を返さない。stderr へも出さない
        # (出力は stdout だけ、という約束)
        discard_stdout()
        return EXIT_UNABLE
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
