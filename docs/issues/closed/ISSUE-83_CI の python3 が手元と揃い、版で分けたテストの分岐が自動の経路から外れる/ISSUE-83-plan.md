# 版で変わる stdlib の継ぎ目を外す 実装プラン

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** jev-lint-curated の製品コードから、Python の版で挙動が変わる pathlib の呼び出しを外し、版で期待値を分けるテストを 0 件にしてテストで固定する。

**Architecture:** 確かめられないときに止めたい場所は、葉のモジュール `jevlint_fs.py` の `stat_or_none` (無いときだけ None、ほかの OSError は投げる) に寄せる。止めずに数えるだけの場所は `os.path` に寄せる。パスの解決は `os.path.realpath` にする。残した例外の腕は、実物の条件で全版から踏み、腕ごとの文面を `assertRaisesRegex` で pin する。

**Tech Stack:** Python の標準ライブラリだけ、テストは unittest。

**Spec:** 同じディレクトリの `issue.md` の「決定」節。試作の差分 (参考、そのまま貼らない) は追跡外の `.cache/c3.diff` にある。

## Global Constraints

- 標準ライブラリだけで書く。pytest を入れない (プロジェクトの CLAUDE.md)
- jev-lint-curated の Python は 3.9 で import できる形に保つ。各ファイルの先頭の文は `from __future__ import annotations`、`tomllib` を使わない (`test_jevlint.py` の `test_py39_source` が見る)
- テストに skip と expectedFailure を書かない (`scripts/run-python-tests.py` が赤にする)
- コメントは日本語で WHY を書く。版の差や OS の癖に由来する実装には実測した内容を添える
- errno の値を比べない (ELOOP は macOS と Linux で値が違う)
- ラッパのエラーの文面は日本語。1 行で、値は必要なものだけ

## Review Focus

- root で走らせたとき: 権限 0 のテストは root では赤になる。CI の runner と ISSUE-82 のコンテナは非 root なので許容し、テストの docstring に書く (Task 2)
- 埋め込みの NUL を含むパス: pathlib の `is_file()` は False を返していた。`stat_or_none` は ValueError を None にして保つ (Task 1 のテスト)
- symlink のループの下のパス: どの関数でも `RuntimeError` を投げず、決まった腕の文面で止まる (Task 2・3 のテスト)
- 読めない祖先: sgconfig と pnpm-workspace.yaml の祖先の検査は、確かめられないとき止まる (fail closed)。`count_suffix` だけは止めない (上流を呼んで課金した後の要約なので) (Task 3 のテスト)
- 祖先を set で回す順序は PYTHONHASHSEED で変わる。どの候補から例外が出ても同じ腕の文面になることをテストが前提にしてよい

### Task 1: 葉のモジュール jevlint_fs

**Files:**
- Create: `skills/tooling/jev-lint-curated/scripts/jevlint_fs.py`
- Create: `skills/tooling/jev-lint-curated/scripts/test_jevlint_fs.py`

**Interfaces:**
- Produces: `stat_or_none(path: Path) -> "os.stat_result | None"`。`Path.stat()` を呼び、`FileNotFoundError`・`NotADirectoryError`・`ValueError` のときだけ None、ほかの `OSError` はそのまま投げる。jevlint*.py を 1 つも import しない

- [ ] **Step 1: テストを書く** (`StatOrNoneTests`)
  - `test_regular_file_returns_stat`: 通常ファイルで `stat.S_ISREG(result.st_mode)` が真
  - `test_missing_returns_none` / `test_not_a_directory_parent_returns_none` (親が通常ファイル) / `test_embedded_nul_returns_none` (`Path("a\0b")`)
  - `test_permission_denied_raises`: 権限 0 のディレクトリの下のファイルで `PermissionError`
  - `test_symlink_loop_raises`: 自分を指す symlink の下で `OSError` (型だけを見る。errno は比べない)
- [ ] **Step 2: 失敗を確かめる**: `cd skills/tooling/jev-lint-curated/scripts && python3 -B -m unittest test_jevlint_fs -v` が import の失敗で赤
- [ ] **Step 3: 実装する**。docstring に、pathlib の述語が版で権限エラーを握りつぶすか変わること (3.12 までは投げ、3.14 は False。`stat()` はどの版でも投げる。実測) を書く
- [ ] **Step 4: 緑を確かめる** (同じコマンド。`/usr/bin/python3` の 3.9.6 でも同じコマンドで緑)
- [ ] **Step 5: コミット**

### Task 2: jevlint_host を stat と realpath へ

**Files:**
- Modify: `skills/tooling/jev-lint-curated/scripts/jevlint_host.py` (`host_reusable`、`_reject_pnpm_workspace_ancestor`、`_replace_host_atomically`、モジュールの docstring の import の決まり)
- Test: `skills/tooling/jev-lint-curated/scripts/test_jevlint_host.py`

