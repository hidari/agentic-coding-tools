---
description: "Universal Red Team security tester. Reads <project>/.claude/security-profile.yml and dispatches security-blue-red-team:red-team-agent. Defaults to Layer 3 (active state-changing) + Layer 4 (high-risk static). 「Red Team 走らせて」「ペンテストして」「攻撃面を能動的に検証」「能動攻撃シミュレーション」「セキュリティ侵入テスト」と依頼された時にも起動する。"
argument-hint: "[--layer=1|2|3|4|all] [--target=local|staging] [--purple]"
---

# /security-redteam

product-agnostic な Red Team セキュリティテストを起動する。 詳細手順・安全制約・責務境界は `agents/red-team-agent.md` の system prompt が canonical なので、 本 command は **起動と連鎖だけ**を持つ。

## 引数 parsing

`$ARGUMENTS` から以下を抽出:

- `--layer=<1|2|3|4|all|csv>` (default: `3,4` — 能動攻撃 + 高リスク静的、 Red Team の本旨)。 SAST + 受動を回したいなら対の `/security-vulnerability-assessment` を使うか、 本 command で `--layer=1,2` を明示
- `--target=<local|staging>` (default: profile の `environment.kind` から自動解決)
- `--purple` (flag, default: false): true なら Red Team 完了後に Blue Team Mode A を連鎖起動 (Purple Team モード)

未対応の値 → エラーで終了。

## 事前検証

以下を Bash で実行 (text grep ではなく **構造化 YAML parse** で行う。 `kind: "production" # was staging` のようなコメント混在や継続行を text match で取りこぼさないため):

1. `<cwd>/.claude/security-profile.yml` が存在するか確認。 無ければ「 profile が無い。 wrap skill が初期化する必要がある」 と出力して exit
2. 以下の Python one-liner で profile を構造化 parse し、 `environment.kind` と `environment.allow_targets` を抽出:
   ```bash
   python3 -c "
   import sys, yaml
   p = yaml.safe_load(open('.claude/security-profile.yml'))
   env = p.get('environment', {})
   if env.get('kind') == 'production':
       sys.stderr.write('ABORTED: environment.kind=production. /security-redteam refuses to run.\n')
       sys.exit(1)
   print(len(env.get('allow_targets', []) or []))
   "
   ```
   - exit code 非ゼロなら **即時拒否し exit** (production または YAML parse 失敗)
   - stdout の数字が 0 で `--layer` が `2|3|4|all` を含むなら警告を出して `--layer=1` に縮退するか確認

## Dispatch

Agent tool で `red-team-agent` を起動:

- `subagent_type`: `security-blue-red-team:red-team-agent`
- 引数として渡す値:
  - `SECURITY_PROFILE`: `<cwd>/.claude/security-profile.yml` の絶対パス
  - `LAYERS`: parsed `--layer` 値
  - `TARGET`: parsed `--target` 値
  - `OUTPUT_DIR`: `docs/security-reviews/` (cwd 相対)

## Dispatch 後

Agent の出力 (findings.json と red-team.md のパス、 severity 集計) を user に報告。 後続 (Issue 起票 / PR 作成 / cleanup 実行) は wrap layer か user の判断に委ねる。

## Purple Team 連鎖 (`--purple` フラグ時のみ)

`--purple` が true で、 agent が **正常終了** した場合 (= `findings.json` と `red-team.md` の両方が生成された場合) のみ、 続けて Blue Team Mode A を連鎖起動する:

1. agent の出力から日付ディレクトリ (`findings.json` と `red-team.md` を含む `<output_dir>/<YYYY-MM-DD>/`) の絶対パスを取得
2. Agent tool で `blue-team-agent` を以下の引数で起動:
   - `subagent_type`: `security-blue-red-team:blue-team-agent`
   - `SECURITY_PROFILE`: `<cwd>/.claude/security-profile.yml` の絶対パス
   - `MODE`: `a` (agent の入力契約は小文字。 `A` は受け付けられない)
   - `REPORT`: 日付ディレクトリの絶対パス (ディレクトリを渡すと agent が配下の `findings.json` と `red-team.md` を読む。 契約は `agents/blue-team-agent.md` の Inputs が canonical)
   - `OUTPUT_DIR`: `docs/security-reviews/` (cwd 相対)
3. Blue Team は findings の `fingerprint` を引用しつつ P0-P3 triage + S/M/L/XL 実装規模付与を行う
4. Blue Team 出力 (`blue-team.md` の絶対パス) を user に報告

Agent の termination が異常な場合 (= output dir に `findings.json` が存在しない、 もしくは abort 系メッセージで終わった場合) は **連鎖を行わない**。 user に「 Red Team が完走しなかったため Purple モードを skip」 と明示的に報告。

## 使われない条件

- 単発の「この PR 大丈夫?」 程度の確認 → 公式 `/security-review` slash command を使う
- 品質レビュー (バグ / 可読性) → `simplify` / `feature-dev:code-reviewer`
- フロント変更の E2E 影響だけ → `dev-workflow:e2e-scenario-impact-check`
- 防御機構の監査・改善計画 → 対の `/security-blueteam`
- 定期 SAST / 受動アセスメント (能動攻撃なし、 副作用なし、 月次 cron 想定) → 対の `/security-vulnerability-assessment`

## 関連

- `agents/red-team-agent.md`: Layer 1〜4 の具体実行ロジック / Production Gate / Safety Constraints / 責務境界の本体
- `${CLAUDE_PLUGIN_ROOT}/schemas/security-profile.schema.yml`: profile YAML の入力契約
- `${CLAUDE_PLUGIN_ROOT}/schemas/findings.schema.json`: findings.json の出力契約
