"""check-issue-closure.py の仕様。

git を歩くので実ツリーではなく tempfile + git init の fixture で検証する。実ツリーに
依存すると、現ツリーがたまたま合格していることに寄りかかった dead pin になる。
GIT_CONFIG_GLOBAL / GIT_CONFIG_SYSTEM を潰すのは、global の core.excludesfile が
`*.md` を ignore していると git ls-files の走査集合だけが縮むため (同じ理由と形を
scripts/test_check_related_refs.py が持つ)。
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKER = "scripts/check-issue-closure.py"
HOOK_ID = "issue-closure"

GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "probe",
    "GIT_AUTHOR_EMAIL": "probe@example.invalid",
    "GIT_COMMITTER_NAME": "probe",
    "GIT_COMMITTER_EMAIL": "probe@example.invalid",
}


def load():
    """ハイフン名のスクリプトは import 文では読めないため importlib で読む。"""
    path = ROOT / CHECKER
    spec = importlib.util.spec_from_file_location("check_issue_closure", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = load()


def issue_md(status: str, tasks: list[str], heading: str = "## タスク") -> str:
    body = "\n".join(tasks)
    return f"---\nstatus: {status}\n---\n\n# probe\n\n{heading}\n\n{body}\n"


class Fixture:
    """docs/issues/ を持つ使い捨てリポジトリ。"""

    def __init__(self, tmp: str):
        self.root = Path(tmp)
        self._git("init", "-q")

    def _git(self, *args):
        subprocess.run(["git", *args], cwd=self.root, env=GIT_ENV, check=True,
                       capture_output=True)

    def add_issue(self, rel: str, text: str, filename: str = "issue.md"):
        path = self.root / "docs" / "issues" / rel
        path.mkdir(parents=True, exist_ok=True)
        (path / filename).write_text(text, encoding="utf-8")

    def add_issue_encoded(self, rel: str, text: str, encoding: str, filename: str = "issue.md"):
        """UTF-8 以外のエンコーディングで issue.md を書く。read_text の例外パスを検証する。"""
        path = self.root / "docs" / "issues" / rel
        path.mkdir(parents=True, exist_ok=True)
        (path / filename).write_bytes(text.encode(encoding))

    def commit(self):
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "probe")


class FixtureCase(unittest.TestCase):
    """fixture を組んで検査スクリプトを走らせる共通ヘルパ。テストメソッドは持たない。

    InvariantB 以降がここを継承する形にするのは、unittest.TestCase のサブクラス化が
    テストメソッドまで継承してしまうため。InvariantB(InvariantA) にすると InvariantA の
    3 テストが InvariantB 名義でも実行され、manifest に実体のない重複 ID が入る。
    """

    def _run(self, build) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            fx = Fixture(tmp)
            build(fx)
            fx.commit()
            proc = subprocess.run(
                [sys.executable, str(ROOT / CHECKER), "--root", str(fx.root)],
                capture_output=True, text=True, env=GIT_ENV,
            )
            return proc.returncode, proc.stdout, proc.stderr


class InvariantA(FixtureCase):
    """タスクを全部消化した Issue が active に残っていないこと。"""

    def test_unfinished_issue_passes(self):
        rc, out, _ = self._run(lambda fx: fx.add_issue(
            "ISSUE-1_probe", issue_md("open", ["- [x] 済み", "- [ ] 未"])))
        self.assertEqual(rc, 0)
        # "1" だけだと closed/missing/unscanned のどの桁にも化けて通ってしまう弱い
        # assertion だったので、active の件数として出ていることまで確かめる
        self.assertIn("active 1 個", out)

    def test_all_checked_active_issue_is_a_violation(self):
        rc, _, err = self._run(lambda fx: fx.add_issue(
            "ISSUE-1_probe", issue_md("open", ["- [x] 済み", "- [x] 済み"])))
        self.assertEqual(rc, 1)
        self.assertIn("ISSUE-1_probe", err)

    def test_all_checked_closed_issue_passes(self):
        rc, _, _ = self._run(lambda fx: fx.add_issue(
            "closed/ISSUE-1_probe", issue_md("closed", ["- [x] 済み"])))
        self.assertEqual(rc, 0)

    def test_task_section_with_zero_boxes_passes(self):
        """`## タスク` はあるが箱が 0 個の active な Issue は違反にしない。

        「未チェックが 0」は箱が 1 つも無いときにも成り立つので、箱 0 個のガードが無いと
        中身が空のタスク節を「全て消化済み」と読む。 純粋関数を直に叩かず CLI 経由で回すのは、
        この分岐が collect() の中にあるため。 純粋関数 (scan_tasks) を直に叩くテストと
        述語 (is_completed) を叩くテストだけでは、 collect() がその述語を通っていることを
        pin できない (実測: collect() のガードを外しても両者は緑のままだった)。
        """
        rc, out, err = self._run(lambda fx: fx.add_issue(
            "ISSUE-1_probe", issue_md("open", [])))
        self.assertEqual(rc, 0, err)
        self.assertIn("違反 0 件", out)


class InvariantB(FixtureCase):
    """配置 (closed/ に居るか) と frontmatter の status が食い違わないこと。

    本タスク着手前はこの検査が status を一度も読んでいなかった (scripts/ と plugins/ を
    `status:` で走査して確認。ヒットはテストの fixture 文字列だけだった)。したがって
    `git mv` だけして frontmatter を書き換えない状態はどこからも見えなかった。
    """

    def test_closed_dir_with_open_status_is_a_violation(self):
        rc, _, err = self._run(lambda fx: fx.add_issue(
            "closed/ISSUE-1_probe", issue_md("open", ["- [x] 済み"])))
        self.assertEqual(rc, 1)
        self.assertIn("status", err)

    def test_active_dir_with_closed_status_is_a_violation(self):
        rc, _, err = self._run(lambda fx: fx.add_issue(
            "ISSUE-1_probe", issue_md("closed", ["- [ ] 未"])))
        self.assertEqual(rc, 1)
        self.assertIn("status", err)

    def test_unreadable_status_is_counted_not_passed(self):
        # 読めなかったものを合格へ倒さない。件数に出して沈黙させない
        rc, out, _ = self._run(lambda fx: fx.add_issue(
            "ISSUE-1_probe", "# 見出しだけ\n\n## タスク\n\n- [ ] 未\n"))
        self.assertEqual(rc, 0)
        self.assertIn("status が読めない 1 個", out)


class UnscannableInputs(FixtureCase):
    """走査できなかった入力 (閉じ忘れフェンス・非 UTF-8) は違反にも合格にもせず件数へ出す。

    D4: 元は InvariantA に同居していたが、検証しているのは「タスクの完了漏れ」ではなく
    「そもそも安全に走査できない」という別種の不変条件なので独立させた。中身は移動のみで
    変えていない。
    """

    def test_unclosed_fence_is_unscanned_not_a_violation(self):
        """全箱チェック済みでも、フェンスが閉じていなければ走査できず違反にもならない。

        閉じ忘れ自体は issue-id.py --check が別途検出するので、ここでは二重に違反として
        報告せず「走査できなかった」件数へ寄せる (コントローラの裁定)。
        """
        text = (
            "---\nstatus: open\n---\n\n# probe\n\n"
            "```\n"
            "## タスク\n\n- [x] 済み\n"
        )
        rc, out, err = self._run(lambda fx: fx.add_issue("ISSUE-1_probe", text))
        self.assertEqual(rc, 0)
        self.assertIn("走査できなかった 1 個", out)
        self.assertIn("違反 0 件", out)
        self.assertIn("ISSUE-1_probe", err)
        # 走査できなかった注記は違反 ([x]) とは別記号で出す
        self.assertIn("[-]", err)
        self.assertNotIn("[x]", err)

    def test_cp932_issue_is_unscanned_not_a_crash(self):
        """UTF-8 で読めない issue.md は例外を伝播させず「走査できなかった」件数へ寄せる。

        修正前は UnicodeDecodeError が未捕捉のまま抜け、Python 既定の rc 1 が
        「違反あり」と誤認される形になっていた (コントローラの実測)。
        """
        text = issue_md("open", ["- [x] 済み"])
        rc, out, err = self._run(
            lambda fx: fx.add_issue_encoded("ISSUE-1_probe", text, "cp932"))
        self.assertEqual(rc, 0)
        self.assertIn("走査できなかった 1 個", out)
        self.assertIn("違反 0 件", out)
        self.assertNotIn("Traceback", err)


class FormatVariants(unittest.TestCase):
    """scan_tasks (純粋関数) の挙動全般を pin する。

    行頭アンカーの素朴な正規表現だと、完了済み Issue が 9 通りの書式ゆれで素通りする
    (fixture で実測)。GitHub 上では完了済みとして正常に描画されるので、レビューでも
    気づけない。吸収する範囲を仕様として固定する。

    書式ゆれとは別に、箱を数える範囲がタスク節の内側に限られないことも合わせてここで
    固定する (節の外へ囮を 1 個置くだけで免除される形にしない。D5: このクラスは書式ゆれ
    9 通りだけでなく scan_tasks の挙動全般を対象にしているため、節スコープを持たない
    このテストも守備範囲に含める)。
    """

    def _scan(self, body: str, heading: str = "## タスク"):
        return checker.scan_tasks(f"{heading}\n\n{body}\n")

    def test_uppercase_x_counts_as_checked(self):
        self.assertEqual(self._scan("- [X] 済み"), (True, 1, 0))

    def test_asterisk_and_plus_markers_count(self):
        self.assertEqual(self._scan("* [x] 済み\n+ [x] 済み"), (True, 2, 0))

    def test_indented_list_counts(self):
        self.assertEqual(self._scan("  - [x] 済み"), (True, 1, 0))

    def test_fullwidth_space_is_unchecked(self):
        self.assertEqual(self._scan("- [　] 未"), (True, 1, 1))

    def test_heading_level_and_spacing_variants(self):
        self.assertTrue(self._scan("- [x] 済み", heading="### タスク")[0])
        self.assertTrue(self._scan("- [x] 済み", heading="##タスク")[0])

    def test_boxes_inside_a_code_fence_are_ignored(self):
        body = "- [x] 済み\n\n```\n- [ ] 囮\n```"
        self.assertEqual(self._scan(body), (True, 1, 0))

    def test_boxes_inside_an_html_comment_are_ignored(self):
        body = "- [x] 済み\n\n<!--\n- [ ] 囮\n-->"
        self.assertEqual(self._scan(body), (True, 1, 0))

    def test_a_box_outside_the_task_section_still_counts(self):
        # 節の外へ囮を 1 個置くだけで免除される形にしない。C.3 も全文を数える
        body = "- [x] 済み"
        text = f"## タスク\n\n{body}\n\n## 関連\n\n- [ ] 囮\n"
        self.assertEqual(checker.scan_tasks(text), (True, 2, 1))

    def test_scan_tasks_respects_closing_fence_length(self):
        """4 連バッククォートで開いたフェンスは、内側の 3 連の行では閉じない。

        単純トグルだと 3 連の行を閉じと誤認し、直後の本物のタスク節がフェンス内へ
        吸い込まれて消える (コントローラの実測)。CommonMark どおり、開始と同じ長さ以上の
        情報文字列なしの行でなければ閉じないことを確かめる。D4: 元は InvariantA に
        居たが、検証対象は scan_tasks の書式吸収なので FormatVariants へ移した。
        """
        text = (
            "````\n"
            "```\n"
            "````\n"
            "\n"
            "## タスク\n\n- [x] 済み\n"
        )
        self.assertEqual(checker.scan_tasks(text), (True, 1, 0))


_C3_VARS = ("has_task_section", "boxes", "unchecked")


def _extract_c3_lines(path: Path) -> list[str]:
    """SKILL.md の Phase C.3 コードフェンスから has_task_section/boxes/unchecked の
    代入行を、そのままのテキストで取り出す。

    行の中身 (grep -cE に渡す正規表現) を Python 側で再度パースして値だけを吸い出す
    のではなく、行そのものを後段で実際に bash へ渡して実行させる
    (_snippet_says_close)。他言語のデータをこちらの regex で text-parse せず、その
    言語自身に解釈させる (testing-practices.md) ことで、bash のクォート規約をテスト側に
    二重実装して drift させることを避ける。

    フェンスは `has_task_section=` を含む最初の ```bash ブロックとして特定する
    (SKILL.md 内でこの変数名が現れるのはここだけ)。3 本のどれか 1 本でも見つから
    なければ黙って空へ倒さず例外を送出する。抽出 0 件を「一致した」とみなすと、
    この pin 自身が「0 件の緑は健全ではなくそもそも見ていない」を体現してしまう。

    コントローラの実測: 以前の実装は grep -cE のパターンをこのテストへ literal で
    べた書きしており、SKILL.md の実物へ変異 (交替→文字クラス、[[:blank:]]→[ \\t])
    を当てても pin は緑のまま素通りした。この関数と _snippet_says_close はその
    修正として、SKILL.md を実際に読んで実行する形に置き換えたもの。
    """
    text = path.read_text(encoding="utf-8")
    block = None
    for m in re.finditer(r"```bash\n(.*?)\n```", text, re.DOTALL):
        if "has_task_section=" in m.group(1):
            block = m.group(1)
            break
    if block is None:
        raise AssertionError(f"{path} に has_task_section= を含む ```bash フェンスが見つからない")
    found: dict[str, str] = {}
    for line in block.splitlines():
        for name in _C3_VARS:
            if line.startswith(f"{name}=$(grep"):
                found[name] = line
    missing = [name for name in _C3_VARS if name not in found]
    if missing:
        raise AssertionError(
            f"{path} の C.3 フェンスに {', '.join(missing)}=... の行が見つからない"
        )
    return [found[name] for name in _C3_VARS]


