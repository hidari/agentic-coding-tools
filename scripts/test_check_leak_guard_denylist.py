#!/usr/bin/env python3
"""check-leak-guard-denylist.py の仕様と、その取り付けの pin。

禁止語はすべて架空語にする。実際の禁止語をここへ書くと、このテストファイル自身が
露出になる (ISSUE-15-spec.md の「テスト」節)。同型の問題を層 1 の
scripts/check-leak-guard-rules.py が NAME 変数で解いている。

この検査は「緑のまま何も見ていない」形が最も危険なので、テストの重心は検出側ではなく
「検査不能を緑にしないこと」に置く。禁止語リストが空・BOM 付き・NFD・fold の片側適用
ミスのいずれでも、素朴な実装は「走査したファイル全件 / 違反なし」という最も健全に
見える要約で緑を返す (前セッションの失敗モード列挙で 5 角度すべてが独立に指摘した形)。

fixture は tempfile + git init で作る。実ツリーに依存すると、現ツリーがたまたま合格して
いることに寄りかかった dead pin になる。GIT_* を環境から落とす理由は GIT_ENV のコメント。
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
CHECKER = "scripts/check-leak-guard-denylist.py"
PRE_COMMIT_CONFIG = ROOT / ".pre-commit-config.yaml"

# 各 hook が持ってよいキー。絞り込みの手段は列挙し切れないので、許可する側を pin して
# 知らないキーが増えたら赤にする。commit-msg stage では渡るファイルが message ファイル
# 1 本しかないため、ファイル名やファイル型で絞る指定はどれも集合を空にして skip になる。
# pre-commit stage 側は走査対象を追跡ファイル全体で固定するので同じく絞らない。
TRACKED_HOOK_KEYS = frozenset(
    {"id", "name", "language", "entry", "pass_filenames", "always_run", "verbose"}
)
COMMIT_MSG_HOOK_KEYS = frozenset(
    {"id", "name", "language", "entry", "stages", "always_run", "verbose"}
)

# fixture が tempdir で `git add` を走らせるので、GIT_INDEX_FILE を継承すると書き込み先が
# その指し先になり、呼び出し元リポジトリの index を fixture の内容で上書きする
# (scripts/test_check_issue_closure.py が実測を記録している)。テストの終了コードには
# 現れず、上書きしたまま緑を返す。個別の変数名を並べないのは git が変数を増やしたとき
# 列挙だけが古びるため。
GIT_ENV = {
    **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "probe",
    "GIT_AUTHOR_EMAIL": "probe@example.invalid",
    "GIT_COMMITTER_NAME": "probe",
    "GIT_COMMITTER_EMAIL": "probe@example.invalid",
}

# GIT_ENV が守るのは env= を渡した subprocess だけ。このファイルは scan_tracked と main を
# プロセス内でも呼び、その先の checker._git は env= 無しで git を起こすので os.environ を
# 読む。git は commit -a / commit -- <paths> のとき hook へ GIT_INDEX_FILE を渡すため、
# 隔離が無いと fixture の tempdir ではなく呼び出し元の index を読む (実測: この形で
# TrackedSurface の 7 件が errors になる)。先例と理由は scripts/test_check_related_refs.py
_GIT_ENV_PATCH = mock.patch.dict(os.environ, GIT_ENV, clear=True)


def setUpModule() -> None:
    _GIT_ENV_PATCH.start()


def tearDownModule() -> None:
    _GIT_ENV_PATCH.stop()


def git_vars(env) -> dict[str, str]:
    """環境の GIT_* だけを取り出す。プロセスの環境と GIT_ENV を同じ規約で比べるため。"""
    return {k: v for k, v in env.items() if k.startswith("GIT_")}


def load():
    """ハイフン名のスクリプトは import 文では読めないため importlib で読む。"""
    spec = importlib.util.spec_from_file_location("check_leak_guard_denylist", ROOT / CHECKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = load()

# 架空語。どれも実在の固有名詞ではない。
WORD = "zorblatt"
WORD_JA = "ほげ社内"
# casefold と lower の差を作る語。片側 lower・片側 casefold の実装で外れる
WORD_SHARP_S = "Straße"
# 表現差の対照。WORD の全角大文字形と、半角形を持つカタカナ語を定数で持つ。
# 漢字を含む WORD_JA には半角形が無いのでカナ側は別に要る。inline の literal に
# しないのは、架空語であることの保証と assertNoSecrets の射程を 1 箇所へ寄せるため
WORD_FW = "ＺＯＲＢＬＡＴＴ"
WORD_KANA = "ゾルバット"
WORD_KANA_HW = "ｿﾞﾙﾊﾞｯﾄ"
FICTIONAL = (WORD, WORD_JA, WORD_SHARP_S, WORD_FW, WORD_KANA, WORD_KANA_HW, "quuxcorp")

# 見えない文字。fold がこれらを吸収すること (本文側) と、エントリが実質空になる形を
# 検査不能へ倒すこと (リスト側) の両方で使う
ZWSP = "​"
SHY = "­"
BOM = "﻿"
NBSP = " "


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        check=True,
        env=GIT_ENV,
    )


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    return path


def add(root: Path, rel: str, content: str | bytes) -> None:
    """ファイルを書いて index へ載せる。"""
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        target.write_bytes(content)
    else:
        target.write_text(content, encoding="utf-8")
    git(root, "add", "--", rel)


def denylist(path: Path, *words: str) -> Path:
    path.write_text("".join(f"{w}\n" for w in words), encoding="utf-8")
    return path


def run_cli(*args: str, env: dict[str, str] | None = None, cwd: Path | None = None):
    """スクリプトを subprocess で起動し (rc, stdout+stderr) を返す。

    出力の非表示規則は「印字しないこと」が仕様なので、プロセス境界の外で見る。
    関数を直接呼ぶ経路では、top-level の例外ハンドラと argparse の文言が射程から外れる。
    """
    base = {k: v for k, v in GIT_ENV.items()}
    base.pop(checker.ENV_VAR, None)
    if env:
        base.update(env)
    proc = subprocess.run(
        [sys.executable, str(ROOT / CHECKER), *args],
        capture_output=True,
        text=True,
        env=base,
        cwd=str(cwd) if cwd else None,
    )
    return proc.returncode, proc.stdout + proc.stderr


def entries_of(*words: str) -> list:
    """テスト内で組む Entry 列。行番号はリストファイルの行番号を模す。"""
    return [checker.Entry(i, checker.fold(w), w) for i, w in enumerate(words, 1)]


class Fold(unittest.TestCase):
    """照合の前にリスト側と本文側へ同一に掛ける正規化の性質。

    両側で違う関数を使う / 片側だけ余分に正規化する形は、エラーも警告も出さずに
    「検出 0 件で緑」になる。かな・漢字には差が出ないので、日本語だけの fixture では
    絶対に見つからない (前セッションの実測)。
    """

    def test_identity_holds_for_tricky_literals(self):
        # 同じ literal を両側へ置いたとき必ず当たること。lower と casefold の混用や、
        # 本文側だけ NFKC をもう一度掛ける実装はここで落ちる
        for word in (WORD, WORD_JA, WORD_SHARP_S, "ǰ", "ΐ", "がぎぐ", WORD_FW):
            with self.subTest(word=word):
                hits = checker.scan_text(f"x {word} y", entries_of(word))
                self.assertEqual(hits, [(1, 1)], f"{word!r} が自分自身に一致しない")

    def test_fold_is_idempotent(self):
        # 先頭の NFKC を守る対照。これを外すと fold(fold(x)) != fold(x) になる
        # code point が BMP に現れる (実測で拾ったギリシャ文字を fixture に置く)。
        # 照合の結果そのものは変わらないので、冪等性を見ないと外しても気づけない。
        # 件数ではなく fixture 単位で pin するのは、NFKC と casefold の表が Python の
        # バージョンに依存するため
        for word in (
            WORD, WORD_JA, WORD_SHARP_S, "ǰ", "ΐ", "ﬁ", "ｶ゙",
            "ͺ", "ϒ", "ϓ", "ϔ", "ϲ",
        ):
            with self.subTest(word=repr(word)):
                once = checker.fold(word)
                self.assertEqual(checker.fold(once), once)

    def test_recomposes_after_dropping_invisible_characters(self):
        # 末尾の NFKC を守る対照。Cf 除去がゼロ幅文字を落とすと、それまで隣接して
        # いなかった base と結合文字が隣り合う。そこで合成をやり直さないと、
        # 「見た目は同じなのに当たらない」形が残る。ゼロ幅文字は Web や PDF からの
        # ペーストで入るので、難読化ではなく事故として起きる組み合わせ
        cases = [
            ("ガ", f"ｶ{ZWSP}ﾞ"),
            ("が", f"か{ZWSP}゙"),
            ("josé", f"jose{SHY}́"),
        ]
        for word, line in cases:
            with self.subTest(word=word):
                self.assertTrue(
                    checker.scan_text(line, entries_of(word)),
                    f"{word!r} が {line!r} に一致しない (Cf 除去後の再合成が無い)",
                )

    def test_absorbs_width_and_composition(self):
        # IME の全角、Finder / zip 由来の NFD、旧システムの半角カナ。どれも運用者の
        # 事故として現実に入る表現差で、素朴な部分一致は 3 形とも素通りする
        cases = [
            (WORD, f"{WORD_FW} project"),
            ("がぎぐ", unicodedata.normalize("NFD", "がぎぐ")),
            (WORD_KANA, WORD_KANA_HW),
            ("a b", "a　b"),
        ]
        for word, line in cases:
            with self.subTest(word=word):
                self.assertTrue(checker.scan_text(line, entries_of(word)))

    def test_absorbs_width_and_composition_from_the_list_side(self):
        # 逆向き。fold がリスト側にも掛かっていることを pin する。片側適用だと
        # 上のテストだけが緑になり、リストへ全角で書いた語が永久に当たらない
        cases = [
            (WORD_FW, f"{WORD} project"),
            (unicodedata.normalize("NFD", "がぎぐ"), "がぎぐ"),
        ]
        for word, line in cases:
            with self.subTest(word=word):
                self.assertTrue(checker.scan_text(line, entries_of(word)))

    def test_absorbs_invisible_format_characters(self):
        # Cf は NFKC が長さ 1 のまま残し、isspace() も False なので strip も落とさない。
        # PDF / Word 由来の SHY、Web ページ由来の ZWSP がこの形で入る
        for c in (ZWSP, SHY, BOM, "⁠"):
            with self.subTest(char=hex(ord(c))):
                self.assertTrue(checker.scan_text(f"zorb{c}latt", entries_of(WORD)))

    def test_does_not_absorb_spelling_variants(self):
        # 負の pin。ここが吸収する側へ動いたらこのテストが赤くなり、docstring の
        # 線引きの更新を強制する。ダッシュ異体とラテンのアクセントは NFKC が畳まず、
        # 畳むと 'ー' がかな文字と衝突する。綴りの異体は書き手がリストへ列挙する側
        self.assertEqual(checker.scan_text("foo–bar", entries_of("foo-bar")), [])
        self.assertEqual(checker.scan_text("josé", entries_of("jose")), [])
        # 対照として NFKC が吸収する側を並べ、境界を両側から挟む
        self.assertTrue(checker.scan_text("foo－bar", entries_of("foo-bar")))

    def test_does_not_match_across_lines(self):
        # 行単位の照合なので行を跨いだ語は原理的に当たらない。既知の限界として pin する
        self.assertEqual(checker.scan_text("zorb\nlatt", entries_of(WORD)), [])


class EnvironmentIsolation(unittest.TestCase):
    """プロセス内呼び出しが呼び出し元の git 環境を継承しないことを固定する。

    run-python-tests.py の child_env も GIT_* を落とすが、防御を 1 層に頼らない。
    直接 `python3 -m unittest` で回す開発時や、git hook から継承した環境ではその層が無い。
    """

    def test_the_process_environment_carries_the_isolated_git_vars(self):
        # 非空虚性を先に見るのは、GIT_ENV から GIT_* の追加が落ちると両辺が空になり
        # 比較が無条件に通るため。合格の観測値と、機構が働かなかったときの観測値が
        # 同じになる形をこの 1 行が分けている
        self.assertTrue(git_vars(GIT_ENV), "GIT_ENV が GIT_* を持たず pin が空虚")
        self.assertEqual(git_vars(GIT_ENV), git_vars(os.environ))


class LineNumbering(unittest.TestCase):
    """報告する行番号の数え方。

    語を出力しない設計では行番号が唯一の手がかりなので、git / grep / エディタと
    食い違うと「示された行を開いても何も無い」状態になり、運用者は誤検出と判断して
    その語をリストから外す。露出を防ぐ検査が防御を外させる方向へ働く。
    """

    def test_counts_only_newline_as_a_boundary(self):
        # str.splitlines() は \v \f \x1c-\x1e NEL U+2028 U+2029 と単独の \r も行境界に
        # するが、git と grep は \n だけを境界にする (先例 issue-id.py の _split_lines)
        for sep in ("\x0c", "\x85", " ", " ", "\x0b", "\r"):
            with self.subTest(sep=hex(ord(sep))):
                text = f"alpha\nbeta{sep}{WORD}\ngamma\n"
                hits = checker.scan_text(text, entries_of(WORD))
                self.assertEqual([lineno for lineno, _ in hits], [2])

    def test_crlf_does_not_shift_the_number(self):
        text = f"alpha\r\nbeta\r\n{WORD}\r\n"
        hits = checker.scan_text(text, entries_of(WORD))
        self.assertEqual([lineno for lineno, _ in hits], [3])

    def test_trailing_newline_does_not_add_a_line(self):
        self.assertEqual(len(checker.split_lines("a\nb\n")), 2)
        self.assertEqual(len(checker.split_lines("a\nb")), 2)


class DenylistParsing(unittest.TestCase):
    """禁止語リストの読み込み。

    リスト側の見えない差はどれも例外を出さず、エントリ数だけが静かに減るか、
    ゴミエントリが増える。仕様が「禁止語そのものを出力しない」と決めているため、
    出力を見てもどのエントリが死んでいるかは分からない。
    """

    def test_strips_bom_crlf_and_surrounding_space(self):
        # BOM 付き (Windows の Notepad / PowerShell 5 の Out-File の既定) を
        # encoding='utf-8' で読むと 1 行目だけが永久に当たらない。しかも 1 行目が
        # コメントなら '﻿#' が startswith('#') を外れてエントリへ昇格し、
        # 「リスト内の位置」が全部ずれる
        raw = f"{BOM}# comment\r\n{WORD} \r\nquuxcorp{NBSP}\r\n".encode("utf-8")
        entries = checker.parse_entries(raw)
        self.assertEqual([e.lineno for e in entries], [2, 3])
        self.assertTrue(checker.scan_text(f"the {WORD}", entries))
        self.assertTrue(checker.scan_text("a quuxcorp b", entries))

    def test_comment_detection_happens_after_strip(self):
        raw = f"   # indented comment\n{WORD}\n".encode("utf-8")
        self.assertEqual([e.lineno for e in checker.parse_entries(raw)], [2])

    def test_position_is_the_file_line_number_not_the_parse_index(self):
        # コメント行・空行・重複除去・sort のどれでもパース後の index はファイルの
        # 行番号からずれる。運用者はその番号を頼りにリストを開くので、指す先が
        # ずれると別のエントリを消す
        raw = f"# a\n# b\n# c\n\n{WORD}\n".encode("utf-8")
        entries = checker.parse_entries(raw)
        self.assertEqual([e.lineno for e in entries], [5])


class DenylistValidation(unittest.TestCase):
    """「ファイルはある」と「比較に使えるエントリが取れる」を別の検査として分ける。

    仕様の 3 分岐はファイルの存在までしか見ていない。存在するのにエントリが 0 件でも
    2 番目の分岐へ入って全ファイルを走査し、比較を 1 度も行わずに緑を返す。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_empty_and_comment_only_lists_are_unable(self):
        for label, body in (
            ("empty", ""),
            ("comment-only", "# nothing here\n"),
            ("blank-only", "   \n\t\n"),
        ):
            with self.subTest(label=label):
                path = self.dir / f"{label}.txt"
                path.write_text(body, encoding="utf-8")
                with self.assertRaises(checker.Unable):
                    checker.load_entries(path)

    def test_entry_that_folds_to_empty_is_unable(self):
        # エディタでは空行に見えるが strip も落とさない。素朴な実装ではこの行が
        # 全ファイル全行に一致し、原因のエントリを特定できない騒がしい赤になる
        path = self.dir / "zwsp.txt"
        path.write_text(f"{WORD}\n{ZWSP}\n", encoding="utf-8")
        with self.assertRaises(checker.Unable) as cm:
            checker.load_entries(path)
        self.assertIn("2", str(cm.exception))

    def test_non_lf_line_boundaries_are_unable(self):
        # 本文側とリスト側で同じ「\n だけを境界にする」規約の帰結が違う。リスト側では
        # エントリが結合して比較対象から消え、entries 非 0 / self_check 成功 / 検出 0 件
        # という最も健全に見える形で緑になる。self_check の canary は parse 後の raw から
        # 作るのでこの形を原理的に見られず、ここで止めるしかない
        for label, body in (
            ("cr-only", f"{WORD}\r{WORD_JA}\r"),
            ("line-separator", f"{WORD} {WORD_JA}\n"),
            ("after-comment", f"# note {WORD}\n"),
        ):
            with self.subTest(label=label):
                path = self.dir / f"{label}.txt"
                path.write_text(body, encoding="utf-8")
                with self.assertRaises(checker.Unable) as cm:
                    checker.load_entries(path)
                self.assertIn("1", str(cm.exception))
                # 語を出さないこと。座標だけで報告する
                self.assertNotIn(WORD, str(cm.exception))

    def test_lf_and_crlf_lists_still_load(self):
        # 上の負の対照。CRLF の \r は strip が落とすので行境界の検査に掛からない。
        # これが無いと「全部 Unable にする」実装でも上のテストが緑になる
        for label, body in (
            ("lf", f"{WORD}\n{WORD_JA}\n"),
            ("crlf", f"{WORD}\r\n{WORD_JA}\r\n"),
        ):
            with self.subTest(label=label):
                path = self.dir / f"{label}.txt"
                path.write_text(body, encoding="utf-8", newline="")
                entries = checker.load_entries(path)
                self.assertEqual([e.raw for e in entries], [WORD, WORD_JA])

    def test_non_utf8_list_is_unable_not_silently_partial(self):
        # UTF-16 保存を errors='replace' で読むとエントリ数は数えられるのに 1 件も
        # 当たらない。encoding 指定だけだと traceback で落ちる
        path = self.dir / "utf16.txt"
        path.write_bytes(f"{WORD}\n".encode("utf-16"))
        with self.assertRaises(checker.Unable):
            checker.load_entries(path)

    def test_valid_list_loads(self):
        path = denylist(self.dir / "ok.txt", WORD, WORD_JA)
        entries = checker.load_entries(path)
        self.assertEqual([e.lineno for e in entries], [1, 2])


