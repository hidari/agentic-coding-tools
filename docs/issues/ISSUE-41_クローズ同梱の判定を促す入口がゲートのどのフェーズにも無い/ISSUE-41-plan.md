# ISSUE-41 実装計画: クローズ同梱の入口と閉じ忘れの状態検査

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Issue のクローズを feature PR へ同梱する判断を gate の手順へ組み込み、同梱し忘れを機械で止める。

**Architecture:** 散文と検査の 2 層。散文は `pre-merge-quality-gate` と `in-repo-issue` の SKILL.md に置き、apm で配布先へ届く。検査は `scripts/check-issue-closure.py` として repo-local に置き、pre-commit と CI の両方へ取り付ける。検査は PR 本文や event payload を一切見ず、追跡下のツリーの状態だけを見る。

**Tech Stack:** Python 3 標準ライブラリのみ (argparse / importlib / re / subprocess)。テストは `unittest`。

**Spec:** `docs/issues/ISSUE-41_クローズ同梱の判定を促す入口がゲートのどのフェーズにも無い/ISSUE-41-spec.md`

## Global Constraints

- `scripts/` 配下と検証対象の Python は標準ライブラリのみ。`pip install` も `setup-python` も足さない
- テストは `unittest`。pytest を入れない
- コメントは日本語。説明するのは WHY で、WHAT は識別子で示す
- 絶対パス (`/Users/<name>/...`)、メールアドレス、個人を特定する情報を書かない
- 終了コードは 0 (合格) / 1 (違反あり) / 2 (検査不能)。2 を 1 と分ける理由の canonical は `scripts/check-leak-guard-rules.py` の docstring
- 機械検証可能な制約を散文と検査の両方に literal で書かない。散文は値を再掲せず canonical をファイル名で参照する
- 記法の canonical (`FENCE_LINE` / `issue_dirs` / `resolve_root` など) は写さずに `issue-id.py` から借りる
- `python3 scripts/run-python-tests.py --update-manifest` を実行したら diff ごとコミットする

---

## File Structure

| ファイル | 責務 |
| --- | --- |
| `scripts/check-issue-closure.py` (新規) | 2 つの不変条件の判定と要約出力。母集団は `issue-id.py` から借りる |
| `scripts/test_check_issue_closure.py` (新規) | 上の仕様。fixture リポジトリ / 終了コードの subprocess pin / 取り付けの pin / C.3 との挙動一致 |
| `.pre-commit-config.yaml` (変更) | `repo: local` hook を 1 つ追加 |
| `.github/workflows/ci.yml` (変更) | `package-shape` job へ step を 1 つ追加 |
| `scripts/python-tests-manifest.txt` (変更) | 追加テストの ID |
| `plugins/dev-workflow/skills/pre-merge-quality-gate/SKILL.md` (変更) | Phase 0 / 2 / 3 と関連節 |
| `plugins/dev-workflow/skills/in-repo-issue/SKILL.md` (変更) | 同梱節の判定手順 / C.3 スニペット / Phase F / PR 規約 |
| `docs/issues/ISSUE-41_*/issue.md` (変更) | タスクの消化とクローズ |

---

### Task 1: 検査スクリプトの骨格と不変条件 A

active な Issue が「タスク全消化なのに閉じていない」状態を報告する。母集団は `issue-id.py` から借りる。

**Files:**
- Create: `scripts/check-issue-closure.py`
- Create: `scripts/test_check_issue_closure.py`

**Interfaces:**
- Consumes: `issue-id.py` の `issue_dirs(root) -> list[tuple[str, str]]`、`resolve_root(explicit: str | None) -> Path` (`GitError` を送出する)、`FENCE_LINE`
- Produces: `scan_tasks(text) -> tuple[bool, int, int]` (タスク節の有無, 箱の総数, 未チェック数)、`collect(root) -> tuple[list[str], int, int, int]`、`main(argv) -> int`

**注意**: `issue_dirs` は git ではなくファイルシステムを歩く (`iterdir`)。したがって母集団には未追跡の Issue ディレクトリも入る。追跡下へ絞ると `templates/` と `closed/` の除外規則を自前で書き直すことになるので、借用を優先してこの差は受け入れる。全ての箱が `[x]` の未追跡ドラフトは正常な状態ではないので実害は小さい。

- [ ] **Step 1: 失敗するテストを書く**

`scripts/test_check_issue_closure.py` を作る。fixture リポジトリを組む helper と、不変条件 A の 3 ケースを置く。

