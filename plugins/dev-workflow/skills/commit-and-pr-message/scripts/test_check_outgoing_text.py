#!/usr/bin/env python3
"""check-outgoing-text.py の仕様。

公開される本文を git / gh へ渡す前に通す入口が、層 1 (形の決まったルール、gitleaks) と
層 2 (禁止語リスト) の両方を当て、検出できないまま ok を返す経路 (fail-open) を持たない
ことを pin する。判定表は実物の gitleaks と、テスト内で作る架空語の禁止語リストで見る。

形は 3 つ。P はプロセス境界 (sys.executable で起動し、stdout と stderr の両方を捕捉)、
M は main() の呼び出し (stdout と stderr を捕捉)、U は純粋関数。例外は P の
test_stdout_unwritable で、stdout を書けない状態で起動するので stdout を捕捉せず、rc と
stderr が空であることだけを見る。それ以外の P と M では、状態行を出す全ケースで rc に加えて
両層の status= と reason= を見て、全ケースで出力の非表示を Case.parse が見る: stderr が空で
あること、出力の全行が決めた形のどれかに一致すること (文法は parse_output)、架空語・合成値・
Traceback と、パスのうちこのテスト自身の一時ディレクトリ (入力・stub・写しの config を置く) と
入口の隣 (実物の config を置く) を含まないこと。入口が内部で作る一時ディレクトリのパスは、
この検査の射程に無い。

合成したユーザーパス・UUID・トークンは、このファイル自身が層 1 に捕まらないよう変数と
連結で組み立てる。禁止語はすべて架空語で、リストは一時ファイル。env は os.environ から
禁止語リストの変数と GIT_* を除いた dict を基点にし、必要な値だけを明示する。gitleaks が
無い状態は PATH を空の一時ディレクトリ 1 つにして作る (実行元の PATH には version manager
の shim と実体の両方がありうる)。

写しの config は元の本文への文字列置換で作り、置換が 1 回起きたことを assert する。起きて
いないとテストが対象を壊していない dead pin になる。

SkillTable は、隣の SKILL.md の「送る前の検査」節にある (終了コード, result) ごとの行動の表と、
入口の定数の一致を見る。行動の canonical は SKILL.md、値の canonical は入口で、片方だけが
動くと手順は存在しない組を読むか、ある組の行動を持たなくなる。表がどの組にも当たらない
結果を受ける「上記以外」の行を 1 本持つことも見る。
"""

from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import itertools
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# 入口・層 2・config はこのテストの隣にある。配布先でも同じ位置関係で読めるよう、
# リポジトリの root からではなくファイルの隣を指す
HERE = Path(__file__).resolve().parent
ENTRY = HERE / "check-outgoing-text.py"
DENYLIST = HERE / "check-leak-guard-denylist.py"
CUSTOM_RULES = HERE / "leak-guard.gitleaks.toml"
DEFAULT_RULES = HERE / "leak-guard-default.gitleaks.toml"
SKILL_MD = HERE.parent / "SKILL.md"

# SKILL.md の表を引く節の見出し。見出しを変えるときはここも変える
CHECK_SECTION = "### 送る前の検査"
EXIT_TABLE_ROW = re.compile(r"^\| (\d+) / ([a-z]+) \|")
FALLBACK_ROW = "| 上記以外"
FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#+) ")


def load(name: str, path: Path):
    """ハイフン名のスクリプトは import 文では読めないため importlib で読む。"""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


entry = load("check_outgoing_text", ENTRY)
denylist_checker = load("check_leak_guard_denylist", DENYLIST)
ENV_VAR = denylist_checker.ENV_VAR

# 架空語。どれも実在の固有名詞ではない。リストの 1 行目と 2 行目に置く
WORD = "zorblatt"
WORD2 = "quuxcorp"

# 合成値は連結で組み立てる。literal で書くと、このファイル自身が層 1 に捕まる。
# `/Users/{NAME}` はソース上 `{` が続くのでルールの文字クラスに掛からない
NAME = "alice"
USER_PATH = f"/Users/{NAME}/dev/project"
UUID = "-".join(("1234abcd", "5678", "4901", "8abc", "def012345678"))
# 既定ルール github-pat に当たる形 (ghp_ + 英数字 36 文字)。true / false / null を含めない
# (既定 config の全体除外に掛かる)。名前に key / token / secret を入れない (既定ルールの
# generic-api-key がこのファイルを検出する)
PAT = "ghp" + "_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
MARK = "gitleaks:allow"

CHECKED = (entry.STATE_CHECKED, "")

# 出力の文法。summary の行は入口の定数そのものと比べる
STATUS_LINE = re.compile(
    r"^(layer[12]) status=(checked|skipped|unable|not-applicable)"
    r"(?: reason=([a-z-]+))? findings=(\d+)$"
)
HIT_LINE = re.compile(r"^  \[x\] file (\d+) line (\d+): layer([12]) (.+)$")
HIT_LABEL = {"1": re.compile(r"^[A-Za-z0-9._-]+$"), "2": re.compile(r"^denylist line \d+$")}
RESULT_LINE = re.compile(r"^result=(ok|finding|unable|skipped)$")
ERROR_LINE = re.compile(r"^error=[A-Za-z_][A-Za-z0-9_]*$")
EXIT_BY_RESULT = {result: code for code, result in entry.RESULT_BY_EXIT.items()}


def base_env() -> dict[str, str]:
    """禁止語リストの変数と GIT_* を落とした env。全ケースがここから作る。

    subagent やエージェントのシェルは起動元の変数を継承するので、落とさないと実物の
    禁止語リストで層 2 が走る。GIT_* は git を呼ぶテストの先例に合わせて落とす。
    """
    return {k: v for k, v in os.environ.items() if k != ENV_VAR and not k.startswith("GIT_")}


def ascii_escaped(text: str) -> str:
    """stdout が ASCII しか書けないときに入口が書く形。入口は backslashreplace で書く。"""
    return text.encode("ascii", "backslashreplace").decode("ascii")


def parse_output(out: str, summary=lambda text: text) -> dict:
    """出力を文法で読み、合わなければ AssertionError。

    返すのは {"layer1": (state, reason), "layer2": (state, reason), "hits": [...],
    "result": str} か、想定外の例外の形なら {"error": 型名, "result": "unable"}。
    状態行の findings= と座標の行数が層ごとに一致することも見る。summary は、要約の行と
    比べる前に入口の定数へ掛ける変換 (stdout の符号化を変えたケースが使う)。
    """
    if not out.endswith("\n"):
        raise AssertionError(f"出力が改行で終わらない: {out!r}")
    lines = out[:-1].split("\n")
    if len(lines) < 3:
        raise AssertionError(f"出力が 3 行に満たない: {out!r}")
    if ERROR_LINE.match(lines[0]):
        if len(lines) != 3 or lines[1] != f"result={entry.RESULT_BY_EXIT[entry.EXIT_UNABLE]}":
            raise AssertionError(f"想定外の例外の形が 3 行でない: {out!r}")
        if lines[-1] != summary(entry.SUMMARY_UNEXPECTED):
            raise AssertionError(f"要約が想定外の例外のものでない: {lines[-1]!r}")
        return {"error": lines[0][len("error="):], "result": "unable"}
    heads = [STATUS_LINE.match(lines[0]), STATUS_LINE.match(lines[1])]
    if not heads[0] or heads[0].group(1) != "layer1":
        raise AssertionError(f"1 行目が layer1 の状態行でない: {lines[0]!r}")
    if not heads[1] or heads[1].group(1) != "layer2":
        raise AssertionError(f"2 行目が layer2 の状態行でない: {lines[1]!r}")
    result = RESULT_LINE.match(lines[-2])
    if not result:
        raise AssertionError(f"末尾から 2 行目が result= の行でない: {lines[-2]!r}")
    if lines[-1] != summary(entry.SUMMARY_BY_EXIT[EXIT_BY_RESULT[result.group(1)]]):
        raise AssertionError(f"要約が result と対応しない: {lines[-1]!r}")
    hits = []
    counts = {"1": 0, "2": 0}
    for line in lines[2:-2]:
        m = HIT_LINE.match(line)
        if not m or not HIT_LABEL[m.group(3)].match(m.group(4)):
            raise AssertionError(f"座標の行の形でない: {line!r}")
        counts[m.group(3)] += 1
        hits.append(line[len("  [x] "):])
    for m in heads:
        if int(m.group(4)) != counts[m.group(1)[-1]]:
            raise AssertionError(f"findings= と座標の行数が合わない: {out!r}")
    return {
        "layer1": (heads[0].group(2), heads[0].group(3) or ""),
        "layer2": (heads[1].group(2), heads[1].group(3) or ""),
        "hits": hits,
        "result": result.group(1),
    }