class SelfCheck(unittest.TestCase):
    """リストが実際に効いているかを毎回の実行で確かめる実行時ガード。

    「エントリ数は非 0 なのに 1 件も当たらない」形は、エントリ数の要約まで正常に
    見えるので出力からは読めない。リストに書いた語をそのまま本文へ書いたら検出される
    ことを照合の前に確かめ、失敗したら検査不能で止める。
    """

    def test_passes_for_a_healthy_list(self):
        self.assertEqual(checker.self_check(entries_of(WORD, WORD_JA, WORD_SHARP_S)), [])

    def test_catches_an_entry_that_cannot_match_itself(self):
        # fold が壊れた状態を模す。entry の folded 側だけが本文と噛み合わない形を
        # 直接組み、自己照合が検出できることを見る
        broken = [checker.Entry(3, "zzz-never-appears", WORD)]
        self.assertEqual([e.lineno for e in checker.self_check(broken)], [3])

    def test_uses_the_raw_entry_not_the_folded_one(self):
        # canary は fold 前の語を本文へ埋める。fold 済みの語を埋めると「本文側に fold が
        # 掛かっていない」実装を検出できず、自明に通る空の検査になる。fold は冪等なので
        # self_check の戻り値では raw 側と folded 側を区別できない。canary が組む本文を
        # 直接見ないとこの pin は dead になる
        entry = checker.Entry(1, checker.fold(WORD_FW), WORD_FW)
        self.assertIn(WORD_FW, checker.canary_text(entry))
        self.assertEqual(checker.self_check([entry]), [])