```python
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
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKER = "scripts/check-issue-closure.py"

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

    def commit(self):
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "probe")


class InvariantA(unittest.TestCase):
    """タスクを全部消化した Issue が active に残っていないこと。"""

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

    def test_unfinished_issue_passes(self):
        rc, out, _ = self._run(lambda fx: fx.add_issue(
            "ISSUE-1_probe", issue_md("open", ["- [x] 済み", "- [ ] 未"])))
        self.assertEqual(rc, 0)
        self.assertIn("1", out)  # 走査件数を出す

    def test_all_checked_active_issue_is_a_violation(self):
        rc, _, err = self._run(lambda fx: fx.add_issue(
            "ISSUE-1_probe", issue_md("open", ["- [x] 済み", "- [x] 済み"])))
        self.assertEqual(rc, 1)
        self.assertIn("ISSUE-1_probe", err)

    def test_all_checked_closed_issue_passes(self):
        rc, _, _ = self._run(lambda fx: fx.add_issue(
            "closed/ISSUE-1_probe", issue_md("closed", ["- [x] 済み"])))
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 赤くなることを確認する**

Run: `python3 scripts/test_check_issue_closure.py -v` (リポジトリのルートで実行する)

Expected: `scripts/check-issue-closure.py` が無いので `FileNotFoundError` か `spec.loader` が None で ERROR。

`scripts/` は package ではないのでファイルを直接渡す。`python3 -m unittest scripts.test_...` の形は解決に失敗する。

- [ ] **Step 3: 最小の実装を書く**

```python
#!/usr/bin/env python3
"""active な Issue の閉じ忘れと、配置と status の不整合を検出する。

in-repo Issue は「## タスク が全て [x] なら閉じる」という規約で運用されるが、その規約は
in-repo-issue skill の Phase C.3 が散文で持つだけで、守られたかを見る層がどこにも無い。
実装 PR でタスクを埋めてクローズを次の PR へ回すと、その間 Issue は「完了なのに open」に
なる (実測: 過去の main の c33ab70 と d1de3ea がその状態)。

この検査を入れると、その状態でコミットできなくなる。つまりクローズは実装 PR へ同梱する
ことが強制される。main が保護されていて直 push を選べないこのリポジトリではそれが正しいが、
post-merge クローズを既定とするリポジトリとは両立しない。**そのまま配布しないこと。**

記法と母集団の canonical は借用先が持つ。ここでは写さない。
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 母集団と記法の canonical。写さずに借りる (同じ形を scripts/check-related-refs.py が採る)
NOTATION_SOURCE = "plugins/dev-workflow/skills/in-repo-issue/scripts/issue-id.py"

# 借りる名前。rename が静かな素通りにならないよう実在を検査する
BORROWED = ("FENCE_LINE", "GitError", "issue_dirs", "resolve_root")

CLOSED_SEGMENT = "closed"

# 見出しのレベルと空白の揺れを吸収する。厳密に `## タスク` だけを見ると、`### タスク` で
# 書かれた完了済み Issue が「タスク節なし」として素通りする (fixture で実測)
TASK_HEADING = re.compile(r"^#{2,3} *タスク *$")

# 箱として認めるのは半角/全角スペースと x/X だけ。GitHub もこれ以外は箱として描かない
CHECKBOX = re.compile(r"^[ \t]*[-*+] \[([ 　xX])\]")
CHECKED = ("x", "X")

STATUS_LINE = re.compile(r"^status: *(\S+) *$")

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


def scan_tasks(text: str) -> tuple[bool, int, int]:
    """(タスク節の有無, 箱の総数, 未チェック数) を返す。

    箱の数え方を節の内側に限らないのは C.3 に揃えるため。節の外へ囮を置くだけで
    免除される形を作らない。
    """
    fence = notation().FENCE_LINE
    in_fence = False
    in_comment = False
    has_heading = False
    total = 0
    unchecked = 0
    for line in text.splitlines():
        if in_comment:
            if "-->" in line:
                in_comment = False
            continue
        if "<!--" in line and "-->" not in line:
            in_comment = True
            continue
        if fence.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if TASK_HEADING.match(line):
            has_heading = True
            continue
        m = CHECKBOX.match(line)
        if m:
            total += 1
            if m.group(1) not in CHECKED:
                unchecked += 1
    return has_heading, total, unchecked


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


def collect(root: Path) -> tuple[list[str], int, int, int]:
    """(違反, active 件数, closed 件数, issue.md が無いディレクトリ数) を返す。"""
    n = notation()
    dirs = n.issue_dirs(root)
    if not dirs:
        raise CheckError("Issue ディレクトリが 1 件も無い。走査対象ゼロは合格ではない")
    violations: list[str] = []
    active = closed = missing = 0
    for rel_dir, name in dirs:
        is_closed = CLOSED_SEGMENT in Path(rel_dir).parts
        if is_closed:
            closed += 1
        else:
            active += 1
        path = issue_md_path(root, rel_dir)
        if path is None:
            missing += 1
            continue
        text = path.read_text(encoding="utf-8")
        if is_closed:
            continue
        has_heading, total, unchecked = scan_tasks(text)
        if has_heading and total >= 1 and unchecked == 0:
            violations.append(
                f"{rel_dir}: タスクが全て消化済みなのに active に居る "
                f"(箱 {total} 個 / 未チェック 0)。クローズを同じ PR へ同梱する"
            )
    return violations, active, closed, missing


def build_parser() -> argparse.ArgumentParser:
    # 短縮形が別モードへ静かに落ちるのを防ぐ (既存 3 本と同じ理由)
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--root", default=None, help="リポジトリのルート (既定: git が返す)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = resolve_root(args.root)
        violations, active, closed, missing = collect(root)
    except CheckError as e:
        print(f"[x] {e}", file=sys.stderr)
        return 2
    for line in violations:
        print(f"  [x] {line}", file=sys.stderr)
    print(
        f"検査した Issue: active {active} 個 / closed {closed} 個"
        f" / issue.md が無い {missing} 個 / 違反 {len(violations)} 件"
    )
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 緑になることを確認する**

Run: `python3 scripts/test_check_issue_closure.py -v`
Expected: 3 件 PASS

- [ ] **Step 5: 実ツリーへ当てて 0 件であることを確認する**

Run: `python3 scripts/check-issue-closure.py; echo "rc=$?"`
Expected: `検査した Issue: active 31 個 / closed 11 個 / issue.md が無い 0 個 / 違反 0 件` と `rc=0`

active の件数が 0 なら母集団の作り方が間違っている。件数が非 0 であることを目視で確かめる。

- [ ] **Step 6: コミット**

```bash
git add scripts/check-issue-closure.py scripts/test_check_issue_closure.py
git commit -F .cache/commit-task1.txt
```

コミット本文は `dev-workflow:commit-and-pr-message` に従い Write でファイルへ書いてから `-F` で渡す。

---

### Task 2: 書式の揺れを吸収していることを pin する

Task 1 の実装は既に揺れを吸収しているが、それが意図であることをテストで固定する。吸収を外す変異が赤くなる状態を作るのが目的。

**Files:**
- Modify: `scripts/test_check_issue_closure.py`

**Interfaces:**
- Consumes: Task 1 の `scan_tasks(text) -> tuple[bool, int, int]`
- Produces: なし

- [ ] **Step 1: 失敗するテストを書く**

`scripts/test_check_issue_closure.py` へ追加する。

```python
class FormatVariants(unittest.TestCase):
    """行頭アンカーの素朴な正規表現だと、完了済み Issue が 9 通りで素通りする
    (fixture で実測)。GitHub 上では完了済みとして正常に描画されるので、レビューでも
    気づけない。吸収する範囲を仕様として固定する。
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
```

- [ ] **Step 2: 実行して結果を見る**

Run: `python3 scripts/test_check_issue_closure.py -v`
Expected: 全て PASS (Task 1 の実装が既に吸収しているため)。1 件でも FAIL したら Task 1 の実装を直す。

- [ ] **Step 3: 変異で pin が生きていることを確認する**

`CHECKBOX` の文字クラスから `X` を落とす変異を入れて `test_uppercase_x_counts_as_checked` が赤くなること、`FENCE_LINE` によるフェンス追跡を外す変異で `test_boxes_inside_a_code_fence_are_ignored` が赤くなることを 1 件ずつ隔離して確認する。復元は退避コピーから行う (`git checkout --` は HEAD へ戻すので未コミットの編集ごと失う)。

- [ ] **Step 4: コミット**

```bash
git add scripts/test_check_issue_closure.py
git commit -F .cache/commit-task2.txt
```

---

### Task 3: 不変条件 B (配置と status の整合)

`git mv` だけして frontmatter を書き換えない状態を検出する。これが無いと、不変条件 A の赤を緑へ戻す最短手が `git mv` 1 回になり、検査が「verify しない 1 手を落とさせる」形になる。

**Files:**
- Modify: `scripts/check-issue-closure.py`
- Modify: `scripts/test_check_issue_closure.py`

**Interfaces:**
- Consumes: Task 1 の `collect(root)`
- Produces: `read_status(text) -> str | None`

- [ ] **Step 1: 失敗するテストを書く**

```python
class InvariantB(InvariantA):
    """配置 (closed/ に居るか) と frontmatter の status が食い違わないこと。

    このリポジトリの検査は現在 1 本も status を読まない (scripts/ と plugins/ を
    `status:` で走査して確認。ヒットはテストの fixture 文字列だけ)。したがって
    Phase D の 3 手のうち D.1 を落とした状態はどこからも見えない。
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
```

- [ ] **Step 2: 赤くなることを確認する**

Run: `python3 scripts/test_check_issue_closure.py InvariantB -v`
Expected: 3 件 FAIL

- [ ] **Step 3: 実装を足す**

`scripts/check-issue-closure.py` へ追加する。

```python
def read_status(text: str) -> str | None:
    """frontmatter の status を返す。読めなければ None。

    frontmatter は先頭の `---` で開いて次の `---` で閉じる。閉じる前だけを見るのは、
    本文中に status: と書かれた行を拾わないため。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            return None
        m = STATUS_LINE.match(line)
        if m:
            return m.group(1)
    return None
