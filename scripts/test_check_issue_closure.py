"""check-issue-closure.py の仕様と、この検査が単独では成り立たない依存の pin。

git を歩くので実ツリーではなく tempfile + git init の fixture で検証する。実ツリーに
依存すると、現ツリーがたまたま合格していることに寄りかかった dead pin になる。
GIT_CONFIG_GLOBAL / GIT_CONFIG_SYSTEM を潰すのは、global の core.excludesfile が
`*.md` を ignore していると git ls-files の走査集合だけが縮むため (同じ理由と形を
scripts/test_check_related_refs.py が持つ)。

検査スクリプト本体の仕様に加えて、周りが動いたときに黙って効かなくなる継ぎ目もここへ集める。
取り付け先の設定ファイル、借用先の記法、SKILL.md が持つ判定や規約との一致がそれで、どれも
機構そのものは無傷のまま失効する形を捕まえる。継ぎ目を見るクラスを名前で列挙しないのは、
増やすたびに列挙が実態より狭くなるため。各クラスの docstring が何を見ているかを名乗る。
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
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
CHECKER = "scripts/check-issue-closure.py"
HOOK_ID = "issue-closure"

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


# `GIT_ENV` は `env=` を渡した subprocess にしか届かない。production をプロセス内で呼ぶ
# 経路では、その先の git が `os.environ` を読む。hook の環境には `GIT_INDEX_FILE` を
# 含む複数の `GIT_*` が入り、形も絶対と相対が混ざる (実測: 素の `commit` は相対、
# `commit -a` と `commit -- <paths>` は絶対)。相対形が今無害なのは production の git
# 呼び出しが `git -C <root>` だからで、構造に依存した無害さでしかない。
#
# このファイルには読み取り側の経路が今無い (借用する `issue_dirs` はファイルシステムを
# 走査する)。免疫は構造から来ているだけなので、借用先が index を読む形へ変わったときに
# 素通りしないよう予防で置く。どちらの側にどの対照を置けるかは `EnvironmentIsolation` が持つ。
#
# module scope に置くのは、呼び出しごとの `with` が「書いた場所」しか覆わないため。
# プロセス内呼び出しは将来も増えるが、増やした人が隔離を書き忘れても症状は汚染下でしか
# 出ないので、書き忘れに気づく経路が無い。`setUpModule` は unittest がこのモジュールの
# テストを 1 件でも走らせる前に必ず呼ぶので、クラス構成にも呼び方にも依存しない。
_GIT_ENV_PATCH = mock.patch.dict(os.environ, GIT_ENV, clear=True)


def setUpModule() -> None:
    _GIT_ENV_PATCH.start()


def tearDownModule() -> None:
    _GIT_ENV_PATCH.stop()


def load():
    """ハイフン名のスクリプトは import 文では読めないため importlib で読む。"""
    path = ROOT / CHECKER
    spec = importlib.util.spec_from_file_location("check_issue_closure", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = load()


def issue_md(
    status: str,
    tasks: list[str],
    heading: str = "## タスク",
    frontmatter: tuple[str, ...] = (),
) -> str:
    """fixture 用の issue.md を組む。frontmatter は status の後ろへ足す行。

    親子リンク用に別のビルダを立てず既存のここへ引数を足すのは、fixture の派生方法が
    増えるとテストごとに違う形の issue.md が生まれるため (ISSUE-42 が扱っている問題)。
    """
    head = "\n".join(["---", f"status: {status}", *frontmatter, "---"])
    body = "\n".join(tasks)
    return f"{head}\n\n# probe\n\n{heading}\n\n{body}\n"


def git_vars(env) -> dict[str, str]:
    """環境の GIT_* だけを取り出す。プロセスの環境と GIT_ENV を同じ規約で比べるため。"""
    return {k: v for k, v in env.items() if k.startswith("GIT_")}


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
        """fixture をコミットする。

        検査対象は git を読まない (借用する issue_dirs はファイルシステムを歩き、
        `--root` を渡すので resolve_root も git を呼ばない)。つまりコミットを外しても
        テストは緑のまま通る (実測: 全件緑、実行時間は 18% 短縮)。それでも積むのは、
        追跡下のリポジトリが production の見る構造上の形そのものだから。借用先が
        `git ls-files` を見る形へ変わったとき、未コミットの fixture は production の
        欠陥ではない理由で赤くなる。速いからという理由で外さないこと。
        """
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

    不変条件 A だけだと `git mv` 1 回で緑に戻せてしまう。Phase D のうち D.2 だけを実行し
    D.1 (frontmatter の書き換え) を落とすと、`closed/` に居るのに `status: open` という
    状態が残り、この検査以外にそれを見る層が無い。
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
        # 読めなかったものを合格へ倒さない。件数と、どの Issue かを名指しする注記に出す
        rc, out, err = self._run(lambda fx: fx.add_issue(
            "ISSUE-1_probe", "# 見出しだけ\n\n## タスク\n\n- [ ] 未\n"))
        self.assertEqual(rc, 0)
        self.assertIn("status が読めない 1 個", out)
        self.assertIn("[-] docs/issues/ISSUE-1_probe", err)

    def test_status_with_a_trailing_comment_is_read(self):
        """frontmatter スキーマが例示する末尾コメント付きの status を読めること。

        in-repo-issue の「frontmatter スキーマ」節は `status: open  # open / in_progress /
        closed` の形で例示しているので、それを写した issue.md が実際に現れる。読めないと
        status が None になり、その Issue は不変条件 B の検査から静かに外れる。
        """
        rc, out, err = self._run(lambda fx: fx.add_issue(
            "ISSUE-1_probe",
            "---\nstatus: closed  # open / in_progress / closed\n---\n\n"
            "# probe\n\n## タスク\n\n- [ ] 未\n"))
        self.assertEqual(rc, 1)
        self.assertIn("status が closed なのに active に居る", err)
        self.assertIn("status が読めない 0 個", out)

    def test_frontmatter_without_a_closing_marker_is_unreadable(self):
        """閉じ `---` が現れない文書では、本文の status: 行を frontmatter の値にしないこと。

        拾うと、frontmatter を持たない Issue の本文にたまたま現れた語で配置の整合を判定する。
        """
        rc, out, err = self._run(lambda fx: fx.add_issue(
            "ISSUE-1_probe",
            "---\n\n# probe\n\nstatus: closed\n\n## タスク\n\n- [ ] 未\n"))
        self.assertEqual(rc, 0, err)
        self.assertIn("status が読めない 1 個", out)
        self.assertIn("[-] docs/issues/ISSUE-1_probe", err)


class InvariantC(FixtureCase):
    """親子リンクの整合と、子が全て closed なのに親が active に残る形。

    assert を rc だけにしない。この検査は reader (frontmatter の値を読む regex) が
    盲目化しても赤いままになりうる。`children` の値書式を読めなくすると、辺は親側から
    立たなくなるが子側の `parent` からは立つので、違反は「片側にしか無い」へすり替わって
    rc 1 のまま残り、母集団行だけが 0 組へ落ちる。rc を見るだけの pin はこの変異で
    緑のまま通る (dead pin)。違反メッセージ本文と母集団行の両方まで見ること。

    判定に配置 (closed/ 配下か) だけを使うのは Phase E に揃えるため。配置と frontmatter の
    status の不整合は不変条件 B の担当で、ここでは二重に見ない。
    """

    def test_parent_with_all_children_closed_is_a_violation(self):
        def build(fx):
            fx.add_issue("ISSUE-1_親", issue_md(
                "open", ["- [ ] 未"], frontmatter=("children: [ISSUE-2]",)))
            fx.add_issue("closed/ISSUE-2_子", issue_md(
                "closed", ["- [x] 済み"], frontmatter=("parent: ISSUE-1",)))
        rc, out, err = self._run(build)
        self.assertEqual(rc, 1)
        self.assertIn("子 Issue が全て closed なのに active に居る (子 1 件)", err)
        self.assertIn("親子リンク: 1 組 / 親 1 個", out)
        self.assertIn("違反 1 件", out)

    def test_one_active_child_keeps_the_parent_green(self):
        """対照。子が 1 件でも active なら親は違反にならない。

        この対照が無いと、親を無条件に違反にする実装が上のテストだけで緑になる。
        """
        def build(fx):
            fx.add_issue("ISSUE-1_親", issue_md(
                "open", ["- [ ] 未"], frontmatter=("children: [ISSUE-2, ISSUE-3]",)))
            fx.add_issue("closed/ISSUE-2_子", issue_md(
                "closed", ["- [x] 済み"], frontmatter=("parent: ISSUE-1",)))
            fx.add_issue("ISSUE-3_子", issue_md(
                "open", ["- [ ] 未"], frontmatter=("parent: ISSUE-1",)))
        rc, out, err = self._run(build)
        self.assertEqual(rc, 0, err)
        self.assertIn("親子リンク: 2 組 / 親 1 個", out)
        self.assertNotIn("何も見ていない", out)

    def test_a_closed_parent_is_not_reported(self):
        """親も closed なら提案先が無い。

        Phase E が `PARENT_PATH` を `-maxdepth 2` で引いて closed の親を解決しないのと
        同じ向き。深さで表現されている判断をここでは配置の判定として持つ。
        """
        def build(fx):
            fx.add_issue("closed/ISSUE-1_親", issue_md(
                "closed", ["- [x] 済み"], frontmatter=("children: [ISSUE-2]",)))
            fx.add_issue("closed/ISSUE-2_子", issue_md(
                "closed", ["- [x] 済み"], frontmatter=("parent: ISSUE-1",)))
        rc, out, err = self._run(build)
        self.assertEqual(rc, 0, err)
        # 辺は 1 組あるが判定した親は 0 個。母集団と判定を同じ数で名乗ると、
        # 「見たが違反が無かった」と「そもそも見ていない」が区別できなくなる
        self.assertIn("親子リンク: 1 組 / 親 1 個 / 判定した親 0 個", out)
        self.assertIn("不変条件 C の親判定はこの実行で何も見ていない", out)

    def test_no_link_at_all_is_named_as_an_empty_population(self):
        """辺が 0 組のとき「何も見ていない」と名乗ること。

        このリポジトリの実ツリーには親子 Issue が 1 件も無いので、不変条件 C は空虚に
        緑を返す。名乗りが無いと、その緑を「守られていた証拠」として引用できてしまう。
        件数は不変条件と同じ 1 パスから出すので、印字された 0 と検査された 0 は
        食い違えない。
        """
        rc, out, err = self._run(lambda fx: fx.add_issue(
            "ISSUE-1_probe", issue_md("open", ["- [ ] 未"])))
        self.assertEqual(rc, 0, err)
        self.assertIn("親子リンク: 0 組 / 親 0 個 / 判定した親 0 個"
                      " (不変条件 C の親判定はこの実行で何も見ていない)", out)

    def test_a_link_declared_only_by_the_parent_is_a_violation(self):
        """親だけが `children` を書き、子が `parent` を書いていない形。

        両面から集めた辺の union を母集団にする。intersection にすると片側欠けの辺が
        母集団から静かに落ち、「1 組」が「0 組」になる。母集団行まで見るのはそのため。
        """
        def build(fx):
            fx.add_issue("ISSUE-1_親", issue_md(
                "open", ["- [ ] 未"], frontmatter=("children: [ISSUE-2]",)))
            fx.add_issue("ISSUE-2_子", issue_md("open", ["- [ ] 未"]))
        rc, out, err = self._run(build)
        self.assertEqual(rc, 1)
        self.assertIn("親子リンクが片側にしか無い (子側の parent が欠けている)", err)
        self.assertIn("親子リンク: 1 組 / 親 1 個", out)
        self.assertIn("違反 1 件", out)

    def test_a_link_declared_only_by_the_child_is_a_violation(self):
        """逆向き。子だけが `parent` を書き、親が `children` を書いていない形。

        片方向だけを検査すると、もう片方の欠けが素通りする。両向きを別のテストで持つ。
        """
        def build(fx):
            fx.add_issue("ISSUE-1_親", issue_md("open", ["- [ ] 未"]))
            fx.add_issue("ISSUE-2_子", issue_md(
                "open", ["- [ ] 未"], frontmatter=("parent: ISSUE-1",)))
        rc, out, err = self._run(build)
        self.assertEqual(rc, 1)
        self.assertIn("親子リンクが片側にしか無い (親側の children が欠けている)", err)
        self.assertIn("親子リンク: 1 組 / 親 1 個", out)
        self.assertIn("違反 1 件", out)

    def test_a_reference_to_a_missing_issue_is_a_violation(self):
        """指し先が実在しない参照。

        frontmatter の参照実在を見る canonical は現在どこにも無いので、ここが 1 つ目に
        なる (`## 関連` 節を見る scripts/check-related-refs.py の射程はその節に限る)。
        """
        def build(fx):
            fx.add_issue("ISSUE-1_親", issue_md(
                "open", ["- [ ] 未"], frontmatter=("children: [ISSUE-9]",)))
        rc, out, err = self._run(build)
        self.assertEqual(rc, 1)
        self.assertIn("children が指す ISSUE-9 が実在しない", err)
        self.assertIn("違反 1 件", out)

    def test_a_value_that_is_not_an_identifier_is_a_violation(self):
        """識別子の形でない値。

        裸の数字を識別子として受けないのは、frontmatter の値には `_<title>` のような
        後続が無く、受けると `parent: 2026` のような散文がそのまま番号へ解決するため。
        """
        def build(fx):
            fx.add_issue("ISSUE-1_子", issue_md(
                "open", ["- [ ] 未"], frontmatter=("parent: 7",)))
        rc, out, err = self._run(build)
        self.assertEqual(rc, 1)
        self.assertIn("parent の値 7 が識別子の形でない", err)
        self.assertIn("違反 1 件", out)

    def test_an_unreadable_child_does_not_fake_a_one_sided_link(self):
        """読めなかった Issue が絡む辺を片側欠けとして報告しないこと。

        子の issue.md が読めなければ `parent` も読めない。それを違反にすると、
        「読めなかった」が「規約違反」を名乗る。母集団には親側の宣言から入れたまま、
        対称性の検査だけを飛ばす。
        """
        def build(fx):
            fx.add_issue("ISSUE-1_親", issue_md(
                "open", ["- [ ] 未"], frontmatter=("children: [ISSUE-2]",)))
            fx.add_issue_encoded("ISSUE-2_子", issue_md(
                "open", ["- [ ] 未 日本語"], frontmatter=("parent: ISSUE-1",)), "cp932")
        rc, out, err = self._run(build)
        self.assertEqual(rc, 0, err)
        self.assertIn("親子リンク: 1 組 / 親 1 個", out)
        self.assertIn("違反 0 件", out)
        self.assertIn("[-] docs/issues/ISSUE-2_子", err)

    def test_zero_padded_identifiers_resolve_to_the_same_issue(self):
        """`ISSUE-07` と `ISSUE-7` が同じ Issue を指すこと。

        番号を文字列のまま鍵にすると一致せず、違反が「子が全て closed」から
        「実在しない」へすり替わる。メッセージまで見ることでそこを分ける。
        """
        def build(fx):
            fx.add_issue("ISSUE-1_親", issue_md(
                "open", ["- [ ] 未"], frontmatter=("children: [ISSUE-07]",)))
            fx.add_issue("closed/ISSUE-7_子", issue_md(
                "closed", ["- [x] 済み"], frontmatter=("parent: ISSUE-1",)))
        rc, out, err = self._run(build)
        self.assertEqual(rc, 1)
        self.assertIn("子 Issue が全て closed なのに active に居る (子 1 件)", err)
        self.assertNotIn("実在しない", err)
        self.assertIn("親子リンク: 1 組 / 親 1 個", out)
        self.assertIn("違反 1 件", out)

    def test_a_self_referencing_link_is_a_violation(self):
        """自分自身を親 / 子として宣言した形を辺にしないこと。

        辺にすると親が自分自身の子になる。親は active なので「子が全て closed」が必ず偽に
        なり、同じ親が持つ本物の子が全て closed でも不変条件 C が沈黙する。しかも自己参照を
        両側へ書くと対称性の検査も通るので、rc 0 で注記も出ない完全な無音になる。

        片側だけ自己参照を書いた状態は「片側にしか無い」で赤くなるので、その指摘どおりに
        もう片側を足すと無音へ遷移する。検査自身の修正指示が検査を殺す形になっていた。
        """
        def build(fx):
            fx.add_issue("ISSUE-1_親", issue_md(
                "open", ["- [ ] 未"],
                frontmatter=("parent: ISSUE-1", "children: [ISSUE-1, ISSUE-2]")))
            fx.add_issue("closed/ISSUE-2_子", issue_md(
                "closed", ["- [x] 済み"], frontmatter=("parent: ISSUE-1",)))
        rc, out, err = self._run(build)
        self.assertEqual(rc, 1)
        self.assertIn("parent が自分自身を指している", err)
        self.assertIn("children が自分自身を指している", err)
        # 自己参照を落とした結果、本物の辺だけが残って不変条件 C が本来の判定に戻る
        self.assertIn("子 Issue が全て closed なのに active に居る (子 1 件)", err)
        self.assertIn("親子リンク: 1 組 / 親 1 個", out)

    def test_an_unparsable_frontmatter_does_not_fake_a_one_sided_link(self):
        """frontmatter を切り出せない Issue が絡む辺を片側欠けとして報告しないこと。

        閉じ `---` が無い issue.md からは parent も children も読めない。読めないことを
        「規約違反」として報告すると、読めなさの扱いが経路で非対称になる (同じ読めなさでも
        ファイルが開けない側は注記で済む)。
        """
        def build(fx):
            fx.add_issue("ISSUE-1_親", issue_md(
                "open", ["- [ ] 未"], frontmatter=("children: [ISSUE-2]",)))
            fx.add_issue("ISSUE-2_子",
                         "---\n\n# probe\n\nstatus: open\n\n## タスク\n\n- [ ] 未\n")
        rc, out, err = self._run(build)
        self.assertEqual(rc, 0, err)
        self.assertNotIn("片側にしか無い", err)
        self.assertIn("親子リンク: 1 組 / 親 1 個", out)
        self.assertIn("status が読めない 1 個", out)

    def test_an_unreadable_parent_is_not_judged(self):
        """親の issue.md が読めないときは不変条件 C の判定へ進まないこと。

        読めない親の `children` は読めていないので、子側から立った辺だけが残る。その部分
        集合で「子は全て closed」と断定すると、読めなかった宣言に居る active な子を見落とす。
        母集団からは外さず、判定だけを止める。
        """
        def build(fx):
            fx.add_issue_encoded("ISSUE-1_親", issue_md(
                "open", ["- [ ] 未 日本語"], frontmatter=("children: [ISSUE-2]",)), "cp932")
            fx.add_issue("closed/ISSUE-2_子", issue_md(
                "closed", ["- [x] 済み"], frontmatter=("parent: ISSUE-1",)))
        rc, out, err = self._run(build)
        self.assertEqual(rc, 0, err)
        self.assertNotIn("子 Issue が全て closed", err)
        self.assertIn("親子リンク: 1 組 / 親 1 個 / 判定した親 0 個", out)
        self.assertIn("走査できなかった 1 個", out)

    def test_the_empty_population_notice_names_only_the_parent_judgement(self):
        """辺が 0 組でも不変条件 C が違反を出す実行があること、その名乗りが過大でないこと。

        参照が実在しないと辺は立たないが、その報告自体は不変条件 C が出している。ここで
        「不変条件 C はこの実行で何も見ていない」と名乗ると、出力の中に反例が並んだまま
        宣言が実態より広くなる。名乗るのは親判定だけに限る。
        """
        rc, out, err = self._run(lambda fx: fx.add_issue(
            "ISSUE-1_親", issue_md("open", ["- [ ] 未"],
                                  frontmatter=("children: [ISSUE-9]",))))
        self.assertEqual(rc, 1)
        self.assertIn("children が指す ISSUE-9 が実在しない", err)
        self.assertIn("親子リンク: 0 組 / 親 0 個 / 判定した親 0 個"
                      " (不変条件 C の親判定はこの実行で何も見ていない)", out)

    def test_an_unresolvable_child_reference_stops_the_parent_judgement(self):
        """`children` に解決できない要素があるとき、親の判定へ進まないこと。

        読めた分だけで「子は全て closed」と断定すると、解決できなかった参照が active な子の
        打ち間違いだったときに誤った指示を出す。しかも参照の違反と親の違反が同じ出力に並ぶ
        ので、上から直す人ほど踏む。読めなかった親を判定しないのと同じ理屈で、宣言を完全に
        は把握できていない親は判定しない。
        """
        for label, value in (("実在しない", "ISSUE-2, ISSUE-9"), ("識別子でない", "ISSUE-2, 9")):
            with self.subTest(label=label):
                def build(fx):
                    fx.add_issue("ISSUE-1_親", issue_md(
                        "open", ["- [ ] 未"], frontmatter=(f"children: [{value}]",)))
                    fx.add_issue("closed/ISSUE-2_子", issue_md(
                        "closed", ["- [x] 済み"], frontmatter=("parent: ISSUE-1",)))
                rc, out, err = self._run(build)
                self.assertEqual(rc, 1)
                self.assertNotIn("子 Issue が全て closed", err)
                self.assertIn("判定した親 0 個", out)
                self.assertIn("違反 1 件", out)

    def test_an_empty_element_in_children_is_not_a_child(self):
        """区切りだけが残った要素を子として数えないこと。

        `children: []` はここへ到達しない。`_read_key` が空文字を返し、read_links が
        その前で「子は 0 件」へ倒すため。フィルタに実際に届くのは末尾カンマのように
        要素が空になる形で、フィルタを外すとその空文字が「識別子の形でない」違反になる。

        最初に書いた `children: []` の fixture は変異注入で SURVIVED した (実測)。
        docstring が説明している挙動を、テストが別経路で満たしていた形だった。
        """
        for label, value in (("空リスト", "[]"), ("末尾カンマ", "[ISSUE-2, ]")):
            with self.subTest(label=label):
                def build(fx):
                    fx.add_issue("ISSUE-1_親", issue_md(
                        "open", ["- [ ] 未"], frontmatter=(f"children: {value}",)))
                    fx.add_issue("ISSUE-2_子", issue_md(
                        "open", ["- [ ] 未"], frontmatter=("parent: ISSUE-1",)))
                rc, out, err = self._run(build)
                self.assertNotIn("識別子の形でない", err)
                self.assertIn("値を読めない 0 個", out)

    def test_a_link_key_that_cannot_be_read_is_not_a_missing_declaration(self):
        """キー行はあるのに値を読めない形を「宣言が無い」と同じに扱わないこと。

        標準的な YAML のブロックリスト形 (`children:` の下に `- ISSUE-2` を並べる) は
        `children: [...]` の reader に一致しない。区別しないと、子が `parent` を書いて
        いなければ静かな緑になり、書いていれば規約どおり書いてある親が「children が
        欠けている」と違反を名乗らされる (どちらも実測)。
        """
        block = ("children:", "  - ISSUE-2")

        def without_parent(fx):
            fx.add_issue("ISSUE-1_親", issue_md("open", ["- [ ] 未"], frontmatter=block))
            fx.add_issue("closed/ISSUE-2_子", issue_md("closed", ["- [x] 済み"]))

        def with_parent(fx):
            fx.add_issue("ISSUE-1_親", issue_md("open", ["- [ ] 未"], frontmatter=block))
            fx.add_issue("closed/ISSUE-2_子", issue_md(
                "closed", ["- [x] 済み"], frontmatter=("parent: ISSUE-1",)))

        rc, out, err = self._run(without_parent)
        self.assertEqual(rc, 0, err)
        self.assertIn("children の値を読めなかった", err)
        self.assertIn("値を読めない 1 個", out)

        rc, out, err = self._run(with_parent)
        self.assertEqual(rc, 0, err)
        self.assertNotIn("片側にしか無い", err)
        self.assertNotIn("子 Issue が全て closed", err)
        self.assertIn("children の値を読めなかった", err)

    def test_a_directory_without_a_number_is_counted_separately(self):
        """番号が採れないディレクトリを「走査できなかった」に混ぜないこと。

        その Issue は不変条件 A と B の走査を通っている。解決できないのは親子リンクだけ
        なので、走査できなかった件数へ混ぜると要約行の宣言が実態より広くなる。ディレクトリ
        名の形式そのものは issue-id.py --check が違反として報告するので、ここでは母集団が
        縮んだことを見えるようにするだけ。
        """
        def build(fx):
            fx.add_issue("ISSUE-1_親", issue_md("open", ["- [ ] 未"]))
            fx.add_issue("番号なし", issue_md("closed", ["- [x] 済み"]))
        rc, out, err = self._run(build)
        self.assertEqual(rc, 1, err)
        # 不変条件 B は走っている (closed でないのに status: closed) ことが、走査を通った証拠
        self.assertIn("status が closed なのに active に居る", err)
        self.assertIn("走査できなかった 0 個", out)
        self.assertIn("番号が採れない 1 個", out)

    def test_a_child_without_an_issue_md_is_placed_but_not_judged(self):
        """issue.md が無いディレクトリを索引へ載せ、辺の対称性からは外すこと。

        載せないと、実在する Issue への参照が「実在しない」と誤報される。外さないと、
        読めていないだけの `parent` が「欠けている」と誤報される。索引への登録が
        issue.md を読む前に行われていることを、この 2 つの誤報の不在が見ている。
        """
        def build(fx):
            fx.add_issue("ISSUE-1_親", issue_md(
                "open", ["- [ ] 未"], frontmatter=("children: [ISSUE-2]",)))
            (fx.root / "docs" / "issues" / "ISSUE-2_子").mkdir(parents=True)
            (fx.root / "docs" / "issues" / "ISSUE-2_子" / "README.md").write_text(
                "probe\n", encoding="utf-8")
        rc, out, err = self._run(build)
        self.assertEqual(rc, 0, err)
        self.assertNotIn("実在しない", err)
        self.assertNotIn("片側にしか無い", err)
        self.assertIn("親子リンク: 1 組 / 親 1 個", out)
        self.assertIn("issue.md が無い 1 個", out)

    def test_a_legacy_directory_without_the_prefix_still_resolves(self):
        """接頭辞を持たない旧形式のディレクトリも索引へ載ること。

        借用先の ANY_ISSUE_DIR が接頭辞を任意にしているのは移行中のリポジトリのため。
        こちらだけ厳格にすると、旧形式のディレクトリが親子リンクの母集団から静かに落ちる。
        """
        def build(fx):
            fx.add_issue("ISSUE-1_親", issue_md(
                "open", ["- [ ] 未"], frontmatter=("children: [ISSUE-2]",)))
            fx.add_issue("closed/2_子", issue_md(
                "closed", ["- [x] 済み"], frontmatter=("parent: ISSUE-1",)))
        rc, out, err = self._run(build)
        self.assertEqual(rc, 1)
        self.assertNotIn("実在しない", err)
        self.assertIn("子 Issue が全て closed なのに active に居る (子 1 件)", err)
        self.assertIn("親子リンク: 1 組 / 親 1 個", out)

    def test_prefer_active_does_not_depend_on_the_order_of_appearance(self):
        """倒し方の判定を両順序で直に叩く。

        collect() 経由では逆順を作れない。借用先の issue_dirs が active を先に返すので、
        「先に見たものを残す」だけの実装でも fixture は緑になる。つまり fixture 経由の
        pin では倒し方の条件を消しても赤くならない (到達不能分岐の dead pin)。走査順に
        依存しない形であることは、この関数を直に叩くここだけが見ている。
        """
        active = ("docs/issues/ISSUE-2_子", False)
        closed = ("docs/issues/closed/ISSUE-2_子", True)
        self.assertEqual(checker.prefer_active(None, closed), closed)
        self.assertEqual(checker.prefer_active(closed, active), active)
        self.assertEqual(checker.prefer_active(active, closed), active)

    def test_the_same_number_on_both_sides_falls_to_active(self):
        """同じ番号が active と closed の両方に居る形が、実際の走査でも緑になること。

        倒し方そのものの pin は上のテストが持つ。ここは collect() がその判定を通っている
        ことと、重複を検査不能 (rc 2) や違反へ倒していないことを見る。
        """
        def build(fx):
            fx.add_issue("ISSUE-1_親", issue_md(
                "open", ["- [ ] 未"], frontmatter=("children: [ISSUE-2]",)))
            fx.add_issue("closed/ISSUE-2_子", issue_md(
                "closed", ["- [x] 済み"], frontmatter=("parent: ISSUE-1",)))
            fx.add_issue("ISSUE-2_子", issue_md(
                "open", ["- [ ] 未"], frontmatter=("parent: ISSUE-1",)))
        rc, out, err = self._run(build)
        self.assertEqual(rc, 0, err)
        self.assertIn("親子リンク: 1 組 / 親 1 個", out)
        self.assertIn("違反 0 件", out)


class Population(FixtureCase):
    """どの Issue ディレクトリが母集団に入るか。

    判定 (InvariantA / InvariantB) を pin するテストは母集団の中身しか見ないので、母集団が
    縮んだり膨らんだりしても緑のまま通る。母集団は借用先 issue-id.py の issue_dirs と
    issue_md_path が決めるので、ここだけが借用の実効を見る層になる。
    """

    def test_templates_directory_is_out_of_the_population(self):
        """templates/ 配下は全て [x] でも母集団に入らない。

        実ツリーのテンプレートは箱が全て未チェックなので、除外を外しても実ツリーに対しては
        違反が出ない。fixture 側へ全て [x] のテンプレートを置いて初めて除外が pin される。
        """
        def build(fx):
            fx.add_issue("ISSUE-1_probe", issue_md("open", ["- [ ] 未"]))
            fx.add_issue("templates", issue_md("open", ["- [x] 済み"]))

        rc, out, err = self._run(build)
        self.assertEqual(rc, 0, err)
        self.assertIn("active 1 個", out)
        self.assertIn("違反 0 件", out)

    def test_capitalized_issue_md_is_in_the_population(self):
        """`Issue.md` で作られた Issue も走査される。

        macOS の既定は core.ignorecase=true で、追跡名は作成時の綴りが記録される。名前の
        大小を無視しないと、この Issue は「issue.md が無い」へ落ちて不変条件 A と B の
        両方から静かに抜ける。
        """
        rc, out, err = self._run(lambda fx: fx.add_issue(
            "ISSUE-1_probe", issue_md("open", ["- [x] 済み"]), filename="Issue.md"))
        self.assertEqual(rc, 1)
        self.assertIn("ISSUE-1_probe", err)
        self.assertIn("issue.md が無い 0 個", out)

    def test_issue_directory_without_an_issue_md_is_named(self):
        """issue.md を持たない Issue ディレクトリは合格へ倒さず、識別子つきで注記に出す。

        件数だけだと、どの Issue が走査できていないのか出力から特定できない。
        """
        def build(fx):
            fx.add_issue("ISSUE-1_probe", issue_md("open", ["- [ ] 未"]))
            fx.add_issue("ISSUE-2_probe", "# spec\n", filename="ISSUE-2-spec.md")

        rc, out, err = self._run(build)
        self.assertEqual(rc, 0, err)
        self.assertIn("active 2 個", out)
        self.assertIn("issue.md が無い 1 個", out)
        self.assertIn("[-] docs/issues/ISSUE-2_probe", err)


class UnscannableInputs(FixtureCase):
    """走査できなかった入力 (閉じ忘れフェンス・非 UTF-8) は違反にも合格にもせず件数へ出す。

    InvariantA と分けるのは、検証しているのが「タスクの完了漏れ」ではなく「そもそも安全に
    走査できない」という別種の不変条件だから。
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

        捕捉しないと UnicodeDecodeError がそのまま抜け、Python 既定の rc 1 が「違反あり」と
        誤認される (実測)。
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
    固定する (節の外へ囮を 1 個置くだけで免除される形にしない)。
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
        情報文字列なしの行でなければ閉じないことを確かめる。
        """
        text = (
            "````\n"
            "```\n"
            "````\n"
            "\n"
            "## タスク\n\n- [x] 済み\n"
        )
        self.assertEqual(checker.scan_tasks(text), (True, 1, 0))


# 検査対象のファイルと、Markdown の見出しの形。_extract_c3_lines 以降の複数の層が使う
# ので、最初の利用者より前へ置く
PRE_COMMIT_CONFIG = ROOT / ".pre-commit-config.yaml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
SKILL_MD = ROOT / "plugins" / "dev-workflow" / "skills" / "in-repo-issue" / "SKILL.md"
GATE_SKILL_MD = (
    ROOT / "plugins" / "dev-workflow" / "skills" / "pre-merge-quality-gate" / "SKILL.md"
)
HEADING = re.compile(r"^#{1,6} +(\S.*)$")


_C3_VARS = ("has_task_section", "boxes", "unchecked")


def _extract_c3_lines(path: Path) -> list[str]:
    """SKILL.md の Phase C.3 コードフェンスから has_task_section/boxes/unchecked の
    代入行を、そのままのテキストで取り出す。

    行の中身 (grep -cE に渡す正規表現) を Python 側で再度パースして値だけを吸い出す
    のではなく、行そのものを後段で実際に bash へ渡して実行させる
    (_snippet_says_close)。他言語のデータをこちらの regex で text-parse せずその言語自身に
    解釈させることで、bash のクォート規約をテスト側に二重実装して drift させることを避ける。

    フェンスは `has_task_section=` を含む最初の ```bash ブロックとして特定する
    (SKILL.md 内でこの変数名が現れるのはここだけ)。3 本のどれか 1 本でも見つから
    なければ黙って空へ倒さず例外を送出する。抽出 0 件を「一致した」とみなすと、
    この pin 自身が「0 件の緑は健全ではなくそもそも見ていない」を体現してしまう。

    パターンをこのテストへ literal でべた書きしないのは、そうすると SKILL.md の実物へ
    変異 (交替→文字クラス、[[:blank:]]→[ \\t]) を当てても pin が緑のまま素通りするため
    (実測)。SKILL.md を読んで実際に実行する形だけが、そこを見ている。
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
        ("## タスク\n\n\t- [x] 済み\n", True),  # タブ字下げ。[ \t] だと取りこぼす
        ("## タスク\n\ntt- [x] 囮\n", False),  # t 始まりの囮。[ \t] だと誤ヒットし箱として数える
        ("## タスク\n\n- [x] 済み\n- [　] 未\n", False),  # 全角スペースの箱が残っている
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
        ubuntu では GNU grep を叩く (コントローラが実測して確認)。lc_all は
        test_snippet_is_locale_independent が LC_ALL を上書きするために使う。
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
            # rc と stderr を診断へ含めるのは、bash 側が想定外に失敗したときに
            # unpack の ValueError だけが飛んで原因が読めなくなるため
            fields = proc.stdout.strip().split(":")
            if proc.returncode != 0 or len(fields) != 3:
                raise AssertionError(
                    f"C.3 のスニペットの実行に失敗した (rc {proc.returncode}): "
                    f"stdout={proc.stdout!r} stderr={proc.stderr.strip()!r}"
                )
            has_task_s, boxes_s, unchecked_s = fields
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
        """LC_ALL=C でも既定ロケールでも新検査と一致すること。

        赤くなるのは `unchecked` 側の交替 `( |　)` が文字クラス `[ 　]` へ戻ったとき。
        C ロケールでは全角スペースがバイト単位に分解されて箱として数えられず、全角スペースの
        箱が残るケースで `unchecked` が過小に出る (実測で KILLED)。

        `boxes` 側の交替 `( |　|x|X)` を同じように戻しても、ここは赤くならない (実測で
        SURVIVED)。`boxes` は `boxes == 0` 分岐にしか効かず、全角スペースの箱は定義上
        未チェックなので、数え落ちても「close しない」という同じ答えに落ちるため。
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


def _section(path: Path, prefix: str) -> list[tuple[str, str]]:
    """ファイルから節を切り出す。中身は _section_from_text が持つ。"""
    return _section_from_text(path.read_text(encoding="utf-8"), prefix, label=str(path))


def _section_from_text(text: str, prefix: str, label: str = "入力") -> list[tuple[str, str]]:
    """見出しが prefix で始まる節を、行と種別の組で返す。

    プロダクトコードの classify_lines をそのまま使う。散文だけを返す
    _strip_fences_and_comments では足りないのは、スキーマ節の pin が読みたいのがまさに
    yaml フェンスの中身だから。種別を持つ分類器を通せば、フェンスの中身を残したまま
    見出しの判定を散文の行だけに限れる。

    状態機械をこちらへ書き直さないのは、写しがずれるため。実際、以前ここへ置いていた
    2 つ目の状態機械は HTML コメントを見ておらず、コメントの中にフェンスの開き行がある
    文書で本番と割れていた (節の終端になる見出しをフェンス内と誤判定し、節が次の見出しまで
    伸びた。実測)。

    見出しの判定を散文の行に限るガードは、現在の SKILL.md では発火しない (実測: 外しても
    全テストが緑のまま)。該当する形のコメント行は「検索手順」節に 4 本あるが、切り出して
    いる 3 節はどれもそれより手前で次の見出しに当たって終端するため、スキャンがそこへ
    届かない。つまり今は防御で、壊れている実例に対する手当てではない。それでも持つのは、
    節が増えたときに静かに切り詰められる形だから。到達しない分岐を pin だけで守ると dead
    になるので、ヘルパ自身の入力域 (任意の Markdown) を直に渡すテストで押さえる。

    見つからないときは黙って空へ倒さず落とす。抽出 0 件を「一致した」とみなすと、
    節名を変えるだけで pin が空虚な緑になる。
    """
    classified, _ = checker.classify_lines(text)
    start: int | None = None
    level = 0
    for i, (line, kind) in enumerate(classified):
        if kind != checker.LINE_PROSE:
            continue
        m = HEADING.match(line)
        if not m:
            continue
        depth = len(line) - len(line.lstrip("#"))
        if start is None:
            if m.group(1).strip().startswith(prefix):
                start, level = i + 1, depth
            continue
        if depth <= level:
            return classified[start:i]
    if start is None:
        raise AssertionError(f"{label} に「{prefix}」で始まる見出しが無い")
    return classified[start:]


def _section_text(path: Path, prefix: str) -> str:
    """節の本文をそのままのテキストで返す。"""
    return "\n".join(line for line, _ in _section(path, prefix))


SCHEMA_SECTION = "frontmatter スキーマ"
BUNDLED_SECTION = "クローズ経路: feature PR 同梱を優先"
PHASE_D_SECTION = "Phase D:"


class SectionExtraction(unittest.TestCase):
    """節の切り出し自身の pin。

    切り出しが壊れると ParityWithTheSchema も BundledCloseNamesThePropagationStep も
    「節が空」ではなく「別の範囲を見た」結果で判定するので、赤くならずに誤った緑を返す
    余地がある。ここだけが切り出しの意味論を見ている。
    """

    def _body(self, text: str) -> list[str]:
        return [line for line, _ in _section_from_text(text, "対象")]

    def test_a_fenced_comment_is_not_treated_as_a_heading(self):
        """フェンスの中の `#` 行で節が終端しないこと。

        現在の SKILL.md はこの形を持たない (_section_from_text の docstring 参照) ので、
        ヘルパの入力域へ直に渡して押さえる。
        """
        body = self._body(
            "## 対象\n"
            "本文 1\n"
            "```bash\n"
            "# これはコメントであって見出しではない\n"
            "grep -c x file\n"
            "```\n"
            "本文 2\n"
            "## 次の節\n"
            "ここは入らない\n"
        )
        self.assertIn("本文 2", body)
        self.assertNotIn("ここは入らない", body)

    def test_a_longer_fence_is_not_closed_by_a_shorter_one(self):
        """4 連で開いたフェンスが内側の 3 連で閉じないこと。

        単純トグルにすると 3 連の行で閉じたと誤判定し、そこから後ろがフェンス外に見える。
        """
        body = self._body(
            "## 対象\n"
            "````markdown\n"
            "```\n"
            "# 内側のコメント\n"
            "```\n"
            "````\n"
            "本文\n"
            "## 次の節\n"
            "ここは入らない\n"
        )
        self.assertIn("本文", body)
        self.assertNotIn("ここは入らない", body)

    def test_a_fence_opened_inside_an_html_comment_does_not_swallow_the_next_heading(self):
        """HTML コメントの中にフェンスの開き行があっても節が伸びないこと。

        テスト側に 2 つ目の状態機械を書いていたとき、この形で本番と割れていた (実測:
        コメント内の ``` をフェンスの開始と読み、節の終端になる見出しをフェンス内と
        誤判定して次の見出しまで伸びた)。プロダクトの分類器を通す形にして揃えたので、
        写しが戻ったらここが赤くなる。
        """
        body = self._body(
            "## 対象\n"
            "<!-- 説明\n"
            "```\n"
            "-->\n"
            "本文\n"
            "## 次の節\n"
            "ここは入らない\n"
        )
        self.assertIn("本文", body)
        self.assertNotIn("ここは入らない", body)

    def test_a_missing_section_is_an_error_not_an_empty_body(self):
        with self.assertRaises(AssertionError):
            _section_from_text("## 別の節\n本文\n", "存在しない節")


