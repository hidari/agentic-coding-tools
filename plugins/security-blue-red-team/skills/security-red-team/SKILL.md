---
name: security-red-team
description: "攻撃者視点での能動攻撃シミュレーション (penetration testing) を実行する product-agnostic skill。 `<project>/.claude/security-profile.yml` を読み込み、 環境分離度チェック → デフォルトで Layer 3 (能動 state-changing テスト: IDOR / file upload attack / webhook forgery / business logic abuse 等) + Layer 4 (高リスク項目の静的分析: WAF / SSRF / cloud metadata 等) を実行し、 `<output_dir>/<YYYY-MM-DD>/red-team.md` と `findings.json` を生成する。 `LAYERS` 引数で `1,2` 等を渡せば SAST + 受動も実行可能だが、 定期 SAST / 受動アセスメントには対の `security-vulnerability-assessment` skill を使う方が分担として正しい。 「 Red Team 走らせて」「ペンテストして」「攻撃面を能動的に検証」「能動攻撃シミュレーション」「セキュリティ侵入テスト」と指示された時に起動。 公式の `/security-review` slash command は単発 PR 用、 本 skill は profile 駆動で継続運用される設計 (四半期 + 手動運用を想定、 Layer 3 で seed されたリソースは cleanup-queue.json + /security-cleanup で purge する流れ)。 environment.kind が production の profile に対しては **必ず即時拒否** し実行しない (二重防御: profile.environment.kind チェック + allow_targets allowlist チェック)。 Purple Team モード (`--purple`) で Blue Team Mode A を連鎖起動できる。 本 skill は findings.json を出力するまでが責任範囲で、 issue 起票 / PR レビュー / 実ブラウザ動作確認 など他 skill の領域には踏み込まない (wrap skill / user に委ねる)。"
---

# Security Red Team

攻撃者視点での layered security testing を実行する universal skill。 実体は `red-team-agent` (Opus) を `Skill` tool 経由または `/security-redteam` slash command 経由で dispatch する。

## 入力

- **profile** (必須): `<project>/.claude/security-profile.yml`。 schema は `${CLAUDE_PLUGIN_ROOT}/schemas/security-profile.schema.yml`。
- **layers**: 実行する Layer の指定 (`1` / `2` / `3` / `4` / `all` / カンマ区切り e.g. `1,2` `3,4`)。 **デフォルト `3,4`** (能動 + 高リスク静的)。 SAST + 受動だけ実行したい場合は対の `security-vulnerability-assessment` skill を使うか、 本 skill で `--layer=1,2` を明示。
- **target**: `local` / `staging`。 profile の `environment.kind` と `allow_targets` から自動解決可能。
- **output_dir**: レポート出力ディレクトリ。 デフォルト `docs/security-reviews/`。
- **purple** (任意 flag): true なら Red Team 完了後に Blue Team Mode A を連鎖起動 (Purple Team モード)。

## 起動経路

1. **Slash command**: `/security-redteam [--layer=1|2|3|4|all|<csv>] [--target=local|staging] [--purple]`
2. **Skill tool 経由**: メインエージェントが `Skill` tool で本 skill を呼ぶ (wrap skill から呼ばれることが多い)
3. **自然言語起動**: 上記 description の trigger 語彙が含まれる依頼

いずれの経路でも、 最終的に `red-team-agent` を Agent tool で dispatch する。

## 実行フロー (概要)

1. `<project>/.claude/security-profile.yml` を Read
2. **Production Gate**:
   - `environment.kind == "production"` → 即時拒否、 findings.json は生成せず終了
   - `environment.allow_targets` が空かつ requested layers が 2/3/4 を含む → Layer 1 のみに縮退 or 拒否
3. `red-team-agent` を Agent tool で dispatch (subagent_type=red-team-agent)、 上記 4 入力を引数で渡す
4. Agent 完了後、 `<output_dir>/<date>/red-team.md` と `findings.json` の path を user に報告

詳細手順は `agents/red-team-agent.md` の system prompt に集約 (DRY)。

## 出力契約

- `<output_dir>/<YYYY-MM-DD>/red-team.md`: 人間可読サマリ
- `<output_dir>/<YYYY-MM-DD>/findings.json`: 機械可読、 `${CLAUDE_PLUGIN_ROOT}/schemas/findings.schema.json` 準拠
  - `findings[].fingerprint` (sha256 ハッシュ) は重複起票防止に必須

## 責務境界 (DO NOT)

- 他 skill (`dev-workflow:in-repo-issue`, `dev-workflow:pre-merge-quality-gate`, `chrome-devtools-debugger`, `playwright-test`, `simplify`, `feature-dev:code-reviewer`) を呼ばない
- 実装変更 (Edit / Write) を行わない (findings.json と red-team.md の生成のみ)
- profile に書かれていない product-specific チェックを勝手に行わない (それは wrap skill の責務)
- production 文字列を含む target に絶対に送信しない

## 使われない条件

- 単発の「この PR 大丈夫?」 程度の確認 → 公式 `/security-review` slash command を使う
- 品質レビューだけ (バグ / 可読性) → `simplify` / `feature-dev:code-reviewer`
- フロント変更の E2E 影響だけ → `dev-workflow:e2e-scenario-impact-check`
- 防御機構の監査・改善計画 → `security-blue-team` (本 skill の対) を使う
- 定期 SAST / 受動アセスメント (能動攻撃なし、 副作用なし、 月次 cron 想定) → 対の `security-vulnerability-assessment` skill を使う

## 関連

- `agents/red-team-agent.md`: Layer 1〜4 の具体実行ロジック / Production Gate / Safety Constraints の本体
- `commands/security-redteam.md`: slash command の入口
- `schemas/security-profile.schema.yml`: profile YAML の契約
- `schemas/findings.schema.json`: findings.json の契約
