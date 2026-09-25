---
status: open
---

# feat: jev-test-filter を消費側で使えるように取り込む

## 背景

mizchi/jev-test-filter は、git の差分を Jev (TypeSafe AI の採点 API) へ送り、テスト1件ごとに「この変更で結果が変わりうるか」を採点させて、テストランナーへ渡すテストを絞るツールである。lint ではなくテストの選別を担う。ISSUE-65 (jev-lint の採用方式) と同時に取り込めないかを検討した。

## 確かめたこと (2026-09-25、955bdf4 の 0.1.3 をソースで読んだだけで、実行はしていない)

- 対応するのは vitest、jest、node、bun、playwright、Rust、Go で、Python は無い。本リポジトリの unittest には当てられない。仮に対応しても、`scripts/run-python-tests.py` は実行したテスト ID の集合を manifest と照合するので、絞った実行は赤になる
- 本リポジトリで対応する形式のテストは node:test の `.mjs` が2本あるだけで、どちらも pre-commit と CI から呼ばれていない。絞り込む対象としての実質が無い
- 外部へ送るのは、差分の全文、`--stat`、変更ファイルの一覧と、各テストのパス・名前・行範囲。差分から秘密情報を除く処理は無く、`--base` も `--staged` も付けないと未コミットの差分を送る
- キーと送り先の環境変数は jev-lint と同じ名前 (`TYPESAFE_API_KEY` と `TYPESAFEAI_API_KEY`、`TYPESAFE_BASE_URL` と `TYPESAFEAI_BASE_URL`)。jev-lint と違い、設定ファイルで送り先を差し替える経路は無い
- 依存は `@ast-grep/napi`、`@ast-grep/lang-rust`、`@ast-grep/lang-go` で、Node 24以上が要る。lang-rust と lang-go は `@ast-grep/setup-lang` の postinstall を持つ (中身は未読)
- 上流の README は `pnpm add -D` による導入を先に案内し、npx はインストールしない代替として示す。skill は両方を並べ、command はインストール済みのコマンドを直接呼ぶ。どの経路も版を固定していない
- 4日間で59コミット、タグと CI は無い。plugin manifest の版 (0.1.0) が package の版 (0.1.3) とずれている
- 参考: mizchi/jev-playground の task-filter の実験 (`docs/23-task-filter.md`) は、扇形の少ない小さなリポジトリでは glob だけの無料の判定に負け、使わないと結論している。jev-test-filter がその後継かは、どちらの文書にも根拠が無い

## 決めたこと (2026-09-25、ユーザー裁定)

- 本リポジトリには絞り込む対象が無いので、今回は取り込まない。使いたい消費側のリポジトリ (JS/TS、Rust、Go) が出てから着手する
- 着手するときも、実行は手動の advisory に限り、送るのはコミット済みの範囲にする (ISSUE-65と同じ裁定)

## タスク

- [ ] 使う消費側のリポジトリを決める
- [ ] ISSUE-65の採用方式に合わせて、ラッパを共通にできる部分とパッケージごとの部分を切り分ける
- [ ] `@ast-grep/setup-lang` の postinstall を読んでから、`--allow-build` の対象を決める
- [ ] 消費側のリポジトリで選別の質 (取りこぼしと過剰な選択) を測る。採点は非決定的だと上流の README が書いている

## 関連

ISSUE-65 (jev-lint の採用方式。ラッパの形とキーの扱いを共有しうる)