class ParityWithTheSchema(unittest.TestCase):
    """frontmatter スキーマ節と checker の reader が、キーでも値でも一致すること。

    キー集合の一致だけでは足りない。`children` の値書式 (角括弧) を読めなくする変異は
    キー集合を変えないので、キーだけを見る parity は緑のまま通る。そのとき違反は消えず
    「片側にしか無い」へすり替わって rc 1 のまま残るので、fixture の rc も変わらない
    (InvariantC の docstring が持つ機序)。だから節の実際の行を reader へ流して、
    値まで読めることを見る。
    """

    def _schema_lines(self) -> list[str]:
        lines = [line for line, kind in _section(SKILL_MD, SCHEMA_SECTION)
                 if kind == checker.LINE_FENCE]
        if not lines:
            raise AssertionError(f"「{SCHEMA_SECTION}」節の yaml フェンスが空")
        return lines

    def test_the_schema_keys_are_exactly_the_reader_keys(self):
        """スキーマが持つキーと reader が持つキーが過不足なく一致すること。

        キーの切り出しだけはテスト側で行う (stdlib に YAML パーサが無い)。値の書式は
        テスト側で一切パースせず reader へ流すので、書式の規約が二重にならない。
        """
        keys = {line.split(":", 1)[0] for line in self._schema_lines() if ":" in line}
        self.assertTrue(keys, f"「{SCHEMA_SECTION}」節からキーが 1 つも読めない")
        self.assertEqual(keys, set(checker.FRONTMATTER_READERS))

    def test_the_schema_lines_flow_through_the_readers(self):
        """節の行をそのまま reader へ流し、値まで取り出せること。

        placeholder の literal をこちらへ書かない。`children` の値が `parent` の値と
        同じ placeholder 2 つへ割れることを見る形にすると、値書式の変異 (角括弧を
        読めなくする / 区切りを `,` 以外にする) の両方がここで落ちる。
        """
        read: dict[str, str] = {}
        for line in self._schema_lines():
            for key, pattern in checker.FRONTMATTER_READERS.items():
                m = pattern.match(line)
                if m:
                    read[key] = m.group(1)
        self.assertEqual(set(read), set(checker.FRONTMATTER_READERS),
                         f"reader が読めなかったキーがある: {read}")
        self.assertEqual(checker.split_children(read["children"]),
                         [read["parent"], read["parent"]])


