---
status: closed
---

# test: CI の python3 が手元と揃い、版で分けたテストの分岐が自動の経路から外れる

## 背景

このリポジトリの Python は 3.9 で動く形に保っている (例: `plugins/dev-workflow/skills/commit-and-pr-message/scripts/check-outgoing-text.py` の docstring、`skills/tooling/jev-lint-curated/scripts/test_jevlint.py` の `test_py39_source`)。標準ライブラリの挙動が版で違う経路は、`sys.version_info` で期待値を分けたテストで押さえている。2026-09-28 時点では `skills/tooling/jev-lint-curated/scripts/test_jevlint_host.py` の 1 箇所で、3.13 以上とそれ未満とで `pathlib` の `PermissionError` の扱いが違う。

2026-09-28 時点で、自動の経路が使う python3 は次のとおり。

- CI: runner の `python3`。`ubuntu-latest` (24.04) では 3.12 系
- 手元の pre-commit: `language: system` の hook が `#!/usr/bin/env python3` を起動する。開発機では Homebrew の 3.14 系 (3.14.7 を実測)

今は 3.13 未満の側を CI だけが走らせている。ISSUE-82 で CI を ubuntu-26.04 (python3 は 3.14 系) へ移すと、どちらの経路も 3.13 以上になり、3.13 未満の側はどこでも走らなくなる。`ubuntu-latest` のままでも、2026-11-19 までに同じことになる。テストは skip ではなく版ごとの期待値を検証する形なので赤にならず、`scripts/run-python-tests.py` が照合するテスト ID の集合も変わらない。分岐が走らなくなったことに、どの検査も気づかない。

## 決定 (2026-09-29、ユーザー裁定)

分岐を自動の経路へ戻すのではなく、版で挙動が変わる stdlib の継ぎ目を製品から外して、分岐そのものを消す。

- 数え直すと、版で結果が変わる箇所は 1 つではなかった。jev-lint-curated の本体の例外の腕のうち 4 つ (host の `is_file()` の PermissionError、祖先の `resolve()` の RuntimeError と `exists()` の PermissionError、`tmpdir_from_env` の `resolve()` の RuntimeError) は、今は CI の 3.12 でしか通らない。期待値を版で分けていたテストは 1 つだけで、残りの 3 つはテストが例外の型だけを見ていたので、新しい版では別の腕を通って緑のままだった (3.12.12 と 3.14.7 に変異を入れて実測)
- 権限 0 の下の `stat()` は、どの版でも PermissionError を投げる。`os.path.realpath()` は symlink のループでも例外にならない。`is_file()` / `exists()` は 3.12 までは投げ、3.14 では False を返す。`resolve()` は 3.12 までは RuntimeError にする (3.9.6・3.11.15・3.12.12・3.14.7 で実測、macOS)。確かめられないときに止めたい場所は `stat()` に、止めずに数えるだけの場所は `os.path` に寄せる
- 読めない host に対する `host_reusable` は、どの版でも HostError にそろえる。docstring が書く「判定不能を「無い」に丸めない」に合わせるためで、3.13 以上では挙動が変わる
- `sys.version_info` / `sys.hexversion` の参照は、リポジトリ全体でテストが禁じる。jev-lint-curated の製品コードでは pathlib の述語 (`exists` / `is_file` / `is_dir` / `is_symlink` / `resolve`) もテストが禁じる
- ISSUE-82 より前に入れる
- 採らなかった案: 手元の pre-commit に uv 経由の古い版で全テストを回す hook を足す案は、uv と開発機ごとの版が要り、網が手元の 1 層だけになる。CI の python-tests job で runner の toolcache の古い版でも回す案は、ISSUE-82 の後にしか入れられず、pin できない toolcache への依存が増える。どちらも古い側を走らせ続けるだけで、別の腕を通って緑になる形は検出に変わらない。検証系の下限 (3.11) を CI で守る floor は起票しない

## タスク

- [x] 3.13 未満の分岐を自動の経路へ戻すかと、戻し方を決める。候補は、手元の pre-commit で別の版の python3 でもテストを回す、CI に 24.04 の job を残す、など。CI に `setup-python` を置かない方針と CI の費用との兼ね合いも含めて決める
- [x] `sys.version_info` で期待値を分けたテストが 0 件であることを検査で固定し、残した腕を外すと手元で測った 3.14.7 と 3.9.6 の両方で赤になることを、変異注入で示す (CI の 3.12 での結果は、この変更の PR の CI が最初の測定)

## 結果 (2026-09-29)

