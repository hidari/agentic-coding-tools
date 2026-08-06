---
description: "Universal Blue Team security planner. Reads <project>/.claude/security-profile.yml and dispatches blue-team-agent in Mode A (Red Team report response) or Mode B (defensive surface audit)."
argument-hint: "[--mode=a|b] [--report=<path>]"
---

# /security-blueteam

product-agnostic な Blue Team セキュリティ計画策定を起動する。 詳細手順は `blue-team-agent` の system prompt に集約されているので、 本 command は **薄い entry point** に徹する。

## 引数 parsing

`$ARGUMENTS` から以下を抽出:

- `--mode=<a|b>` (省略時は auto detect、 下記参照)
- `--report=<path>` (Mode A 時のみ意味あり、 Red Team 出力ディレクトリ または `red-team.md` のパス)

未対応の値 → エラーで終了。

## Mode 自動判定

引数 + ファイル存在から確定:

1. `--mode=a` または `--mode=b` が明示されていればそれを採用
2. `--mode` 未指定 + `--report=<path>` 指定あり + そのパスが存在 → Mode A (`--report` をそのまま採用)
3. `--mode` 未指定 + `--report` 未指定 + `docs/security-reviews/<latest date>/red-team.md` が存在 → Mode A (パスを自動補完して `--report` に設定)
4. 上記いずれにも該当しない → Mode B

`<latest date>` は `docs/security-reviews/` 配下のディレクトリ名を ISO 8601 YYYY-MM-DD として降順ソートした先頭。 存在しないか不正なディレクトリ名しかない場合は Mode B にフォールバック。

## 事前検証

以下を Bash で実行 (text grep ではなく **構造化 YAML parse** で行う。 `kind: "production" # was staging` のようなコメント混在や継続行を text match で取りこぼさないため):

1. `<cwd>/.claude/security-profile.yml` が存在するか確認。 無ければ「 profile が無い。 wrap skill が初期化する必要がある」 と出力して exit
2. 以下の Python one-liner で profile を構造化 parse し、 `environment.kind` を抽出:
   ```bash
   python3 -c "
   import sys, yaml
   p = yaml.safe_load(open('.claude/security-profile.yml'))
   if p.get('environment', {}).get('kind') == 'production':
       sys.stderr.write('ABORTED: environment.kind=production. /security-blueteam refuses to run.\n')
       sys.exit(1)
   "
   ```
   - exit code 非ゼロなら **即時拒否し exit** (production または YAML parse 失敗)
3. Mode A 確定済 + `REPORT` パスが解決できない → 「 Red Team レポートが見つからない。 先に `/security-redteam` を実行するか、 `--mode=b` で防御機構監査に切り替えてください」 と出力して exit

## Dispatch

Agent tool で `blue-team-agent` を起動:

- `subagent_type`: `blue-team-agent`
- 引数として渡す値:
  - `SECURITY_PROFILE`: `<cwd>/.claude/security-profile.yml` の絶対パス
  - `MODE`: 確定した `a` または `b`
  - `REPORT`: Mode A の場合のみ、 解決済みの絶対パス (Mode B では渡さない)
  - `OUTPUT_DIR`: `docs/security-reviews/` (cwd 相対)

## Dispatch 後

Agent の出力 (生成された blue-team.md のパス + mode 別サマリ: Mode A は priority 集計、 Mode B は surface 別の gap 数) を user に報告。 後続 (Issue 起票 / PR 作成 / 実装スケジューリング) は user の判断に委ねる。