class BundledCloseNamesThePropagationStep(unittest.TestCase):
    """同梱節が Phase E を起動する手順を名指ししていること。

    同梱経路では post-merge の Phase C が「既に closed」で no-op になるため Phase D へ
    進まず、D.5 (親伝播の起動) がどの経路からも通らなくなる。射程の canonical は同梱節
    1 箇所なので、そこが名指しを失うと辺が消える。

    見るのは「D.5 という文字列が在る」ことではなく、実行を指示する一文が在ること。前者では
    足りないことを変異注入で実測した。同梱節は「マージ後には D.5 も通らない」という理由の
    側でも D.5 を名指しするので、実行を指示する箇条書きを丸ごと消しても文字列は残り、
    「在るか」だけを見る pin は緑のまま通る。

    射程の限界: 逆に、一文を残したまま「ただし実行しなくてよい」と続ける骨抜きは緑で通る。
    test_boxes_zero_branch_is_documented と同じ強度で、機械抽出できない散文に対してはここが
    上限になる。文を書き換えるとこの pin は赤くなるので、書き換える側は意味が保たれたかを
    その場で判断することになる。
    """

    def test_the_bundled_section_tells_you_to_run_the_step(self):
        self.assertIn("D.5 は同梱の側で実行する", _section_text(SKILL_MD, BUNDLED_SECTION))

    def test_the_step_it_names_exists_in_phase_d(self):
        """名指しした先が実在すること。

        参照だけを pin すると、Phase D 側から D.5 が消えても同梱節の文字列が残る限り
        緑になる。宙に浮いた名指しは「読めば辿れる」を満たさない。
        """
        self.assertIn("D.5", _section_text(SKILL_MD, PHASE_D_SECTION))


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