実装の手順は同じディレクトリの `ISSUE-83-plan.md` にある。

変えたこと:

- 葉のモジュール `jevlint_fs.py` に `stat_or_none` を置いた。無いとき (FileNotFoundError・NotADirectoryError・ValueError) だけ None を返し、ほかの OSError は投げる。確かめられないときに止める場所 (host の再利用、既存の host を退避する `_replace_host_atomically`、pnpm-workspace.yaml と sgconfig の祖先、展開した木の sgconfig、`--json-out` の保存先) はこれを使う。`count_suffix` は上流を呼んで課金した後の要約なので、`os.path.isfile` で止めない。包含を見る `jevlint_tree.is_inside` も `os.path.samefile` の OSError を飛ばし、止めない
- 祖先の検査 (pnpm-workspace.yaml と sgconfig) は `jevlint_fs.find_in_ancestors` にまとめた。与えられた表記の祖先と `os.path.realpath` で解決した表記の祖先を、それぞれ近い順に見て、確かめられない候補の OSError はそのまま投げる。呼ぶ側は OSError を「確認できない」に、当たりを「がある」に写すだけにした
- パスの解決は `os.path.realpath` にし、`Path.resolve()` の RuntimeError の腕を消した。symlink を通った祖先が解決の後にしか見えない形は、`test_jevlint_fs.py` の `FindInAncestorsTests` が押さえる
- 読めない host に対する `host_reusable` は、測った 3.14.7 と 3.9.6 の両方で HostError になった (3.14 では False から変わった。3.13 は未測定)。確かめられないときの文面は `一時ディレクトリの祖先を確認できない` (sgconfig の祖先) と `--json-out の保存先を確かめられない` (OS のエラーを添える) が加わった
- 残した腕は、実物の条件 (chmod 0、symlink のループ、symlink を通った祖先) で 3.14.7 と 3.9.6 から踏み、腕ごとの文面を pin した。実物では作れない 2 つの条件 (gettempdir が使える候補を持たない、展開した木の sgconfig を stat できない) だけは注入で作った
- `scripts/test_run_python_tests.py` の VersionBranchBan が、リポジトリ全体で `sys.version_info` / `sys.hexversion` の明示的な参照 (別名の import を含む) を禁じる。`test_jevlint.py` の PathlibPredicateBan が、jev-lint-curated の製品モジュールで pathlib の 5 つの述語 (`exists` / `is_file` / `is_dir` / `is_symlink` / `resolve`) の参照を禁じる。どちらも stdlib の挙動差で暗黙に分かれる経路は見ない

検証 (7ef43cb):

- 3.14.7: `scripts/run-python-tests.py` が 17 ファイル / 1073 件で manifest と一致し、rc=0
- 3.9.6 (`/usr/bin/python3`): jev-lint-curated の 6 ファイル 321 件と `scripts/test_run_python_tests.py` の 26 件が OK。リポジトリ全体の runner は、この変更と関係のない test_winvm / test_macvm が `enterContext` (3.11+) で落ちるので 3.9.6 では回していない
- 3.12 系を自動で走らせるのは、この変更の PR の CI の python-tests (ubuntu-latest の 3.12) だけで、Linux での最初の実行もそこになる

変異注入 (`git archive 7ef43cb` を展開した隔離コピー、uid 501 の非 root): 変異を 1 つずつ当て、製品コードの変異は jev-lint-curated のテスト 6 モジュールすべてを、テストの検出関数の変異はそのモジュールを、3.14.7 と 3.9.6 で走らせた。次の 23 の変異は、どれも両方の版で赤になった。各変異の後で対象ファイルを元のバイト列へ戻し、全変異の後に全モジュールを両方の版で走らせて緑を確かめた。どの実行でもモジュールごとのテストの件数は変異の前と同じだった。版で失敗の集合 (テスト名と FAIL / ERROR の別) が割れたのは N15 だけである。

