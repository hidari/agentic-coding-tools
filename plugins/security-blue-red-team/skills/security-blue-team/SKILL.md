---
name: security-blue-team
description: "防御者視点で対象プロダクトのセキュリティ改善計画を策定する product-agnostic skill。 `<project>/.claude/security-profile.yml` と (Mode A の場合) Red Team レポートを読み込み、 Mode A (Red Team レポート対応: findings.json を P0/P1 短期 / P2 中期 / P3 長期に triage、 各 finding に ファイル・関数名・実装規模 S/M/L/XL・実装ヒントを付与。 `--report` 未指定時は直近 `<output_dir>/<YYYY-MM-DD>/red-team.md` を自動補完) または Mode B (防御機構監査: authn/z flow trace / input validation coverage / security headers / RLS audit / logging coverage の 5 ステップ) を実行し、 `<output_dir>/<YYYY-MM-DD>/blue-team.md` を生成する。 「 Blue Team 動かして」「改善計画立てて」「防御機構を監査」「 Red Team レポートに対応策」「セキュリティ改善計画」「防御策を策定」と指示された時に起動。 公式の `/security-review` slash command は単発 PR 用、 本 skill は profile 駆動で継続運用される設計 (Red Team レポートの triage と防御機構の網羅監査が責務)。 environment.kind が production の profile に対しては **必ず即時拒否** し実行しない (Red Team と同じ二重防御)。 本 skill は blue-team.md を出力するまでが責任範囲で、 実装変更 (Edit / Write) や PR 作成、 Issue 起票には踏み込まない (改善計画は suggestion レベル止め、 実装は別 Issue 別 PR で人間 / wrap skill が判断)。 攻撃面の検出 (Layer 1〜4) は対の `security-red-team` skill を使う。"
---

# Security Blue Team

防御者視点での改善計画策定 + 防御機構監査を実行する universal skill。 実体は `blue-team-agent` (Opus) を `Skill` tool 経由または `/security-blueteam` slash command 経由で dispatch する。

## 入力

- **profile** (必須): `<project>/.claude/security-profile.yml`。 schema は `${CLAUDE_PLUGIN_ROOT}/schemas/security-profile.schema.yml`。
- **mode**: `a` (Red Team レポート対応) / `b` (防御機構監査)。 デフォルト auto detect (下記参照)。
- **report**: Mode A の入力となる Red Team レポートのパス (red-team.md または findings.json)。 Mode A 時のみ意味あり。
- **output_dir**: レポート出力ディレクトリ。 デフォルト `docs/security-reviews/`。

## 起動経路

1. **Slash command**: `/security-blueteam [--mode=a|b] [--report=<path>]`
2. **Skill tool 経由**: メインエージェントまたは wrap skill が `Skill` tool で本 skill を呼ぶ (Astralys の quarterly モードなど)
3. **自然言語起動**: 上記 description の trigger 語彙が含まれる依頼

いずれの経路でも、 最終的に `blue-team-agent` を Agent tool で dispatch する。

## Mode A / Mode B 分岐ルール

- `--mode=a` 明示 → Mode A
- `--mode=b` 明示 → Mode B
- `--mode` 未指定 + `--report=<path>` 指定あり + パス解決可能 → Mode A
- `--mode` 未指定 + `--report` 未指定 + 直近 `<output_dir>/<YYYY-MM-DD>/red-team.md` が存在 → Mode A (パス自動補完)
- 上記いずれにも該当しない → Mode B

## 実行フロー (概要)

1. `<project>/.claude/security-profile.yml` を Read
2. **Production Gate** (Red Team と同一):
   - `environment.kind == "production"` → 即時拒否、 blue-team.md は生成せず終了
3. Mode 分岐 (上記ルール) を確定
4. `blue-team-agent` を Agent tool で dispatch (subagent_type=blue-team-agent)、 上記 4 入力 + 確定 mode を引数で渡す
5. Agent 完了後、 `<output_dir>/<date>/blue-team.md` の path を user に報告

詳細手順は `agents/blue-team-agent.md` の system prompt に集約 (DRY)。

## 出力契約

- `<output_dir>/<YYYY-MM-DD>/blue-team.md`: 人間可読の防御計画 (Mode A: P0-P3 triage + 各 finding に実装ヒント / Mode B: 5 ステップ監査結果)
- Mode A 時、 入力 findings.json の `fingerprint` を blue-team.md 内で引用 (Red Team と Blue Team を fingerprint で突き合わせ可能にする)

## 責務境界 (DO NOT)

- 他 skill (`dev-workflow:in-repo-issue`, `dev-workflow:pre-merge-quality-gate`, `chrome-devtools-debugger`, `playwright-test`, `simplify`, `feature-dev:code-reviewer`) を呼ばない
- 実装変更 (Edit / Write) を行わない (blue-team.md の生成のみ、 改善案は文章で suggestion)
- PR 作成 / Issue 起票を行わない (wrap skill / user に委ねる)
- profile に書かれていない product-specific チェックを勝手に行わない
- Red Team が出力した findings の severity を変更しない (triage で優先度を付与するが、 severity は Red Team の判定を尊重)
- production 文字列を含む target に絶対に送信しない (Mode B の 5 ステップは全て静的解析、 HTTP リクエストはしない)

## 使われない条件

- 攻撃面の検出だけ → 対の `security-red-team` を使う
- 単発の「この PR 大丈夫?」 → 公式 `/security-review`
- 品質レビュー (バグ / 可読性) → `simplify` / `feature-dev:code-reviewer`
- 実コード修正が欲しい → 通常のコーディング依頼として進める (本 skill は計画策定まで)

## 関連

- `agents/blue-team-agent.md`: Mode A / Mode B の具体実行ロジック / Production Gate / Safety Constraints の本体
- `commands/security-blueteam.md`: slash command の入口
- `skills/security-red-team/SKILL.md`: 対となる攻撃面検出 skill (Mode A の入力を生成)
- `schemas/security-profile.schema.yml`: profile YAML の契約
- `schemas/findings.schema.json`: Mode A 入力 findings.json の契約