# 参照先の skill を名指ししている形だけを見る。`「X」節` 単体は同じ文書内の節を指す用法が
# 既に 8 箇所あり (in-repo-issue に 5 / retrospective-codify に 2 / commit-and-pr-message
# に 1)、区別せずに拾うと参照先が別文書だと誤診して赤くなる
CROSS_SKILL_REFERENCE = re.compile(r"`([a-z0-9-]+):([a-z0-9-]+)` の「([^」]+)」節")


def _prose_lines(path: Path) -> list[str]:
    """フェンスと HTML コメントを落とした行を返す。落とすのはプロダクトコードの状態機械。

    落とさないと、bash スニペットの `# コメント` を見出しとして数える。参照先が実在するかを
    見る検査でそれを許すと、コメント 1 行で偽の緑が作れる。逆に参照する側では、フェンスの中に
    書かれた節名を prose の参照として数えてしまう。
    """
    lines, _ = checker._strip_fences_and_comments(path.read_text(encoding="utf-8"))
    return lines


def _reference_sources() -> list[Path]:
    """他 skill の節を名指ししうる文書。母集団はファイルシステムの glob そのものが持つ。"""
    found = [ROOT / "CLAUDE.md"]
    found += sorted(ROOT.glob("plugins/**/SKILL.md"))
    found += sorted(ROOT.glob("skills/**/SKILL.md"))
    return [path for path in found if path.is_file()]


