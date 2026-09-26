---
status: open
---

# fix: 層 2 の argparse のエラーが option の値を印字する

## 背景

層 2 (`check-leak-guard-denylist.py`) は語もパスも印字しない設計で、argparse の `unrecognized arguments` が argv を並べる経路は `parse_known_args` で塞いである (ISSUE-15)。値をそのまま印字する経路がもう 1 つ残っている。store_true の `--check` に `=値` を付けると、argparse が `error: argument --check: ignored explicit argument '<値>'` を stderr へ出す。`--check-text=<path>` のつもりで `--check=<path>` と書き損じると、パスがそのまま出る (2026-09-27 に実測)。ISSUE-77 のマージ前ゲートの検証役は、`--help=<値>` も同じで、CPython 3.9.6 から 3.14.7 まで変わらないと報告した。

入口の `check-outgoing-text.py` は層 2 の stderr を捕まえて座標だけを読むので、入口を通る経路では外へ出ない。出るのは、pre-commit の Failed ブロックと、層 2 を直接起動した端末やログである。

## タスク

- [ ] argparse が値を印字する経路を列挙し、option 名だけを出して終了コード 2 にする形を決めて直す。候補は `exit_on_error=False` で `ArgumentError` を受ける形で、版によって対象になるエラーが違うので、確かめた版を書く
- [ ] `=` の値に目印を入れた `--check=<目印>` と `--help=<目印>` が目印を出さないことを、テストで押さえる

## 関連

ISSUE-15 (argparse が余分な引数を印字する経路を `parse_known_args` で塞いだ。この Issue はその網から漏れた経路)
ISSUE-77 (この経路を見つけたマージ前ゲートの対象)