```

`collect()` の戻り値へ `unreadable` を足し、ループ本体を差し替える。

```python
        status = read_status(text)
        if status is None:
            unreadable += 1
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
```

要約の print も差し替える。

```python
    print(
        f"検査した Issue: active {active} 個 / closed {closed} 個"
        f" / issue.md が無い {missing} 個 / status が読めない {unreadable} 個"
        f" / 違反 {len(violations)} 件"
    )
```

- [ ] **Step 4: 緑になることを確認する**

Run: `python3 scripts/test_check_issue_closure.py -v`
Expected: 全て PASS

- [ ] **Step 5: 実ツリーへ当てる**

Run: `python3 scripts/check-issue-closure.py; echo "rc=$?"`
Expected: `違反 0 件` / `rc=0`。`status が読めない` が 0 でなければ、その Issue の frontmatter を調べる (spec の「不変条件 B の現状は未測定」がここで解消される)

- [ ] **Step 6: コミット**

```bash
git add scripts/check-issue-closure.py scripts/test_check_issue_closure.py
git commit -F .cache/commit-task3.txt
```

---

### Task 4: 終了コードと検査不能の pin

関数を直接呼ぶテストだけだと「違反ありで `return 0`」の 1 行の変異が全緑のまま生き残る。終了コードは subprocess で起動して pin する。

**Files:**
- Modify: `scripts/test_check_issue_closure.py`

**Interfaces:**
- Consumes: Task 3 までの `main(argv)`
- Produces: なし

- [ ] **Step 1: 失敗するテストを書く**

```python
class ExitCodes(unittest.TestCase):
    """終了コードの写像を subprocess で pin する。関数を直接呼ぶテストだけだと
    「違反ありで return 0」の 1 行の変異が全緑のまま生き残る。
    """

    def _rc(self, root: Path) -> int:
        return subprocess.run(
            [sys.executable, str(ROOT / CHECKER), "--root", str(root)],
            capture_output=True, text=True, env=GIT_ENV,
        ).returncode

    def test_no_issue_directory_at_all_is_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx = Fixture(tmp)
            (fx.root / "README.md").write_text("probe\n", encoding="utf-8")
            fx.commit()
            self.assertEqual(self._rc(fx.root), 2)

    def test_no_active_issue_is_zero_not_two(self):
        # 「open な Issue が 1 件も無い」は正常な状態なので検査不能にしない
        with tempfile.TemporaryDirectory() as tmp:
            fx = Fixture(tmp)
            fx.add_issue("closed/ISSUE-1_probe", issue_md("closed", ["- [x] 済み"]))
            fx.commit()
            self.assertEqual(self._rc(fx.root), 0)

    def test_abbreviated_flag_is_rejected(self):
        # 短縮形が別モードへ静かに落ちるのを防ぐ
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
```

- [ ] **Step 2: 実行する**

Run: `python3 scripts/test_check_issue_closure.py ExitCodes BorrowedNames -v`
Expected: `test_no_issue_directory_at_all_is_two` は Task 1 の `CheckError` で既に PASS。他が FAIL するなら実装を直す。`--ro` の rc は argparse が 2 を返すので PASS。

- [ ] **Step 3: 変異で pin を確認する**

`main()` の `return 1 if violations else 0` を `return 0` に変える変異で `InvariantA.test_all_checked_active_issue_is_a_violation` が赤くなることを確認する。

- [ ] **Step 4: コミット**

```bash
git add scripts/test_check_issue_closure.py
git commit -F .cache/commit-task4.txt
```

---

### Task 5: 取り付けとその pin

pre-commit と CI の両方へ取り付け、取り付け自体をテストで固定する。

**Files:**
- Modify: `.pre-commit-config.yaml`
- Modify: `.github/workflows/ci.yml`
- Modify: `scripts/test_check_issue_closure.py`
- Modify: `scripts/python-tests-manifest.txt`

**Interfaces:**
- Consumes: `scripts/check-issue-closure.py`
- Produces: なし

- [ ] **Step 1: 失敗するテストを書く**

```python
PRE_COMMIT_CONFIG = ROOT / ".pre-commit-config.yaml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


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

    def test_pre_commit_runs_the_checker(self):
        self.assertIn(f"entry: {CHECKER}", self._lines(PRE_COMMIT_CONFIG))

    def test_pre_commit_hook_does_not_take_filenames(self):
        # 母集団は全 Issue なので、変更ファイルだけを渡されると走査集合が縮む
        lines = self._lines(PRE_COMMIT_CONFIG)
        start = lines.index(f"entry: {CHECKER}")
        block = lines[start : start + 3]
        self.assertIn("pass_filenames: false", block)
        self.assertIn("always_run: true", block)

    def test_ci_runs_the_checker_with_an_explicit_name(self):
        # job 名がこの step を覆えないので、step に name を置いて実態を名乗らせる
        lines = self._lines(CI_WORKFLOW)
        run = f"run: python3 {CHECKER}"
        self.assertIn(run, lines)
        self.assertTrue(lines[lines.index(run) - 1].startswith("- name:"))