class SectionReferences(unittest.TestCase):
    """`<plugin>:<skill>` の「<節名>」節 という名指しの参照先が実在すること。

    gate は同梱の判定も手順も写さず節名で名指しして読ませる形を採っており、散文層はこの
    参照へ依存する。参照先の見出しが変わっても名指しした側は何も言わないので、手順が宙に
    浮いたまま緑で通る。値をどちらかへ二重に持たせるのではなく参照の整合だけを見る形は、
    Issue 間の参照に対して scripts/check-related-refs.py が既に採っている。

    参照元を gate に限らないのは、同じ形の名指しをリポジトリ直下の CLAUDE.md も持つため。
    参照元も参照先も名前で列挙せず、参照そのものから解決する。

    照合を skill 名まで含めて行うのは、`「X」節` だけを見ると同じ文書内の節を指す用法まで
    拾ってしまい、実在する参照を「別文書に無い」と誤診するため。

    解決できるのはこのリポジトリの plugins/ 配下にある skill だけ。外部 plugin (superpowers
    など) の節をこの記法で引用すると、参照先が読めないので赤くなる。実在を確かめられない
    ものを緑にすると綴り間違いまで素通りするので、そちらへは倒していない。
    """

    def _references(self) -> list[tuple[Path, str, str]]:
        found = []
        for source in _reference_sources():
            text = "\n".join(_prose_lines(source))
            for plugin, skill, section in CROSS_SKILL_REFERENCE.findall(text):
                target = ROOT / "plugins" / plugin / "skills" / skill / "SKILL.md"
                found.append((source, target, section))
        if not found:
            # 抽出 0 件を「名指しした節が全て実在した」とみなさない。0 件の緑は健全ではなく
            # そもそも見ていない
            raise AssertionError(
                "`<plugin>:<skill>` の「<節名>」節 の形の参照が 1 つも見つからない"
            )
        return found

    def test_gate_names_the_bundling_skill(self):
        """gate が同梱の手順を持つ skill を名指ししていること。

        参照先の実在だけを見ると、gate 側が名指しを丸ごと落としても他の文書の参照が残る限り
        緑のまま通る。節名は pin しない (見出しは動いてよく、動いたことは上の検査が見る)。

        射程はここまで。見ているのは「gate のどこかに 1 本ある」ことだけで、入口が Phase 0 /
        2 / 3 に在ることは見ていない。3 箇所を消しても Phase 5 の名指しをこの記法へ書き換えれば
        緑になる。ISSUE-41 が直した状態そのもの (main 時点の gate はこの記法に 0 件しか
        マッチしない) は捕まるが、同じ状態の別の作り方は捕まらない。
        """
        text = "\n".join(_prose_lines(GATE_SKILL_MD))
        targets = {
            f"{plugin}:{skill}"
            for plugin, skill, _ in CROSS_SKILL_REFERENCE.findall(text)
        }
        self.assertIn("dev-workflow:in-repo-issue", targets)

    def test_referenced_sections_exist(self):
        for source, target, section in self._references():
            with self.subTest(source=source.name, section=section):
                self.assertTrue(
                    target.is_file(),
                    f"{source} が名指しする {target} が無い。"
                    "このリポジトリの plugins/ 配下に無い skill は参照先を確かめられない",
                )
                headings = [
                    m.group(1).strip()
                    for m in map(HEADING.match, _prose_lines(target))
                    if m
                ]
                # 完全一致ではなく前方一致で見るのは、見出しが括弧つきの補足を続けて持つため
                # (実測: 名指しは「クローズ経路: feature PR 同梱を優先」、見出しは
                # `### クローズ経路: feature PR 同梱を優先 (main 直 push を避ける)`)
                self.assertTrue(
                    any(heading.startswith(section) for heading in headings),
                    f"{source} が名指しする「{section}」で始まる見出しが {target} に無い",
                )


