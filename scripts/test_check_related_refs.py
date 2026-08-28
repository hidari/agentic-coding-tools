#!/usr/bin/env python3
"""check-related-refs.py の検査機構と、その取り付けを検証する。

陽性対照は実際の issue.md と同じ構造上の位置へ置く。`## 関連` 節を単独の 1 行として
渡すと、節の切り出しが「見出しから次の h1/h2 まで」ではなく別経路で成立してしまい、
節の境界を壊す変異を検出できない (同種の取りこぼしを ISSUE-24 の実測で 1 度踏んでいる)。

免除の検査は「免除されること」と「免除が広がりすぎていないこと」を対で置く。免除は
届きすぎる方向へ広がっても違反 0 件の緑にしかならず、出力を見ても気づけない。

CommandLine ではなく ExitCodes が checker を subprocess で起動して終了コードを pin する。
関数を直接呼ぶテストだけだと、違反ありで `return 0` にする 1 行の変異が全緑のまま生き残る
(隔離コピーで実測)。その 1 行で pre-commit も CI も永久に緑になるので、rc の写像は別に固定する。
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CHECKER = "scripts/check-related-refs.py"
PRE_COMMIT_CONFIG = ROOT / ".pre-commit-config.yaml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

# 数字記法を fixture として持つために組み立てる。issue-id.py --check は追跡ファイル全体を
# 走査するので、`#<数字>` を literal で書くとこのファイル自身が記法違反として報告される
# (実測: この定数を入れる前は 3 件で赤くなった)。免除される `PR #<N>` 形へ寄せ替えると、
# 免除される側しか対照に置けなくなる
HASH = "#"

# 実行環境の global / system 設定を読ませない。同じ理由と形を
# plugins/dev-workflow/skills/in-repo-issue/scripts/test_issue_id.py が持つ。
# この検査に固有の事情がもう 1 つあり、global の core.excludesfile が `*.md` を ignore して
# いると `git ls-files` の走査集合だけが縮む。実在判定はファイルシステムを見るので縮まず、
# 「違反 0 件」を主張するテストが小さくなった標本で通る (実測)
# os.environ をそのまま流さず GIT_* を落としてから足し直す。fixture は tempdir で
# `git add` を走らせるが、GIT_INDEX_FILE を継承すると書き込み先がその指し先になり、
# 呼び出し元のリポジトリの index を fixture の内容で上書きする (実測: 実際にこの
# リポジトリの index が 23159 byte / 123 件から 4837 byte / 1 件へ壊れた。壊れた
# index が持っていた唯一のエントリは本ファイルの fixture のパスだった)。
#
# テストの終了コードでは検出できない。上書きしたまま緑を返す (実測: 同条件で
# scripts/test_check_issue_closure.py は 34 件 OK のまま指し先を 267 byte にする)。
# 判定にはテストの rc ではなく指し先ファイルのハッシュを使うこと。
#
# 個別の変数名を並べないのは、git が変数を増やしたとき列挙だけが古びるため。
GIT_ENV = {
    **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "probe",
    "GIT_AUTHOR_EMAIL": "probe@example.invalid",
    "GIT_COMMITTER_NAME": "probe",
    "GIT_COMMITTER_EMAIL": "probe@example.invalid",
}

# fixture の前文。frontmatter の書式を 1 箇所に持つ (3 箇所へ写すと、直し漏れが
# 「その fixture だけ別の文書構造でテストしている」という気づきにくい形になる)
DOC_HEAD = "---\nstatus: open\n---\n\n# fix: alpha\n\n"


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


crr = _load(CHECKER, "check_related_refs_under_test")
# 借用先は checker が読み込んだものをそのまま使う。別に読み込むとプロセス内に canonical の
# 実体が 2 つできて、片方だけを差し替えるテストが意味を失う
issue_id = crr.notation()


def issue_doc(related: str) -> str:
    """実際の issue.md と同じ構造の本文を作る。

    `## 関連` を最後に置き、その前に別の h2 と h3 を挟む。節の切り出しが h3 で
    止まる変異と、節の外を拾う変異の両方が対照に当たるようにするため。
    """
    return (
        f"{DOC_HEAD}"
        "## 背景\n"
        "\n"
        "節の外に ISSUE-999 と書いてあるが、射程は `## 関連` 節に限る。\n"
        "\n"
        "### 下位の見出し\n"
        "\n"
        "ここも節の外なので ISSUE-998 は読まれない。\n"
        "\n"
        "## タスク\n"
        "\n"
        "- [ ] なにか\n"
        "\n"
        "## 関連\n"
        "\n"
        f"{related}\n"
    )


class Fixture:
    """検査対象と同じ規約で fixture リポジトリを組む。"""

    def __init__(self, test: unittest.TestCase):
        self.dir = Path(tempfile.mkdtemp(prefix="related-refs-"))
        test.addCleanup(lambda: shutil.rmtree(self.dir, ignore_errors=True))
        subprocess.run(["git", "init", "-q", str(self.dir)], check=True, env=GIT_ENV)

    def add_raw(self, rel: str, data: bytes | str) -> str:
        path = self.dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            path.write_bytes(data)
        else:
            path.write_text(data, encoding="utf-8")
        return rel

    def add_text(self, dirname: str, text: str, closed: bool = False) -> str:
        base = "docs/issues/closed" if closed else "docs/issues"
        return self.add_raw(f"{base}/{dirname}/issue.md", text)

    def add_issue(self, dirname: str, related: str, closed: bool = False) -> str:
        return self.add_text(dirname, issue_doc(related), closed=closed)

    def close_issue(self, dirname: str) -> None:
        """Phase D.2 のクローズと同じく `closed/` 配下へ移す。"""
        base = self.dir / "docs" / "issues"
        (base / "closed").mkdir(parents=True, exist_ok=True)
        shutil.move(str(base / dirname), str(base / "closed" / dirname))

    def stage(self) -> None:
        subprocess.run(["git", "-C", str(self.dir), "add", "-A"], check=True, env=GIT_ENV)

    def check(self, baseline: dict[str, int] | None = None):
        self.stage()
        return crr.check(self.dir, baseline or {})

    def run_cli(self) -> subprocess.CompletedProcess[str]:
        self.stage()
        return subprocess.run(
            [sys.executable, str(ROOT / CHECKER), "--root", str(self.dir)],
            capture_output=True,
            text=True,
            check=False,
            env=GIT_ENV,
        )


def messages(findings) -> str:
    return "\n".join(f"{where}: {detail} — {msg}" for where, detail, msg in findings)


class SectionScope(unittest.TestCase):
    """射程は `## 関連` 節に限る。"""

    def test_identifiers_outside_the_section_are_not_read(self):
        fx = Fixture(self)
        fx.add_issue("ISSUE-1_alpha", "- ISSUE-1: 自分自身")
        findings, summary = fx.check()
        self.assertEqual([], findings, messages(findings))
        # 節の外に置いた ISSUE-999 / ISSUE-998 が読まれていれば実在しない番号として
        # 報告されるはず。報告が 0 件であることと、節の中は実際に読めていることを対で見る
        self.assertEqual(1, summary["identifiers"], "節の中の識別子が読めていない")
        self.assertEqual(1, summary["sections"])

    def test_heading_quoting_the_section_name_does_not_open_a_section(self):
        # ISSUE-24 の実ファイルが持つ形。h3 見出しがバッククォート内に節名を literal で
        # 含む。節を部分文字列で切り出す実装だとここで偽の節が開き、節が 1 つずれる
        fx = Fixture(self)
        fx.add_text(
            "ISSUE-1_alpha",
            f"{DOC_HEAD}"
            "### `## 関連` 節の外にもう 1 つの形式がある\n\n"
            "ここは節の外なので ISSUE-999 は読まれない。\n\n"
            "## 関連\n\n- ISSUE-1: 本物の節\n",
        )
        findings, summary = fx.check()
        self.assertEqual([], findings, messages(findings))
        self.assertEqual(1, summary["sections"])
        self.assertEqual(1, summary["identifiers"], "偽の節が開いている")

    def test_section_does_not_end_at_a_lower_heading(self):
        fx = Fixture(self)
        related = "- ISSUE-1: 先頭\n\n### 節の中の小見出し\n\n- ISSUE-1: 小見出しの後ろ"
        fx.add_issue("ISSUE-1_alpha", related)
        _, summary = fx.check()
        self.assertEqual(2, summary["identifiers"], "h3 で節が打ち切られている")

    def test_lower_heading_lines_are_scanned_as_body(self):
        # h3 以下は節の境界にならないので、その行自体も走査対象に残す。落とすと見出しの
        # 中に書かれた識別子と括りの見出しが無検査になる
        fx = Fixture(self)
        fx.add_issue("ISSUE-1_alpha", "### ISSUE-77 の扱い\n\n- ISSUE-1: 実在する")
        findings, _ = fx.check()
        self.assertEqual(1, len(findings), f"h3 の行が走査されていない: {messages(findings)}")


class SectionPresence(unittest.TestCase):
    """`issue.md` は `## 関連` 節を必ず持つ。持たなければ配下が無検査になる。"""

    def _findings(self, heading: str) -> list:
        fx = Fixture(self)
        fx.add_text("ISSUE-1_alpha", f"{DOC_HEAD}{heading}\n\n- ISSUE-999: 実在しない\n")
        findings, _ = fx.check()
        return findings

    def test_missing_section_is_reported(self):
        findings = self._findings("## 別の節")
        self.assertEqual(1, len(findings), messages(findings))
        self.assertEqual(crr.RELATED_HEADING, findings[0][1])

    def test_heading_variants_do_not_open_a_section(self):
        # 見出しの等価変形で節が開かないこと自体は仕様。開かなければ配下が無検査になるので、
        # 「節が無い」として報告されることを対で確かめる (実測: 変形はどれも描画上は同じ h2)
        for heading in ("## 関連 ##", "##  関連", "   ## 関連", "##関連"):
            with self.subTest(heading=heading):
                findings = self._findings(heading)
                self.assertTrue(
                    [f for f in findings if f[1] == crr.RELATED_HEADING],
                    f"{heading!r} が節として通り、かつ節無しとしても報告されていない",
                )

    def test_trailing_space_after_the_heading_is_accepted(self):
        # 行末の空白は落として比較する。散文が「余分な空白では開かない」と読める形だと
        # 実態とずれるので、受理される側も pin しておく
        findings = self._findings(f"{crr.RELATED_HEADING} ")
        self.assertEqual(1, len(findings), messages(findings))
        self.assertEqual("ISSUE-999", findings[0][1])

    def test_auxiliary_documents_are_not_required_to_have_one(self):
        fx = Fixture(self)
        fx.add_issue("ISSUE-1_alpha", "- ISSUE-1: 自分自身")
        fx.add_raw("docs/issues/ISSUE-1_alpha/ISSUE-1-spec.md", "# spec\n\n本文\n")
        findings, summary = fx.check()
        self.assertEqual([], findings, messages(findings))
        self.assertEqual(2, summary["files"])
        self.assertEqual(1, summary["issue_files"])


class MissingIdentifier(unittest.TestCase):
    """実在しない識別子は報告される (陽性対照)。"""

    def test_missing_identifier_is_reported(self):
        fx = Fixture(self)
        fx.add_issue("ISSUE-1_alpha", "- ISSUE-77: 実在しない")
        findings, _ = fx.check()
        self.assertEqual(1, len(findings), messages(findings))
        self.assertEqual("ISSUE-77", findings[0][1])

    def test_closed_issue_resolves(self):
        fx = Fixture(self)
        fx.add_issue("ISSUE-1_alpha", "- ISSUE-2: closed 配下にある")
        fx.add_issue("ISSUE-2_beta", "- ISSUE-1: 逆向き", closed=True)
        findings, _ = fx.check()
        self.assertEqual([], findings, messages(findings))

    def test_legacy_directory_name_resolves(self):
        # 接頭辞なしの旧記法も実在とみなす (canonical は issue-id.py の ANY_ISSUE_DIR)
        fx = Fixture(self)
        fx.add_issue("ISSUE-1_alpha", "- ISSUE-2: 旧記法のディレクトリ")
        fx.add_issue("2_legacy", "- なし")
        findings, _ = fx.check()
        self.assertEqual([], findings, messages(findings))

    def test_zero_padded_identifier_resolves(self):
        # 番号の同一性は整数。文字列のままだと `ISSUE-07` が `ISSUE-7` を指せない
        # (借用先は int() を鍵にしており、写した側だけが正規化を落としていた)
        fx = Fixture(self)
        fx.add_issue("ISSUE-7_alpha", "- ISSUE-07: 同じ Issue をゼロ詰めで書いた")
        findings, _ = fx.check()
        self.assertEqual([], findings, messages(findings))

    def test_all_four_notations_are_read(self):
        fx = Fixture(self)
        for notation in ("ISSUE-77", "Issue 77", f"Issue {HASH}77", f"{HASH}77"):
            with self.subTest(notation=notation):
                fx.add_issue("ISSUE-1_alpha", f"- {notation}: 実在しない")
                findings, _ = fx.check()
                self.assertEqual(1, len(findings), f"{notation} が読まれていない")

    def test_one_occurrence_is_not_counted_twice(self):
        fx = Fixture(self)
        fx.add_issue("ISSUE-1_alpha", f"- Issue {HASH}77: 実在しない")
        findings, summary = fx.check()
        self.assertEqual(1, summary["identifiers"], "`Issue #N` が 2 回数えられている")
        self.assertEqual(1, len(findings), messages(findings))

    def test_identifier_touching_japanese_is_read(self):
        # `\b` は日本語を語構成文字として扱うので、かな漢字の直後では境界が成立せず
        # 識別子が黙って読まれなくなる (実測)。ASCII の境界なら書き方に依らず読める
        fx = Fixture(self)
        fx.add_issue("ISSUE-1_alpha", "- 詳細はISSUE-77を参照")
        findings, _ = fx.check()
        self.assertEqual(1, len(findings), "日本語の直後の識別子が読まれていない")


class Exemptions(unittest.TestCase):
    """免除は「効くこと」と「広がりすぎていないこと」を対で見る。"""

    def test_github_pr_number_is_exempt(self):
        fx = Fixture(self)
        fx.add_issue("ISSUE-1_alpha", f"- 本 Issue は PR {HASH}77 のレビューで検出した")
        findings, summary = fx.check()
        self.assertEqual([], findings, messages(findings))
        self.assertEqual(0, summary["identifiers"])

    def test_pr_prefix_does_not_exempt_the_word_form(self):
        # 借用先の規則は数字記法だけを守る。語形まで広げると `PR ISSUE-<N>` が免除される
        fx = Fixture(self)
        fx.add_issue("ISSUE-1_alpha", "- PR ISSUE-77 のレビューで検出した")
        findings, _ = fx.check()
        self.assertEqual(1, len(findings), "`PR ` の免除が語形まで広がっている")

    def test_cross_repo_github_number_is_exempt(self):
        # 数字記法の枝が実際に CROSS_REPO_REF まで到達することを対で確かめる。到達せずに
        # パターン段階で落ちていると、免除の分岐が一度も評価されないまま緑になる
        body = "hidari/dotfiles" + HASH + "77"
        self.assertEqual([], crr._hash_spans(f"- 経緯は {body} が持つ"))
        self.assertTrue(
            issue_id.GITHUB_REF.search(body),
            "借用したパターンが `<owner>/<repo>#<N>` に一致しない。"
            "一致しないと免除の分岐へ到達せず、テストが別経路で緑になる",
        )
        fx = Fixture(self)
        fx.add_issue("ISSUE-1_alpha", f"- 経緯は {body} が持つ")
        findings, _ = fx.check()
        self.assertEqual([], findings, messages(findings))

    def test_file_path_is_not_taken_for_a_repository_reference(self):
        # `CROSS_REPO_REF` は識別子の直前にしか効かない (借用先も rstrip しない)。
        # 空白越しに広げると関連節へ書いたファイルパスが前置として通る。
        #
        # 対照は数字記法で置く。GitHub 前置の免除は数字記法の枝にしか効かないので、
        # 語形 (`ISSUE-<N>`) で書くと CROSS_REPO_REF を一度も通らない。識別子の直前を
        # 空白だけにするのも要点で、間に `の` を挟むと `$` アンカーがそこで外れる。
        # どちらを外しても rstrip を戻す変異が緑のまま生き残った (実測 2 回)
        fx = Fixture(self)
        fx.add_issue("ISSUE-1_alpha", f"- scripts/check-related-refs.py {HASH}77 を参照")
        findings, _ = fx.check()
        self.assertEqual(1, len(findings), "ファイルパスが `<owner>/<repo>` として通っている")

    def test_known_foreign_repo_prefix_is_exempt(self):
        fx = Fixture(self)
        fx.add_issue("ISSUE-1_alpha", "- dotfiles の Issue 77: 他リポジトリの Issue")
        findings, _ = fx.check()
        self.assertEqual([], findings, messages(findings))

    def test_unknown_repo_prefix_is_not_exempt(self):
        # 集合に無い名前を前置と認めると、免除が広がりすぎても違反 0 件の緑にしかならない
        fx = Fixture(self)
        fx.add_issue("ISSUE-1_alpha", "- some-other-repo の ISSUE-77: 未知のリポジトリ")
        findings, _ = fx.check()
        self.assertEqual(1, len(findings), "未知のリポジトリ名が前置として通っている")

    def test_repo_name_inside_a_longer_token_is_not_exempt(self):
        # 部分文字列で照合すると、名前を含むだけの無関係な語が前置として通る
        fx = Fixture(self)
        fx.add_issue("ISSUE-1_alpha", "- my-dotfiles-backup の ISSUE-77 を参照")
        findings, _ = fx.check()
        self.assertEqual(1, len(findings), "リポジトリ名の部分文字列が前置として通っている")

    def test_prefix_does_not_carry_to_the_next_line(self):
        # 折り返した継続行が前置を失う形。実在する番号なら静かに別の Issue へ解決される
        fx = Fixture(self)
        related = "- dotfiles の Issue 77 と\n  ISSUE-78 を参照する"
        fx.add_issue("ISSUE-1_alpha", related)
        findings, _ = fx.check()
        self.assertEqual(1, len(findings), messages(findings))
        self.assertEqual("ISSUE-78", findings[0][1])

    def test_prefix_carries_to_later_identifiers_on_the_same_line(self):
        fx = Fixture(self)
        fx.add_issue("ISSUE-1_alpha", "- dotfiles の Issue 77 と Issue 78")
        findings, _ = fx.check()
        self.assertEqual([], findings, messages(findings))

    def test_identifier_before_a_foreign_prefix_is_still_checked(self):
        # 行全体を免除すると、同じ行に並ぶ自リポジトリの識別子が素通りする
        fx = Fixture(self)
        fx.add_issue("ISSUE-1_alpha", "- ISSUE-77 と dotfiles の Issue 78")
        findings, _ = fx.check()
        self.assertEqual(1, len(findings), messages(findings))
        self.assertEqual("ISSUE-77", findings[0][1])

    def test_fenced_code_is_exempt(self):
        fx = Fixture(self)
        related = "- 例を示す\n\n```\nISSUE-77 はフェンスの中\n```\n\n- ISSUE-1: 実在する"
        fx.add_issue("ISSUE-1_alpha", related)
        findings, summary = fx.check()
        self.assertEqual([], findings, messages(findings))
        self.assertEqual(1, summary["identifiers"], "フェンスの外まで免除が広がっている")

    def test_inline_code_is_exempt(self):
        fx = Fixture(self)
        fx.add_issue("ISSUE-1_alpha", "- `ISSUE-77` はインラインコード。ISSUE-1 は実在する")
        findings, summary = fx.check()
        self.assertEqual([], findings, messages(findings))
        self.assertEqual(1, summary["identifiers"], "インラインコードの外まで免除が広がっている")

    def test_identifier_inside_local_link_text_is_not_read(self):
        # 角括弧が `PR ` の直前判定を壊すため、識別子を読む前にリンクを潰す。ローカル
        # リンクはリンク側の検査が本数として報告するので、テキストからは読まない
        fx = Fixture(self)
        fx.add_issue("ISSUE-1_alpha", "- [ISSUE-77: 実在しない](../ISSUE-77_x/issue.md)")
        findings, summary = fx.check({"ISSUE-1_alpha": 1})
        self.assertEqual([], findings, messages(findings))
        self.assertEqual(0, summary["identifiers"])

    def test_identifier_inside_a_foreign_link_text_is_read(self):
        # 外部 URL を指すリンクは本数に数えないので、テキストからも読まないと識別子が
        # どちらの機構からも見えなくなる (実測)
        fx = Fixture(self)
        fx.add_issue("ISSUE-1_alpha", "- [ISSUE-77: 消えた](https://example.invalid/x)")
        findings, summary = fx.check()
        self.assertEqual(1, len(findings), "外部リンクのテキスト内の識別子が読まれていない")
        self.assertEqual(0, summary["links"])


class ScopeHeading(unittest.TestCase):
    """リポジトリ名でまとめて括る段落見出しは、それ自体を報告する。"""

    def test_scope_heading_is_reported(self):
        for heading in ("dotfiles 側:", "dotfiles 側：", "dotfiles リポジトリ側:"):
            with self.subTest(heading=heading):
                fx = Fixture(self)
                fx.add_issue("ISSUE-1_alpha", f"{heading}\n\n- Issue 1: 配下は前置を失う")
                findings, _ = fx.check()
                self.assertTrue(
                    [f for f in findings if "側" in f[1]],
                    f"{heading!r} が報告されていない",
                )


class LinkBaseline(unittest.TestCase):
    """リンク本数は baseline と突き合わせる。増えた側も減った側も報告する。"""

    LINK = "- [ISSUE-2](../ISSUE-2_beta/issue.md) 説明"
    KEY = "ISSUE-1_alpha"

    def _repo(self) -> Fixture:
        fx = Fixture(self)
        fx.add_issue(self.KEY, self.LINK)
        fx.add_issue("ISSUE-2_beta", "- なし")
        return fx

    def test_link_over_baseline_is_reported(self):
        findings, _ = self._repo().check()
        self.assertEqual(1, len(findings), messages(findings))
        self.assertEqual("1 > 0", findings[0][1])

    def test_link_at_baseline_passes(self):
        findings, summary = self._repo().check({self.KEY: 1})
        self.assertEqual([], findings, messages(findings))
        self.assertEqual(1, summary["links"], "リンクが数えられていない")

    def test_link_under_baseline_is_reported(self):
        findings, _ = self._repo().check({self.KEY: 2})
        self.assertEqual(1, len(findings), messages(findings))
        self.assertEqual("1 < 2", findings[0][1])

    def test_baseline_entry_without_a_file_is_reported(self):
        findings, _ = self._repo().check({self.KEY: 1, "ISSUE-9_gone": 1})
        self.assertTrue(
            [f for f in findings if "ISSUE-9_gone" in f[0]],
            f"実体の無い baseline が報告されていない: {messages(findings)}",
        )

    def test_baseline_survives_closing_an_issue(self):
        # 鍵をパスにすると、クローズの `git mv` で「baseline に記録があるのに見つからない」と
        # 「リンクが増えた」の 2 件が同時に出る。後者は 1 本も増えていないので診断が誤りで、
        # 読んだ人はリンクを外しにいく (実測)。鍵は移動で変わらない識別子の側に置く
        fx = self._repo()
        self.assertEqual([], fx.check({self.KEY: 1})[0])
        fx.close_issue(self.KEY)
        findings, _ = fx.check({self.KEY: 1})
        self.assertEqual([], findings, f"クローズで baseline が壊れた: {messages(findings)}")

    def test_external_url_is_not_counted(self):
        fx = Fixture(self)
        fx.add_issue("ISSUE-1_alpha", "- [公式](https://example.invalid/x) 説明")
        _, summary = fx.check()
        self.assertEqual(0, summary["links"])

    def test_angle_bracketed_target_is_counted(self):
        # 半角空白を含むパスは山括弧で囲う形と %20 形の両方が実在する
        fx = Fixture(self)
        fx.add_issue("ISSUE-1_alpha", "- [ISSUE-2](<../ISSUE-2_with space/issue.md>) 説明")
        _, summary = fx.check()
        self.assertEqual(1, summary["links"], "山括弧で囲ったターゲットが数えられていない")

    def test_percent_encoded_target_is_counted(self):
        fx = Fixture(self)
        fx.add_issue("ISSUE-1_alpha", "- [ISSUE-2](../ISSUE-2_with%20space/issue.md) 説明")
        _, summary = fx.check()
        self.assertEqual(1, summary["links"], "%20 形のターゲットが数えられていない")

    def test_reference_style_link_is_counted(self):
        # 描画も移動で切れる性質もインライン形と同じ。数えないと規約を丸ごと迂回できる
        fx = Fixture(self)
        related = "- [ISSUE-2][ref] 説明\n\n[ref]: ../ISSUE-2_beta/issue.md"
        fx.add_issue("ISSUE-1_alpha", related)
        _, summary = fx.check()
        self.assertEqual(1, summary["links"], "参照形式のリンクが数えられていない")

    def test_link_inside_inline_code_is_not_counted(self):
        # 識別子の側はインラインコードを潰しているので、リンクだけ潰さないと同じ節の中で
        # 2 つの規則が食い違い、リンク記法の例示が違反として報告される
        fx = Fixture(self)
        fx.add_issue("ISSUE-1_alpha", "- 旧形式は `[ISSUE-2](../ISSUE-2_beta/issue.md)` だった")
        findings, summary = fx.check()
        self.assertEqual([], findings, messages(findings))
        self.assertEqual(0, summary["links"])


class ExitCodes(unittest.TestCase):
    """終了コードの写像を subprocess で pin する。"""

    def test_clean_repository_exits_zero(self):
        # 実リポジトリで確かめる。pre-commit と CI が根拠にしているのはこの rc そのもの
        proc = subprocess.run(
            [sys.executable, str(ROOT / CHECKER)],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(ROOT),
        )
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("違反なし", proc.stdout)

    def test_violation_exits_one(self):
        fx = Fixture(self)
        fx.add_issue("ISSUE-1_alpha", "- ISSUE-77: 実在しない")
        proc = fx.run_cli()
        self.assertEqual(1, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("ISSUE-77", proc.stderr)

    def test_nothing_to_scan_exits_two(self):
        # 「規約違反」と「検査を走らせられなかった」を同じ赤にしない
        fx = Fixture(self)
        fx.add_raw("README.md", "# 関連する Issue は無い\n")
        proc = fx.run_cli()
        self.assertEqual(2, proc.returncode, proc.stdout + proc.stderr)


class Vacuity(unittest.TestCase):
    """走査対象ゼロは合格ではない。"""

    def test_no_target_files_raises(self):
        fx = Fixture(self)
        fx.add_raw("README.md", "# 関連する Issue は無い\n")
        fx.stage()
        with self.assertRaises(crr.CheckError):
            crr.check(fx.dir, {})

    def test_undecodable_target_raises(self):
        # skip にすると不正なバイト 1 個でファイル全体が無検査のまま緑になる
        fx = Fixture(self)
        fx.add_raw("docs/issues/ISSUE-1_alpha/issue.md", b"## \xe9\x96\xa2\xff\xfe\n")
        fx.stage()
        with self.assertRaises(crr.CheckError):
            crr.check(fx.dir, {})

    def test_real_repository_reports_what_it_saw(self):
        _, summary = crr.check(ROOT)
        self.assertGreater(summary["files"], 0, "実リポジトリの走査対象が 0 件")
        self.assertGreater(summary["sections"], 0, "実リポジトリの `## 関連` 節が 0 件")
        self.assertGreater(summary["identifiers"], 0, "実リポジトリの識別子が 0 件")
        self.assertEqual(
            summary["issue_files"],
            summary["sections"],
            "`## 関連` 節を持たない issue.md がある",
        )


class BorrowedNotation(unittest.TestCase):
    """記法は issue-id.py から借りる。借用が切れたら exit 2 で落ちる。"""

    def test_all_borrowed_names_exist(self):
        module = crr.notation()
        missing = [name for name in crr.BORROWED if not hasattr(module, name)]
        self.assertEqual([], missing, f"借用先に無い名前: {missing}")

    def test_missing_borrowed_name_raises_check_error(self):
        # 借用先が rename されたときに traceback ではなく理由の分かる exit 2 で落ちること
        saved = crr._notation
        self.addCleanup(lambda: setattr(crr, "_notation", saved))
        crr._notation = None
        original = crr.BORROWED
        self.addCleanup(lambda: setattr(crr, "BORROWED", original))
        crr.BORROWED = (*original, "この名前は存在しない")
        with self.assertRaises(crr.CheckError):
            crr.notation()

    def test_empty_allowed_prefix_does_not_exempt_everything(self):
        # 借用先の定数が空になると `endswith("")` が常に真になり、全ての数字記法が免除されて
        # 違反 0 件の緑になる。借用先の _scan_line は truthy ガードを持つので、こちらも同じ
        # 形にしてある。ガードは定数が空のときにしか効かないので、その条件を対照側で作る
        saved = issue_id.GITHUB_REF_ALLOWED_PREFIX
        self.addCleanup(lambda: setattr(issue_id, "GITHUB_REF_ALLOWED_PREFIX", saved))
        issue_id.GITHUB_REF_ALLOWED_PREFIX = ""
        self.assertTrue(
            crr._hash_spans(f"- {HASH}77 を参照"),
            "免除の定数が空のとき、全ての数字記法が免除されている",
        )

    def test_real_directory_names_pass_the_borrowed_pattern(self):
        # 記法が変わってディレクトリ名が変われば、この対照が赤くなる
        names = [name for _, name in issue_id.issue_dirs(ROOT)]
        self.assertTrue(names, "実リポジトリの Issue ディレクトリが 0 件")
        unmatched = [n for n in names if not issue_id.ANY_ISSUE_DIR.match(n)]
        self.assertEqual([], unmatched, f"借用したパターンに一致しない実在名: {unmatched}")

    def test_unclosed_fence_is_reported_by_the_canonical_scanner(self):
        # フェンスの閉じ忘れは以降の全行をこの検査から隠す。閉じ忘れ自体を報告するのは
        # issue-id.py の側なので、その依存が生きていることを pin する
        text = f"## 関連\n\n```\nISSUE-77 {HASH}77\n"
        self.assertTrue(
            issue_id.scan_text(text, "fixture"),
            "issue-id.py がフェンスの閉じ忘れを報告しない。"
            f"{CHECKER} はこの報告に依存しているので、閉じ忘れが無検査になる",
        )

    def test_fence_handling_agrees_with_the_canonical_scanner(self):
        # prose_lines は借用先の状態機械を借りずに同じ形で組んでいる。上流が状態機械だけを
        # 変えると名前の実在検査を素通りして drift するので、挙動の一致を pin する
        text = f"外\n\n```\n中 {HASH}77\n```\n\n外 {HASH}78\n"
        outside = list(crr.prose_lines(text))
        self.assertNotIn(f"中 {HASH}77", outside, "フェンスの中が外として扱われている")
        self.assertIn(f"外 {HASH}78", outside, "フェンスの後ろが隠れている")
        self.assertEqual(
            1,
            len(issue_id.scan_text(text, "fixture")),
            "借用先のフェンス走査と結果が食い違う",
        )


class Attachment(unittest.TestCase):
    """取り付けを pin する。検査機構が緑でも呼ばれていなければ一度も走らない。

    走査ヘルパは scripts/test_issue_id_attachment.py と同じ形を採る。stdlib に YAML
    パーサが無いため、コメント行を除いた行の部分文字列で見るという判断もあちらの
    docstring が持つ (YAML 構造としての妥当性は pre-commit 自身と check-yaml hook が担う)。
    共有しないのは、あちらが flag 付きの部分一致で pin するのに対しこちらは起動行の完全
    一致で pin しており、厳しさが意図的に分岐しているため。
    """

    HOOK_START = re.compile(r"^\s*-\s+id:")

    # 文字列の存在だけを見ると `entry: echo <path>` のような変異が緑のまま通る (実測)。
    # 実行そのものの検証は重いので、起動行の形まで pin して届く範囲を広げる
    PRE_COMMIT_ENTRY = f"entry: {CHECKER}"
    CI_RUN = f"run: python3 {CHECKER}"

    def live_lines(self, path: Path) -> list[str]:
        return [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        ]

    def hook_block(self, lines: list[str]) -> list[str]:
        hits = [i for i, line in enumerate(lines) if CHECKER in line]
        if not hits:
            return []
        start = hits[0]
        while start > 0 and not self.HOOK_START.match(lines[start]):
            start -= 1
        end = start + 1
        while end < len(lines) and not self.HOOK_START.match(lines[end]):
            end += 1
        return lines[start:end]

    def test_checker_path_exists(self):
        self.assertTrue(
            (ROOT / CHECKER).is_file(),
            f"{CHECKER} が無い。取り付けを探す文字列が実在しないパスになっている",
        )

    def test_pre_commit_runs_the_checker(self):
        self.assertTrue(
            [
                line
                for line in self.live_lines(PRE_COMMIT_CONFIG)
                if line.strip() == self.PRE_COMMIT_ENTRY
            ],
            f"pre-commit の entry が `{self.PRE_COMMIT_ENTRY}` でない",
        )

    def test_pre_commit_hook_is_not_narrowed(self):
        # 走査対象は git の pathspec で固定する。files: で絞ると、Issue を触らない
        # コミットでは走らず、baseline の実態とのずれが次のコミットまで見えなくなる。
        # stages: を足すと通常の pre-commit stage から外れて一度も発火しなくなる
        block = self.hook_block(self.live_lines(PRE_COMMIT_CONFIG))
        self.assertTrue(block, f"{CHECKER} の hook 定義が見つからない")
        self.assertFalse(
            [
                line
                for line in block
                if line.lstrip().startswith(("files:", "exclude:", "stages:"))
            ],
            f"{CHECKER} の hook が files: / exclude: / stages: で絞られている",
        )
        self.assertTrue(
            [line for line in block if "always_run: true" in line],
            f"{CHECKER} の hook に always_run: true が無い",
        )

    def test_ci_runs_the_checker(self):
        self.assertTrue(
            [line for line in self.live_lines(CI_WORKFLOW) if line.strip() == self.CI_RUN],
            f"ci.yml の run が `{self.CI_RUN}` でない",
        )


if __name__ == "__main__":
    unittest.main()
