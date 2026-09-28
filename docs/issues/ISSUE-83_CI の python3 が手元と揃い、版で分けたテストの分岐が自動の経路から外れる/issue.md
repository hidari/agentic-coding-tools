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

## 関連

ISSUE-80 (版による argparse の挙動差を扱う)
ISSUE-82 (CI の runner を ubuntu-26.04 へ移す。この Issue のきっかけ)