```

- [ ] **Step 2: 赤くなることを確認する**

Run: `python3 scripts/test_check_issue_closure.py Attachment -v`
Expected: 3 件 FAIL

- [ ] **Step 3: `.pre-commit-config.yaml` へ hook を足す**

`related-refs` hook の直後へ置く。

```yaml
      # 上の 2 本が記法と参照を見るのに対し、これはライフサイクルの状態を見る。
      # 母集団は issue-id.py の issue_dirs を借りるので pass_filenames は取らない。
      - id: issue-closure
        name: 閉じ忘れと配置と status の整合を検査する
        language: system
        entry: scripts/check-issue-closure.py
        pass_filenames: false
        always_run: true
```

- [ ] **Step 4: `ci.yml` の `package-shape` job へ step を足す**

`関連節の Issue 参照を検査する` step の直後へ置く。

```yaml
      # 同じ理由でこの job へ相乗りさせている。この検査も index と作業ツリーだけを
      # 見るので checkout の既定 (fetch-depth 1) で足りる。
      - name: 閉じ忘れと配置と status の整合を検査する
        run: python3 scripts/check-issue-closure.py
```

- [ ] **Step 5: 実行ビットを立てる**

Run: `chmod +x scripts/check-issue-closure.py`

`language: system` の hook は entry をそのまま起動するので、実行ビットが無いと取り付けだけが失敗する。

- [ ] **Step 6: 緑になることを確認する**

Run: `python3 scripts/test_check_issue_closure.py -v && python3 scripts/run-python-tests.py --update-manifest && pre-commit run --all-files`
Expected: テスト全 PASS、manifest の diff は追加のみ (削除 0)、pre-commit 全 hook Passed

- [ ] **Step 7: コミット**

```bash
git add .pre-commit-config.yaml .github/workflows/ci.yml scripts/check-issue-closure.py scripts/test_check_issue_closure.py scripts/python-tests-manifest.txt
git commit -F .cache/commit-task5.txt
```

---

### Task 6: C.3 のスニペットを揃え、挙動の一致を pin する

`## タスク` 節があって箱が 0 個の Issue を、C.3 は `unchecked == 0` として close へ送り、新検査は免除する。literal を照合する pin ではこの乖離を捕まえられないので、C.3 側を直して挙動で pin する。

