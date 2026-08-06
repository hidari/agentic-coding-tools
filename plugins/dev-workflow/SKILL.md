---
name: dev-workflow
description: 個人開発のワークフローを支える skill バンドルの入口。ブランチ運用、リポジトリ内 Issue 管理、マージ前の品質ゲート、振り返りのルール化、E2E 影響の静的検出、コミットと PR 本文の作法を集約する。個別の作業は component skill を直接呼ぶ。
---

# dev-workflow

個人開発のワークフローを 7 つの skill にまとめたバンドル。hooks も MCP サーバも持たず、
install 時にも runtime にも自動実行されるコードを含まない。

このファイルは入口の案内のみを持つ。実際の手順は各 component skill が持つ。

## component

| skill | 役割 |
|---|---|
| `git-branch-switcher` | 作業開始前に適切なブランチを判断して切り替える |
| `in-repo-issue` | リポジトリ内 Markdown で Issue を起票・更新・クローズする |
| `issue-scoped-artifacts` | spec と plan を Issue ディレクトリ配下へ置く規約 |
| `pre-merge-quality-gate` | マージ直前に simplify / レビュー / E2E 影響チェックを並列で通す |
| `e2e-scenario-impact-check` | フロントエンド変更が E2E を将来壊す可能性を静的に検出する |
| `commit-and-pr-message` | git / gh へ渡す本文をファイル経由にする作法 |
| `retrospective-codify` | 試行錯誤の学びを lint ルール / skill / CLAUDE.md へ言語化する |

呼び出しは `dev-workflow:<component 名>` の修飾名で行う。

## 前提

`in-repo-issue` と `issue-scoped-artifacts` は `docs/issues/<NNN>_<title>/` という
ディレクトリ規約を前提にする。この規約を採らないプロジェクトでは、`issue-scoped-artifacts` は
プロジェクトの CLAUDE.md にポインタがある場合にのみ適用される opt-in 設計になっている。

`commit-and-pr-message` は日本語の散文をコマンド文字列へ載せない作法を扱う。特定のフック実装を
前提とした説明を含むが、作法そのもの (本文はファイルに書いて `-F` / `--body-file` で渡す) は
環境に依存しない。
