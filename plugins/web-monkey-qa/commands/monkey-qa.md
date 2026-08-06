---
description: "Universal Monkey QA explorer. Reads <project>/.claude/monkey-qa-profile.yml and dispatches monkey-explorer-agent per section."
argument-hint: "[--target=local|staging]"
---

# /monkey-qa

product-agnostic な AI 探索型モンキーテストを起動する。詳細手順 (profile parse / 環境ガード / fan-out / 集約) は `web-monkey-qa:monkey-qa` skill の system prompt に集約されているので、本 command は **薄い entry point** に徹する。

## 事前検証

1. `<cwd>/.claude/monkey-qa-profile.yml` の存在を確認する。無ければ `${CLAUDE_PLUGIN_ROOT}/schemas/monkey-qa-profile.template.yml` を提示して停止する。
2. 構造化 parse (`python3 -c "import yaml; yaml.safe_load(...)"`。text grep は禁止 — コメント混在で `environment.kind` を誤読しうる) で `environment.kind` を確認する。production は read-only で続行する (完全な挙動は `web-monkey-qa:monkey-qa` skill の実行フロー Step 2 を参照。ここでは再定義しない)。
3. parse に失敗した場合はエラーを報告して停止する。

## Dispatch

`Skill(skill="web-monkey-qa:monkey-qa")` を起動する。引数として渡す値:

- `MONKEY_PROFILE`: `<cwd>/.claude/monkey-qa-profile.yml` の絶対パス
- `TARGET` (任意・informational): `$ARGUMENTS` の `--target=<local|staging>`。**環境ガードの判定には使わない** — skill は常に profile の `environment.kind` から gate を再導出する (override で production を回避させないための設計)。表示・ログ目的の補助情報にとどまる

## Dispatch 後

skill の出力 (`findings.json` / `monkey-report.md` の絶対パスと statistics) を user に報告する。後続 (Issue 起票 / PR 作成) は wrap layer か user の判断に委ねる。
