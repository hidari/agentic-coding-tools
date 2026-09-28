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

## タスク

- [ ] 3.13 未満の分岐を自動の経路へ戻すかと、戻し方を決める。候補は、手元の pre-commit で別の版の python3 でもテストを回す、CI に 24.04 の job を残す、など。CI に `setup-python` を置かない方針と CI の費用との兼ね合いも含めて決める
- [ ] 決めた形を入れ、`sys.version_info` で分けたテストのどちらの分岐も、どこかの自動の経路で走ることを示す

## 関連

ISSUE-80 (版による argparse の挙動差を扱う)
ISSUE-82 (CI の runner を ubuntu-26.04 へ移す。この Issue のきっかけ)