class ParityWithPhaseC3(unittest.TestCase):
    """C.3 のスニペットと新検査の判定が一致すること。

    照合は literal コピーではなく SKILL.md の実物を実行して行う。分岐
    (has_task_section==0 / boxes==0 / unchecked>0 / unchecked==0) は SKILL.md では
    散文の箇条書きであり、grep -cE の引数のような機械抽出できる形を持たないため、
    ここでは判定ロジックとしてべた書きせざるを得ない。機械抽出できる 3 パターンは
    _extract_c3_lines 経由で実行して pin する一方、分岐そのものの存在は
    test_boxes_zero_branch_is_documented が literal の存在確認で別途 pin する
    (存在確認すら無いと、分岐を丸ごと削除する変更は 3 パターンの抽出には影響しない
    ため検出されずに緑のまま通ってしまう)。

    fixture にフェンスと HTML コメントを含めないのは、C.3 が grep なので追跡できず、
    そこだけは意図的に新検査が厳しいため。

    全角スペースによる字下げのケースは含めない。`[[:blank:]]` が U+3000 を blank
    として扱うかは locale 依存な一方、全角スペース字下げは markdown のリストとして
    描画されない実在しない構文なので、pin すると locale 依存の赤を作るだけになる。
    """

    CASES = (
        ("## タスク\n\n- [ ] 未\n", False),
        ("## タスク\n\n- [x] 済み\n", True),
        ("## タスク\n\n", False),          # 箱 0 個
        ("# 見出し\n\n- [x] 済み\n", False),  # タスク節なし
        ("## タスク\n\n\t- [x] 済み\n", True),  # タブ字下げ。[ \t] だと取りこぼす (R5)
        ("## タスク\n\ntt- [x] 囮\n", False),  # t 始まりの囮。[ \t] だと誤ヒットし箱として数える (R5)
        ("## タスク\n\n- [x] 済み\n- [　] 未\n", False),  # 全角スペースの箱が残っている (R7)
    )

    def setUp(self):
        # 抽出は各テストの実行前に 1 回だけ行う。失敗時は例外がそのまま setUp を
        # 落とし、このクラスの全テストが ERROR になる (「明示的に落とす」の実装)。
        self.lines = _extract_c3_lines(SKILL_MD)

    def _snippet_says_close(self, text: str, *, lc_all: str | None = None) -> bool:
        """SKILL.md から抽出した 3 行 (has_task_section/boxes/unchecked の代入) を
        実際に bash へ渡して実行し、判定する。

        `grep` を PATH 解決に任せるのは C.3 自身がそうしているため。subprocess.run は
        シェル関数を継承しないので、この開発機でも実際には /usr/bin/grep を、CI の
        ubuntu では GNU grep を叩く (コントローラが実測して確認)。lc_all は R7 の
        回帰網 (test_snippet_is_locale_independent) が LC_ALL を上書きするために使う。
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "issue.md"
            path.write_text(text, encoding="utf-8")

            env = dict(os.environ)
            if lc_all is not None:
                env["LC_ALL"] = lc_all
            env["ISSUE_PATH"] = str(path)

            script = "\n".join(self.lines) + (
                '\nprintf "%s:%s:%s" "$has_task_section" "$boxes" "$unchecked"\n'
            )
            proc = subprocess.run(
                ["bash", "-c", script], capture_output=True, text=True, env=env,
            )
            has_task_s, boxes_s, unchecked_s = proc.stdout.strip().split(":")
            has_task, boxes, unchecked = int(has_task_s), int(boxes_s), int(unchecked_s)
            if not has_task or boxes == 0:
                return False
            return unchecked == 0

    def test_snippet_and_checker_agree(self):
        for text, expected in self.CASES:
            with self.subTest(text=text):
                # 式を写さず、プロダクトコードの述語をそのまま呼ぶ。写すと collect() 側の
                # 判定を壊しても両者が同じだけ壊れるので、差が出ずに緑のまま通る (実測)
                checker_says = checker.is_completed(*checker.scan_tasks(text))
                self.assertEqual(checker_says, expected)
                self.assertEqual(self._snippet_says_close(text), expected)

    def test_snippet_is_locale_independent(self):
        """R7 の回帰網。LC_ALL=C でも既定ロケールでも新検査と一致すること。

        SKILL.md の交替 `( |　|x|X)` が文字クラス `[ 　xX]` へ戻ると、全角スペースの
        箱が残るケースだけ C ロケールで判定がずれ (箱がバイト単位に分解され
        `unchecked` が過小に出る)、ここが赤くなる。
        """
        for text, expected in self.CASES:
            with self.subTest(text=text, lc_all="C"):
                self.assertEqual(self._snippet_says_close(text, lc_all="C"), expected)
            with self.subTest(text=text, lc_all=None):
                self.assertEqual(self._snippet_says_close(text, lc_all=None), expected)

    def test_boxes_zero_branch_is_documented(self):
        """`boxes == 0` の分岐が SKILL.md に書かれていることを literal で確認する。

        分岐は散文の箇条書きなので _extract_c3_lines のような機械抽出ができない。
        機械抽出できる 3 パターンは実行して pin する一方、分岐そのものは存在確認に
        留める (このクラスの docstring を参照)。
        """
        text = SKILL_MD.read_text(encoding="utf-8")
        self.assertIn("`boxes == 0`", text)


class ExitCodes(unittest.TestCase):
    """subprocess 経由で起動した main() の終了コード (0/2) と、argparse エラーによる rc 2
    を pin する。

    rc 1 (違反あり) を見ているのは InvariantA (FixtureCase._run 経由で subprocess へ rc を
    渡す) と InvariantB なので、ここでは二重に pin しない。同じ規則の二重管理を避け、
    関数を直接呼ぶテストだけでは見えない層 (外部コマンドの実行と argparse の動作) に
    focus する。manifest が InvariantA のテストを追跡しているため、将来 InvariantA が
    変わって rc 1 の pin が失われると manifest 不一致で検出される。
    """

    def _run(self, root: Path) -> tuple[int, str, str]:
        proc = subprocess.run(
            [sys.executable, str(ROOT / CHECKER), "--root", str(root)],
            capture_output=True, text=True, env=GIT_ENV,
        )
        return proc.returncode, proc.stdout, proc.stderr

    def test_no_issue_directory_at_all_is_two(self):
        # 走査対象ゼロは正常ではなく CheckError となり rc 2。別の理由で同じ rc になっても
        # 検知できるよう stderr の内容で「実際に Issue ディレクトリが無い」ことを確認
        with tempfile.TemporaryDirectory() as tmp:
            fx = Fixture(tmp)
            (fx.root / "README.md").write_text("probe\n", encoding="utf-8")
            fx.commit()
            rc, _, err = self._run(fx.root)
            self.assertEqual(rc, 2)
            self.assertIn("走査対象ゼロ", err)

    def test_no_active_issue_is_zero_not_two(self):
        # 「open な Issue が 1 件も無い」は正常な状態なので検査不能にしない (rc 0)。
        # closed issue だけで active 0 であることを stdout で確認
        with tempfile.TemporaryDirectory() as tmp:
            fx = Fixture(tmp)
            fx.add_issue("closed/ISSUE-1_probe", issue_md("closed", ["- [x] 済み"]))
            fx.commit()
            rc, out, _ = self._run(fx.root)
            self.assertEqual(rc, 0)
            self.assertIn("active 0 個", out)

    def test_abbreviated_flag_is_rejected(self):
        # 短縮形が別モードへ静かに落ちるのを防ぐ。argparse が allow_abbrev=False で rc 2
        proc = subprocess.run(
            [sys.executable, str(ROOT / CHECKER), "--ro", "."],
            capture_output=True, text=True, env=GIT_ENV,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("unrecognized arguments", proc.stderr)


class BorrowedNames(unittest.TestCase):
    """借用先が rename されたら素通りではなく検査不能で落ちること。"""

    def test_missing_borrowed_name_is_check_error(self):
        original = checker.BORROWED
        checker.BORROWED = original + ("この名前は存在しない",)
        checker._notation = None
        try:
            with self.assertRaises(checker.CheckError):
                checker.notation()
        finally:
            checker.BORROWED = original
            checker._notation = None


PRE_COMMIT_CONFIG = ROOT / ".pre-commit-config.yaml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
SKILL_MD = ROOT / "plugins" / "dev-workflow" / "skills" / "in-repo-issue" / "SKILL.md"


class Attachment(unittest.TestCase):
    """検査機構そのものと、その取り付けは別に pin する。取り付けを外す変更は
    機構のテストでは捕まらない。stdlib に YAML パーサが無いのでコメント行を
    除いた行で照合する。
    """

    def _lines(self, path: Path) -> list[str]:
        return [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

    def _hook_block(self, path: Path, hook_id: str) -> list[str]:
        # hook の同一性は `- id:` 行から始まる。起点を `entry:` 行に取ると、
        # entry より前に置かれたキーが窓の外へ落ちる (実機で再現: pass_filenames /
        # always_run を entry より前へ動かすと、取り付けは有効なままなのに
        # FAIL した = 偽陽性)。YAML のマッピングはキー順序に意味を持たないので、
        # hook 内のどこにキーを書いても pin は同じ結果を返す必要がある。
        # 終端を「次の `- id:` 行の直前 (無ければ末尾)」に取るのは、repo: local の
        # hooks リストで各 hook が必ず `- id:` から始まるため。
        lines = self._lines(path)
        start = lines.index(f"- id: {hook_id}")
        end = start + 1
        while end < len(lines) and not lines[end].startswith("- id:"):
            end += 1
        return lines[start:end]

    def test_pre_commit_runs_the_checker(self):
        # ファイル全体ではなく hook のブロックに絞るのは、hook id を
        # 別の名前へ変えても entry 行さえどこかに残っていれば緑になる抜け道を
        # 塞ぐため。「issue-closure という id を持つ hook が、この entry を
        # 持つ」ことまで pin する。
        block = self._hook_block(PRE_COMMIT_CONFIG, HOOK_ID)
        self.assertIn(f"entry: {CHECKER}", block)

    def test_pre_commit_hook_does_not_take_filenames(self):
        # 母集団は全 Issue なので、変更ファイルだけを渡されると走査集合が縮む
        block = self._hook_block(PRE_COMMIT_CONFIG, HOOK_ID)
        self.assertIn("pass_filenames: false", block)
        self.assertIn("always_run: true", block)

    def test_ci_runs_the_checker_with_an_explicit_name(self):
        # job 名がこの step を覆えないので、step に name を置いて実態を名乗らせる
        lines = self._lines(CI_WORKFLOW)
        run = f"run: python3 {CHECKER}"
        self.assertIn(run, lines)
        self.assertTrue(lines[lines.index(run) - 1].startswith("- name:"))


if __name__ == "__main__":
    unittest.main()
