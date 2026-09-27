---
status: closed
---

# fix: 層 2 の argparse のエラーが option の値を印字する

## 背景

層 2 (`check-leak-guard-denylist.py`) は語もパスも印字しない設計で、argparse の `unrecognized arguments` が argv を並べる経路は `parse_known_args` で塞いである (ISSUE-15)。値をそのまま印字する経路がもう 1 つ残っている。store_true の `--check` に `=値` を付けると、argparse が `error: argument --check: ignored explicit argument '<値>'` を stderr へ出す。`--check-text=<path>` のつもりで `--check=<path>` と書き損じると、パスがそのまま出る (2026-09-27 に実測)。ISSUE-77 のマージ前ゲートの検証役は、`--help=<値>` も同じで、CPython 3.9.6 から 3.14.7 まで変わらないと報告した。

入口の `check-outgoing-text.py` は層 2 の stderr を捕まえて座標だけを読むので、入口を通る経路では外へ出ない。出るのは、pre-commit の Failed ブロックと、層 2 を直接起動した端末やログである。入口自身は argparse を使わない。

2026-09-28 に手元の 4 つのインタプリタ (3.9.6・3.11.15・3.12.12・3.14.7) で目印を値に入れて測った。`--check=<目印>`・`--help=<目印>`・`-h=<目印>` はどの版でも目印を出して終了コード 2 になった。`-h<目印>` は 3.9.6 だけが同じエラーで目印を出し、3.11.15 以降はヘルプを出して終了コード 0 になった。経路が版で変わるので、経路を列挙して 1 つずつ塞ぐ形は、列挙の止め時が決まらない。

## タスク

- [x] argparse のエラーの出口を塞ぐ。ArgumentParser の `error()` を上書きして、値を補間しない固定の文面で終了コード 2 にする。`exit_on_error` は既定のままにする。既定では argparse が `ArgumentError` を捕まえて `error()` へ回すので、出口は 1 つで済む。`exit_on_error=False` で option 名を出す形を併せると、値を運ぶ経路がすべてそちらへ逸れ、`error()` の上書きを外しても赤くならなくなる。`_StoreOnceNonEmpty` の文面 (option 名だけを持つ) は、上書きを通さずにそのまま出す
- [x] 上の 4 形のテストを足す。どれも stdout と stderr に目印が出ないことを押さえ、`-h<目印>` 以外は終了コード 2 も押さえる (`-h<目印>` は版で終了コードが変わる)。`error()` の上書きを外すとテストが赤くなることを変異注入で確かめる
- [x] 4 つのインタプリタで 4 形を実行して目印が出ないことを確かめ、確かめた版をコードのコメントに書く
- [x] docstring の分岐表に、この経路の終了コード 2 の行を足す

## 関連

ISSUE-15 (argparse が余分な引数を印字する経路を `parse_known_args` で塞いだ。この Issue はその網から漏れた経路)
ISSUE-77 (この経路を見つけたマージ前ゲートの対象)