**Interfaces:**
- Consumes: `jevlint_fs.stat_or_none`
- Produces: 文面 `host を確認できない: ...` (`host_reusable`)、`host の祖先を確認できない: ...` (`_reject_pnpm_workspace_ancestor`)。`host の祖先を解決できない` の腕は無くなる

- [ ] **Step 1: テストを直す・足す**
  - `test_permission_denied_host_directory`: `sys.version_info` の分岐を消し、`assertRaisesRegex(HostError, "^host を確認できない")`
  - `test_symlink_loop_in_cache_path_becomes_hosterror` と `test_permission_denied_ancestor_becomes_hosterror`: `assertRaisesRegex(HostError, "^host の祖先を確認できない")`。版を説明するコメントを消し、文面まで見る理由 (どの腕が止めたかを pin する) を書く
  - `test_host_parent_that_is_a_file_becomes_hosterror` を足す: host の親を通常ファイルにすると `^host の親ディレクトリを作れない` で、`run` は呼ばれない
  - モジュールの docstring から版で期待値を分けている旨の段落を消し、`import sys` が不要になれば消す。権限 0 のテストは root では赤になる旨を 1 文足す
- [ ] **Step 2: 失敗を確かめる**: `python3 -B -m unittest test_jevlint_host -v` で上の 4 件が赤 (3.14.7 で)
- [ ] **Step 3: 実装する**
  - `host_reusable`: 2 つの `is_file()` を `stat_or_none` と `stat.S_ISREG` に替え、`except OSError` を `HostError("host を確認できない: {host}: {error}")` にする。docstring の版差の記述を、確かめられないときは HostError にする、へ直す
  - `_reject_pnpm_workspace_ancestor`: 解決を `Path(os.path.realpath(host))` にし、`try/except (OSError, RuntimeError)` を消す。`candidate.exists()` を `stat_or_none(candidate) is not None` にし、既存の `except OSError` の腕 (`host の祖先を確認できない`) は残す
  - `_replace_host_atomically`: `host.exists()` を `stat_or_none(host) is not None` にする (既存の `except OSError` の中にある)
  - モジュールの docstring の「他の jevlint* モジュールを import しない」を、葉の `jevlint_fs` のほかは import しない、に直す
- [ ] **Step 4: 緑を確かめる**: 3.14.7 と `/usr/bin/python3` (3.9.6) の両方で `test_jevlint_host` が緑
- [ ] **Step 5: コミット**

### Task 3: jevlint_tree を stat と realpath へ

**Files:**
- Modify: `skills/tooling/jev-lint-curated/scripts/jevlint_tree.py` (`tmpdir_from_env`、`reject_sgconfig_in_ancestors`、`expanded_commit` の sgconfig の判定、`count_suffix`、モジュールの docstring)
- Test: `skills/tooling/jev-lint-curated/scripts/test_jevlint_tree.py`

**Interfaces:**
- Consumes: `jevlint_fs.stat_or_none`
- Produces: 文面 `一時ディレクトリを作れない` (ループの TMPDIR。既存の mkdtemp の腕)、`一時ディレクトリの置き場を解決できない` (gettempdir の失敗)、`一時ディレクトリの祖先を確認できない: ...` (新しい。sgconfig の祖先を確かめられないとき)

- [ ] **Step 1: テストを直す・足す**
  - `test_tmpdir_through_a_symlink_loop_is_a_tree_error`: `assertRaisesRegex(TreeError, "^一時ディレクトリを作れない")`、版のコメントを消す
  - `test_no_usable_system_temp_dir_is_a_tree_error` を足す: `mock.patch("jevlint_tree.tempfile.gettempdir", side_effect=FileNotFoundError(...))` の下で `tmpdir_from_env({})` が `^一時ディレクトリの置き場を解決できない` (gettempdir はプロセスで結果をキャッシュするので実物では作れない、とコメント)
  - `reject_sgconfig_in_ancestors` の、権限 0 の祖先の下のパスで `^一時ディレクトリの祖先を確認できない` になるテストを足す
  - `count_suffix` の、権限 0 のディレクトリを対象にしても例外にならず数え続けるテストを足す
- [ ] **Step 2: 失敗を確かめる**: `python3 -B -m unittest test_jevlint_tree -v` で新しい・直したテストが赤
- [ ] **Step 3: 実装する**
  - `tmpdir_from_env`: `try` は `tempfile.gettempdir()` だけを囲んで `except OSError` にし、`RuntimeError` を外す。返り値は `Path(os.path.realpath(candidate))`。docstring の「3.9 では返るパスも相対になる」を「3.11 までは」に直す
  - `reject_sgconfig_in_ancestors`: 解決を `os.path.realpath` にし、`exists()` を `stat_or_none` に。`OSError` は `TreeError("一時ディレクトリの祖先を確認できない: {candidate}: {error}")`
  - `expanded_commit` の sgconfig: `is_dir()` / `is_file()` を 1 回の `stat_or_none` と `stat.S_ISDIR` / `stat.S_ISREG` にする。`OSError` は既存の文面に合わせて TreeError
  - `count_suffix`: `target.is_file()` を `os.path.isfile(target)` にし、止めない理由 (上流を呼んで課金した後の要約) を 1 行書く
