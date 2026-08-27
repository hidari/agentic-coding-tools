---
description: "Universal Monkey QA explorer. Reads <project>/.claude/monkey-qa-profile.yml, applies the environment gate, fans out monkey-explorer-agent per section, and aggregates findings into findings.json + monkey-report.md. 「モンキーテストして」「探索テストして」「ランダムに歩き回って壊れてないか見て」「デプロイ後の探索 QA」と依頼された時にも起動する。"
argument-hint: "[--target=local|staging]"
---

# /monkey-qa

product-agnostic な AI 探索型モンキーテストの dispatcher。実体は `agents/monkey-explorer-agent.md` を
profile.sections[] ごとに `Agent(subagent_type="web-monkey-qa:monkey-explorer-agent")` で fan-out し、
各 explorer が書く findings フラグメントを集約する薄いオーケストレータ。探索ループ・検知器・denylist
照合の実装は一切ここに置かない (DRY、詳細は agent 側の system prompt が唯一の source of truth)。

## 入力

- **profile** (必須): `<project>/.claude/monkey-qa-profile.yml`。schema は `${CLAUDE_PLUGIN_ROOT}/schemas/monkey-qa-profile.schema.yml`。無ければ `${CLAUDE_PLUGIN_ROOT}/schemas/monkey-qa-profile.template.yml` を提示して停止。
- **output_dir**: レポート出力先。デフォルト `profile.output_dir` (通常 `tmp/monkey-qa/`)。
- **target**: `local` / `staging` / `production`。`profile.environment.kind` から自動解決する。`$ARGUMENTS` の `--target=<local|staging>` は表示・ログ目的の補助情報にとどめ、**環境ガードの判定には使わない** (override で production を回避させないための設計。gate は常に profile から再導出する)。

## 実行フロー

1. **profile を構造化 parse** する (`python3 -c "import yaml; yaml.safe_load(open(...))"` 等。PyYAML はシステムの python に入っていないことがあるので、無ければ `uv run --with pyyaml python3 -c ...` のように調達する。text grep は禁止 — コメントアウトされた `kind: production` 行や継続行を誤読して production を見落とし/誤検知しうる)。ファイルが無い、または parse に失敗したら `monkey-qa-profile.template.yml` を提示して停止する (この時点で abort する場合、date directory は作らない)。
2. **環境ガード (二層防御の宣言的層。ここが canonical — 他ファイルはこの記述を参照するのみで再定義しない)**:
   - `environment.kind == production` の場合、**read-only モードを強制**する。`sections[]` のうち `auth: seed_login` の区画は explorer を dispatch せず skip し、その section 名と「production read-only によりスキップ」を記録する。`auth: none` の区画のみ匿名探索として実行対象に残す。
   - **production で実行対象に残る全 section には、破壊的操作 (fill / submit / 認証) への到達を実装で封じるため次を強制する (profile の値を無視する)**: `SUBMIT_ALLOWLIST` を**空配列**に上書きし、`READ_ONLY: true` を渡す。この 2 つ (`auth: seed_login` の section を dispatch しない + 残る section に空 `SUBMIT_ALLOWLIST` と `READ_ONLY: true` を渡す) が production read-only の canonical な担保であり、宣言 (責務境界) と実装 (dispatch 引数) をここで一致させる。
   - `environment.kind` が `local` / `staging` の場合、全 section をそのまま実行対象にする (`SUBMIT_ALLOWLIST` は profile の値、`READ_ONLY` は `false`)。
   - この判定は section の実行可否を決めるだけであり、実行される explorer 側の denylist 照合 (procedural 層) を代替しない。両層は独立して効く。
   - 実行対象が 0 件になった場合 (例: production で全 section が `seed_login`)、**abort** する。「全 section が production read-only によりスキップされたため中断」と報告し、date directory・findings.json・monkey-report.md のいずれも作らない。
3. **入力コンパイル**: 実行対象の各 section について、agent の Inputs に対応する引数を組み立てる。
   - `MONKEY_PROFILE`: profile の絶対パス
   - `SECTION`: section の `name`
   - `BASE_URL`: `environment.base_url_env` が指す環境変数名を実行環境から解決した値 (profile YAML に URL/secret を直接書かないため、値は必ず env 経由で読む)
   - `OUTPUT_DIR`: `profile.output_dir`
   - `DATE_DIR`: `<OUTPUT_DIR>/<YYYY-MM-DD>`。`<YYYY-MM-DD>` は **UTC** (`date -u +%Y-%m-%d`) で組み立てる (wrapper 側が `date -u` で同じ path を再構築するため。timezone を canonical に固定しないと 00:00-09:00 JST の窓で dispatcher (JST) と wrapper (UTC) が別ディレクトリを指し、High findings の起票が silent にゼロ件になる)。このディレクトリは実行対象が1件以上確定した時点で初めて作成する
   - `DENYLIST_TEXTS` / `DENYLIST_URLS` / `SUBMIT_ALLOWLIST`: `safety.denylist_texts` / `safety.denylist_urls` / `safety.submit_allowlist` をそのまま渡す (ただし production では step 2 の通り `SUBMIT_ALLOWLIST` を空配列に上書きする)
   - `BUDGET`: `{pages_per_agent, actions_per_page}` (`budget` から)
   - `VIEWPORT`: section の `viewport` (既定は agent 側の Inputs が持つ)
   - `AUTH_RECIPE`: section の `auth` が `seed_login` の場合のみ profile 直下の `auth.recipe` を渡す。`auth: none` の section には渡さない (空)
   - `HTTP_AUTH_USERNAME_ENV` / `HTTP_AUTH_PASSWORD_ENV`: `environment.http_auth` があればその `username_env` / `password_env` の**変数名をそのまま**渡す。**値は解決しない** (profile / dispatcher / agent prompt に secret を載せないため)。`http_auth` が無ければ両方とも空。origin 全体の transport なので `auth: none` の section にも渡す (section の auth mode と無関係)
   - `READ_ONLY`: `environment.kind == production` なら `true`、それ以外は `false`