**Files:**
- Modify: `plugins/dev-workflow/skills/in-repo-issue/SKILL.md` (Phase C.3)
- Modify: `scripts/test_check_issue_closure.py`

**Interfaces:**
- Consumes: Task 3 までの `scan_tasks(text)`
- Produces: なし

- [ ] **Step 1: 失敗するテストを書く**

```python
class ParityWithPhaseC3(unittest.TestCase):
    """C.3 のスニペットと新検査の判定が一致すること。

    照合は literal ではなく挙動で行う。両者は同じ literal を共有したまま分岐構造で
    判定が割れていた (箱 0 個の Issue を C.3 は close へ送り、新検査は免除する)。
    fixture にフェンスと HTML コメントを含めないのは、C.3 が grep なので追跡できず、
    そこだけは意図的に新検査が厳しいため。
    """

    CASES = (
        ("## タスク\n\n- [ ] 未\n", False),
        ("## タスク\n\n- [x] 済み\n", True),
        ("## タスク\n\n", False),          # 箱 0 個
        ("# 見出し\n\n- [x] 済み\n", False),  # タスク節なし
    )

    def _snippet_says_close(self, text: str) -> bool:
        """SKILL.md の C.3 スニペットを逐語で再現する。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "issue.md"
            path.write_text(text, encoding="utf-8")

            def count(pattern: str) -> int:
                proc = subprocess.run(
                    ["grep", "-cE", pattern, str(path)],
                    capture_output=True, text=True,
                )
                return int(proc.stdout.strip() or 0)

            has_task = count(r"^#{2,3} *タスク *$")
            boxes = count(r"^[ \t]*[-*+] \[[ 　xX]\]")
            unchecked = count(r"^[ \t]*[-*+] \[[ 　]\]")
            return bool(has_task) and boxes >= 1 and unchecked == 0

    def test_snippet_and_checker_agree(self):
        for text, expected in self.CASES:
            with self.subTest(text=text):
                has_heading, total, unchecked = checker.scan_tasks(text)
                checker_says = has_heading and total >= 1 and unchecked == 0
                self.assertEqual(checker_says, expected)
                self.assertEqual(self._snippet_says_close(text), expected)
```