- [ ] **Step 4: 緑を確かめる**: 3.14.7 と 3.9.6 の両方で `test_jevlint_tree` が緑
- [ ] **Step 5: コミット**

### Task 4: --json-out の保存先の検査

**Files:**
- Modify: `skills/tooling/jev-lint-curated/scripts/jevlint.py` (`_check_out_path`)
- Test: `skills/tooling/jev-lint-curated/scripts/test_jevlint.py`

**Interfaces:**
- Consumes: `jevlint_fs.stat_or_none`
- Produces: 文面 `--json-out の保存先を確かめられない: {shown!r}` (UsageError。新しい)

- [ ] **Step 1: テストを足す**: 権限 0 のディレクトリの下を保存先にすると、上流を起動する前に `^jevlint: --json-out の保存先を確かめられない` で終了コード 2 (既存の `--json-out` のテストと同じ形で main を呼ぶ)
- [ ] **Step 2: 失敗を確かめる** (3.14.7 では今は「親ディレクトリが無い」になるので赤)
- [ ] **Step 3: 実装する**: `path.resolve()` を `Path(os.path.realpath(path))` に、`is_dir()` の 2 つを `stat_or_none` と `stat.S_ISDIR` に。`OSError` を上の UsageError にする
- [ ] **Step 4: 緑を確かめる** (3.14.7 と 3.9.6)
- [ ] **Step 5: コミット**

### Task 5: 版の分岐と pathlib の述語をテストで禁じる

**Files:**
- Modify: `scripts/test_run_python_tests.py`、`scripts/run-python-tests.py` (docstring の限界の節に 1 行)
- Modify: `skills/tooling/jev-lint-curated/scripts/test_jevlint.py` (`Py39SourceTests` の隣)

**Interfaces:**
- Consumes: `runner.ROOT`、`runner.SKIP_DIRS` (test_run_python_tests.py が既に読み込んでいる runner)

- [ ] **Step 1: テストを書く**
  - `VersionBranchBan.test_no_version_references_in_repository`: `ROOT.rglob("*.py")` から `SKIP_DIRS` を相対パスの成分で除き、AST で `version_info` / `hexversion` を名前に持つ `ast.Attribute` と `ast.Name` を集めて 0 件。見たファイル数が 0 でないことも assert する
  - 対照 2 つ: 合成したソース `import sys\nif sys.version_info >= (3, 13):\n    pass\n` は検出され、`s = "sys.version_info"` は検出されない (検出の関数を 1 つにして両方に当てる)
  - `PathlibPredicateBan.test_no_pathlib_predicates_in_product_modules` (test_jevlint.py): テスト以外の `jevlint*.py` で、`exists` / `is_file` / `is_dir` / `is_symlink` / `resolve` の呼び出しのうち、受け手が `os.path` でないものが 0 件。対照: 合成の `p.is_file()` は検出、`os.path.isfile(p)` と `os.path.exists(p)` は非検出
- [ ] **Step 2: 失敗を確かめる**: Task 2〜4 の後なら緑のはず。変異として `test_jevlint_host.py` に `sys.version_info` の参照を 1 行足すと赤になり、戻すと緑になることを確かめる (コミットしない)
- [ ] **Step 3: runner の docstring**: 限界の節に「テストの中で版によって通る経路は ID の集合に現れない。その形は test_run_python_tests.py のテストが禁じる」を 1 行足す
- [ ] **Step 4: 全体の緑を確かめる**: `python3 scripts/run-python-tests.py --update-manifest` のあと `python3 scripts/run-python-tests.py` が rc=0
- [ ] **Step 5: コミット** (manifest の diff ごと)

### Task 6: 検証と記録 (コントローラが行う)

- [ ] 変異注入: 残した腕 (`host_reusable` の `except OSError`、祖先の `except OSError`、`tmpdir_from_env` の `except OSError`、`reject_sgconfig_in_ancestors` の新しい腕、`_check_out_path` の新しい腕) を 1 つずつ外すと、3.14.7 と 3.9.6 の両方で対応するテストが赤になり、戻すと緑になることを、隔離コピーで確かめる
- [ ] `pre-commit run --all-files` が rc=0
- [ ] ISSUE-83 に「結果」節 (変異注入の結果、版ごとの実行) を書き、タスク 2 を [x] にしてクローズを同梱する。ISSUE-84 に判定役の側の材料、ISSUE-82 の完了の定義 5 の文言を直す
