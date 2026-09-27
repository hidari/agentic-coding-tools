---
status: open
---

# fix: issue-id.py の CLI に、検査不能が緑や検出に化ける経路が残る

## 背景

ISSUE-78 と ISSUE-79 のマージ前ゲートで、敵対的な検証役が次の 2 つを見つけた。どちらも main でも同じで、あの PR の変更が持ち込んだものではない。2026-09-28 に手元で再現した。

- `GIT_DIR` を設定し `GIT_WORK_TREE` を設定しない環境では、git は起動したディレクトリを作業ツリーの top-level とみなす。`git -C <リポジトリ>/docs rev-parse --show-toplevel` は `<リポジトリ>/docs` を返すので、`issue-id.py --check --root <リポジトリ>/docs` は root を正規化しても配下しか見ず、「検査した Issue ディレクトリ: 0 個 / 走査したファイル: 0 個」の終了コード 0 になった。git は linked worktree からコミットするとき hook へ `GIT_DIR` を渡す (検証役の実測)。pre-commit と CI は `--root` を渡さず、hook は作業ツリーの top-level で走るので、今の実害は `--root` を付けて手やエージェントが起動したときに限られる。同じ `resolve_root` を借りる `scripts/check-related-refs.py` と `scripts/check-issue-closure.py` も同じ形になる
- CPython 3.9.6 の argparse は `-h=` (値が空) で `error()` を通らずに IndexError を投げる。`issue-id.py`・`scripts/check-related-refs.py`・`scripts/check-issue-closure.py` の 3 本とも traceback を出して終了コード 1 になり、検査不能が違反として報告される。3.11.15 以降は usage の終了コード 2 になる。層 2 の同じ経路は ISSUE-78 で塞いだ

## タスク

- [ ] root を求める rev-parse を、`GIT_DIR` と `GIT_WORK_TREE` の影響を受けない形にするか、影響を受けたことを検出して終了コード 2 にするかを決めて直す。`GIT_DIR` だけを設定してサブディレクトリを渡す形をテストで押さえる
- [ ] 3 本の parse を例外の受けの内側に入れ、argparse 自身の例外を終了コード 2 にする。版を問わず到達させるため、テストは parse の失敗を注入で作る

## 関連

ISSUE-78 (層 2 の同じ argparse の経路を塞いだ)
ISSUE-79 (root の正規化。この Issue の 1 つ目はその網から漏れた経路)
