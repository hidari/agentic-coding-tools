---
name: dev-workflow
description: 個人開発のワークフローを支える skill バンドルの入口。ブランチ運用、リポジトリ内 Issue 管理、マージ前の品質ゲート、振り返りのルール化、E2E 影響の静的検出、コミットや PR の本文を漏洩検査してから渡す手順を集約する。個別の作業は component skill を直接呼ぶ。
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
| `commit-and-pr-message` | 公開される本文をファイルに書き、同梱の入口で漏洩検査を通してから git / gh へ渡す手順 |
| `retrospective-codify` | 試行錯誤の学びを lint ルール / skill / CLAUDE.md へ言語化する |

呼び出しは `dev-workflow:<component 名>` の修飾名で行う。

## 前提

`in-repo-issue` と `issue-scoped-artifacts` は `docs/issues/<ID>_<title>/` という
ディレクトリ規約を前提にする。`<ID>` の形式と採番規則の canonical は `in-repo-issue` 同梱の
`scripts/issue-id.py` で、このファイルは識別子の形も接頭辞も再掲しない。
この規約を採らないプロジェクトでは、`issue-scoped-artifacts` は
プロジェクトの CLAUDE.md にポインタがある場合にのみ適用される opt-in 設計になっている。

`commit-and-pr-message` は、公開される本文をファイルに書き、同梱の入口で漏洩検査を通してから
`-F` / `--body-file` で渡す手順を扱う。入口は python3 と gitleaks と、環境変数で指す禁止語リストを
使う。python3 か gitleaks が無い環境のように検査を完了できないときは、手順は渡す前に止まって
ユーザーの判断を仰ぐ。止まる条件と行動は `dev-workflow:commit-and-pr-message` の「送る前の検査」節が
持つ。コマンド文字列を検査する特定のフック実装を前提とした説明も含むが、手順そのものはフックの有無に
依存しない。