- N1 `host_reusable` の stat の OSError を False に丸める → `test_permission_denied_host_directory`
- N2 `stat_or_none` が PermissionError も None にする → `test_permission_denied_raises`、`FindInAncestorsTests.test_permission_denied_ancestor_raises`、`test_permission_denied_host_directory`、`test_unverifiable_ancestor_becomes_a_tree_error`、`--json-out` の権限の 2 本
- N3 `find_in_ancestors` から解決した表記の祖先を外す → `test_found_only_in_an_ancestor_of_the_resolved_notation`
- N4 `find_in_ancestors` から与えられた表記の祖先を外す → `test_found_only_in_an_ancestor_of_the_given_notation`
- N5 `find_in_ancestors` の中で候補の OSError を握りつぶす → `FindInAncestorsTests` の権限とループの 2 本、`test_symlink_loop_in_cache_path_becomes_hosterror`、`test_unverifiable_ancestor_becomes_a_tree_error`
- N6 `find_in_ancestors` の祖先を遠い順に回す → `test_the_nearest_ancestor_of_the_given_notation_comes_first`
- N7 `find_in_ancestors` が最初の名前だけを見る → `test_every_name_is_looked_for`、`test_sgconfig_in_an_ancestor_is_rejected_by_its_name`
- N8 `_reject_pnpm_workspace_ancestor` が OSError を HostError に写さない → `test_symlink_loop_in_cache_path_becomes_hosterror` (ERROR)
- N9 `_reject_pnpm_workspace_ancestor` が当たりを拒否しない → `test_pnpm_workspace_ancestor_is_rejected`
- N10 `reject_sgconfig_in_ancestors` が OSError を TreeError に写さない → `test_unverifiable_ancestor_becomes_a_tree_error` (ERROR)
- N11 `reject_sgconfig_in_ancestors` が当たりを拒否しない → `test_sgconfig_in_an_ancestor_is_rejected_by_its_name`、`expanded_commit` 越しの 2 本 (`test_rejects_when_an_ancestor_of_the_tree_holds_sgconfig`、`test_failure_before_registration_leaves_other_stale_worktrees_alone`)、main の経路の `test_sgconfig_above_the_scratch_is_2_before_any_config_is_loaded`
- N12 `tmpdir_from_env` の gettempdir の OSError を握りつぶす → `test_no_usable_system_temp_dir_is_a_tree_error`
- N13 `expanded_commit` の展開後の sgconfig の OSError の腕を握りつぶす → `test_permission_error_on_the_post_materialize_sgconfig_stat_is_a_tree_error`
- N14 `count_suffix` を fail-closed にする → `test_permission_denied_file_target_does_not_raise_and_counts_zero_for_it` (ERROR)
- N15 `_check_out_path` の realpath を `resolve()` に戻す → 3.14.7 では PathlibPredicateBan の `test_no_pathlib_predicates_in_product_modules` だけ、3.9.6 ではそれに加えて `test_json_out_through_a_symlink_loop_is_2_before_the_upstream`。3.14.7 だけで回すと、この変異を捕まえるのは禁止のテストだけになる
- N16 `_check_out_path` の OSError の腕を握りつぶす → `--json-out` の権限の 2 本と symlink のループの 1 本
- N17 `_replace_host_atomically` が既存の host を退避しない → `test_version_mismatch_rebuilds_host` (ERROR)
- N18〜N20 VersionBranchBan の検出から属性・名前・import 文の分岐をそれぞれ外す → 対応する陽性の対照 (import 文の分岐を外すと、名前の対照と別名なしの import の対照も落ちる)
- N21 VersionBranchBan が import 文を別名付きのときだけ数える → `test_synthetic_by_name_version_check_is_detected`、`test_unaliased_import_alone_is_detected`
- N22〜N23 PathlibPredicateBan から `resolve` を外す、呼び出さない参照を見なくする → 対応する陽性の対照

祖先の検査の腕のうち、解決した表記の祖先 (N3)・与えられた表記の祖先 (N4)・順序 (N6) を捕まえるのは `FindInAncestorsTests` だけで、呼ぶ側のテストが持つのは写し (N8・N10) と拒否 (N9・N11) の文面の pin である。N5 で host のテストが赤になるのは接頭辞の文面を pin しているからである。そのテストの形 (host の経路そのものに symlink のループがある) では、祖先の検査が OSError を握っても、後に続く `host_reusable` の stat が同じ OSError を HostError にして止める。候補そのものだけが確かめられない形 (祖先の pnpm-workspace.yaml が自己ループの symlink である等) では、N5 の下では握りつぶされて取得へ進む。host の側でこの形を捕まえるのは `FindInAncestorsTests` の権限とループのテストだけで、host の呼ぶ側のテスト (`prepare_host` 経由) には無い。tree の呼ぶ側の `test_unverifiable_ancestor_becomes_a_tree_error` は同じ形 (権限の無いディレクトリの下の候補) を使い、後に続く止めが無いので N5 でも落ちる (コードを読んで判断した。この変異の下での文面は記録していない)。

## 関連

ISSUE-80 (版による argparse の挙動差を扱う)
ISSUE-82 (CI の runner を ubuntu-26.04 へ移す。この Issue のきっかけ)