class EnvironmentIsolation(unittest.TestCase):
    """プロセスの git 環境が隔離されていることを固定する。

    読み取り側は対照を置けない。このファイルの検査対象は今のところ index を読まない
    (借用している `issue_dirs` はファイルシステムを走査する) ので、隔離が外れても挙動に
    出ない。免疫は構造から来ているだけで、借用先が `git ls-files` へ変わると静かに
    脆弱になる。そのぶんを状態の pin が予防で受けている。

    書き込み側は対照を置ける。fixture 自身が `git add -A` を走らせるので、指し先へ
    書かないことを直接見られる。
    """

    def test_the_process_environment_carries_the_isolated_git_vars(self):
        # 状態の pin。取り付けの撤去は汚染の無い環境では挙動に出ないので、ここで見る。
        # 非空虚性を先に見るのは、GIT_ENV から GIT_* の追加が落ちると両辺が空になり
        # 比較が無条件に通るため。合格を意味する観測値と、機構が働かなかったときの
        # 観測値が同じになる形を、この 1 行が分けている
        self.assertTrue(git_vars(GIT_ENV), "GIT_ENV が GIT_* を持たず pin が空虚")
        self.assertEqual(git_vars(GIT_ENV), git_vars(os.environ))

    def test_staging_does_not_write_to_an_inherited_index(self):
        # 書き込み側 (subprocess へ `env=GIT_ENV` を渡す層) が生きていることを固定する。
        # プロセスの環境を消毒すると子プロセスは `env=` 無しでも清浄な環境を継承するので、
        # この pin が無いと層を外しても症状が出ない (実測: `env=GIT_ENV` を 4 箇所すべて
        # 外しても 36 件 OK)。防御が構造的に 1 層へ潰れる
        sentinel = self.seeded_index()
        before = sentinel.read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            fx = Fixture(tmp)
            fx.add_issue("ISSUE-2_probe", issue_md("open", ["- [ ] 未"]))
            with mock.patch.dict(os.environ, {"GIT_INDEX_FILE": str(sentinel)}):
                fx.commit()
        self.assertEqual(before, sentinel.read_bytes(), "子プロセスが継承した index へ書いた")

    def seeded_index(self) -> Path:
        """中身のある index を作って返す。空ファイルを指すと git が壊れた index として
        エラーで倒れ、書き込みの有無ではなく別経路で判定が成立する。"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        seed = Fixture(tmp.name)
        seed.add_issue("ISSUE-1_probe", issue_md("open", ["- [ ] 未"]))
        seed.commit()
        index = seed.root / "sentinel-index"
        index.write_bytes((seed.root / ".git" / "index").read_bytes())
        return index


if __name__ == "__main__":
    unittest.main()
