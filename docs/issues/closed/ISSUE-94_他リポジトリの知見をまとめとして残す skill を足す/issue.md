---
status: closed
---

# feat: 他リポジトリの知見をまとめとして残す skill を足す

## 背景

別のリポジトリで作業したセッションが得た知見を、集約先のリポジトリへ届ける仕組みを作っている (dotfiles の ISSUE-126)。セッションはまとめを自リポジトリの `.cache/` に書き、集約先側の hook がセッションの終わりに届ける。まとめの置き場は hook が SessionStart の告知と Stop の案内で告げる。

この skill が持つのはまとめの書式と書き方だけで、置き場は決めない。hook は skill とは別の経路で配られるため、hook が無い環境でも skill は使える形にする (その場合は自動では届かないので、まとめのファイルの場所をユーザーに伝える)。

## タスク

- [x] `skills/meta/cross-repo-learnings/` に SKILL.md と template.md を足す
- [x] README を `scripts/gen-readme.py` で再生成する

## 関連

dotfiles の ISSUE-126 (集約先の hook と、まとめの置き場の仕組み)
