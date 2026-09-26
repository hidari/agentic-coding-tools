---
status: closed
---

# fix: 検査スクリプトの option を繰り返すと先の値を黙って捨てる

## 背景

層 2 (`check-leak-guard-denylist.py`) と `issue-id.py` は、`--check-text` を argparse の既定の store で受ける。2 回渡すと先の値を黙って捨て、最後の 1 本だけを見る。読めないパスのあとに読めるファイルを足すと、終了コードは 2 から 0 に変わった (2026-09-27 に両方で実測)。検査の入口では、取り違えがエラーではなく緑として返る。今の呼び出し元 (commit-msg の hook と、入口の `check-outgoing-text.py`) は 1 回に 1 本しか渡さないので実害はまだ無いが、ISSUE-68 の項目 2 (層 2 が複数の入力を受ける形) へ移る途中の書き損じは静かに通る。

`issue-id.py` の `--base` と `--root` も同じ形で、`--base` の繰り返しは意図と違う範囲を緑にする。

空の値も同じ種類の取り違えになる。`issue-id.py` の `--base=` は範囲が `...HEAD` になって追加行 0 行の緑、`--root=` は cwd になる。層 2 の `--check-text=` は、環境変数が未設定だと読む前の skip で 0 になる (どれも 2026-09-27 に実測)。`--base "$BASE"` のように、変数が未設定のまま配線した形で踏む。

範囲はスクリプト単位で層 2 と `issue-id.py` に絞り、その中の値を取る option はそろえて塞ぐ。同じ形は `scripts/check-issue-closure.py` の `--root` と、対話的に使う道具 (windows-vm-verification の winvm.py、macos-vm-verification の macvm.py、markdown-to-pdf の render.py、jev-lint-curated の jevlint.py) にもある。前者はこのリポジトリ専用の検査で配布されず、pre-commit と CI の検査は `--root` を渡さない (渡すのはテストが固定の root を 1 回渡す形だけ)。後者は結果が使う人の目に見える。どちらもこの Issue の範囲外とする。

## タスク

- [x] 層 2 の `--check-text` の繰り返しを、受け付けの段で終了コード 2 にする。どちらのパスも印字しない。終了コードの表に行を足す
- [x] `issue-id.py` の `--check-text`・`--base`・`--root` の繰り返しを、終了コード 2 にする
- [x] 上の option の空の値も、受け付けの段で終了コード 2 にする
- [x] ISSUE-68 の項目 2 にある繰り返しの注意を、直した後の事実に合わせる
- [x] 触った `issue-id.py` で、ISSUE-68 の項目 8 にあった `allow_abbrev` の説明の誤りを直す

## 関連

ISSUE-68 (項目 2 に、層 2 が複数の入力を受ける形へまとめる案と、繰り返しの注意を持つ)
ISSUE-78 (この Issue のマージ前ゲートで見つけた、層 2 の argparse のエラーが値を印字する経路)
ISSUE-79 (この Issue のマージ前ゲートで見つけた、`issue-id.py` の `--root` にサブディレクトリを渡すと配下だけを見る件)
