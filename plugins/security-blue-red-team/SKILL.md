---
name: security-blue-red-team
description: Red Team (攻撃者視点の能動検証) と Blue Team (防御者視点の改善計画) を profile 駆動で継続運用する skill バンドルの入口。個別の実行は security-red-team / security-blue-team / security-vulnerability-assessment を直接呼ぶ。
---

# security-blue-red-team

product-agnostic なセキュリティ検証を profile 駆動で継続運用するためのバンドル。
`<project>/.claude/security-profile.yml` を読み、対象と制約をそこから決める。

このファイルは入口の案内のみを持つ。実際の手順は各 component skill が持つ。

## component

| skill | 役割 |
|---|---|
| `security-red-team` | 能動攻撃シミュレーション (Layer 3 の state-changing テスト + Layer 4 の高リスク静的分析) |
| `security-blue-team` | Red Team レポートの triage、または防御機構 5 面の監査 |
| `security-vulnerability-assessment` | 定期の SAST と受動アセスメント (Layer 1 + 2) |

agent 2 個 (`blue-team-agent` / `red-team-agent`) と command 4 個を伴う。
呼び出しは `security-blue-red-team:<component 名>` の修飾名で行う。

## 安全側の制約

`environment.kind` が production の profile に対しては、いずれの skill も実行を拒否する。
profile の `environment.kind` チェックと `allow_targets` の allowlist チェックによる二重防御。

Layer 3 で seed されたリソースは `cleanup-queue.json` に記録され、`/security-cleanup` で
purge する。cleanup はキューの自由文字列を実行せず、profile のテンプレートから削除コマンドを
再導出する。

## schema の所在

`findings.json` と `cleanup-queue.json` および profile の schema は本パッケージの `schemas/`
にある。`agents/` `commands/` と `skills/` 配下の SKILL.md からは
`${CLAUDE_PLUGIN_ROOT}/schemas/<name>` で参照する。root のこのファイルでは
`${CLAUDE_PLUGIN_ROOT}` が展開されないため、schema を名指しする処理をここへ書いてはならない。

インストール先を絶対パスで書いてはならない。開発機の配置にしか当たらず、配布先では解決しない。
`scripts/check-package-shape.py` がこの形を検出する。