class EnvBranching(unittest.TestCase):
    """置き場所の指定をどう受け取るか。

    3 分岐の表に無い値 (空文字列・空白だけ・末尾改行) と、指し先が通常ファイルでない
    形が、設計上の skip / 検査 / 停止のどれとも違う経路へ落ちる。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_unset_is_skip(self):
        self.assertIsNone(checker.resolve_denylist({}))

    def test_blank_shaped_values_are_unable(self):
        # 空文字列は .env の値なし行、`export VAR=$UNSET_VAR`、値を取るラッパの失敗で
        # 日常的に生じる。`if not os.environ.get(VAR)` 型は未設定と同じ skip へ落とし、
        # `Path('')` は PosixPath('.') になって exists() が True を返す
        for value in ("", " ", "\t", "x.txt\n"):
            with self.subTest(value=repr(value)):
                with self.assertRaises(checker.Unable) as cm:
                    checker.resolve_denylist({checker.ENV_VAR: value})
                # 後段の stat / S_ISREG も同じ値を検査不能へ倒すので、Unable が上がった
                # ことだけではこの分岐が生きているか分からない (実測: この判定を外しても
                # 4 形とも rc 2 のままだった)。原因が読めることがこの分岐の存在理由なので
                # 文言まで見る
                self.assertIn(checker.BLANK_VALUE_HINT, str(cm.exception))

    def test_non_regular_targets_are_unable(self):
        target_dir = self.dir / "adir"
        target_dir.mkdir()
        dir_link = self.dir / "dirlink"
        dir_link.symlink_to(target_dir)
        broken = self.dir / "broken"
        broken.symlink_to(self.dir / "nonexistent")
        missing = self.dir / "missing.txt"
        for label, path in (
            ("dir", target_dir),
            ("symlink-to-dir", dir_link),
            ("broken-symlink", broken),
            ("missing", missing),
        ):
            with self.subTest(label=label):
                with self.assertRaises(checker.Unable):
                    checker.resolve_denylist({checker.ENV_VAR: str(path)})

    def test_unreadable_file_is_unable(self):
        path = denylist(self.dir / "deny.txt", WORD)
        path.chmod(0o000)
        self.addCleanup(path.chmod, 0o644)
        if os.access(path, os.R_OK):
            self.skipTest("root で走っているので読み取り拒否を作れない")
        with self.assertRaises(checker.Unable):
            checker.load_entries(checker.resolve_denylist({checker.ENV_VAR: str(path)}))

    def test_regular_file_resolves(self):
        path = denylist(self.dir / "deny.txt", WORD)
        self.assertEqual(checker.resolve_denylist({checker.ENV_VAR: str(path)}), path)


class TrackedSurface(unittest.TestCase):
    """追跡ファイルの走査面。

    照合対象は worktree の中身ではなく index の blob にする。コミットされるのは blob で、
    worktree は symlink 追従・smudge filter・sparse-checkout の 3 経路で blob と食い違う。
    起動位置で走査集合が痩せる形も、0 件ガードでは捕まらない (0 件ではなく部分欠落)。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.repo = make_repo(self.dir / "repo")
        self.deny = denylist(self.dir / "deny.txt", WORD, WORD_JA)
        self.entries = checker.load_entries(self.deny)

    def scan(self, root: Path | None = None):
        return checker.scan_tracked(root or self.repo, self.entries)

    def test_reads_the_index_blob_not_the_worktree(self):
        # staged した後に worktree だけを直しても、コミットされるのは staged した方。
        # `pre-commit run --all-files` は unstaged をスタッシュしないので、この乖離は
        # 手動検証の経路でそのまま残る
        add(self.repo, "a.md", f"line with {WORD}\n")
        (self.repo / "a.md").write_text("clean\n", encoding="utf-8")
        report = self.scan()
        self.assertEqual(len(report.findings), 1)
        self.assertEqual(report.findings[0].lineno, 1)

    def test_scans_symlink_targets_as_the_blob(self):
        # 追跡 symlink の blob はリンク先のパス文字列。worktree を read_text で読むと
        # 実体 (リポジトリ外) を走査してしまい、コミットされる文字列を一度も見ない
        outside = self.dir / f"{WORD}-secret.txt"
        outside.write_text("harmless\n", encoding="utf-8")
        (self.repo / "link.md").symlink_to(outside)
        git(self.repo, "add", "--", "link.md")
        report = self.scan()
        self.assertEqual(len(report.findings), 1, "リンク先パス文字列が照合されていない")

    def test_counts_do_not_shrink_when_started_from_a_subdirectory(self):
        # git ls-files は cwd 相対。サブディレクトリから起動すると配下しか返さず、
        # 「走査 1 件 / 読めずに飛ばした 0 件」という完全に健全な形で緑になる
        add(self.repo, "top.md", "harmless\n")
        add(self.repo, "sub/deep/inner.md", f"has {WORD}\n")
        from_root = self.scan()
        from_sub = checker.scan_tracked(self.repo / "sub" / "deep", self.entries)
        self.assertEqual(from_sub.scanned, from_root.scanned)
        self.assertEqual(len(from_sub.findings), len(from_root.findings))

    def test_non_ascii_paths_are_not_lost_to_c_quoting(self):
        # git ls-files の既定出力は非 ASCII パスを C クォートする。このリポジトリは
        # 追跡ファイルの 4 割強が非 ASCII パスなので、-z を外すとその分が壊れる。
        #
        # 対照には非 ASCII の禁止語をパスへ置く。C クォートは非 ASCII バイトだけを
        # エスケープして ASCII 部分をそのまま残すので、ASCII の語だけを対照にすると
        # -z を外しても当たってしまい何も pin できない (実測: -z を外す変異が緑で通った)
        add(self.repo, f"docs/{WORD_JA}/メモ.md", "harmless content\n")
        report = self.scan()
        self.assertEqual(report.scanned, 1)
        self.assertEqual(len(report.findings), 1)
        self.assertTrue(report.findings[0].is_path)

    def test_matches_the_path_itself(self):
        # ディレクトリ名へ固有名詞が入る経路は実在する (Issue ディレクトリ名は
        # 日本語タイトルを含む)。内容だけを走査すると素通りする
        add(self.repo, f"docs/{WORD}/notes.md", "harmless content\n")
        report = self.scan()
        self.assertEqual(len(report.findings), 1)
        self.assertTrue(report.findings[0].is_path)

    def test_path_is_folded_before_matching(self):
        # 上の fixture は fold(x) == x の語しか使わないので、scan_path の fold を外しても
        # 当たってしまい何も pin しない (実測: folded = fold(path) を folded = path に
        # 変えても全件緑)。全角は Issue ディレクトリ名に実際に入る形
        add(self.repo, f"docs/{WORD_FW}/notes.md", "harmless content\n")
        report = self.scan()
        self.assertEqual([f.is_path for f in report.findings], [True])

    def test_oversize_blobs_are_excluded_without_being_read(self):
        # read_text は「decode できないから安全に飛ばす」ように見えて、例外が上がる前に
        # ファイル全体を読み切っている。ホストには cgroup 境界が無い
        big = b"x" * (checker.MAX_BLOB_BYTES + 1)
        add(self.repo, "big.bin", big)
        add(self.repo, "small.md", f"has {WORD}\n")
        report = self.scan()
        self.assertEqual(report.oversize, 1)
        self.assertEqual(report.scanned, 1)
        self.assertEqual(len(report.findings), 1)
        # 上の 3 件は「全部読んでから捨てる」実装でも同じ値になるので、名前の後半
        # (without being read) を検査していない。読む関数自身が上限を知っていることを
        # 関数境界で見る。呼び出し側の選別を外す変更はここで止まる
        # decode を省くと oid が bytes のまま f-string へ入り、git が解釈できずに
        # 別の Unable が上がる。assertRaises は通るが上限の検査には一度も到達しない
        oid = git(self.repo, "rev-parse", ":big.bin").stdout.decode().strip()
        with self.assertRaises(checker.Unable):
            list(checker._iter_blobs(self.repo, [oid], [len(big)]))

    def test_a_short_blob_read_is_unable_not_a_partial_green(self):
        # zip は短い方で黙って止まるので、読み出しが 1 件足りないと「一部だけ走査して
        # 違反なし」に化ける。逐次消費では len() が取れず、数え落としが見えない。
        # 実際に短い読み出しを起こすには読み出し側を差し替えるしかない
        add(self.repo, "a.md", f"has {WORD}\n")
        add(self.repo, "b.md", "harmless\n")
        real = checker._read_chunk
        with mock.patch.object(
            checker, "_read_chunk", lambda root, oids: real(root, oids)[:-1]
        ):
            with self.assertRaises(checker.Unable):
                self.scan()

    def test_binary_blobs_are_counted_separately(self):
        add(self.repo, "image.bin", b"\x89PNG\x00\x01\x02")
        add(self.repo, "text.md", "harmless\n")
        report = self.scan()
        self.assertEqual(report.binary, 1)
        self.assertEqual(report.scanned, 1)

    def test_utf16_text_blob_is_scanned_not_counted_as_binary(self):
        # UTF-16 は ASCII 域の文字ごとに NUL を持つので、NUL を先に見る実装では丸ごと
        # 未走査になる。binary は増えるが findings は 0 件で、要約も緑も正常に見える。
        # Windows のエディタが書く形なので CP932 と同じく現実の混入経路
        add(self.repo, "notes.txt", f"{WORD} project\n".encode("utf-16"))
        report = self.scan()
        self.assertEqual(report.binary, 0)
        self.assertEqual(report.scanned, 1)
        self.assertEqual([(f.lineno, f.entry_lineno) for f in report.findings], [(1, 1)])

    def test_utf16_without_bom_stays_binary(self):
        # 上の負の対照。BOM 無しから byte order を当てる形は採らない (BE は「成功」した
        # うえで中身が化ける)。拾えない範囲は docstring が宣言しており、binary の
        # 件数として要約に出る。これが無いと「全部走査する」実装でも上のテストが緑になる
        add(self.repo, "notes.txt", f"{WORD} project\n".encode("utf-16-le"))
        add(self.repo, "text.md", "harmless\n")
        report = self.scan()
        self.assertEqual(report.binary, 1)
        self.assertEqual(report.findings, [])

    def test_undecodable_text_blob_is_unable(self):
        # 非 ASCII の禁止語を運ぶ可能性が最も高いファイル形式 (CP932 のテキスト) が
        # そのまま最も検査されない形式になる。unreadable に数えて緑を返す実装は
        # 「唯一の保有者が CP932 だった」ケースを取りこぼす
        add(self.repo, "cp932.txt", WORD_JA.encode("cp932"))
        with self.assertRaises(checker.Unable) as cm:
            self.scan()
        # 序数だけだと一時 index で別のファイルを指す。検出側と同じ位置指標を使うこと
        # (序数だけへ戻す変異はこの assert が無いと緑で通る)
        self.assertRegex(str(cm.exception), r"tracked file \d+ \(oid [0-9a-f]{12}\)")

    def test_empty_tracked_set_is_unable(self):
        with self.assertRaises(checker.Unable):
            self.scan()

    def test_merge_conflict_index_is_unable(self):
        # merge conflict 中の index では `git ls-files -s` が同じパスを stage 1/2/3 で
        # 3 回返す。件数も照合結果も実態とずれるので、競合の解決前は止める。
        # 競合中でもコミットはできないが、`pre-commit run --all-files` は手で回せる
        add(self.repo, "base.md", "base\n")
        git(self.repo, "commit", "-q", "-m", "base")
        git(self.repo, "checkout", "-q", "-b", "side")
        add(self.repo, "conflict.md", f"side has {WORD}\n")
        git(self.repo, "commit", "-q", "-m", "side")
        git(self.repo, "checkout", "-q", "main")
        add(self.repo, "conflict.md", "main is harmless\n")
        git(self.repo, "commit", "-q", "-m", "main")
        merge = subprocess.run(
            ["git", "-C", str(self.repo), "merge", "side"],
            capture_output=True,
            env=GIT_ENV,
        )
        self.assertNotEqual(merge.returncode, 0, "競合が起きていない (fixture の前提が崩れた)")
        with self.assertRaises(checker.Unable):
            self.scan()

    def test_non_repository_root_is_unable(self):
        plain = self.dir / "notrepo"
        plain.mkdir()
        with self.assertRaises(checker.Unable):
            checker.scan_tracked(plain, self.entries)


