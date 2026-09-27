---
status: open
---

# fix: issue-id.py の CLI に、検査不能が緑や検出に化ける経路が残る

## 背景

ISSUE-78 と ISSUE-79 のマージ前ゲートで、敵対的な検証役が次の 2 つを見つけた。どちらも main でも同じで、あの PR の変更が持ち込んだものではない。2026-09-28 に手元で再現した。

- `GIT_DIR` を設定し `GIT_WORK_TREE` を設定しない環境では、git は起動したディレクトリを作業ツリーの top-level とみなす。`git -C <リポジトリ>/docs rev-parse --show-toplevel` は `<リポジトリ>/docs` を返し、`--show-prefix` は空を返す。そのため `issue-id.py --check --root <リポジトリ>/docs` は top-level かの確かめ (ISSUE-79 の show-prefix による拒否) を通って配下しか見ず、「検査した Issue ディレクトリ: 0 個 / 走査したファイル: 0 個」の終了コード 0 になった (2026-09-28 に show-prefix の版でも再現した)。`--root` を渡さずにサブディレクトリから起動しても、`--show-toplevel` が起動したディレクトリを返すので同じ形になる (検証役の実測)。`GIT_WORK_TREE` だけをサブディレクトリに向けた形や、`GIT_DIR` が別のリポジトリを指す形でも、終了コード 0 のまま別の範囲を見た (検証役の実測)。git は linked worktree からコミットするとき hook へ `GIT_DIR` を渡す (検証役の実測)。pre-commit と CI は `--root` を渡さず、hook は作業ツリーの top-level で走るので、今の実害は、これらの変数が設定された環境で手やエージェントがサブディレクトリを起点に起動したとき (`--root` の有無を問わない) に限られる。同じ `resolve_root` を借りる `scripts/check-related-refs.py` と `scripts/check-issue-closure.py` も同じ形になる
- CPython 3.9.6 の argparse は `-h=` (値が空) で `error()` を通らずに IndexError を投げる。`issue-id.py`・`scripts/check-related-refs.py`・`scripts/check-issue-closure.py` の 3 本とも traceback を出して終了コード 1 になり、検査不能が違反として報告される。3.11.15 以降は usage の終了コード 2 になる。層 2 の同じ経路は ISSUE-78 で塞いだ

## タスク

- [ ] root を決める 2 つの rev-parse (`--root` があるときの `--is-inside-work-tree` と `--show-prefix`、無いときの `--show-toplevel`) を、`GIT_DIR` と `GIT_WORK_TREE` の影響を受けない形にするか、影響を受けたことを検出して終了コード 2 にするかを決めて直す。`GIT_DIR` だけを設定してサブディレクトリを起点にする形を、`--root` の有無の両方でテストで押さえる
- [ ] 3 本の parse を例外の受けの内側に入れ、argparse 自身の例外を終了コード 2 にする。版を問わず到達させるため、テストは parse の失敗を注入で作る

## 関連

ISSUE-78 (層 2 の同じ argparse の経路を塞いだ)
ISSUE-79 (top-level でない root の拒否。この Issue の 1 つ目はその網から漏れた経路)
