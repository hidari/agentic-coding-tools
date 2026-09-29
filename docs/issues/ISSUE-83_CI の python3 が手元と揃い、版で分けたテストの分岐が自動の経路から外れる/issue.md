---
status: open
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
- [ ] `sys.version_info` で期待値を分けたテストが 0 件であることを検査で固定し、残した腕を外すと CI と手元のどちらの版でも赤になることを、変異注入で示す

## 結果 (2026-09-29)

実装の手順は同じディレクトリの `ISSUE-83-plan.md` にある。

変えたこと:

- 葉のモジュール `jevlint_fs.py` に `stat_or_none` を置いた。無いとき (FileNotFoundError・NotADirectoryError・ValueError) だけ None を返し、ほかの OSError は投げる。確かめられないときに止める場所 (host の再利用、pnpm-workspace.yaml と sgconfig の祖先、展開した木の sgconfig、`--json-out` の保存先) はこれを使う。`count_suffix` だけは上流を呼んで課金した後の要約なので、`os.path.isfile` で止めない
- パスの解決は `os.path.realpath` にし、`Path.resolve()` の RuntimeError の腕を消した。symlink を通った祖先が解決の後にしか見えない形を、pnpm-workspace.yaml と sgconfig の両方でテストが押さえる
- 読めない host に対する `host_reusable` は、どの版でも HostError になった (3.13 以上では False から変わった)。確かめられないときの文面は `一時ディレクトリの祖先を確認できない` (sgconfig の祖先) と `--json-out の保存先を確かめられない` (OS のエラーを添える) が加わった
- 残した腕は、実物の条件 (chmod 0、symlink のループ、symlink を通った祖先) で全版から踏み、腕ごとの文面を pin した。実物では作れない 2 つの条件 (gettempdir が使える候補を持たない、展開した木の sgconfig を stat できない) だけは注入で作った
- `scripts/test_run_python_tests.py` の VersionBranchBan が、リポジトリ全体で `sys.version_info` / `sys.hexversion` の明示的な参照 (別名の import を含む) を禁じる。`test_jevlint.py` の PathlibPredicateBan が、jev-lint-curated の製品モジュールで pathlib の 5 つの述語 (`exists` / `is_file` / `is_dir` / `is_symlink` / `resolve`) の参照を禁じる。どちらも stdlib の挙動差で暗黙に分かれる経路は見ない

検証 (0514f5e):

- 3.14.7: `scripts/run-python-tests.py` が 17 ファイル / 1067 件で manifest と一致し、rc=0
- 3.9.6 (`/usr/bin/python3`): jev-lint-curated の 6 ファイル 315 件と `scripts/test_run_python_tests.py` の 26 件が OK。リポジトリ全体の runner は、この変更と関係のない test_winvm / test_macvm が `enterContext` (3.11+) で落ちるので 3.9.6 では回していない
- 3.12 系を自動で走らせるのは、この PR の CI の python-tests (ubuntu-latest の 3.12) だけで、Linux での最初の実行もそこになる

変異注入 (`git archive 0514f5e` を展開した隔離コピー、非 root): 次の 16 の変異は、どれも 3.14.7 と 3.9.6 の両方で赤になり、戻すと両方で緑になった。

- M1 `host_reusable` の stat の OSError を False に丸める → `test_permission_denied_host_directory`
- M2 `_reject_pnpm_workspace_ancestor` から解決後の祖先を落とす → `test_pnpm_workspace_ancestor_found_only_via_realpath_is_rejected`
- M3 同じ関数の OSError の腕を握りつぶす → `test_permission_denied_ancestor_becomes_hosterror`
- M4 `stat_or_none` が PermissionError も None にする → `test_permission_denied_raises`
- M5 `tmpdir_from_env` の gettempdir の OSError を握りつぶす → `test_no_usable_system_temp_dir_is_a_tree_error`
- M6 `reject_sgconfig_in_ancestors` から解決後の祖先を落とす → RejectSgconfigInAncestorsTests の symlink を通った祖先のテスト
- M7 同じ関数の OSError の腕を握りつぶす → RejectSgconfigInAncestorsTests の権限のテスト
- M8 `expanded_commit` の sgconfig の OSError の腕を握りつぶす → 注入のテスト
- M9 `count_suffix` を fail-closed にする → CountSuffixTests の権限のテスト
- M10 `_check_out_path` の realpath を `resolve()` に戻す → 3.14.7 では PathlibPredicateBan、3.9.6 では symlink のループの `--json-out` のテスト
- M11 `_check_out_path` の OSError の腕を握りつぶす → `--json-out` の権限のテスト
- M12〜M14 VersionBranchBan の検出から属性・名前・別名の import をそれぞれ外す → 対応する陽性の対照
- M15〜M16 PathlibPredicateBan から `resolve` を外す、呼び出さない参照を見なくする → 対応する陽性の対照

## 関連

ISSUE-80 (版による argparse の挙動差を扱う)
ISSUE-82 (CI の runner を ubuntu-26.04 へ移す。この Issue のきっかけ)