class CommitMessageSurface(unittest.TestCase):
    """コミットメッセージ入口の走査面。

    渡るのは git の cleanup より前の message ファイル全文で、コメント行も
    `git commit -v` の diff も含まれる。ここで素朴に `#` 行を落とすと、本リポジトリの
    日常経路 (`-F`) でちょうど穴が開く: 既定の cleanup は編集経由が strip、-m / -F は
    whitespace なので、同じ本文でも `#` 行の運命が逆になる。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.deny = denylist(self.dir / "deny.txt", WORD)

    def run_message(self, body: str):
        """メッセージ入口を production の経路で呼ぶ。

        checker.scan_text を直接呼ぶと run_check_text の走査面を一切 pin しない。
        コメント行の除去や scissors 切りは入口の側に足されるものなので、そこを
        通さないテストは剥がす向きの変更を緑のまま通す (実測: 両方の変異が通った)。
        """
        path = self.dir / "COMMIT_EDITMSG"
        path.write_text(body, encoding="utf-8")
        return run_cli("--check-text", str(path), env={checker.ENV_VAR: str(self.deny)})

    def test_hash_prefixed_lines_are_scanned(self):
        # 日本語本文に Markdown 見出しを書けば `#` 始まりの行はごく自然に発生し、
        # -F 経由では commit object に残る (git の実測)
        rc, out = self.run_message(f"feat: x\n\n# {WORD} の見出し\n")
        self.assertEqual(rc, 1)
        self.assertNotIn(WORD, out)

    def test_lines_below_the_scissors_are_scanned(self):
        # 本文中に scissors 行を含む message を -F で渡すと、--cleanup=scissors を
        # 明示しても切り落とされずに commit object へ残る。切り落とす実装は
        # scissors 行より下に置かれた禁止語を静かに見逃す
        scissors = "# ------------------------ >8 ------------------------"
        rc, out = self.run_message(f"feat: x\n\n{scissors}\n{WORD}\n")
        self.assertEqual(rc, 1)
        self.assertNotIn(WORD, out)

    def test_undecodable_message_is_unable(self):
        # 追跡ファイル側の test_undecodable_text_blob_is_unable の対称形。読めなかった
        # ときの終了コードを見ないと、`return EXIT_OK` へ潰す変更が緑で通る
        path = self.dir / "COMMIT_EDITMSG"
        path.write_bytes(WORD_JA.encode("cp932"))
        rc, out = run_cli(
            "--check-text", str(path), env={checker.ENV_VAR: str(self.deny)}
        )
        self.assertEqual(rc, 2)
        self.assertNotIn(WORD_JA, out)

    def test_missing_message_file_is_unable(self):
        rc, out = run_cli(
            "--check-text",
            str(self.dir / "nonexistent"),
            env={checker.ENV_VAR: str(self.deny)},
        )
        self.assertEqual(rc, 2)
        self.assertNotIn("nonexistent", out, "読めなかった対象のパスが出力に漏れている")

    def test_a_clean_message_passes(self):
        # 上の 2 つは「剥がさない」方向の pin なので、剥がさないことが誤検出を
        # 増やしていないかを対照で見る。テンプレート相当のコメント行だけなら緑
        rc, _ = self.run_message(
            "feat: 何かを足す\n\n# Please enter the commit message\n# Changes:\n#   modified: a.md\n"
        )
        self.assertEqual(rc, 0)


class Redaction(unittest.TestCase):
    """出力の非表示規則。

    語は「決して出ない」が唯一守るべき性質。出力の宛先は端末だけではなく、tmux や
    script のログ、Issue や PR への貼り付け、CI ログ、エージェント経由なら会話ログにも
    残る。座標はローカルで安価に解決できるので、語より座標を出す。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.repo = make_repo(self.dir / "repo")

    def assertNoSecrets(self, out: str, *extra: str):
        for word in (*FICTIONAL, *extra):
            self.assertNotIn(word.lower(), out.lower(), f"出力に {word!r} が漏れている")

    def test_missing_list_reports_errno_without_the_path(self):
        # リストは PUBLIC に書けないから外に置く。その置き場所のパス自体がユーザー名と
        # 私的プロジェクト名を含みがちで、「リストが無い」という最もありふれた設定ミスの
        # たびに露出する。exit code も 1 だと「違反あり」と区別が付かない
        marker = "zz-private-marker"
        target = self.dir / marker / "deny.txt"
        add(self.repo, "a.md", "harmless\n")
        rc, out = run_cli(
            "--check", env={checker.ENV_VAR: str(target)}, cwd=self.repo
        )
        self.assertEqual(rc, 2)
        self.assertNotIn(marker, out)
        self.assertIn("errno", out.lower())
        self.assertNotIn("Traceback", out)

    def test_no_traceback_escapes_to_the_output(self):
        # top-level で例外を受けないと、pre-commit の Failed ブロックへ traceback が
        # 丸ごと出る。pre-commit は hook の stdout+stderr を切り詰めずに表示する
        target = self.dir / "adir"
        target.mkdir()
        add(self.repo, "a.md", "harmless\n")
        rc, out = run_cli("--check", env={checker.ENV_VAR: str(target)}, cwd=self.repo)
        self.assertEqual(rc, 2)
        self.assertNotIn("Traceback", out)

    def test_findings_carry_coordinates_only(self):
        deny = denylist(self.dir / "deny.txt", WORD)
        add(self.repo, "docs/a.md", f"the {WORD} appears here\n")
        rc, out = run_cli("--check", env={checker.ENV_VAR: str(deny)}, cwd=self.repo)
        self.assertEqual(rc, 1)
        self.assertNoSecrets(out)
        self.assertNotIn("the ", out)
        self.assertRegex(out, r"docs/a\.md:1: denylist line 1")
        # 列オフセットや一致長を添えると 1 件で語が確定する。付いていないことを pin する
        self.assertNotRegex(out, r":\d+:\d+")

    def test_path_findings_do_not_print_the_path(self):
        # パスも照合対象なので、パス由来の検出でパスを印字すると出力が語そのものになる
        deny = denylist(self.dir / "deny.txt", WORD)
        # 本文にも語を入れる。内容が harmless だとパス由来の finding しか生まれず、
        # 内容由来の finding が汚染パス判定を通る分岐が一度も実行されない。その分岐だけを
        # パス印字へ戻す変異は assertNoSecrets を素通りする (実測)
        add(self.repo, f"docs/{WORD}/notes.md", f"has {WORD}\n")
        rc, out = run_cli("--check", env={checker.ENV_VAR: str(deny)}, cwd=self.repo)
        self.assertEqual(rc, 1)
        self.assertNoSecrets(out)
        # パス由来 1 件 + 内容由来 1 件の両方が位置指標になること
        self.assertEqual(len(re.findall(r"tracked file 1 \(oid [0-9a-f]{12}\)", out)), 2)
        # 序数は走査した index に対するものなので oid を併記する。git は commit -a /
        # commit -- <pathspec> のとき hook へ一時 index を渡し、そこでの序数は運用者が
        # 後から引く `git ls-files` とずれる (実測)。oid はどの index からでも引ける
        self.assertRegex(out, r"tracked file \d+ \(oid [0-9a-f]{12}\): denylist line \d+")
        self.assertIn("index=default", out)

    def test_index_kind_is_default_when_the_variable_points_at_the_real_index(self):
        # 変数の有無で分けると、as-is の `git commit` が hook へ渡す `.git/index` まで
        # temporary になる (実測)。毎回 temporary が出るなら序数ずれの手がかりにならない。
        # 有無ではなく値で分けていることをこちら側から pin する
        deny = denylist(self.dir / "deny.txt", WORD)
        add(self.repo, "a.md", "harmless\n")
        rc, out = run_cli(
            "--check",
            env={checker.ENV_VAR: str(deny), "GIT_INDEX_FILE": ".git/index"},
            cwd=self.repo,
        )
        self.assertEqual(rc, 0)
        self.assertIn("index=default", out)

    def test_index_kind_is_temporary_for_a_git_written_temporary_index(self):
        # git が `commit -- <pathspec>` で使う形。実 index とエントリ集合が違うので、
        # そこでの序数は運用者が後から引く `git ls-files` とずれる
        deny = denylist(self.dir / "deny.txt", WORD)
        add(self.repo, "a.md", "harmless\n")
        tmp_index = self.repo / ".git" / "next-index-1.lock"
        tmp_index.write_bytes((self.repo / ".git" / "index").read_bytes())
        rc, out = run_cli(
            "--check",
            env={checker.ENV_VAR: str(deny), "GIT_INDEX_FILE": str(tmp_index)},
            cwd=self.repo,
        )
        self.assertEqual(rc, 0)
        self.assertIn("index=temporary", out)

    def test_entry_position_is_the_list_file_line_number(self):
        # コメント 3 行 + 空行 1 行の後に語を置く。パース後の index なら 1 行目を指す
        deny = self.dir / "deny.txt"
        deny.write_text(f"# a\n# b\n# c\n\n{WORD}\n", encoding="utf-8")
        add(self.repo, "a.md", f"{WORD}\n")
        rc, out = run_cli("--check", env={checker.ENV_VAR: str(deny)}, cwd=self.repo)
        self.assertEqual(rc, 1)
        self.assertIn("denylist line 5", out)

    def test_check_text_does_not_print_the_source_path(self):
        # source は禁止語と照合されないので、message ファイルのパスに語が入ればそのまま
        # 出力になる。さらに linked worktree からのコミットでは git が commit-msg hook へ
        # `<main>/.git/worktrees/<name>/COMMIT_EDITMSG` という絶対パスを渡す (実測)。
        # その先頭はホームディレクトリなので、層 1 が守るユーザー名が Failed ブロックへ出る
        deny = denylist(self.dir / "deny.txt", WORD)
        msgdir = self.dir / f"{WORD}-branch"
        msgdir.mkdir()
        msg = msgdir / "COMMIT_EDITMSG"
        msg.write_text(f"feat: x\n\n{WORD}\n", encoding="utf-8")
        rc, out = run_cli("--check-text", str(msg), env={checker.ENV_VAR: str(deny)})
        self.assertEqual(rc, 1)
        self.assertNoSecrets(out)
        self.assertNotIn(str(self.dir), out, "走査対象のパスが出力に漏れている")
        self.assertRegex(out, r"line \d+: denylist line \d+")

    def test_extra_arguments_are_not_echoed(self):
        # argparse の `unrecognized arguments` は argv をそのまま出す。hook から
        # pass_filenames: false が落ちると追跡パスが引数で渡り、汚染パスの置き換えが
        # 隠すはずのパス (= 語そのもの) が Failed ブロックへ並ぶ (実測)。配線に依存しない
        # 防御として、スクリプト側でも件数だけを報告して止める
        deny = denylist(self.dir / "deny.txt", WORD)
        add(self.repo, f"docs/{WORD}/notes.md", "harmless\n")
        rc, out = run_cli(
            "--check",
            f"docs/{WORD}/notes.md",
            "a.md",
            env={checker.ENV_VAR: str(deny)},
            cwd=self.repo,
        )
        self.assertEqual(rc, 2)
        self.assertNoSecrets(out)
        self.assertIn("2 件", out)

    def test_output_never_uses_the_github_number_notation(self):
        # このリポジトリは #N を GitHub の番号空間を指す記法として機械検査で禁じている。
        # 出力をコミットメッセージや Issue へ貼ると記法検査が違反として弾くので、
        # 座標にも検査不能の文言にもこの形を使わない
        deny = denylist(self.dir / "deny.txt", WORD)
        add(self.repo, f"docs/{WORD}/notes.md", f"has {WORD}\n")
        outputs = []
        outputs.append(run_cli("--check", env={checker.ENV_VAR: str(deny)}, cwd=self.repo)[1])
        add(self.repo, "cp932.txt", WORD_JA.encode("cp932"))
        outputs.append(run_cli("--check", env={checker.ENV_VAR: str(deny)}, cwd=self.repo)[1])
        for out in outputs:
            self.assertNotRegex(out, r"#\d", f"出力に GitHub の数字記法が混ざっている: {out!r}")

    def test_unexpected_exception_prints_only_the_type_name(self):
        # top-level の受けは正常系から到達しないので、到達性を注入で作る。例外の str は
        # リストのパスも語も載せることがあり、pre-commit は Failed ブロックへ hook の
        # 出力を切り詰めずに出す。型名だけを印字することを見る (Traceback の不在だけでは
        # 「str を 1 行で印字する」形が素通りする)
        marker = "zz-private-marker"
        original = checker.resolve_denylist

        def boom(env):
            raise RuntimeError(f"secret path {marker}")

        checker.resolve_denylist = boom
        self.addCleanup(setattr, checker, "resolve_denylist", original)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(io.StringIO()):
            rc = checker.main(["--check"], env={})
        out = buf.getvalue()
        self.assertEqual(rc, checker.EXIT_UNABLE)
        self.assertNotIn(marker, out)
        self.assertNotIn("Traceback", out)
        self.assertIn("RuntimeError", out)

    def test_main_stops_when_self_check_fails(self):
        # self_check は「静かな緑を騒がしい赤へ変える」ためだけに置いた実行時ガードなので、
        # main への取り付けが外れると存在意義が丸ごと消える。正しい fold の下では自己照合が
        # 失敗するエントリをリストから作れない (BMP 全域を走査して 0 件) ため、機構本体を
        # 壊す変異では取り付けの欠落を捕まえられない。到達性は注入で作る
        deny = denylist(self.dir / "deny.txt", WORD)
        add(self.repo, "a.md", "harmless\n")
        original = checker.self_check
        checker.self_check = lambda entries: list(entries)
        self.addCleanup(setattr, checker, "self_check", original)
        buf = io.StringIO()
        cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, cwd)
        with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(io.StringIO()):
            rc = checker.main(["--check"], env={checker.ENV_VAR: str(deny)})
        out = buf.getvalue()
        self.assertEqual(rc, checker.EXIT_UNABLE)
        self.assertIn("自己照合に失敗", out)
        self.assertNoSecrets(out)

    def test_unable_paths_never_echo_the_words(self):
        # 検査不能の各経路で語が出ないこと。自己照合の失敗はエントリの内容に触るので
        # 特に漏れやすい
        deny = self.dir / "deny.txt"
        deny.write_text(f"{WORD}\n{ZWSP}\n", encoding="utf-8")
        add(self.repo, "a.md", "harmless\n")
        rc, out = run_cli("--check", env={checker.ENV_VAR: str(deny)}, cwd=self.repo)
        self.assertEqual(rc, 2)
        self.assertNoSecrets(out)