- [ ] **Step 2: 赤くなることを確認する**

Run: `python3 scripts/test_check_issue_closure.py ParityWithPhaseC3 -v`
Expected: 箱 0 個のケースで FAIL (現行の C.3 相当は close と判定する)。テスト内のスニペット再現は既に修正後の形なので、FAIL するのは SKILL.md を直す前にこのテストを書いた場合の対照確認になる。実際には SKILL.md を直してから緑になる。

- [ ] **Step 3: SKILL.md の C.3 を直す**

`plugins/dev-workflow/skills/in-repo-issue/SKILL.md` の C.3 スニペットを差し替える。

```bash
has_task_section=$(grep -cE '^#{2,3} *タスク *$' "$ISSUE_PATH")
boxes=$(grep -cE '^[ \t]*[-*+] \[[ 　xX]\]' "$ISSUE_PATH")
unchecked=$(grep -cE '^[ \t]*[-*+] \[[ 　]\]' "$ISSUE_PATH")
```

分岐の記述へ「`boxes == 0`: チェックリストが空なので自動 close 対象外」を足す。

- [ ] **Step 4: 緑になることを確認する**

Run: `python3 scripts/test_check_issue_closure.py ParityWithPhaseC3 -v`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add plugins/dev-workflow/skills/in-repo-issue/SKILL.md scripts/test_check_issue_closure.py
git commit -F .cache/commit-task6.txt
```

---

### Task 7: gate SKILL.md へ判定の入口を足す

**Files:**
- Modify: `plugins/dev-workflow/skills/pre-merge-quality-gate/SKILL.md`

**Interfaces:**
- Consumes: なし
- Produces: なし

- [ ] **Step 1: Phase 0 へ 1 行足す**

「Phase 0: コンテキスト収集」の本文末尾へ追加する。判定手順は書かず、ポインタに留める。

```markdown
PR をこれから作る経路では、Issue のクローズをこの PR へ同梱するかの判定に要る事実
(main が保護されているか) もここで集める。判定の手順は `dev-workflow:in-repo-issue` の
「クローズ経路: feature PR 同梱を優先」節が持つので、写さずにそちらを読む。
```

- [ ] **Step 2: Phase 2 の分類表へ 1 行足す**

```markdown
| **クローズ同梱** (この PR が Issue のタスクを全消化するか) | 同梱するなら Phase 3 で `dev-workflow:in-repo-issue` の D.1〜D.4 を適用する。判定は Phase 0 で集めた事実に基づく |
```

- [ ] **Step 3: Phase 3 へ 1 ステップ足す**

「Phase 3: 修正反映 + 再検証」の手順 1 の直後へ挿入する。

```markdown
2. 同梱すると判断したなら `dev-workflow:in-repo-issue` の D.1〜D.4 を feature ブランチの
   コミットへ含める。Phase 4 (執行) ではなくここへ置くのは、同梱が issue.md の編集と
   `git mv` と相対リンクの補正というファイル変更を伴うため。Phase 4 に置くと、この
   Phase の再検証を通らない変更が PR へ入る