4. **Fan-out**: 実行対象の section ごとに `Agent(subagent_type="web-monkey-qa:monkey-explorer-agent")` を dispatch する。1 section = 1 explorer instance。**並行数の上限は `budget.agents`** — 実行対象 section 数が `budget.agents` を超える場合は `budget.agents` 個ずつ wave に分けて dispatch し、各 wave の完了を待ってから次の wave に進む (section 数が `budget.agents` 以下なら 1 wave で全 section 並行)。1 体が失敗 (abort/エラー終了) しても他の explorer には波及させない (partial salvage) — 失敗した section は「探索失敗」として集約時に記録し、その他の section の findings は正常に集約する。
5. **集約**: 完走した各 section が書いた `<DATE_DIR>/section-<SECTION>.findings.json` (JSON 配列、agent Phase 4 の出力契約) を全て読む。
   - 全 finding を1つの配列に concat し、`fingerprint` が同一の finding は 1 件に merge する (dedup、最初に出現したものを代表として残す)。
   - dedup 後の配列から `severity` 別に `high` / `medium` / `low` の件数を集計する。
   - `metadata.date` (`YYYY-MM-DD`、DATE_DIR と同じく **UTC** `date -u +%Y-%m-%d`)、`metadata.environment.kind`/`target` (共に profile の `environment.kind` を写す)、`metadata.sections_run` (実行対象になった section 名の配列。production でスキップされた section は含めない) を組み立てる。
   - `findings.schema.json` (`${CLAUDE_PLUGIN_ROOT}/schemas/findings.schema.json`) 準拠の `{metadata, findings, statistics}` オブジェクトとして `<DATE_DIR>/findings.json` に書く。
6. **レポート**: `<DATE_DIR>/monkey-report.md` を生成する。冒頭に statistics サマリ (High/Medium/Low 件数、production でスキップした section があればその一覧) を置き、以降 severity 順 (High → Medium → Low) に finding を列挙する。各 finding は `category` / `url` / `signal` / `repro_steps` / `screenshot` へのリンクを含める。
7. 出力ファイル (`findings.json` / `monkey-report.md` / `screenshots/`) の絶対パスと statistics を user に報告する。後続 (Issue 起票 / PR 作成) は wrap layer か user の判断に委ねる。

## 出力契約

- `<output_dir>/<YYYY-MM-DD>/findings.json` (`findings.schema.json` 準拠、`{metadata, findings, statistics}`)
- `<output_dir>/<YYYY-MM-DD>/monkey-report.md` (severity 順・人間可読)
- `<output_dir>/<YYYY-MM-DD>/screenshots/*.png` (explorer が書いたものをそのまま集約先に残す)
- abort 時 (profile 無し / parse 失敗 / production で全 section が seed_login スキップ) は date directory を作らない。

## 責務境界 (DO NOT)

- 他 skill (`dev-workflow:in-repo-issue` 等) を呼ばない
- 実装変更 (Edit / Write) を行わない (`findings.json` / `monkey-report.md` の生成のみ)
- PR 作成 / Issue 起票を行わない (wrap skill / user に委ねる)
- profile に無い product-specific チェックを勝手に行わない
- production では破壊的操作 (fill / submit / 認証) に絶対到達しない (`auth: seed_login` の section を dispatch しない **+ 残る section に `READ_ONLY: true` と空の `SUBMIT_ALLOWLIST` を渡す**ことで担保する。実行フロー 2 が canonical)
  - この dispatcher を経由しない経路 (agent type としての直接 dispatch) では上の引数が届かないので、`agents/monkey-explorer-agent.md` の Phase 0 が profile から独立に同じ判定を行う。層は 2 つあり、canonical はここのまま
- 探索ループ・検知器のロジックをここに複製しない (`agents/monkey-explorer-agent.md` が唯一の実装)

## 使われない条件

- 単発 PR の pending changes レビュー → 公式 `/security-review` や feature-dev の責務
- 回帰テスト → 恒久 E2E spec (モンキーは探索であって回帰ではない)
- セキュリティの能動攻撃シミュレーション → 対の `/security-redteam` (`security-blue-red-team` bundle)

## 関連

- `agents/monkey-explorer-agent.md` — 探索本体 (探索ループ・検知器・denylist 照合) と Phase 0 の production gate (直接 dispatch でも効く独立層)
- `${CLAUDE_PLUGIN_ROOT}/schemas/monkey-qa-profile.schema.yml` — profile の入力契約
- `${CLAUDE_PLUGIN_ROOT}/schemas/findings.schema.json` — findings.json の出力契約
