---
status: open
---

# feat: jev-lint-curated で MoonBit の rule を走らせる

## 背景

ISSUE-65 で、jev-lint の厳選した rule の一覧に moonbit の 7 項目を載せた (ユーザー裁定)。ただし MoonBit は ast-grep に文法が組み込まれていないので、ビルドした tree-sitter の parser (プラットフォームごとのネイティブライブラリ) を jev-lint の設定の `languages:` で宣言しないと走らない。宣言が無いと上流は moonbit の rule を落とす。

jev-lint-curated は、設定の `languages:` を書き出さない設計にしている。`libraryPath` のライブラリは、キーを持つ上流のプロセスが起動する ast-grep に読み込まれるので、消費側のリポジトリの中身がそのパスを決められる形を作らないためである。そのため ISSUE-65 では、moonbit の 7 項目は一覧に載せて compat で追従するだけにし、parser の対応をこの Issue に切り出した。いまは要約で、対象の範囲にある `.mbt` の本数を「parser が無いので見ていない」として出している。

ISSUE-65 のブレインストーミングで挙がって、まだ採っていない形は 2 つある。

- 利用者がビルドした parser のパスを実行時の引数で受け、ラッパが `languages.moonbit` を書き出す。パスを決めるのは利用者の意思なので、コミットされた中身が決める形にはならない
- ラッパが tree-sitter-moonbit の commit を pin してビルドする。tree-sitter の CLI と C コンパイラが要り、pin するものと compat で見るものが増える

## タスク

- [ ] parser の用意の仕方を決める (上の 2 つ、または別の形)
- [ ] 決めた形で、ネイティブライブラリを読み込ませる経路が消費側の中身から決められないことをテストで押さえる
- [ ] MoonBit のコードで精度を測る (ISSUE-65 の測定には MoonBit のリポジトリが無かった)

## 関連

ISSUE-65 (jev-lint-curated の取り込み。moonbit の 7 項目を一覧に載せた)
