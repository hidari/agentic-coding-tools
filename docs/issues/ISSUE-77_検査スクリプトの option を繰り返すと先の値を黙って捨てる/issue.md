---
status: open
---

# fix: 検査スクリプトの option を繰り返すと先の値を黙って捨てる

## 背景

層 2 (`check-leak-guard-denylist.py`) と `issue-id.py` は、`--check-text` を argparse の既定の store で受ける。2 回渡すと先の値を黙って捨て、最後の 1 本だけを見る。読めないパスのあとに読めるファイルを足すと、終了コードは 2 から 0 に変わった (2026-09-27 に両方で実測)。検査の入口では、取り違えがエラーではなく緑として返る。今の呼び出し元 (commit-msg の hook と、入口の `check-outgoing-text.py`) は 1 回に 1 本しか渡さないので実害はまだ無いが、ISSUE-68 の項目 2 (層 2 が複数の入力を受ける形) へ移る途中の書き損じは静かに通る。

`issue-id.py` の `--base` と `--root` も同じ形で、`--base` の繰り返しは意図と違う範囲を緑にする。

同じ形は `scripts/check-issue-closure.py` の `--root` と、対話的に使う道具 (windows-vm-verification の winvm.py、macos-vm-verification の macvm.py、markdown-to-pdf の render.py、jev-lint-curated の jevlint.py) にもある。前者は pre-commit も CI も `--root` を渡さず、後者は結果が使う人の目に見えるので、この Issue の範囲外とする。

## タスク

- [ ] 層 2 の `--check-text` の繰り返しを、受け付けの段で終了コード 2 にする。どちらのパスも印字しない。終了コードの表に行を足す
- [ ] `issue-id.py` の `--check-text`・`--base`・`--root` の繰り返しを、終了コード 2 にする
- [ ] ISSUE-68 の項目 2 にある繰り返しの注意を、直した後の事実に合わせる

## 関連

ISSUE-68 (項目 2 に、層 2 が複数の入力を受ける形へまとめる案と、繰り返しの注意を持つ)