class Reporting(unittest.TestCase):
    """skip と「検査して 0 件」を出力で区別できるようにする。

    pre-commit は rc 0 の hook の stdout も stderr も表示しないので、層 2 が
    「未設定なので skip」と印字しても運用者には届かない。表示は hook 側の
    verbose: true に頼ることになり、表示されたときに 2 つが別物だと読めることが要る。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.repo = make_repo(self.dir / "repo")
        add(self.repo, "a.md", "harmless\n")

    def test_unset_environment_skips_with_a_machine_readable_status(self):
        rc, out = run_cli("--check", cwd=self.repo)
        self.assertEqual(rc, 0)
        self.assertIn(checker.STATUS_SKIPPED, out)
        self.assertNotIn(checker.STATUS_CHECKED, out)

    def test_checked_run_reports_what_it_actually_looked_at(self):
        deny = denylist(self.dir / "deny.txt", WORD)
        rc, out = run_cli("--check", env={checker.ENV_VAR: str(deny)}, cwd=self.repo)
        self.assertEqual(rc, 0)
        self.assertIn(checker.STATUS_CHECKED, out)
        self.assertIn("scanned=1", out)
        self.assertIn("entries=1", out)

    def test_skip_and_checked_outputs_differ(self):
        deny = denylist(self.dir / "deny.txt", WORD)
        _, skipped = run_cli("--check", cwd=self.repo)
        _, checked = run_cli("--check", env={checker.ENV_VAR: str(deny)}, cwd=self.repo)
        self.assertNotEqual(skipped, checked)

    def test_check_text_entry_point(self):
        deny = denylist(self.dir / "deny.txt", WORD)
        msg = self.dir / "COMMIT_EDITMSG"
        msg.write_text(f"feat: x\n\n{WORD}\n", encoding="utf-8")
        rc, out = run_cli(
            "--check-text", str(msg), env={checker.ENV_VAR: str(deny)}, cwd=self.repo
        )
        self.assertEqual(rc, 1)
        self.assertIn("denylist line 1", out)
        self.assertNotIn(WORD, out)

    def test_check_text_without_a_path_is_not_a_silent_pass(self):
        # commit-msg stage でファイル名フィルタが集合を空にすると、pass_filenames が
        # 効いていても引数ゼロで起動する。argparse の必須値にしておけば exit 2 になり、
        # 静かには素通りしない
        rc, _ = run_cli("--check-text", cwd=self.repo)
        self.assertEqual(rc, 2)

    def test_abbreviated_flags_are_rejected(self):
        # allow_abbrev の既定は `--check-t` を `--check-text` の短縮として受理する。
        # 受理されると環境変数未設定の skip へ落ちて rc 0 になるので、拒否と区別できる。
        #
        # `--che` を対照にしてはいけない。--check と --check-text の両方の接頭辞なので
        # allow_abbrev の有無に関わらず ambiguous エラーで rc 2 になり、何も pin しない
        # (実測: allow_abbrev を外す変異が緑で通った)
        msg = self.dir / "m.txt"
        msg.write_text("harmless\n", encoding="utf-8")
        rc, _ = run_cli("--check-t", str(msg), cwd=self.repo)
        self.assertEqual(rc, 2)


class Attachment(unittest.TestCase):
    """検査機構の取り付けを pin する。機構そのものではなく「呼ばれていること」を見る。

    機構のテストが全部緑でも、pre-commit から呼ばれていなければ一度も走らない。
    commit-msg stage は特に、配線 1 行で黙って skip になる形を複数持つ。
    """

    @staticmethod
    def live_lines(path: Path) -> list[str]:
        return [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        ]

    @classmethod
    def invocations(cls, lines: list[str], flag: str) -> list[str]:
        # flag は split() で照合する。部分文字列だと --check が --check-text にも
        # 一致し、片方の hook を消しても両方の pin が緑のままになる
        return [line for line in lines if CHECKER in line and flag in line.split()]

    @classmethod
    def hook_block(cls, lines: list[str], flag: str) -> list[str]:
        hits = [i for i, line in enumerate(lines) if CHECKER in line and flag in line.split()]
        if not hits:
            return []
        start = hits[0]
        hook_start = re.compile(r"^\s*-\s+id:")
        while start > 0 and not hook_start.match(lines[start]):
            start -= 1
        end = start + 1
        while end < len(lines) and not hook_start.match(lines[end]):
            end += 1
        return lines[start:end]

    @staticmethod
    def hook_keys(block: list[str]) -> set[str]:
        """hook 定義ブロックが持つマッピングのキー。

        入れ子のマッピングも同じ形なので拾う。取りこぼす方向ではなく余計に拾う方向へ
        倒してあるのは、allowlist と突き合わせる用途だから (知らないキーは赤にする)。
        """
        key = re.compile(r"^\s*(?:-\s+)?([A-Za-z_][A-Za-z0-9_-]*):")
        return {m.group(1) for line in block if (m := key.match(line))}

    def test_checker_path_exists(self):
        # 取り付けを探す文字列が実在しないパスへ drift すると dead pin になる
        self.assertTrue((ROOT / CHECKER).is_file(), f"{CHECKER} が無い")

    def test_pre_commit_runs_the_tracked_file_check(self):
        self.assertTrue(
            self.invocations(self.live_lines(PRE_COMMIT_CONFIG), "--check"),
            "pre-commit が --check を呼んでいない",
        )

    def test_pre_commit_runs_the_commit_message_check(self):
        self.assertTrue(
            self.invocations(self.live_lines(PRE_COMMIT_CONFIG), "--check-text"),
            "pre-commit が --check-text を呼んでいない",
        )

    def test_commit_message_hook_is_bound_to_the_commit_msg_stage(self):
        block = self.hook_block(self.live_lines(PRE_COMMIT_CONFIG), "--check-text")
        self.assertTrue(block, "--check-text の hook 定義が見つからない")
        self.assertTrue(
            [l for l in block if l.lstrip().startswith("stages:") and "commit-msg" in l],
            "--check-text の hook が commit-msg stage に紐付いていない",
        )

    def test_both_hooks_always_run(self):
        # commit-msg stage では渡るファイルが message ファイル 1 本しかないので、
        # ファイル名で絞る指定は「絞る」ではなく「常に skip」になる。pre-commit stage 側も
        # 走査対象を追跡ファイル全体で固定するために絞らない
        for flag in ("--check", "--check-text"):
            with self.subTest(flag=flag):
                block = self.hook_block(self.live_lines(PRE_COMMIT_CONFIG), flag)
                self.assertTrue(block, f"{flag} の hook 定義が見つからない")
                self.assertTrue(
                    [l for l in block if "always_run: true" in l],
                    f"{flag} の hook に always_run: true が無い",
                )

    def test_both_hooks_are_verbose(self):
        # pre-commit は rc 0 の hook の出力を捨てるので、verbose: true が無いと
        # 「設定し忘れて skip した」と「検査して 0 件だった」が端末上で同じ 1 行になる
        for flag in ("--check", "--check-text"):
            with self.subTest(flag=flag):
                block = self.hook_block(self.live_lines(PRE_COMMIT_CONFIG), flag)
                self.assertTrue(block, f"{flag} の hook 定義が見つからない")
                self.assertTrue(
                    [l for l in block if "verbose: true" in l],
                    f"{flag} の hook に verbose: true が無い。skip 通知が誰の目にも入らない",
                )

    def test_tracked_file_hook_runs_on_the_pre_commit_stage(self):
        # `stages: [manual]` を 1 行足すと、この hook は commit 時にも
        # `pre-commit run --all-files` にも現れないまま追跡ファイル面が消える。
        # Skipped の表示すら出ないので、出力を見比べても異常に見えない (実測)
        # この hook は stages を宣言しないので top-level の default_stages を継承する。
        # hook ブロック内だけを見る形は、宣言が無いとループが一度も回らず空虚に緑になり、
        # 26 行目を [manual] へ変える 1 行で全 stage から消えても捕まらない (実測)
        lines = self.live_lines(PRE_COMMIT_CONFIG)
        block = self.hook_block(lines, "--check")
        self.assertTrue(block, "--check の hook 定義が見つからない")
        own = [l for l in block if l.lstrip().startswith("stages:")]
        effective = own or [l for l in lines if re.match(r"^default_stages:", l)]
        self.assertTrue(effective, "--check の stage を決める宣言がどこにも無い")
        for line in effective:
            self.assertIn(
                "pre-commit", line, "--check の hook が pre-commit stage から外れている"
            )

    def test_tracked_file_hook_does_not_pass_filenames(self):
        # 落ちると always_run のまま追跡パスが引数で渡り、argparse のエラーが argv を
        # そのまま印字する。汚染パスの置き換えが隠すはずのパス (= 語そのもの) が
        # Failed ブロックへ並ぶ (実測)。スクリプト側の parse_known_args と 2 層で塞ぐ
        block = self.hook_block(self.live_lines(PRE_COMMIT_CONFIG), "--check")
        self.assertTrue(block, "--check の hook 定義が見つからない")
        self.assertTrue(
            [l for l in block if "pass_filenames: false" in l],
            "--check の hook に pass_filenames: false が無い",
        )

    def test_hook_blocks_have_no_unvetted_keys(self):
        # 個別の narrowing キーを列挙して禁じる形は採らない。絞り込みの手段は列挙し切れず、
        # pre-commit が新しいキーを足せば列挙の外から同じ穴が開く。許可する側を pin して、
        # 知らないキーが増えたら赤にする (先例は scripts/test_issue_id_attachment.py)
        for flag, allowed in (
            ("--check", TRACKED_HOOK_KEYS),
            ("--check-text", COMMIT_MSG_HOOK_KEYS),
        ):
            with self.subTest(flag=flag):
                block = self.hook_block(self.live_lines(PRE_COMMIT_CONFIG), flag)
                self.assertTrue(block, f"{flag} の hook 定義が見つからない")
                unknown = sorted(self.hook_keys(block) - allowed)
                self.assertFalse(
                    unknown,
                    f"{flag} の hook に未検討のキーがある: {unknown}。"
                    "silent skip を招かないことを確かめてから許可集合へ足す",
                )

    def test_ci_does_not_run_this_check(self):
        # PUBLIC リポジトリの Actions ログは誰でも読める。検出座標を公開ログへ出すと、
        # 対象のコミットは push 済みなので座標の交差から語を復元できる。
        # 取り付けない決定を散文だけでなく negative pin として置く。
        #
        # 1 ファイルだけを見ると、別の workflow ファイルから呼ぶ取り付けが射程の外で
        # 素通りする。走査した件数が 0 でないことも併せて見る (0 件で緑になる形を作らない)
        workflow_dir = ROOT / ".github" / "workflows"
        workflows = sorted(workflow_dir.glob("*.yml")) + sorted(workflow_dir.glob("*.yaml"))
        self.assertTrue(workflows, "workflow が 1 件も無い (negative pin が 0 件で緑になる)")
        for wf in workflows:
            with self.subTest(workflow=wf.name):
                self.assertFalse(
                    [line for line in self.live_lines(wf) if CHECKER in line],
                    f"{wf.name} がこの検査を呼んでいる。検出座標が公開ログへ残る",
                )


if __name__ == "__main__":
    unittest.main()
