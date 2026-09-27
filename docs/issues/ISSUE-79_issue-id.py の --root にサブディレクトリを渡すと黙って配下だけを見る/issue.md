---
status: in_progress
---

# fix: issue-id.py の --root にサブディレクトリを渡すと黙って配下だけを見る

## 背景

`issue-id.py` の `resolve_root` は、明示された `--root` を解決するだけで、そこがリポジトリの top-level かを確かめない。一方、Issue ディレクトリの列挙は root を起点に `docs/issues/` を相対で引くので、サブディレクトリを渡すと配下しか見ない。`--next --root <リポジトリ>/plugins` は既存と重複する識別子 (`ISSUE-1`) を終了コード 0 で返し、`--check --root <リポジトリ>/plugins` は「検査した Issue ディレクトリ: 0 個」「違反なし」の 0 になった (2026-09-27 に実測)。

pre-commit と CI は `--root` を渡さないので、今の実害はテストや手で起動したときに限られる。層 2 の `resolve_root` は、`git ls-files` が cwd 相対でサブディレクトリから起動すると配下しか返さないことを理由に、`git rev-parse --show-toplevel` で top-level を求めている。

一致は文字列ではなく同じディレクトリか (inode) で見る。大小を区別しない APFS では、大小を変えた `--root` を git は正規の綴りで返すので、`resolve()` 後の文字列比較は偽になり、正しい root を拒否する (2026-09-28 に実測。symlink 経由と `/var` 配下の一時ディレクトリは `resolve()` でも一致した)。

## タスク

- [ ] 明示された `--root` で `git -C <root> rev-parse --show-toplevel` を呼び、結果が `--root` と同じディレクトリでなければ終了コード 2 にする。git でない root は rev-parse の失敗として終了コード 2 にする
- [ ] サブディレクトリを渡す形を `--next` と `--check` でテストし、終了コード 2 と、`--next` が識別子を出さないことを押さえる。大小を変えた `--root` が通ることもテストする (大小を区別するファイルシステムでは skip)。一致の確認を外すとサブディレクトリのテストが赤くなることを変異注入で確かめる
- [ ] git でない root を渡す既存のテスト 2 本 (`--next` と `--check` の `test_non_git_root_is_exit_2`) の期待を rev-parse の失敗へ書き換える。`--check` 側は「走査対象ゼロ」の経路と区別できることを保つ。`--next` 側が押さえていた for-each-ref の失敗の経路は、git でない root からは届かなくなる

## 関連

ISSUE-77 (この経路を見つけたマージ前ゲートの対象。空の `--root` と繰り返しの `--root` はあちらで拒否した)