```

以降の番号を繰り下げる。

- [ ] **Step 4: 関連節へ足す**

```markdown
- `dev-workflow:in-repo-issue` (sibling skill): Issue のライフサイクル。Phase 0 の同梱判定と Phase 5 の自動クローズがここを参照する
```

- [ ] **Step 5: 検証**

Run: `pre-commit run --all-files`
Expected: 全 hook Passed

- [ ] **Step 6: コミット**

```bash
git add plugins/dev-workflow/skills/pre-merge-quality-gate/SKILL.md
git commit -F .cache/commit-task7.txt
```

---

### Task 8: in-repo-issue SKILL.md の補強

ポインタ先が情報を持つようにし、Phase F が検査と衝突しないようにする。

**Files:**
- Modify: `plugins/dev-workflow/skills/in-repo-issue/SKILL.md`

**Interfaces:**
- Consumes: なし
- Produces: なし

- [ ] **Step 1: 同梱節へ保護の判定手順を足す**

「クローズ経路: feature PR 同梱を優先」節の冒頭へ挿入する。

```markdown
保護されているかは classic branch protection API の 404 だけで判定しない。repository
ruleset は別系統で classic API に出ないため、両方を見る。

```bash
gh api "repos/<owner>/<repo>/branches/main/protection" >/dev/null 2>&1; echo "classic rc=$?"
gh api "repos/<owner>/<repo>/rulesets" --jq '.[] | select(.target=="branch" and .enforcement=="active") | .rules[].type'
```

`pull_request` が出れば PR が必須なので同梱を選ぶ。終了コードを見るときはパイプへ繋がない
こと。`| head` の形は終端のコマンドの rc になるので `gh` の失敗が消える。
```

- [ ] **Step 2: Phase F へ残作業の追記を足す**

`F.1` の直前へ挿入する。

```markdown
F.0 reopen の理由を `## タスク` へ未チェック項目として追記する。既存の `[x]` は変えない。
closed の Issue は定義上すべての箱が `[x]` なので、追記せずに戻すと「タスクは全消化なのに
active」という状態になり、C.3 は次に回ったときその Issue を「完了」と判定する。reopen とは
まだ終わっていない作業があるという判断なので、その作業が未チェック項目として書かれるのが
本来の形にあたる。箱を `[x]` から戻す形は採らない (reopen 前の作業記録を壊す)。
```

- [ ] **Step 3: PR / コミット規約へ分割時の書き方を足す**

「squash merge の subject は既定に任せず明示する」節の末尾へ追加する。

```markdown
クローズを実装 PR へ同梱せず別 PR に分けたときは、subject に PR 番号が 2 つ並ぶ。
D 形式が要求する「どの PR がこの Issue を閉じたか」と、squash 規約が要求する「どの PR が
このコミットを作ったか」が別の番号になるため。両方書くこと。同梱すれば 1 つになる。
```

- [ ] **Step 4: 検証**

Run: `pre-commit run --all-files`
Expected: 全 hook Passed

- [ ] **Step 5: コミット**

```bash
git add plugins/dev-workflow/skills/in-repo-issue/SKILL.md
git commit -F .cache/commit-task8.txt
```

---

### Task 9: 変異注入バッテリと ISSUE-41 のクローズ同梱

検査機構を足したので 3 種の変異を当てる。そのあと ISSUE-41 のタスクを消化し、**同じコミットでクローズを同梱する** (この PR が導入する規約の最初の適用例になる)。

**Files:**
- Modify: `docs/issues/ISSUE-41_*/issue.md`
- Move: `docs/issues/ISSUE-41_*/` → `docs/issues/closed/ISSUE-41_*/`

**Interfaces:**
- Consumes: Task 5 までの取り付け
- Produces: なし

- [ ] **Step 1: baseline が緑であることを確認する**

Run: `python3 scripts/run-python-tests.py`
Expected: 全 PASS

baseline が赤いと全変異が KILLED に見える。先に確認する。

- [ ] **Step 2: 変異 3 種を 1 件ずつ隔離して当てる**

退避コピーを `.cache/` に取り、1 件ずつ適用して復元する。

| 種 | 変異 | 赤くなるべきテスト |
| --- | --- | --- |
| 検査対象 | fixture の active な Issue の箱を全て `[x]` にする | `InvariantA.test_all_checked_active_issue_is_a_violation` |
| 検査対象 | fixture の `closed/` の status を `open` に戻す | `InvariantB.test_closed_dir_with_open_status_is_a_violation` |
| 機構 | `main()` の `return 1 if violations else 0` を `return 0` に | `InvariantA` / `InvariantB` |
| 機構 | `CLOSED_SEGMENT` の除外を外す | `InvariantA.test_all_checked_closed_issue_passes` |
| 機構 | `total >= 1` のガードを外す | `ParityWithPhaseC3.test_snippet_and_checker_agree` |
| 機構 | `CHECKBOX` から `X` を落とす | `FormatVariants.test_uppercase_x_counts_as_checked` |
| 機構 | フェンス追跡を外す | `FormatVariants.test_boxes_inside_a_code_fence_are_ignored` |
| 取り付け | `.pre-commit-config.yaml` から `entry` 行を消す | `Attachment.test_pre_commit_runs_the_checker` |
| 取り付け | `ci.yml` から `run:` 行を消す | `Attachment.test_ci_runs_the_checker_with_an_explicit_name` |

- [ ] **Step 3: 範囲を数える**

`issue_dirs` が返す集合と、検査が実際に判定した Issue の集合が一致することを確認する。件数ではなくディレクトリ名の集合で照合する。同数の入れ替えは件数では見えない。

- [ ] **Step 4: 復元とツリーの一致を確認する**

Run: `git status --porcelain`
Expected: 変異の痕跡が残っていないこと (Issue の編集を始める前に確認する)

- [ ] **Step 5: ISSUE-41 のタスクを消化してクローズを同梱する**

1. `docs/issues/ISSUE-41_*/issue.md` の `## タスク` を全て `[x]` にする
2. frontmatter を `status: closed` にする
3. `git mv "docs/issues/ISSUE-41_<title>" "docs/issues/closed/ISSUE-41_<title>"`
4. 相対リンクを 3 方向で補正する (D.3)。参照元が `closed/` 配下にあるかで必要な補正が違う
5. 新パスを明示 stage する (D.4)。`git mv` は HEAD の内容を stage するので、frontmatter の編集は unstaged のまま残る

