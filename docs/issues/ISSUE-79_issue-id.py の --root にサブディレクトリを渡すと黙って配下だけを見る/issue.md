---
status: open
---

# fix: issue-id.py の --root にサブディレクトリを渡すと黙って配下だけを見る

## 背景

`issue-id.py` の `resolve_root` は、明示された `--root` を解決するだけで、そこがリポジトリの top-level かを確かめない。一方、Issue ディレクトリの列挙は root を起点に `docs/issues/` を相対で引くので、サブディレクトリを渡すと配下しか見ない。`--next --root <リポジトリ>/plugins` は既存と重複する識別子 (`ISSUE-1`) を終了コード 0 で返し、`--check --root <リポジトリ>/plugins` は「検査した Issue ディレクトリ: 0 個」「違反なし」の 0 になった (2026-09-27 に実測)。

pre-commit と CI は `--root` を渡さないので、今の実害はテストや手で起動したときに限られる。層 2 の `resolve_root` は、`git ls-files` が cwd 相対でサブディレクトリから起動すると配下しか返さないことを理由に、`git rev-parse --show-toplevel` で top-level を求めている。

## タスク

- [ ] 明示された `--root` が `git rev-parse --show-toplevel` の結果と一致するかを確かめ、一致しなければ終了コード 2 にする。サブディレクトリを渡す形をテストで押さえる

## 関連

ISSUE-77 (この経路を見つけたマージ前ゲートの対象。空の `--root` と繰り返しの `--root` はあちらで拒否した)
