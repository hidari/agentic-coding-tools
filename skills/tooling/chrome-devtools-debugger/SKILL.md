---
name: chrome-devtools-debugger
description: 公式 chrome-devtools-mcp plugin の skill 群で収集したデバッグ結果を、標準化された日本語レポート (docs/debug-reports/) へ整形・機密マスクするレイヤー。ネットワーク/コンソール/パフォーマンス/UI の調査結果を既知エラーパターンに対応づけ、優先度付きでレポート化する際に使用する。
allowed-tools:
  - mcp__plugin_chrome-devtools-mcp_chrome-devtools__take_snapshot
  - mcp__plugin_chrome-devtools-mcp_chrome-devtools__take_screenshot
  - mcp__plugin_chrome-devtools-mcp_chrome-devtools__list_network_requests
  - mcp__plugin_chrome-devtools-mcp_chrome-devtools__get_network_request
  - mcp__plugin_chrome-devtools-mcp_chrome-devtools__list_console_messages
  - mcp__plugin_chrome-devtools-mcp_chrome-devtools__get_console_message
  - Read
  - Write
---

# Browser Debug Report スキル

公式 `chrome-devtools-mcp` plugin が提供する skill 群を土台に、その調査結果を **標準化された日本語デバッグレポート**へ整形し、機密情報をマスクして保存することに責務を絞る。

## 役割分担

- データ収集（ページ操作・ナビゲーション・ネットワーク/コンソール/パフォーマンス/メモリ/a11y 調査）は **公式 chrome-devtools-mcp plugin の skill 群に委譲する**:
  - `chrome-devtools`: 一般的なデバッグ/自動化フロー
  - `a11y-debugging`: アクセシビリティ監査
  - `debug-optimize-lcp`: LCP / Core Web Vitals
  - `memory-leak-debugging`: メモリリーク（heap snapshot）
  - `chrome-devtools-cli`: シェルからのブラウザ操作
  - `troubleshooting`: MCP 接続不良の診断（`list_pages` 等が失敗するときはこれを案内）
- 本 skill の責務は **収集結果 → レポート整形 + 機密マスク + 既知エラー対応づけ + 優先度判定** のみ。収集結果の生データ確認に必要な read 系 MCP ツールだけ allowed-tools に残す。

## 前提（レポート出力）

- 出力ベースディレクトリ: `git rev-parse --show-toplevel` の結果 (git リポジトリ内のとき) 配下の `docs/debug-reports/`。git 外ではカレントワーキングディレクトリ配下に同パス。存在しなければ Write の前に Bash の `mkdir -p` で作成。
- レポートファイル名: `{YYYYMMDD}_{HHmmss}_debug_report.md` (ローカルタイム ISO 8601 由来、例 `20260425_143000_debug_report.md`)。
- スクリーンショット保存先: `docs/debug-reports/assets/{YYYYMMDD}_{HHmmss}_{label}.png`。`{label}` はデバイス未指定時は `main`、特定デバイスでの取得時はデバイス識別子 (例: `iphone14` / `pixel7`)。
- 認証が必要な操作で認証情報が手元に無い場合は事前にユーザー確認。本番アカウントは使用しない。

## レポート化フロー

1. 公式 skill 群で対象 URL の調査を実施する（上記「役割分担」）。
2. 調査結果（ネットワーク / コンソール / パフォーマンス / UI・a11y）を下のテンプレに整形する。`get_network_request` / `get_console_message` 等の read 系 MCP ツールで生データを補完してよい。
3. 「機密情報マスク」の置換を Write 直前に必ず適用する。
4. 各 finding を「既知パターン」表に対応づけ、analysis / recommendation を埋める。該当が無ければ「要追調査: <観察事実>」と次アクションを書く（空欄禁止）。
5. 「優先度判定」に従い、末尾に優先度順の推奨対応をまとめる。
6. `Write` で `docs/debug-reports/{YYYYMMDD}_{HHmmss}_debug_report.md` に保存。問題ゼロでもミニマム版を必ず保存する。

## レポートテンプレ

```markdown
# デバッグレポート - {url} - {timestamp}

## 概要
- 対象URL: {url}
- 検査日時: {timestamp}  # ローカルタイム ISO 8601 (例: 2026-04-25T14:30:00+09:00)
- ページタイトル: {title}  # take_snapshot のルート node の name
- ビューポート: {device or size}  # モバイル時のみ

## ネットワーク
### エラー
- [{status_code}] {url} - {error_message}
  - 観察事実: {get_network_request の生データ要約}
  - 推定原因: {analysis}  # 既知パターン (下表) に該当 → 該当を記載、不明 → 「要追調査: <観察事実>」
  - 推奨対応: {recommendation}  # 同上、不明なら「ログ拡充」「再現手順確立」

### 警告
- {description}

## コンソール
### エラー
- {error_message}
  - ファイル: {source}:{line}  # get_console_message から
  - 推定原因: {analysis}
  - 推奨対応: {recommendation}

### 警告
- {warning_message}

## パフォーマンス  # 公式 debug-optimize-lcp 等で計測した場合のみ。未計測なら「未計測」と明記
### Core Web Vitals
- LCP: {value}
- FID: {value}
- CLS: {value}

### 主な問題
- {performance_issue}
  - 影響: {impact}  # 公式 debug-optimize-lcp の severity
  - 推奨対応: {recommendation}

## UI/UX
- {issue_description}
  - スクリーンショット: docs/debug-reports/assets/{YYYYMMDD}_{HHmmss}_{label}.png
  - 推奨対応: {recommendation}

## 優先度順の推奨対応
1. [高] {action_item}
2. [中] {action_item}
3. [低] {action_item}
```

