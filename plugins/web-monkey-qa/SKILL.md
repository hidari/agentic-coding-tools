---
name: web-monkey-qa
description: Web アプリを重み付きランダム操作で探索し、コンソールエラーや HTTP 4xx/5xx、レイアウト崩れ、行き止まり遷移などを検出する monkey test バンドルの入口。実行は monkey-qa を直接呼ぶ。
---

# web-monkey-qa

profile 駆動の monkey test 探索バンドル。`<project>/.claude/monkey-qa-profile.yml` を読み、
セクションごとに探索エージェントを割り当てる。

このファイルは入口の案内のみを持つ。実際の手順は component skill が持つ。

## component

| component | 役割 |
|---|---|
| `monkey-qa` (skill) | profile を読み、セクションごとに探索エージェントを dispatch する |
| `monkey-explorer-agent` (agent) | entry URL から重み付きランダム操作で探索し 7 種の検出器を回す |

呼び出しは `web-monkey-qa:monkey-qa` の修飾名で行う。

## 検出する事象

コンソールおよびランタイムエラー、HTTP 4xx、HTTP 5xx、描画エラー、レイアウトのはみ出し、
壊れた画像、行き止まり遷移の 7 種。分類は `schemas/findings.schema.json` の category enum が
canonical で、探索側はその値に一致させる。

## 安全側の制約

すべての操作は実行前に denylist と照合される。allowlist 外のフォーム送信、オリジン外リンクの
追跡、破壊的操作は行わない。production を対象にする場合は、dispatch の時点で匿名の読み取り専用へ
落とす。

HTTP Basic 認証の背後にあるオリジンでは、認証情報を URL へ埋め込まず専用の経路で渡す。