ここで箱を全て `[x]` にしてからクローズを同じコミットに含めないと、Task 5 で取り付けた hook 自身がこのコミットを止める。**それがこの PR の狙いどおりの挙動である。**

- [ ] **Step 6: 全体を検証する**

Run: `pre-commit run --all-files && python3 scripts/run-python-tests.py && python3 scripts/check-issue-closure.py`
Expected: 全 hook Passed / テスト全 PASS / `違反 0 件`

- [ ] **Step 7: コミット**

```bash
git add "docs/issues/closed/ISSUE-41_<title>/issue.md" <補正した参照元>
git commit -F .cache/commit-task9.txt
```

- [ ] **Step 8: PR を作る**

`dev-workflow:pre-merge-quality-gate` を通してから `gh pr create`。PR 本文には
`Closes [ISSUE-41](../../docs/issues/closed/ISSUE-41_<title>/issue.md)` を書かない
(クローズはこの PR の diff そのものであり、マージの帰結ではない)。代わりに同梱した旨を書く。

---

## 自己レビュー結果

**spec 網羅**: spec の各節に対応するタスクを確認した。

| spec の節 | タスク |
| --- | --- |
| 1 検査 (不変条件 A / 母集団 / 終了コード / 書式の揺れ) | Task 1, 2, 4 |
| 1 検査 (不変条件 B) | Task 3 |
| 2 取り付け | Task 5 |
| 3 二重管理の処理 | Task 6 |
| 4 gate の散文 | Task 7 |
| 5.1 保護の判定手順 / 5.2 Phase F / 5.3 PR 規約 | Task 8 |
| テスト計画の変異注入 | Task 9 |
| 射程外 (配布 / Phase E) | タスク化しない。Phase E は別 Issue へ切り出す (Task 9 の後) |

**型の一貫性**: `scan_tasks` は全タスクで `(bool, int, int)`。`collect` は Task 1 で 4-tuple、Task 3 で 5-tuple へ拡張するので、Task 3 の Step 3 で要約 print も差し替える手順を明記した。

**未解決**: Task 9 の後に Phase E の Issue を起票する。plan には含めない (起票は別の作業単位)。
