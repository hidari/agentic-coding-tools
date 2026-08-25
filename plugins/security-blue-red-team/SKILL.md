---
name: security-blue-red-team
description: Red Team (攻撃者視点の能動検証) と Blue Team (防御者視点の改善計画) を profile 駆動で継続運用する skill バンドルの入口。個別の実行は `/security-redteam` / `/security-blueteam` / `/security-vulnerability-assessment` / `/security-cleanup` を呼ぶ。
---

# security-blue-red-team

product-agnostic なセキュリティ検証を profile 駆動で継続運用するためのバンドル。
`<project>/.claude/security-profile.yml` を読み、対象と制約をそこから決める。

このファイルは入口の案内のみを持つ。実際の手順は command と agent が持つ。

## component

| component | 役割 |
|---|---|
| `/security-redteam` (command) | 能動攻撃シミュレーション (Layer 3 の state-changing テスト + Layer 4 の高リスク静的分析) |
| `/security-blueteam` (command) | Red Team レポートの triage、または防御機構 5 面の監査 |
| `/security-vulnerability-assessment` (command) | 定期の SAST と受動アセスメント (Layer 1 + 2) |
| `/security-cleanup` (command) | Layer 3 で seed されたリソースの purge |
| `red-team-agent` (agent) | Layer 1〜4 の実行本体。Production Gate と Safety Constraints を持つ |
| `blue-team-agent` (agent) | Mode A / Mode B の実行本体。triage と防御機構監査を持つ |

agent の dispatch は `security-blue-red-team:<agent 名>` の修飾名で行う。

## 安全側の制約

`environment.kind` が production の profile に対しては、いずれの経路も実行を拒否する。
profile の `environment.kind` チェックと `allow_targets` の allowlist チェックによる二重防御。

Layer 3 で seed されたリソースは `cleanup-queue.json` に記録され、`/security-cleanup` で
purge する。cleanup はキューの自由文字列を実行せず、profile のテンプレートから削除コマンドを
再導出する。

## schema の所在

`findings.json` と `cleanup-queue.json` および profile の schema は本パッケージの `schemas/`
にある。`agents/` と `commands/` からは `${CLAUDE_PLUGIN_ROOT}/schemas/<name>` で参照する。
root のこのファイルでは `${CLAUDE_PLUGIN_ROOT}` が展開されないため、schema を名指しする処理を
ここへ書いてはならない。

インストール先を絶対パスで書いてはならない。開発機の配置にしか当たらず、配布先では解決しない。
`scripts/check-package-shape.py` がこの形を検出する。