def section_lines(text: str, heading: str) -> list[str]:
    """Markdown の見出し heading の節の行 (見出しの行を除く)。合わなければ AssertionError。

    節は、同じか上の階層の次の見出しまで。コードブロックの中の # で始まる行は見出しに数えない
    (シェルのコメントで節が途中で切れ、表が節の外に落ちる)。見出しがコードブロックの外に
    ちょうど 1 回現れることも見る。無いときに空の節を返すと、表の pin が 0 件の比較になる。
    """
    level = len(HEADING.match(heading).group(1))
    found, inside, fenced, lines = 0, False, False, []
    for line in text.split("\n"):
        if FENCE.match(line):
            fenced = not fenced
        elif not fenced and line == heading:
            found += 1
            inside = True
            continue
        elif not fenced and HEADING.match(line) and len(HEADING.match(line).group(1)) <= level:
            inside = False
        if inside:
            lines.append(line)
    if found != 1:
        raise AssertionError(f"見出し {heading!r} がコードブロックの外に {found} 回ある (1 回であること)")
    return lines


class Case(unittest.TestCase):
    """P と M のケースが共有する土台。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.deny = self.dir / "deny.txt"
        self.deny.write_text(f"{WORD}\n{WORD2}\n", encoding="utf-8")
        self.env = base_env()
        self.env[ENV_VAR] = str(self.deny)
        self._inputs = 0

    def write(self, content) -> str:
        """入力ファイルを書いてパスを返す。str は UTF-8、bytes はそのまま。"""
        self._inputs += 1
        path = self.dir / f"input-{self._inputs}.txt"
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        return str(path)

    def run_process(self, *args: str, env=None, cwd=None, script: Path = ENTRY):
        """P: プロセス境界で起動し (rc, stdout, stderr) を返す。"""
        proc = subprocess.run(
            [sys.executable, str(script), *args],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=self.env if env is None else env,
            cwd=cwd,
        )
        return proc.returncode, proc.stdout, proc.stderr

    def run_main(self, *args: str, env=None, **kwargs):
        """M: main() を呼び (rc, stdout, stderr) を返す。"""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = entry.main(list(args), env=self.env if env is None else env, **kwargs)
        return rc, out.getvalue(), err.getvalue()

    def parse(self, out: str, err: str, *forbidden: str, summary=lambda text: text) -> dict:
        """文法と非表示規則を全ケースで見る。stderr は空であること。"""
        self.assertEqual(err, "", "stderr に何か出ている")
        parsed = parse_output(out, summary)
        lowered = out.lower()
        for word in (WORD, WORD2, NAME, UUID, PAT, str(self.dir), str(HERE), "Traceback", *forbidden):
            self.assertNotIn(word.lower(), lowered, f"出力に {word!r} が漏れている")
        return parsed

    def expect(self, parsed: dict, layer1: tuple, layer2: tuple) -> None:
        self.assertEqual(parsed["layer1"], layer1, "layer1 の状態と理由")
        self.assertEqual(parsed["layer2"], layer2, "layer2 の状態と理由")

    def stub(self, name: str, body: str) -> Path:
        """層 2 の代わりに起動するスクリプト。"""
        path = self.dir / f"stub-{name}.py"
        path.write_text(body, encoding="utf-8")
        return path

    def marker_stub(self) -> tuple:
        """呼ばれたら目印のファイルを作る層 2 の stub と、その目印のパス。

        呼ばれていないことは、最悪の状態を採る規則のため理由では見えない。stub は
        呼ばれれば checked を名乗るので、呼ばれた回は目印の有無に加えて状態でも露見する。
        subTest をまたいで使うときは、各回の前に assert_not_called の側で目印を消す。
        """
        marker = self.dir / "layer2-called"
        stub = self.stub(
            "marker",
            "import pathlib, sys\n"
            f"pathlib.Path({str(marker)!r}).write_text('called')\n"
            f"print({denylist_checker.STATUS_CHECKED!r} + ' lines=1 entries=1 findings=0')\n"
            "sys.exit(0)\n",
        )
        return stub, marker

    def marker_gitleaks(self) -> tuple:
        """呼ばれたら目印のファイルを作る gitleaks だけを PATH に置いた env と、その目印のパス。

        入口は渡された env の PATH だけで gitleaks を探すので、PATH をこのディレクトリ 1 つに
        すると実物の gitleaks は見つからない。偽物は何も出さずに rc 0 で終わるので、呼ばれた回は
        層 1 が report-unreadable になる。
        """
        bindir = self.dir / "fakebin"
        bindir.mkdir()
        marker = self.dir / "gitleaks-called"
        fake = bindir / "gitleaks"
        fake.write_text(f"#!/bin/sh\n: > {shlex.quote(str(marker))}\n", encoding="utf-8")
        fake.chmod(0o755)
        env = dict(self.env)
        env["PATH"] = str(bindir)
        return env, marker

    def sized_input(self, total: int) -> bytes:
        """2 本の config のうち大きい方の payload がちょうど total バイトになる入力。

        全行に user-path の検出を持ち、空行を持たない。gitleaks は区切りを空行まで延ばすので
        (入口の docstring の層 1)、空行があると区切りが行の途中に来ず、取りこぼしが起きない。
        行は検出 1 つだけの最短の形にする。区切りで検出が消えるのは、区切りが `/Users/` の内側
        から名前の 1 文字目の前までに来たときで (前半か後半のどちらかに一致が残れば検出される)、
        行が長いほど区切りが取りこぼしとして現れにくい (入口を pipe へ戻した写しで、ちょうど上限の
        入力を 20 回流した実測: 1 行 27 バイトでは 20 回とも緑、この最短の行では 19 回赤。普通の
        ファイルではこの行でも 20 回とも緑)。
        """
        canaries = (entry.custom_canary(), entry.default_canary())
        # 入力が改行で終わるとき、payload は前後の canary と入力を足したもの
        overhead = max(len(entry.build_payload(canary, b"\n")) for canary in canaries) - 1
        line = f"/Users/{NAME}\n".encode("utf-8")
        count, extra = divmod(total - overhead, len(line))
        # 端数は最後の行の名前を伸ばして吸収する
        data = line * (count - 1) + f"/Users/{NAME}{'x' * extra}\n".encode("utf-8")
        self.assertEqual(max(len(entry.build_payload(c, data)) for c in canaries), total)
        self.assertNotIn(b"\n\n", data)
        return data

    def dicm_body(self) -> str:
        """前に既定の canary を置いても 128 バイト目に DICM が来る入力。

        gitleaks はこの形を canary ごと読み飛ばす (実測)。既定の canary の行から作るので、
        その行が 128 バイト未満であることを assert する。
        """
        line = entry.default_canary()["github-pat"] + "\n"
        prefix = len(line.encode("utf-8"))
        self.assertLess(prefix, 128, "既定の canary の行が 128 バイト以上で、この形を作れない")
        return "a" * (128 - prefix) + "DICM\nharmless\n"

    @contextlib.contextmanager
    def assert_not_called(self, marker: Path, message: str):
        """目印を消してから本体を走らせ、終わっても目印が無いことを見る。

        消さずに続けると、前の subTest で作られた目印が次の subTest の判定を汚す
        (変異注入で実測: UTF-8 の確認を外す変異で、NUL の subTest まで赤になった)。
        """
        marker.unlink(missing_ok=True)
        yield
        self.assertFalse(marker.exists(), message)

    def variant(self, source: Path, old: str, new: str) -> Path:
        """config の写し。置換が 1 回起きたことを assert する。"""
        text = source.read_text(encoding="utf-8")
        self.assertEqual(text.count(old), 1, "置換の対象が 1 箇所でない (dead pin)")
        path = self.dir / source.name
        path.write_text(text.replace(old, new), encoding="utf-8")
        return path


class ProcessBoundary(Case):
    """P。プロセス境界で見る。top-level の例外の受けと stderr の遮断はここでしか見えない。"""

    def test_clean(self):
        rc, out, err = self.run_process(self.write("feat: x\n\n清浄な本文\n"))
        parsed = self.parse(out, err)
        self.assertEqual(rc, entry.EXIT_OK)
        self.expect(parsed, CHECKED, CHECKED)
        self.assertEqual(parsed["hits"], [])
        self.assertEqual(parsed["result"], "ok")

    def test_layer1_custom_hit(self):
        rc, out, err = self.run_process(self.write(f"intro\npath {USER_PATH}\n"))
        parsed = self.parse(out, err)
        self.assertEqual(rc, entry.EXIT_FINDING)
        self.expect(parsed, CHECKED, CHECKED)
        self.assertEqual(parsed["hits"], ["file 1 line 2: layer1 user-path"])
        self.assertEqual(parsed["result"], "finding")

    def test_caller_ignore_file(self):
        # stdin モードは -i に何を渡しても cwd の .gitleaksignore を読み、fingerprint は
        # :<ルール>:<行> の形なので、呼び出し元の免除ファイルが入力の検出も canary も消しうる
        # (gitleaks 8.30.1 で実測)。入口は cwd を実行ごとの空の一時ディレクトリにする
        caller = self.dir / "caller"
        caller.mkdir()
        rules = sorted(entry.custom_canary()) + sorted(entry.default_canary())
        # 前の canary の行数 + 1 が入力の検出行。canary の行も全部指す
        (caller / ".gitleaksignore").write_text(
            "".join(f":{rule}:{n}\n" for rule in rules for n in range(1, 12)), encoding="utf-8"
        )
        rc, out, err = self.run_process(self.write(f"{USER_PATH}\n"), cwd=caller)
        parsed = self.parse(out, err)
        self.assertEqual(rc, entry.EXIT_FINDING)
        self.expect(parsed, CHECKED, CHECKED)
        self.assertEqual(parsed["hits"], ["file 1 line 1: layer1 user-path"])

    def test_gitleaks_absent(self):
        empty = self.dir / "nobin"
        empty.mkdir()
        self.assertIsNone(shutil.which("gitleaks", path=str(empty)))
        env = dict(self.env)
        env["PATH"] = str(empty)
        rc, out, err = self.run_process(self.write("clean\n"), env=env)
        parsed = self.parse(out, err)
        self.assertEqual(rc, entry.EXIT_SKIPPED)
        self.expect(parsed, (entry.STATE_SKIPPED, "gitleaks-not-found"), CHECKED)
        self.assertEqual(parsed["result"], "skipped")

    def test_env_unset(self):
        rc, out, err = self.run_process(self.write("clean\n"), env=base_env())
        parsed = self.parse(out, err)
        self.assertEqual(rc, entry.EXIT_SKIPPED)
        self.expect(parsed, CHECKED, (entry.STATE_SKIPPED, "env-unset"))

    def test_gitleaks_config_failed(self):
        # config を読めないとき gitleaks は rc 1 を返し、stderr に config の絶対パスを
        # そのまま出す (実測)。入口がそれを流すと一時ディレクトリのパスが出力に載る
        marker = "zz-config-marker"
        box = self.dir / "box"
        box.mkdir()
        for src in (ENTRY, DENYLIST, CUSTOM_RULES, DEFAULT_RULES):
            shutil.copy(src, box / src.name)
        (box / CUSTOM_RULES.name).write_text(
            f'title = "broken"\n\n[extend]\npath = "{self.dir / marker / "rules.toml"}"\n',
            encoding="utf-8",
        )
        rc, out, err = self.run_process(self.write("clean\n"), script=box / ENTRY.name)
        parsed = self.parse(out, err, marker)
        self.assertEqual(rc, entry.EXIT_UNABLE)
        self.expect(parsed, (entry.STATE_UNABLE, "gitleaks-failed"), CHECKED)

    def test_no_args(self):
        # --target-private だけを渡した形も入力が無い。入力の前段の規則どおり、not-applicable の
        # 層 2 はそのままにし、層 1 だけを no-input の unable にする
        halted = (entry.STATE_UNABLE, "no-input")
        cases = (
            ("none", (), halted),
            ("target-private-only", (entry.TARGET_PRIVATE,), (entry.STATE_NOT_APPLICABLE, "target-private")),
        )
        for label, args, layer2 in cases:
            with self.subTest(label=label):
                rc, out, err = self.run_process(*args)
                parsed = self.parse(out, err)
                self.assertEqual(rc, entry.EXIT_UNABLE)
                self.expect(parsed, halted, layer2)
                self.assertEqual(parsed["result"], "unable")

    def test_payload_at_limit(self):
        # 大きい方の payload がちょうど上限。全行に検出を持ち空行を持たない入力は、gitleaks が
        # 区切ると行の途中で切れて、その行の検出が消える形 (入口の docstring の層 1)。前後の
        # canary は揃うので、区切られても checked のまま件数だけが減る。上限以下なら 1 回の read
        # に収まり、1 件も落ちない
        data = self.sized_input(entry.MAX_PAYLOAD_BYTES)
        rc, out, err = self.run_process(self.write(data))
        parsed = self.parse(out, err)
        self.assertEqual(rc, entry.EXIT_FINDING)
        self.expect(parsed, CHECKED, CHECKED)
        lines = data.count(b"\n")
        self.assertEqual(
            parsed["hits"], [f"file 1 line {n}: layer1 user-path" for n in range(1, lines + 1)]
        )

    def _assert_ascii_stdout(self, body: str, expected_rc: int, hits: list) -> None:
        env = dict(self.env)
        env["PYTHONIOENCODING"] = "ascii"
        rc, out, err = self.run_process(self.write(body), env=env)
        parsed = self.parse(out, err, summary=ascii_escaped)
        self.assertEqual(rc, expected_rc)
        self.expect(parsed, CHECKED, CHECKED)
        self.assertEqual(parsed["hits"], hits)

    def test_ascii_stdout_clean(self):
        # stdout の符号化が日本語を書けない (PYTHONIOENCODING=ascii、cp1252 のコンソール) と、
        # 既定の strict では要約の行で UnicodeEncodeError になり、未捕捉なら rc 1 (検出あり) に
        # 化けて traceback が stderr へ出る (実測)。入口は backslashreplace で書く
        self._assert_ascii_stdout("clean\n", entry.EXIT_OK, [])

    def test_ascii_stdout_layer2_hit(self):
        # 入口は env をそのまま層 2 へ渡すので、層 2 も同じ符号化で書く。層 2 が status=checked を
        # 出したあと日本語の行で落ちると rc 2 になり、検出が unable に化ける (実測)
        self._assert_ascii_stdout(
            f"intro\n{WORD}\n", entry.EXIT_FINDING, ["file 1 line 2: layer2 denylist line 1"]
        )

    def test_stdout_unwritable(self):
        # 書き出しも最上位の保護の内側にある。書き出せないときも stderr に何も出さずに unable を
        # 返し、出力を読めない呼び出し元へ ok を返さない。fd 1 を閉じて起動すると sys.stdout は
        # None になる。読み手が閉じた pipe では、終了時の flush がもう一度失敗して stderr へ
        # 「Exception ignored」を出し rc 120 になる (実測) ので、入口の側で残りを捨てる
        path = self.write("clean\n")
        for label in ("closed-fd", "broken-pipe"):
            with self.subTest(label=label):
                if label == "closed-fd":
                    proc = subprocess.run(
                        [sys.executable, str(ENTRY), path],
                        stderr=subprocess.PIPE,
                        env=self.env,
                        preexec_fn=lambda: os.close(1),
                    )
                else:
                    read_end, write_end = os.pipe()
                    os.close(read_end)
                    try:
                        proc = subprocess.run(
                            [sys.executable, str(ENTRY), path],
                            stdout=write_end,
                            stderr=subprocess.PIPE,
                            env=self.env,
                        )
                    finally:
                        os.close(write_end)
                self.assertEqual(proc.returncode, entry.EXIT_UNABLE)
                self.assertEqual(proc.stderr, b"")

    def test_dash_relative_path(self):
        # - で始まる相対パスを層 2 へ `--check-text <path>` の 2 引数で渡すと、層 2 の argparse が
        # 値をオプションとして読んで落ち、denylist-unable になる (実測)
        (self.dir / "-dash.txt").write_text("clean\n", encoding="utf-8")
        rc, out, err = self.run_process("-dash.txt", cwd=self.dir)
        parsed = self.parse(out, err, "-dash")
        self.assertEqual(rc, entry.EXIT_OK)
        self.expect(parsed, CHECKED, CHECKED)


class MainCalls(Case):
    """M。main() を呼んで判定表を見る。"""

    def test_layer1_default_hit(self):
        rc, out, err = self.run_main(self.write(f"see {PAT} here\n"))
        parsed = self.parse(out, err)
        self.assertEqual(rc, entry.EXIT_FINDING)
        self.expect(parsed, CHECKED, CHECKED)
        self.assertEqual(parsed["hits"], ["file 1 line 1: layer1 github-pat"])

    def test_layer2_hit(self):
        rc, out, err = self.run_main(self.write(f"intro\nabout {WORD}\n"))
        parsed = self.parse(out, err)
        self.assertEqual(rc, entry.EXIT_FINDING)
        self.expect(parsed, CHECKED, CHECKED)
        self.assertEqual(parsed["hits"], ["file 1 line 2: layer2 denylist line 1"])

    def test_two_files(self):
        first = self.write("clean\n")
        second = self.write(f"{USER_PATH}\n{WORD2}\n")
        rc, out, err = self.run_main(first, second)
        parsed = self.parse(out, err)
        self.assertEqual(rc, entry.EXIT_FINDING)
        self.expect(parsed, CHECKED, CHECKED)
        self.assertEqual(
            parsed["hits"],
            ["file 2 line 1: layer1 user-path", "file 2 line 2: layer2 denylist line 2"],
        )

    def test_allow_marker(self):
        # 行に gitleaks:allow を含むと、その行の検出が消える (HTML コメントの中でも同じ。
        # 実測)。本文を書く本人が書ける印で検査が外れる形なので、入口は印を無視する
        pat_line = entry.default_canary()["github-pat"]
        self.assertTrue(pat_line.endswith(" " + MARK))
        body = f"{USER_PATH} {MARK}\n<!-- {UUID} {MARK} -->\n{pat_line}\n"
        rc, out, err = self.run_main(self.write(body))
        parsed = self.parse(out, err)
        self.assertEqual(rc, entry.EXIT_FINDING)
        self.expect(parsed, CHECKED, CHECKED)
        self.assertEqual(
            parsed["hits"],
            [
                "file 1 line 1: layer1 user-path",
                "file 1 line 2: layer1 vm-uuid",
                "file 1 line 3: layer1 github-pat",
            ],
        )

    def test_first_line_hit(self):
        # 前の canary との継ぎ目。canary の最後の改行が落ちると 1 行目が canary の行に
        # 溶けて、検出が「canary の行の余分」として捨てられる
        rc, out, err = self.run_main(self.write(f"{UUID}\nrest\n"))
        parsed = self.parse(out, err)
        self.assertEqual(rc, entry.EXIT_FINDING)
        self.expect(parsed, CHECKED, CHECKED)
        self.assertEqual(parsed["hits"], ["file 1 line 1: layer1 vm-uuid"])

    def test_no_trailing_newline(self):
        # 後ろの canary との継ぎ目。改行を足さないと最終行が後ろの canary に溶ける
        rc, out, err = self.run_main(self.write(f"a\nb\n{USER_PATH}"))
        parsed = self.parse(out, err)
        self.assertEqual(rc, entry.EXIT_FINDING)
        self.expect(parsed, CHECKED, CHECKED)
        self.assertEqual(parsed["hits"], ["file 1 line 3: layer1 user-path"])

    def test_magic_prefixed(self):
        # gitleaks は先頭バイトで「バイナリ」と判定した入力を rc 0 のまま読み飛ばす
        # (PDF・MZ・RTF の magic で実測)。前に canary を置くと先頭の magic による
        # 読み飛ばしは消える
        for magic in ("%PDF-1.4", "MZ", "{\\rtf1"):
            with self.subTest(magic=magic):
                rc, out, err = self.run_main(self.write(f"{magic} header\n{USER_PATH}\n"))
                parsed = self.parse(out, err)
                self.assertEqual(rc, entry.EXIT_FINDING)
                self.expect(parsed, CHECKED, CHECKED)
                self.assertEqual(parsed["hits"], ["file 1 line 2: layer1 user-path"])

    def test_content_skipped(self):
        # 前に canary を置いても条件が成り立つ形 (128 バイト目に DICM) では、canary ごと
        # 読み飛ばされる (実測)。読み飛ばしは rc 0 と空のレポートで返るので、canary が
        # 無ければ「検査済みの 0 件」になる
        rc, out, err = self.run_main(self.write(self.dicm_body()))
        parsed = self.parse(out, err)
        self.assertEqual(rc, entry.EXIT_UNABLE)
        self.expect(parsed, (entry.STATE_UNABLE, "canary-not-detected"), CHECKED)

    def test_payload_over_limit(self):
        # 上限を 1 バイト超える入力は前段で止め、gitleaks も層 2 も起動しない
        stub, stub_marker = self.marker_stub()
        env, gitleaks_marker = self.marker_gitleaks()
        # 対照。上限以下の入力ではどちらの目印も作られる。目印が作られない仕掛けのままだと、
        # 下の「作られていない」は何も見ていない
        rc, out, err = self.run_main(self.write("clean\n"), env=env, denylist_script=stub)
        parsed = self.parse(out, err)
        self.assertEqual(rc, entry.EXIT_UNABLE)
        self.expect(parsed, (entry.STATE_UNABLE, "report-unreadable"), CHECKED)
        self.assertTrue(gitleaks_marker.exists(), "対照で偽の gitleaks が呼ばれていない")
        self.assertTrue(stub_marker.exists(), "対照で層 2 の stub が呼ばれていない")
        path = self.write(self.sized_input(entry.MAX_PAYLOAD_BYTES + 1))
        # ファイルの大きさだけで上限を超える入力は読む前に止める。NUL だけの中身にしておくと、
        # 全部読んでから判定する形では先に NUL の確認に掛かって input-undecodable になるので、
        # 読む前に止めたかどうかを理由で見分けられる
        unread = self.write(b"\0" * (entry.MAX_PAYLOAD_BYTES + 1))
        halted = (entry.STATE_UNABLE, "input-too-large")
        cases = (
            ("public", (path,), halted),
            ("target-private", (entry.TARGET_PRIVATE, path), (entry.STATE_NOT_APPLICABLE, "target-private")),
            ("oversize-before-read", (unread,), halted),
        )
        for label, args, layer2 in cases:
            gitleaks_marker.unlink(missing_ok=True)
            with self.subTest(label=label), self.assert_not_called(
                stub_marker, "上限を超える入力で層 2 が起動した"
            ):
                rc, out, err = self.run_main(*args, env=env, denylist_script=stub)
                parsed = self.parse(out, err)
                self.assertEqual(rc, entry.EXIT_UNABLE)
                self.expect(parsed, halted, layer2)
                self.assertFalse(gitleaks_marker.exists(), "上限を超える入力で gitleaks が起動した")

    def test_stdin_is_a_regular_file(self):
        # gitleaks へは普通のファイルを stdin として渡す。pipe (input=) だと gitleaks の 1 回の
        # read が返す量が書き手の進み具合で揺れ、上限以下でも行の途中で区切られうる (入口の
        # docstring の層 1)。その取りこぼしは確率的で、件数を見るテストでは pipe へ戻す変異を
        # 決定的に赤にできない。そこで subprocess.run を元の関数へ委ねる wrapper に差し替え、
        # 呼び出しの時点で stdin の形を見る。モックはこの形を見るためだけに使う
        real = subprocess.run
        seen = []

        def spy(argv, *args, **kwargs):
            if argv[1] == "stdin":
                source = kwargs.get("stdin")
                regular = hasattr(source, "fileno") and stat.S_ISREG(os.fstat(source.fileno()).st_mode)
                # cwd は空のまま (payload のファイルを cwd に置かない)
                seen.append(("input" in kwargs, regular, os.listdir(kwargs["cwd"])))
            return real(argv, *args, **kwargs)

        with mock.patch.object(entry.subprocess, "run", spy):
            rc, out, err = self.run_main(self.write(f"{USER_PATH}\n"))
        parsed = self.parse(out, err)
        self.assertEqual(rc, entry.EXIT_FINDING)
        self.expect(parsed, CHECKED, CHECKED)
        self.assertEqual(parsed["hits"], ["file 1 line 1: layer1 user-path"])
        # config 2 本ぶん呼ばれ、どちらも input を使わず、stdin が普通のファイル
        self.assertEqual(seen, [(False, True, [])] * 2)

    def test_combine_layer1_worst_state(self):
        # 層の状態は入力間で最悪を採る。最良の入力や最後の入力の状態を採る壊れ方は、unable の
        # 入力 (canary ごと読み飛ばされる DICM) と checked の入力をどちらの順で渡しても露見する
        for order in ("dicm-first", "dicm-last"):
            with self.subTest(order=order):
                dicm, clean = self.write(self.dicm_body()), self.write("clean\n")
                paths = (dicm, clean) if order == "dicm-first" else (clean, dicm)
                rc, out, err = self.run_main(*paths)
                parsed = self.parse(out, err)
                self.assertEqual(rc, entry.EXIT_UNABLE)
                self.expect(parsed, (entry.STATE_UNABLE, "canary-not-detected"), CHECKED)

    def test_combine_layer2_worst_state(self):
        # 層 2 の側も同じ規則。入力の中身で振る舞いを変える stub を使い、目印の語を含む入力では
        # 何も出さずに rc 0 で終わる (unable)。それ以外は status=checked を出す。stub は
        # --check-text の値を 1 引数の形と 2 引数の形のどちらでも読む
        unable_mark = "zz-stub-unable"
        checked = denylist_checker.STATUS_CHECKED
        stub = self.stub(
            "by-content",
            "import sys\n"
            "arg = sys.argv[-1]\n"
            "path = arg.split('=', 1)[1] if arg.startswith('--check-text=') else arg\n"
            f"if {unable_mark!r} in open(path, encoding='utf-8').read():\n"
            "    sys.exit(0)\n"
            f"print({checked!r} + ' lines=1 entries=1 findings=0')\n"
            "sys.exit(0)\n",
        )
        for order in ("unable-first", "unable-last"):
            with self.subTest(order=order):
                unable, clean = self.write(f"{unable_mark}\n"), self.write("clean\n")
                paths = (unable, clean) if order == "unable-first" else (clean, unable)
                rc, out, err = self.run_main(*paths, denylist_script=stub)
                parsed = self.parse(out, err, unable_mark)
                self.assertEqual(rc, entry.EXIT_UNABLE)
                self.expect(parsed, CHECKED, (entry.STATE_UNABLE, "denylist-unable"))

    def test_combine_keeps_earlier_hits(self):
        # 検出は全入力のものを採る。test_two_files は検出が最後の入力にあるので、最後の入力の
        # 検出だけを採る壊れ方を見られない
        rc, out, err = self.run_main(self.write(f"{USER_PATH}\n{WORD}\n"), self.write("clean\n"))
        parsed = self.parse(out, err)
        self.assertEqual(rc, entry.EXIT_FINDING)
        self.expect(parsed, CHECKED, CHECKED)
        self.assertEqual(
            parsed["hits"],
            ["file 1 line 1: layer1 user-path", "file 1 line 2: layer2 denylist line 1"],
        )

    def test_symlink_input(self):
        target = Path(self.write(f"{USER_PATH}\n"))
        link = self.dir / "link.txt"
        link.symlink_to(target)
        rc, out, err = self.run_main(str(link))
        parsed = self.parse(out, err)
        self.assertEqual(rc, entry.EXIT_FINDING)
        self.expect(parsed, CHECKED, CHECKED)
        self.assertEqual(parsed["hits"], ["file 1 line 1: layer1 user-path"])

    def test_undecodable(self):
        # UTF-16 は gitleaks が読み飛ばす形で、NUL を含む UTF-8 は走査される (実測)。
        # どちらも前段で止め、gitleaks も層 2 も起動しない
        stub, marker = self.marker_stub()
        # ASCII を UTF-16 にした形は NUL を含むので、NUL の確認で止まって UTF-8 の確認へ届かない。
        # 下位バイトが 0 でない CJK だけを BOM 付き・改行なしで UTF-16 にすると NUL を持たない
        cjk_utf16 = "漢字本文".encode("utf-16")
        self.assertNotIn(b"\0", cjk_utf16, "NUL を含み、UTF-8 の確認へ届かない (dead pin)")
        cases = (
            ("utf-16", f"{USER_PATH}\n".encode("utf-16")),
            ("utf-16-without-nul", cjk_utf16),
            ("invalid-utf-8", b"abc\xff\xfe\n"),
            ("nul", b"abc\x00def\n"),
        )
        for label, data in cases:
            with self.subTest(label=label), self.assert_not_called(
                marker, "前段で止まるべき入力で層 2 が起動した"
            ):
                rc, out, err = self.run_main(self.write(data), denylist_script=stub)
                parsed = self.parse(out, err)
                self.assertEqual(rc, entry.EXIT_UNABLE)
                halted = (entry.STATE_UNABLE, "input-undecodable")
                self.expect(parsed, halted, halted)
                self.assertEqual(parsed["hits"], [])

    def test_empty_and_missing(self):
        # 0 byte を通すと、書き忘れたファイルが「検査済みの 0 件」になる
        stub, marker = self.marker_stub()
        # 文字デバイスは読めても通常ファイルではない。確認を外すと /dev/null は 0 byte として
        # 読めて input-empty になり、FIFO は open で止まり、/dev/stdin は端末を読む
        cases = (
            ("empty", self.write(b""), "input-empty"),
            ("missing", str(self.dir / "nonexistent.txt"), "input-unreadable"),
            ("directory", str(self.dir), "input-unreadable"),
            ("char-device", os.devnull, "input-unreadable"),
        )
        for label, path, reason in cases:
            with self.subTest(label=label), self.assert_not_called(
                marker, "前段で止まるべき入力で層 2 が起動した"
            ):
                rc, out, err = self.run_main(path, denylist_script=stub)
                parsed = self.parse(out, err)
                self.assertEqual(rc, entry.EXIT_UNABLE)
                halted = (entry.STATE_UNABLE, reason)
                self.expect(parsed, halted, halted)

    def test_env_not_inherited(self):
        # main は受けた env を層 2 の subprocess へ渡す。渡さないと子は os.environ を継ぐ
        with mock.patch.dict(os.environ, {ENV_VAR: str(self.deny)}):
            rc, out, err = self.run_main(self.write("clean\n"), env=base_env())
        parsed = self.parse(out, err)
        self.assertEqual(rc, entry.EXIT_SKIPPED)
        self.expect(parsed, CHECKED, (entry.STATE_SKIPPED, "env-unset"))

    def test_target_private(self):
        stub, marker = self.marker_stub()
        off = (entry.STATE_NOT_APPLICABLE, "target-private")
        cases = (
            ("denylist-word-only", self.write(f"{WORD}\n"), self.env, entry.EXIT_OK, []),
            (
                "layer1-hit",
                self.write(f"{USER_PATH}\n"),
                self.env,
                entry.EXIT_FINDING,
                ["file 1 line 1: layer1 user-path"],
            ),
            ("env-unset-clean", self.write("clean\n"), base_env(), entry.EXIT_OK, []),
        )
        for label, path, env, expected_rc, hits in cases:
            with self.subTest(label=label), self.assert_not_called(
                marker, "--target-private で層 2 が起動した"
            ):
                rc, out, err = self.run_main(
                    entry.TARGET_PRIVATE, path, env=env, denylist_script=stub
                )
                parsed = self.parse(out, err)
                self.assertEqual(rc, expected_rc)
                self.expect(parsed, CHECKED, off)
                self.assertEqual(parsed["hits"], hits)

    def _assert_canary_missing(self, **kwargs):
        rc, out, err = self.run_main(self.write("clean\n"), **kwargs)
        parsed = self.parse(out, err)
        self.assertEqual(rc, entry.EXIT_UNABLE)
        self.expect(parsed, (entry.STATE_UNABLE, "canary-not-detected"), CHECKED)

    def test_custom_regex_broken(self):
        old = r"regex = '''[\\/]+Users[\\/]+(?i)[a-z_][a-z0-9._-]*'''"
        broken = self.variant(CUSTOM_RULES, old, old.replace("Users", "Userz"))
        self._assert_canary_missing(custom_rules=broken)

    def test_custom_rule_missing(self):
        text = CUSTOM_RULES.read_text(encoding="utf-8")
        start = text.index('[[rules]]\nid = "vm-uuid"')
        end = text.index("[[rules]]", start + 1)
        missing = self.variant(CUSTOM_RULES, text[start:end], "")
        self.assertNotIn('id = "vm-uuid"', missing.read_text(encoding="utf-8"))
        self._assert_canary_missing(custom_rules=missing)

    def test_custom_use_default(self):
        # 既定 config の全体除外 (?i)^true|false|null$ は custom ルールの検出にも効く
        # (実測)。false を含む canary が消えることで露見する
        title = 'title = "leak-guard custom rules"\n'
        extended = self.variant(CUSTOM_RULES, title, title + "\n[extend]\nuseDefault = true\n")
        self._assert_canary_missing(custom_rules=extended)

    def test_default_dropped(self):
        dropped = self.variant(DEFAULT_RULES, "useDefault = true\n", "")
        self._assert_canary_missing(default_rules=dropped)

    def test_denylist_stubs(self):
        # rc だけで判定すると、起動に失敗した Python の rc 1 が「検出あり」に、出力を
        # 出さずに rc 0 で終わる壊れ方が「検査済み」に化ける
        checked = denylist_checker.STATUS_CHECKED
        stubs = (
            ("silent-rc0", "import sys\nsys.exit(0)\n"),
            ("silent-rc1", "import sys\nsys.exit(1)\n"),
            (
                "checked-rc1-no-coords",
                f"import sys\nprint({checked!r} + ' lines=1 entries=1 findings=1')\nsys.exit(1)\n",
            ),
            # rc 0 なのに座標がある。検出を捨てて checked にすると、見つかった検出が消える
            (
                "checked-rc0-with-coords",
                f"import sys\nprint({checked!r} + ' lines=1 entries=1 findings=1')\n"
                "print('  [x] line 1: denylist line 1', file=sys.stderr)\nsys.exit(0)\n",
            ),
        )
        for label, body in stubs:
            with self.subTest(label=label):
                rc, out, err = self.run_main(
                    self.write("clean\n"), denylist_script=self.stub(label, body)
                )
                parsed = self.parse(out, err)
                self.assertEqual(rc, entry.EXIT_UNABLE)
                self.expect(parsed, CHECKED, (entry.STATE_UNABLE, "denylist-unable"))

    def test_unexpected_error(self):
        # 未捕捉の例外は Python の既定で rc 1 になって「検出あり」に化け、traceback に
        # パスが載る。例外の str も同じなので型名だけを出す
        marker = "zz-secret-marker"
        with mock.patch.object(entry, "decide", side_effect=RuntimeError(f"secret {marker}")):
            rc, out, err = self.run_main(self.write("clean\n"))
        parsed = self.parse(out, err, marker)
        self.assertEqual(rc, entry.EXIT_UNABLE)
        self.assertEqual(parsed["error"], "RuntimeError")


class PureFunctions(unittest.TestCase):
    """U。決定・レポートの読み取り・canary の照合・canary の値・ソースの形。"""

    def test_decide_table(self):
        # 検出 > unable > skipped > checked。not-applicable は checked と同じ扱い
        hit = entry.Hit(1, 1, 1, "x")
        states1 = (entry.STATE_CHECKED, entry.STATE_SKIPPED, entry.STATE_UNABLE)
        states2 = states1 + (entry.STATE_NOT_APPLICABLE,)
        for s1, h1, s2, h2 in itertools.product(states1, (0, 1), states2, (0, 1)):
            with self.subTest(layer1=(s1, h1), layer2=(s2, h2)):
                layer1 = entry.Layer(s1, "", [hit] * h1)
                layer2 = entry.Layer(s2, "", [hit] * h2)
                if h1 or h2:
                    expected = entry.EXIT_FINDING
                elif entry.STATE_UNABLE in (s1, s2):
                    expected = entry.EXIT_UNABLE
                elif entry.STATE_SKIPPED in (s1, s2):
                    expected = entry.EXIT_SKIPPED
                else:
                    expected = entry.EXIT_OK
                self.assertEqual(entry.decide(layer1, layer2), expected)

    def test_combine_table(self):
        # 状態は入力間で最悪 (unable > skipped > checked)、理由は最初にその状態になった入力の
        # もの、検出は全入力のものを入力順に連結する
        checked, skipped, unable = entry.STATE_CHECKED, entry.STATE_SKIPPED, entry.STATE_UNABLE

        def hits(file_no: int, count: int) -> list:
            return [entry.Hit(file_no, line, 1, "x") for line in range(1, count + 1)]

        cases = (
            (
                "worst-in-the-middle",
                [
                    (checked, "", hits(1, 1)),
                    (unable, "r1", []),
                    (skipped, "s1", hits(3, 2)),
                    (unable, "r2", hits(4, 1)),
                ],
                (unable, "r1"),
            ),
            ("worst-first", [(unable, "r1", hits(1, 1)), (checked, "", [])], (unable, "r1")),
            (
                "skipped-over-checked",
                [(checked, "", []), (skipped, "s1", []), (checked, "", []), (skipped, "s2", [])],
                (skipped, "s1"),
            ),
            ("all-checked", [(checked, "", hits(1, 1)), (checked, "", hits(2, 1))], (checked, "")),
        )
        for label, rows, expected in cases:
            with self.subTest(label=label):
                combined = entry.combine([entry.Layer(*row) for row in rows])
                self.assertEqual((combined.state, combined.reason), expected)
                self.assertEqual(combined.hits, [hit for row in rows for hit in row[2]])

    def test_payload_limit_is_one_gitleaks_read(self):
        # 上限は gitleaks 8.30.1 の 1 回の read の大きさ (sources/file.go の defaultBufferSize)。
        # 上げる変更は test_payload_at_limit では見えないことがある: 普通のファイルでは、空行の
        # 無い入力でも readUntilSafeBoundary が maxPeekSize まで区切りを延ばすので、その幅に
        # 収まる超過は 1 つの区切りのまま読まれる。値そのものをここで止める
        self.assertEqual(entry.MAX_PAYLOAD_BYTES, 100 * 1_000)

    def _executable(self, directory: Path) -> Path:
        directory.mkdir(parents=True)
        path = directory / "gitleaks"
        path.touch()
        path.chmod(0o755)
        return path

    def test_resolve_gitleaks_relative_path(self):
        # PATH に相対の要素があると which は相対パスを返す。入口は cwd を一時ディレクトリへ移して
        # gitleaks を起動するので、相対のままでは起動できず gitleaks-failed になる (実測)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self._executable(base / "relbin")
        cwd = os.getcwd()
        os.chdir(base)
        self.addCleanup(os.chdir, cwd)
        found = entry.resolve_gitleaks({"PATH": "relbin"})
        # getcwd は symlink を解いたパス (macOS の /var は /private/var) を返し、絶対パスへの
        # 直しも同じ cwd を基準にするので、getcwd と比べる
        self.assertEqual(found, os.path.join(os.getcwd(), "relbin", "gitleaks"))

    def test_resolve_gitleaks_uses_only_the_given_path(self):
        # 渡された env の PATH だけで探す。PATH キーが無いときに which へ None を渡すと、
        # 実行元の PATH へ戻る
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        caller_bin = base / "callerbin"
        self._executable(caller_bin)
        empty = base / "empty"
        empty.mkdir()
        with mock.patch.dict(os.environ, {"PATH": str(caller_bin)}):
            # 対照。実行元の PATH では見つかる。見つからない状態では下の None が何も見ていない
            self.assertIsNotNone(shutil.which("gitleaks"))
            for label, env in (("no-path-key", {}), ("empty-dir", {"PATH": str(empty)})):
                with self.subTest(label=label):
                    self.assertIsNone(entry.resolve_gitleaks(env))

    def test_parse_report_rejects(self):
        for label, raw in (
            ("not-json", b"garbage"),
            ("empty", b""),
            ("not-a-list", b'{"StartLine": 1, "RuleID": "x"}'),
            ("element-not-an-object", b"[1]"),
            ("no-start-line", b'[{"RuleID": "x"}]'),
            ("no-rule-id", b'[{"StartLine": 1}]'),
            ("start-line-not-int", b'[{"StartLine": "1", "RuleID": "x"}]'),
        ):
            with self.subTest(label=label):
                with self.assertRaises(entry.ReportError):
                    entry.parse_report(raw)
        # 対照。読める形では (StartLine, RuleID) を返す
        self.assertEqual(entry.parse_report(b"[]\n"), [])
        self.assertEqual(
            entry.parse_report(b'[{"StartLine": 3, "RuleID": "x", "File": ""}]'), [(3, "x")]
        )

    def test_locate_table(self):
        rules = ["a", "b"]
        lines = 3  # 入力の行数。前の canary が 1..2、入力が 3..5、後ろの canary が 6..7
        full = [(1, "a"), (2, "b"), (4, "a"), (6, "a"), (7, "b")]
        self.assertEqual(entry.locate(full, rules, lines), [(2, "a")])
        cases = (
            ("leading-missing", [r for r in full if r != (1, "a")], entry.CanaryMissing),
            ("trailing-missing", [r for r in full if r != (7, "b")], entry.CanaryMissing),
            ("leading-wrong-rule", [(1, "b"), (2, "a"), (6, "a"), (7, "b")], entry.CanaryMissing),
            ("below-range", full + [(0, "a")], entry.ReportError),
            ("above-range", full + [(8, "a")], entry.ReportError),
        )
        for label, report, exc in cases:
            with self.subTest(label=label):
                with self.assertRaises(exc):
                    entry.locate(report, rules, lines)
        # canary の行の余分な検出は捨てる (上流が既定ルールを足しただけでは止まらない)
        extra = full + [(1, "b"), (7, "zz")]
        self.assertEqual(entry.locate(extra, rules, lines), [(2, "a")])
        # 間の検出は複数でも順に返す
        self.assertEqual(
            entry.locate(full + [(5, "b"), (3, "b")], rules, lines), [(1, "b"), (2, "a"), (3, "b")]
        )

    def test_canary_values(self):
        custom, default = entry.custom_canary(), entry.default_canary()
        for label, canary in (("custom", custom), ("default", default)):
            with self.subTest(label=label):
                self.assertTrue(canary, "canary が空")
                for rule, line in canary.items():
                    self.assertIn(MARK, line, f"{rule} の canary が印を持たない")
                    self.assertNotIn("\n", line, f"{rule} の canary が複数行")
        # custom の config に useDefault が戻ると、既定の全体除外で false を含む検出が消える
        for rule in ("user-path", "email-address"):
            self.assertIn("false", custom[rule], f"{rule} の canary が false を含まない")
        # 既定の canary は、その全体除外に掛かってはいけない
        for rule, line in default.items():
            for word in ("true", "false", "null"):
                self.assertNotIn(word, line.lower(), f"{rule} の canary が {word} を含む")

    def test_output_grammar(self):
        # parse_output 自身の対照。文法に合わない形を通す parse_output は全ケースの
        # 出力の pin を空虚にするので、拒否することをここで見る
        ok = entry.SUMMARY_BY_EXIT[entry.EXIT_OK]
        skipped = entry.SUMMARY_BY_EXIT[entry.EXIT_SKIPPED]
        good = (
            "layer1 status=checked findings=0\n"
            "layer2 status=skipped reason=env-unset findings=0\n"
            f"result=skipped\n{skipped}\n"
        )
        self.assertEqual(parse_output(good)["layer2"], ("skipped", "env-unset"))
        error = f"error=RuntimeError\nresult=unable\n{entry.SUMMARY_UNEXPECTED}\n"
        self.assertEqual(parse_output(error)["error"], "RuntimeError")
        bad = (
            ("no-result-line", f"layer1 status=checked findings=0\nlayer2 status=checked findings=0\n{ok}\n"),
            ("unknown-state", f"layer1 status=done findings=0\nlayer2 status=checked findings=0\nresult=ok\n{ok}\n"),
            ("layer2-with-rule-id", f"layer1 status=checked findings=0\nlayer2 status=checked findings=1\n  [x] file 1 line 1: layer2 user-path\nresult=finding\n{entry.SUMMARY_BY_EXIT[entry.EXIT_FINDING]}\n"),
            ("count-mismatch", f"layer1 status=checked findings=2\nlayer2 status=checked findings=0\n  [x] file 1 line 1: layer1 user-path\nresult=finding\n{entry.SUMMARY_BY_EXIT[entry.EXIT_FINDING]}\n"),
            ("summary-mismatch", f"layer1 status=checked findings=0\nlayer2 status=checked findings=0\nresult=ok\n{skipped}\n"),
            ("error-with-extra-line", f"error=RuntimeError\nresult=unable\n{entry.SUMMARY_UNEXPECTED}\nextra\n"),
            ("no-trailing-newline", good[:-1]),
        )
        for label, out in bad:
            with self.subTest(label=label):
                with self.assertRaises(AssertionError):
                    parse_output(out)

    def test_py39_source(self):
        # 入口と層 2 は 3.9 で動く形に保つ。future import は先頭の文 (docstring の次) で
        # なければ SyntaxError になり、tomllib は 3.11 から
        for path in (ENTRY, DENYLIST):
            with self.subTest(path=path.name):
                tree = ast.parse(path.read_text(encoding="utf-8"), feature_version=(3, 9))
                first = tree.body[1] if isinstance(tree.body[0], ast.Expr) else tree.body[0]
                self.assertIsInstance(first, ast.ImportFrom)
                self.assertEqual(first.module, "__future__")
                self.assertEqual([a.name for a in first.names], ["annotations"])
                imported = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imported |= {a.name for a in node.names}
                    elif isinstance(node, ast.ImportFrom):
                        imported.add(node.module or "")
                self.assertNotIn("tomllib", imported)

    def test_denylist_markers_match_layer2(self):
        # 入口は層 2 を import せず目印を自分で持つ (理由は入口の docstring)。値の canonical は
        # 層 2 なので、ずれをここで止める
        self.assertEqual(entry.DENYLIST_STATUS_SKIPPED, denylist_checker.STATUS_SKIPPED)
        self.assertEqual(entry.DENYLIST_STATUS_CHECKED, denylist_checker.STATUS_CHECKED)


class SkillTable(unittest.TestCase):
    """SKILL.md の「送る前の検査」節の表と、入口の定数。"""

    def test_exit_table_matches_constants(self):
        # 表の行は `| <終了コード> / <result> | <行動> |` の形。組の並びを入口の RESULT_BY_EXIT と
        # 比べる。集合ではなく並べた list で比べ、同じ組の行が 2 本ある (行動が 2 通りに読める)
        # 形も止める
        rows = []
        for line in section_lines(SKILL_MD.read_text(encoding="utf-8"), CHECK_SECTION):
            m = EXIT_TABLE_ROW.match(line)
            if m:
                rows.append((int(m.group(1)), m.group(2)))
        # 0 件を一致とみなさない。下の比較でも赤になるが、表が読めなかったことを名指す
        self.assertTrue(rows, f"{CHECK_SECTION} の節に (終了コード, result) の行が無い")
        self.assertEqual(sorted(rows), sorted(entry.RESULT_BY_EXIT.items()))

    def test_exit_table_has_one_fallback_row(self):
        # 「上記以外」の行は fail-closed の既定で、result= の行が出ない経路 (入口を起動できない
        # など) を受ける。上の一致は (終了コード, result) の行しか見ないので、この行を消しても
        # 緑のまま通る。2 本あると行動が 2 通りに読める。行動の文言は pin しない
        fallback = [
            line
            for line in section_lines(SKILL_MD.read_text(encoding="utf-8"), CHECK_SECTION)
            if line.startswith(FALLBACK_ROW)
        ]
        self.assertEqual(
            len(fallback), 1, f"{CHECK_SECTION} の節に「上記以外」の行が {len(fallback)} 本ある (1 本であること)"
        )

    def test_exit_code_literals(self):
        # 値そのもの。定数と表の番号を揃えて入れ替えると上の一致は保たれるが、終了コードは
        # 出力を読まずに rc だけを見る呼び出し元や記録 (手順は rc と result= の行を残させる) も
        # 読むので、揃えた入れ替えもここで止める
        self.assertEqual(
            (entry.EXIT_OK, entry.EXIT_FINDING, entry.EXIT_UNABLE, entry.EXIT_SKIPPED), (0, 1, 2, 3)
        )

    def test_section_lines(self):
        # section_lines 自身の対照。節の終わりを見誤ると、表の行を取りこぼすか節の外の行を拾う
        text = (
            "## A\n### 送る前の検査\n| 0 / ok | x |\n```bash\n# comment\n```\n"
            "#### 下位\n| 1 / finding | y |\n### 次\n| 2 / unable | z |\n"
        )
        rows = [line for line in section_lines(text, CHECK_SECTION) if EXIT_TABLE_ROW.match(line)]
        self.assertEqual(rows, ["| 0 / ok | x |", "| 1 / finding | y |"])
        for label, broken in (
            ("missing", "## A\n| 0 / ok | x |\n"),
            ("twice", f"{CHECK_SECTION}\n{CHECK_SECTION}\n"),
            ("only-in-fence", f"```\n{CHECK_SECTION}\n```\n"),
        ):
            with self.subTest(label=label):
                with self.assertRaises(AssertionError):
                    section_lines(broken, CHECK_SECTION)


if __name__ == "__main__":
    unittest.main()