### analysis / recommendation の既知パターン

| 観察事実                       | 推定原因           | 推奨対応                           |
|----------------------------|----------------|--------------------------------|
| 401 + Authorization ヘッダー無し | 認証ヘッダー未設定      | API クライアント側でトークン付与を確認          |
| 401 + Authorization ヘッダー有り | トークン期限切れ / 不正  | リフレッシュトークン処理 / トークン再発行         |
| 403                        | 権限不足           | ロール / スコープ確認                   |
| 404 + 静的ファイル               | ビルド出力 / パス間違い  | ビルド成果物の確認、パス修正                 |
| CORS preflight 失敗          | サーバー側 CORS 未設定 | Access-Control-Allow-Origin 設定 |
| CSP violation              | CSP ヘッダー設定     | meta CSP or HTTP ヘッダー側で許可リスト追加 |
| LCP > 2.5s + 大きな画像         | 画像最適化不足        | WebP / AVIF 化、レスポンシブ画像         |
| CLS > 0.1 + 動的要素           | レイアウトシフト       | 寸法予約、aspect-ratio 指定           |

該当パターンが無ければ「要追調査: <観察事実>」と記載し、推奨対応欄に「再現手順確立」「サーバー側ログ確認」など次のアクションを書く。空欄禁止。

### 問題ゼロ時のミニマム版

ネットワーク・コンソール・(取得時)パフォーマンス全部クリアの場合、テンプレの全セクションを残さず、次の最小構成のみで保存:

```markdown
# デバッグレポート - {url} - {timestamp}

## 概要
- 対象URL: {url}
- 検査日時: {timestamp}
- ページタイトル: {title}
- ビューポート: {device or size}  # モバイル時のみ

## 結論
問題なし。

### 確認した観点
- ネットワーク: {n} 件、エラー 0、1000ms 超 0
- コンソール: error 0 / warn 0 (実害ありのみカウント)
- パフォーマンス: {実施した場合は LCP/CLS の値、未実施なら「未計測」}
```

### 優先度判定

- 高: ページが機能しない / セキュリティ警告 / 認証エラー / データ損失リスク
- 中: パフォーマンス低下 / UX 警告 / 4xx (404 含むがメインフロー外) / 重要度の高い deprecated 警告
- 低: マイナーな UI 問題 / コンソール warn / 最適化提案

## 機密情報マスク

レポート保存時に必ず `<REDACTED>` で置換 (Write 直前にチェック):

- リクエストヘッダー: `Authorization`, `Cookie`, `X-API-Key`, `X-Auth-Token`, `Proxy-Authorization`
- レスポンスヘッダー: `Set-Cookie`
- レスポンス本文 / コンソール出力中のフィールド名: `token`, `access_token`, `refresh_token`, `password`, `secret`, `api_key`, `email`
- リクエスト URL のクエリパラメータ: `access_token`, `token`, `code`, `state`, `session`, `api_key` (例: `?access_token=xxx` を `?access_token=<REDACTED>` に置換)
- Bearer トークン本体 (`Bearer xxxx` の `xxxx` 部分)
- スクリーンショット: 個人情報・トークン文字列が映っていないか目視確認。映っていれば該当領域をクロップ or 撮り直し

## 他スキルとの使い分け

| 目的                                            | 担当                                         |
|-----------------------------------------------|--------------------------------------------|
| ライブ接続したブラウザの調査 (ネットワーク/コンソール/perf/メモリ/a11y) | 公式 chrome-devtools-mcp plugin の skill 群     |
| 調査結果の標準レポート化 + 機密マスク + 既知エラー対応づけ            | chrome-devtools-debugger (本 skill)          |
| Playwright Test (E2E) の作成 / レビュー              | playwright-test                            |
| Playwright CLI (codegen / screenshot / shard) | playwright-cli                             |

ライブ接続調査の強みは、開いている Chrome セッションに接続でき手動テストとシームレスに連携できる点 (Playwright は隔離ブラウザを起動するためログインセッション等を引き継げない)。本 skill はその調査結果を、組織標準のレポート形式・機密マスク規則・既知エラー知識に乗せて成果物化することに特化する。
