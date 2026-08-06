---
description: "Consume a cleanup-queue.json emitted by red-team-agent (Layer 3). Re-validates the production gate + seed_id_prefix invariant + seed_id injection-safe charset, then re-derives each delete command from the profile's environment.cleanup.commands template (never executes the queue's free-form cleanup_command). Use after /security-redteam with Layer 3 to purge security_redteam_<UUID> seeds from staging / local."
argument-hint: "[--from=<path>] [--dry-run]"
---

# /security-cleanup

Layer 3 で seed されたテスト用リソースを purge する。 本 command は `red-team-agent` が出力した `cleanup-queue.json` を消費するが、 **実行コマンドは queue の `cleanup_command` をそのまま使わず、 profile の削除テンプレート `environment.cleanup.commands.<seed_type>` から検証済み `seed_id` を再 substitute して導出する**。 production gate・`seed_id_prefix` 不変条件・`seed_id` の injection-safe charset を再検証し、 かつ実行内容を profile (信頼根) のみから決めるので、 万一 cleanup-queue が改変されていても暴走しない。

## 引数 parsing

`$ARGUMENTS` から以下を抽出:

- `--from=<path>` (optional): `cleanup-queue.json` の絶対パスまたは cwd 相対パス。 省略時は `docs/security-reviews/<latest YYYY-MM-DD>/cleanup-queue.json` を自動補完
- `--dry-run` (flag, default: false): true なら resolved コマンド一覧を表示するだけで実行しない (検証用)

## 事前検証 (Production Gate 二重防御 + 不変条件)

1. `<cwd>/.claude/security-profile.yml` の存在確認。 無ければ exit
2. profile を構造化 YAML parse し、 `environment.kind` が `production` でないことを確認 (production なら即時拒否)
3. `cleanup-queue.json` を構造化 JSON parse し、 schema (`${CLAUDE_PLUGIN_ROOT}/schemas/cleanup-queue.schema.json`) に照らして検証 (schema が `metadata.environment_kind` / `metadata.seed_id_prefix` / 各 `seed_id` の injection-safe charset を required にしているので、 これらを欠く / 違反する queue はここで弾かれる)
4. cleanup-queue.json の `metadata.environment_kind` が `local` または `staging` であることを再確認 (production であれば immediate abort、 queue が profile と矛盾している場合も abort)。 `metadata.environment_kind` または `metadata.seed_id_prefix` が **欠落していたら abort** (欠落 = 安全側に倒して拒否。 schema 検証を経ない呼び出し経路への二重の歯止め)
5. profile から `environment.cleanup.seed_id_prefix` (default: `security_redteam_`) と、 各 seed_type の削除テンプレート `environment.cleanup.commands.<seed_type>` を抽出する。 **これら profile のテンプレートだけが実行内容の信頼根**であり、 queue の `cleanup_command` 文字列は実行に使わない (下記 Cleanup 実行を参照)
6. cleanup-queue.items[] を巡回し、 各 `seed_id` が (a) injection-safe charset `^[A-Za-z0-9_-]{1,128}$` にマッチし、 かつ (b) profile の `seed_id_prefix` で **始まる** ことを確認 (1 件でも違反したら 全実行を中止 し報告)。 charset 検証は schema と二重だが、 schema 検証を経ない呼び出し経路でも shell メタ文字混入を防ぐ歯止めとして command 側でも必ず行う

## Cleanup 実行

検証を全て通過したら、 cleanup-queue.items[] を順次:

1. 実行コマンドを **queue の `cleanup_command` から取らず、 profile の `environment.cleanup.commands.<seed_type>` テンプレートを読み直し、 charset 検証済みの `{seed_id}` を再 substitute して導出する**。 これにより実行内容は profile (信頼根) + 検証済み seed_id のみから決まり、 queue が改変されていても実行に影響しない
2. profile に該当 `seed_type` のテンプレートが無い場合は、 そのエントリを **skip して報告** (安全に消せないため実行しない)
3. 再導出したコマンドが queue の `cleanup_command` と食い違う場合は warn を出す (改変 / drift の検出)。 実行するのは常に再導出した側
4. 再導出したコマンドを表示
5. `--dry-run` ならスキップ (次のエントリへ)
6. Bash tool で execute、 stdout / stderr / exit code を記録
7. 1 件失敗しても他のエントリは続行 (集約 report を出すため)

## 出力

- cleanup-queue.json と同じディレクトリに `cleanup-log.json` を生成。 schema:
  ```json
  {
    "items": [
      {
        "seed_type": "...",
        "seed_id": "security_redteam_...",
        "cleanup_command": "...",
        "exit_code": 0,
        "stdout": "...",
        "stderr": "...",
        "executed_at": "ISO 8601 UTC"
      }
    ],
    "summary": { "total": N, "success": N, "failed": N, "dry_run": bool }
  }
  ```
- 終了時に summary を user に報告
- 失敗が 1 件でもあれば command 全体の exit code を非 0 で終了

## 責務境界 (DO NOT)

- production environment に対して絶対に実行しない (二重防御: profile.environment.kind + cleanup-queue.metadata.environment_kind)
- `seed_id_prefix` で始まらない `seed_id`、 または injection-safe charset (`^[A-Za-z0-9_-]{1,128}$`) に違反する `seed_id` を絶対に処理しない (queue が改変されていても safety net として機能する重要なガード)
- cleanup-queue.json schema 違反のエントリを実行しない
- queue の `cleanup_command` 自由文字列を実行に使わない。 実行コマンドは必ず profile の `environment.cleanup.commands.<seed_type>` テンプレートから検証済み seed_id を再 substitute して導出する (queue の `cleanup_command` は参考表示 / drift 検出にのみ用いる)
- 他 skill を呼ばない (本 command は cleanup のみが責務)
