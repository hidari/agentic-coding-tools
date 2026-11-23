# ISSUE-113 技術情報の Issue のトリアージの共通の仕組み 実装プラン

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Goal: spec の「今回の範囲」の決まった処理 (select・triage のループ・post の整形)、アダプタと入口のスクリプト、reusable workflow の生成器と生成物を、ユニットテストと配線のテストで固定して入れる。

Architecture: `tech-briefing/tech_briefing/` の本体は標準ライブラリだけで書き、外との境界 (Messages API、GitHub の API、HTTP の取得、YAML の読み込み) を `protocols.py` の小さな Protocol にする。`tech-briefing/adapters/` が Protocol を実物で満たし、`tech-briefing/bin/` の入口のスクリプトが PEP 723 で依存を宣言して組み立てる。reusable workflow は `workflow_def.py` の dict から YAML を生成する。

Tech Stack: Python 3.11 以上の標準ライブラリ、unittest、`scripts/run-python-tests.py`。実行時だけ anthropic の SDK と YAML のパーサ (PEP 723、版は Task 9 で固定)。

Spec: `docs/issues/ISSUE-113_外部ツールが立てる技術情報の Issue をリポジトリへの影響で分類して報告する共通の仕組みを作る/ISSUE-113-spec.md`

## Global Constraints

- 本体 (`tech-briefing/tech_briefing/`) とテストは標準ライブラリだけ。`adapters/` と `bin/` をテストから import しない
- 構文は Python 3.11 で動く形 (runner の版が image で変わるため)。`type` 文や 3.12 以降の機能を使わない
- `tech-briefing/` の下のファイルの名前 (拡張子を除いた stem) は標準ライブラリのモジュール名と衝突させない (`bin/` のスクリプトを直に起動すると `sys.path[0]` が `bin/` になり、`select` のような名前が標準ライブラリを隠すため。`select` が衝突することは 2026-11-24 に `sys.stdlib_module_names` で確かめた)。パッケージの中のファイルを直に起動しない
- subagent の implementer はコミットしない、manifest に触らない。manifest の再生成とコミットは各 Task の境界でコントローラが行う
- テストは unittest。テストのファイルは `tech-briefing/tests/test_*.py`。各テストは `sys.path` に `tech-briefing/` を足してから `tech_briefing` を import する (runner はファイルごとにその置き場を cwd にして起動する)
- コード内のコメントは日本語で WHY を書く。外部の挙動に由来する実装は確かめた版と日付を添える
- 定数 (`constants.py`): 印 `<!-- tech-briefing-triage -->`、書き込みの名義 `github-actions[bot]`、タイトルの接頭辞 `[tech-briefing] `、時刻帯 `Asia/Tokyo`、URL の置き換え `[link: <host>]`、モデル `claude-opus-5-5`、effort `high`、beta `server-side-fallback-2026-07-01`、`fallbacks` は `"default"`、web fetch `web_fetch_20260318`、web search `web_search_20260209`
- select の結果の値: `no-issue` `wrong-author` `title-mismatch` `already-processed` `triage` `unexpected` `input-too-large` `config-error`。赤で終わるのは `wrong-author` `title-mismatch` `config-error`
- 失敗の種類: `api-error` `loop-limit` `token-limit` `refusal` `no-report` `invalid-report` `internal-error`
- テストを足したら `python3 scripts/run-python-tests.py --update-manifest` で manifest を再生成し、同じコミットに入れる

## Review Focus

- Issue のコメントが100件を超える (GitHub の API の1ページの上限): 全ページを読み、印の判定と入力の絞り込みが欠けない。Task 9 の GitHub の API のアダプタの Fake の側のテストで固定する
- 本文が `null` の Issue と、コメントが0件の Issue: 入力は空の本文として組み立て、落ちない。Task 4 で固定する
- 日付の境界 (UTC の 14:59 と 15:00): JST の当日が切り替わる。Task 4 で固定する
- 国際化ドメインと末尾の句読点・閉じ括弧が付いた URL: ホスト名だけが残り、句読点は本文に残る。Task 1 で固定する
- 誰かが印付きのコメントを後から編集して印を外した: 次の run は未処理として triage へ進む (spec の対照の手順と同じ挙動)。Task 4 で固定する

## ファイルの構成

- `tech-briefing/tech_briefing/constants.py`: Global Constraints の定数
- `tech-briefing/tech_briefing/protocols.py`: `MessagesClient` `GitHubApi` `Fetcher` `YamlLoader` の Protocol と、境界を流れる dataclass
- `tech-briefing/tech_briefing/textproc.py`: advisory の ID の取り出しと URL の置き換え
- `tech-briefing/tech_briefing/config.py`: 設定の検証
- `tech-briefing/tech_briefing/caller_check.py`: 呼び出し側の workflow の検査
- `tech-briefing/tech_briefing/selection.py`: 判定表と input.json の組み立て
- `tech-briefing/tech_briefing/tools.py`: 決まった処理の道具と、Messages API へ渡す道具の定義
- `tech-briefing/tech_briefing/triage.py`: 要求の組み立てとループ
- `tech-briefing/tech_briefing/render.py`: 無害化と本文の整形、post の判定
- `tech-briefing/tech_briefing/workflow_def.py`: reusable workflow の dict と、その書き出し
- `tech-briefing/adapters/`: `anthropic_client.py` `github_api.py` `fetcher.py` `yaml_loader.py`
- `tech-briefing/bin/`: `run_select.py` `run_triage.py` `run_post.py` (PEP 723) と `gen_workflow.py` (標準ライブラリだけ。生成物の `--check` / `--write` の入口)
- `tech-briefing/tests/`: `fakes.py` と `test_*.py`
- `.github/workflows/tech-briefing-triage.yml`: 生成物
- `.github/workflows/ci.yml` と `.pre-commit-config.yaml`: 生成物の drift の検査を足す

### Task 1: 土台と、advisory の ID の取り出しと URL の置き換え

Files:
- Create: `tech-briefing/tech_briefing/__init__.py`、`constants.py`、`textproc.py`
- Test: `tech-briefing/tests/test_textproc.py`

Interfaces:
- Produces: `extract_advisory_ids(text: str) -> list[str]` (出現順、重複なし、CVE は大文字に正規化)、`replace_urls(text: str) -> str`

- [ ] Step 1: 失敗するテストを書く

```python
def test_ids_of_three_kinds_in_order(self):
    t = "see GHSA-abcd-efgh-ijkl and cve-2026-12345, RUSTSEC-2026-0001; again CVE-2026-12345"
    self.assertEqual(extract_advisory_ids(t), ["GHSA-abcd-efgh-ijkl", "CVE-2026-12345", "RUSTSEC-2026-0001"])

def test_url_forms_become_host_only(self):
    cases = {
        "[docs](https://example.com/a?b=1)": "[link: example.com]",
        "<https://example.com/a>": "[link: example.com]",
        "see https://sub.example.org/x/y.": "see [link: sub.example.org].",
        "(https://example.net/z)": "([link: example.net])",
        "https://例え.jp/パス、続き": "[link: 例え.jp]、続き",
    }
    for src, want in cases.items():
        self.assertEqual(replace_urls(src), want, src)

def test_ids_are_taken_before_replacement(self):
    t = "https://github.com/advisories/GHSA-abcd-efgh-ijkl"
    self.assertEqual(extract_advisory_ids(t), ["GHSA-abcd-efgh-ijkl"])
    self.assertNotIn("GHSA", replace_urls(t))
```

- [ ] Step 2: `cd tech-briefing/tests && python3 -m unittest test_textproc -v` が ImportError で落ちることを確かめる
- [ ] Step 3: `textproc.py` を実装する。GHSA は `GHSA-xxxx-xxxx-xxxx` (英小文字と数字の4文字×3)、CVE は `CVE-\d{4}-\d{4,}`、RUSTSEC は `RUSTSEC-\d{4}-\d{4}`。URL の終わりは空白・`)`・`>`・`]`・全角の句読点と、末尾の ASCII の句読点 (`.,;:!?`) を URL に含めない
- [ ] Step 4: 同じコマンドで PASS を確かめる
- [ ] Step 5: コントローラが fmt とテストの緑を確かめ、manifest を再生成してコミットする

### Task 2: 設定の検証

Files:
- Create: `tech-briefing/tech_briefing/config.py`
- Test: `tech-briefing/tests/test_config.py`

Interfaces:
- Produces: `class ConfigError(Exception)`、`@dataclass(frozen=True) class Config: criteria: str; official_sources: tuple[str, ...]; files: tuple[FileRule, ...]; issues_glob: str | None; registries: tuple[str, ...]`、`@dataclass(frozen=True) class FileRule: glob: str; searchable: bool`、`load_config(raw: object) -> Config` (raw は YAML を読んだ dict)。constants に `DENY_HOSTS` と `DENY_SUFFIXES` を足す

- [ ] Step 1: 失敗するテストを書く。表で回す

```python
REJECT = ["github.com", "com", "gist.github.com", "user.github.io", "github.io",
          "https://docs.python.org", "*.example.com", "docs.example.com/blog",
          "аmazon.com", "", "example..com"]
ACCEPT = ["docs.github.com", "docs.python.org", "nvd.nist.gov", "osv.dev"]
def test_domain_table(self):
    for d in REJECT:
        with self.assertRaises(ConfigError, msg=d):
            load_config(valid(official_sources=[d]))
    self.assertEqual(load_config(valid(official_sources=ACCEPT)).official_sources, tuple(ACCEPT))
```

他に: 必須の欄の欠け、未知の欄、`registries` が `npm` `crates` `pypi` の外、`files` が空、glob が `..` か `/` で始まる、`issues_glob` の型の誤り、をそれぞれ ConfigError にするテスト
- [ ] Step 2: `python3 -m unittest test_config -v` が落ちることを確かめる
- [ ] Step 3: `load_config` を実装する。拒否は「項目が DENY_HOSTS のどれかと一致する」「DENY_HOSTS のどれかが `.` + 項目で終わる (項目が親にあたる)」「項目が DENY_SUFFIXES のどれかと一致するか `.` + その接尾辞で終わる」のどれか。DENY_HOSTS は少なくとも `github.com` `gist.github.com` `raw.githubusercontent.com` `gitlab.com` `bitbucket.org`、DENY_SUFFIXES は少なくとも `github.io` `githubusercontent.com` `gitlab.io` `pages.dev` `netlify.app` `vercel.app`。ASCII 以外・スキーム・`*`・`/`・空・連続する `.` を拒否する
- [ ] Step 4: PASS を確かめる
- [ ] Step 5: コントローラが fmt とテストの緑を確かめ、manifest を再生成してコミットする

### Task 3: 呼び出し側の workflow の検査

Files:
- Create: `tech-briefing/tech_briefing/caller_check.py`
- Test: `tech-briefing/tests/test_caller_check.py`

Interfaces:
- Produces: `check_caller(workflow: object, reusable: str) -> list[str]` (workflow は YAML を読んだ dict、reusable は `<owner>/<repo>/.github/workflows/tech-briefing-triage.yml`。違反の説明の一覧を返し、空なら合格)

- [ ] Step 1: 失敗するテストを書く。合格する最小の dict を1つ作り、そこから1か所ずつ壊した dict がそれぞれ違反を1件返すことを表で見る: `@` の後が40桁の16進でない (タグ、ブランチ、短い SHA)、別のファイルを呼ぶ、trigger に `push` か `pull_request` か `issue_comment` がある、top-level の permissions が `{}` でない、呼び出しの job の permissions に `contents: read` と `issues: write` のほかがある、呼び出しの job が無いか2つある。YAML の `on` が真偽値の `True` の鍵で届く形も合格の側で通す
- [ ] Step 2: 落ちることを確かめる
- [ ] Step 3: `check_caller` を実装する
- [ ] Step 4: PASS を確かめる
- [ ] Step 5: コントローラが fmt とテストの緑を確かめ、manifest を再生成してコミットする

### Task 4: select の判定と入力の組み立て

Files:
- Create: `tech-briefing/tech_briefing/protocols.py` (`IssueRef(number: int, title: str, author: str, created_at: str, body: str | None)`、`CommentRef(id: int, author: str, body: str, created_at: str)` と `GitHubApi` Protocol: `open_issues() -> list[IssueRef]`、`comments(number: int) -> list[CommentRef]`、`post_comment(number: int, body: str) -> None`)、`tech-briefing/tech_briefing/selection.py`
- Test: `tech-briefing/tests/test_select.py`、`tech-briefing/tests/fakes.py` (`FakeGitHub`)

Interfaces:
- Consumes: Task 1 の `extract_advisory_ids` `replace_urls`
- Produces: `today_jst(now_utc: datetime) -> str`、`@dataclass(frozen=True) class Decision: result: str; issue: IssueRef | None; input: dict | None; summary: str`、`decide(api: GitHubApi, author: str, now_utc: datetime, max_input_chars: int) -> Decision`、`run_select(raw_config: object, caller_workflow: object, reusable: str, api: GitHubApi, author: str, now_utc: datetime, max_input_chars: int) -> tuple[Decision, Config | None]` (Task 2 の `load_config` と Task 3 の `check_caller` を先に当て、どちらかが誤りなら api を1度も呼ばずに `config-error`)、`is_red(result: str) -> bool`
- Consumes (追加): Task 2 の `load_config` と `ConfigError`、Task 3 の `check_caller`

- [ ] Step 1: 失敗するテストを書く。spec の判定表の6行と、足した2行 (`title-mismatch`、`author` が `github-actions[bot]` のときの `config-error`、`author` が空のときの `config-error`) を表で回す。ほかに:
  - `today_jst(datetime(2026,11,24,14,59,tzinfo=UTC)) == "2026-11-24"`、15:00 は `"2026-11-25"`
  - 入力に入るのは本文と指定アカウントのコメントだけ。印で始まるコメントはどのアカウントのものも外し、指定アカウントの印付きの件数が `removed_marked_by_author` に入る
  - 書き込みの名義の印付きのコメントが後から印を外されていたら `triage`
  - 本文が `None` でコメントが0件でも落ちず、`input["body"] == ""`
  - `input["advisory_ids"]` が本文とコメントから取り出した ID を持ち、`input` のどの文字列にも `https://` が残らない
  - 本文とコメントの文字数の合計が `max_input_chars` を超えたら `input-too-large` で、`input` は `None` (切らない)
  - `is_red` が真になるのは `wrong-author` `title-mismatch` `config-error` だけ
  - `run_select` は、設定がスキーマに合わないときと呼び出し側の検査に違反があるときに `config-error` を返し、`FakeGitHub` の呼び出しの記録が空
- [ ] Step 2: 落ちることを確かめる
- [ ] Step 3: `decide` を実装する。2件以上のときの「新しい方」は `created_at` の新しい方。`summary` は step summary に出す1行で、結果の値を必ず含む
- [ ] Step 4: PASS を確かめる
- [ ] Step 4b: 変異注入。下の変異を1つずつ入れ、狙ったテストだけが赤くなることを確かめ、変異と赤くなったテストの ID の対応を `.cache/mutations-task4.json` に残す (コントローラが同じ変異を再生する)。変異: 判定表の `already-processed` の条件を外す、`wrong-author` を緑にする、印のコメントの除外を外す、日付を UTC で決める
- [ ] Step 5: コントローラが fmt とテストの緑を確かめ、manifest を再生成してコミットする

### Task 5: 決まった処理の道具

Files:
- Create: `tech-briefing/tech_briefing/tools.py`、protocols に `Fetcher` (`get_json(url: str) -> object`、行き先の URL はコードが組み立てる) を足す
- Test: `tech-briefing/tests/test_tools.py`、fakes に `FakeFetcher` (呼ばれた URL を記録する)

Interfaces:
- Consumes: Task 2 の `Config`
- Produces: `class ToolBox: __init__(self, repo_root: Path, config: Config, fetcher: Fetcher, result_limit: int)`、`definitions(self) -> list[dict]` (client tool の定義。`submit_report` を含み、全部 `strict: True`)、`server_tools(self) -> list[dict]`、`call(self, name: str, args: dict) -> str` (形の誤りは例外ではなく、LLM に分かる誤りの文字列を返す)、`REPORT_SCHEMA: dict`

- [ ] Step 1: 失敗するテストを書く
  - `read_file` が `files` の外、`..`、絶対パス、symlink を拒否し、許したファイルは読める (一時ディレクトリに作る)
  - `search_files` が検索の印の無い glob のファイルを見ない。固定文字列で当て、正規表現の記号を記号として扱う
  - `search_issues` は `issues_glob` が無いとき定義に出ず、在るときは親のディレクトリの名前だけを返して本文を返さない
  - `registry_metadata` は `registries` の外の ecosystem を拒否し、パッケージ名の形 (npm のスコープ付き、crates、pypi) の外を拒否する。返すのは repository の欄と最新の版だけ
  - `release_notes` が叩く URL のリポジトリは registry の metadata から決まり、引数でリポジトリを渡せない (FakeFetcher の記録で見る)
  - `advisory` は ID の形の外を拒否し、行き先は固定の advisory のデータベースだけ
  - registry の metadata の `repository` の欄は作者が書けるので、GitHub 以外のホスト、`..` や `?` や `#` を含むパス、owner と repo の2つより多い segment を拒否する (`.git` の接尾辞と `git+` の接頭辞だけは外して読む)
  - どの道具を呼んでも、FakeFetcher が記録した URL のホストが固定の集合 (OSV、3つの registry、api.github.com) に収まる
  - `search_files` も symlink を辿らない
  - 結果が `result_limit` を超えたら切り、切ったことを末尾の1行で示す
  - `server_tools()` の web fetch が `type == "web_fetch_20260318"`、`url_sources == {"client_tool_results": {"type": "none"}}`、`allowed_domains == list(config.official_sources)`、`max_uses` と `max_content_tokens` を持つ。web search が `type == "web_search_20260209"` で同じ `allowed_domains` と `max_uses` を持つ
- [ ] Step 2: 落ちることを確かめる
- [ ] Step 3: `ToolBox` を実装する。advisory のデータベースは OSV (`https://api.osv.dev/v1/vulns/<ID>`)、registry は npm `https://registry.npmjs.org/<name>`、crates `https://crates.io/api/v1/crates/<name>`、pypi `https://pypi.org/pypi/<name>/json`、リリースノートは `https://api.github.com/repos/<owner>/<repo>/releases/tags/<tag>` (どれも作り手の判断。実物での確かめは Issue のタスク)
- [ ] Step 4: PASS を確かめる
- [ ] Step 4b: 変異注入。下の変異を1つずつ入れ、狙ったテストだけが赤くなることを確かめ、変異と赤くなったテストの ID の対応を `.cache/mutations-task5.json` に残す (コントローラが同じ変異を再生する)。変異: `read_file` の symlink の拒否を外す、`registries` の照合を外す、`repository` の欄のホストの検査を外す、`url_sources` を外す
- [ ] Step 5: コントローラが fmt とテストの緑を確かめ、manifest を再生成してコミットする

### Task 6: triage の要求とループ

Files:
- Create: `tech-briefing/tech_briefing/triage.py`、protocols に `MessagesClient` (`create(params: dict) -> dict`。応答は Messages API の JSON の形の dict) を足す
- Test: `tech-briefing/tests/test_triage.py`、fakes に `FakeMessages` (応答の列を順に返し、受けた params を記録する)

Interfaces:
- Consumes: Task 5 の `ToolBox`、Task 4 の input の dict
- Produces: `@dataclass(frozen=True) class Limits: max_rounds: int; max_input_tokens: int`、`@dataclass class Outcome: report: dict | None; failure: str | None; tool_log: list[dict]; usage: dict`、`build_params(system: str, criteria: str, user_input: dict, toolbox: ToolBox) -> dict`、`run_triage(client: MessagesClient, params: dict, toolbox: ToolBox, limits: Limits) -> Outcome`

- [ ] Step 1: 失敗するテストを書く
  - `build_params` の結果が `model == "claude-opus-5-5"`、`thinking == {"type": "adaptive"}`、`output_config == {"effort": "high"}`、`fallbacks == "default"`、`betas` に `server-side-fallback-2026-07-01`、`output_config` に `format` が無い、`tool_choice` が無いか `{"type": "auto"}`、system の最後のブロックに `cache_control`、system のどこにも user_input の文字列が無い、user のメッセージが入力を「信頼できないデータ」の区切りで包む
  - `tool_use` の応答のあと、次の要求の最後の user のメッセージが `tool_result` のブロックだけで、`tools` が同じ
  - 結果の無い `server_tool_use` を含む `tool_use` の応答でも同じ形で続ける
  - `pause_turn` の応答のあと、応答の content をそのまま足して送り直す
  - `submit_report` の入力が `REPORT_SCHEMA` に合えば `report` を返し、合わなければ `invalid-report`
  - `submit_report` が無いまま `end_turn` で終われば `no-report`、`refusal` なら `refusal`、往復が `max_rounds` を超えれば `loop-limit`、usage の入力トークン (`input_tokens` と `cache_read_input_tokens` と `cache_creation_input_tokens` の和) の合計が `max_input_tokens` を超えれば `token-limit`、client の例外は `api-error`
  - `tool_log` に client tool の名前と引数、server tool の query と URL が全部入り、`usage` が合計を持つ
- [ ] Step 2: 落ちることを確かめる
- [ ] Step 3: `build_params` と `run_triage` を実装する。system prompt の文面は spec の「共通の基準」を写した定数にし、基準の文書は別のブロックで差し込む
- [ ] Step 4: PASS を確かめる
- [ ] Step 5: コントローラが fmt とテストの緑を確かめ、manifest を再生成してコミットする

### Task 7: 無害化と本文の整形と post の判定

Files:
- Create: `tech-briefing/tech_briefing/render.py`
- Test: `tech-briefing/tests/test_render.py`

Interfaces:
- Consumes: Task 4 の `Decision`、Task 6 の `Outcome`
- Produces: `sanitize(text: str) -> str`、`render_report(report: dict) -> str`、`render_failure(kind: str) -> str`、`render_unexpected() -> str`、`render_too_large() -> str`、`should_post(api: GitHubApi, number: int) -> bool` (書き込みの名義の印付きのコメントが既に在れば False)、`plan_post(select_result: str, report: dict | None, failure: str | None) -> tuple[str, int]` (投稿する本文と、投稿の後の終了コード)

- [ ] Step 1: 失敗するテストを書く
  - `sanitize` が `@user` `#123` `<script>` `[a](b)` `![x](y)` `|` と改行を、メンション・自動リンク・HTML・リンク・表の区切りにならない形へ変える (zero width space などの見えない文字は、テストの中でもリテラルで書かず `chr(0x200b)` のように code point で組む)
  - どの render の結果も印で始まる
  - `render_failure("no-report")` が「トリアージできなかった: no-report」を含む
  - `render_report` の表の行数が項目の数と一致し、LLM の文字列は全部 `sanitize` を通っている (項目に `| x |` を入れても列が増えない)
  - `should_post` は書き込みの名義の印付きのコメントで False、指定アカウントの印付きのコメントでは True
  - `plan_post`: `triage` で report が在れば報告の本文と0、failure が在れば失敗の本文と1、どちらも無ければ `internal-error` の本文と1、`unexpected` と `input-too-large` はそれぞれの定型と1
- [ ] Step 2: 落ちることを確かめる
- [ ] Step 3: 実装する
- [ ] Step 4: PASS を確かめる
- [ ] Step 4b: 変異注入。下の変異を1つずつ入れ、狙ったテストだけが赤くなることを確かめ、変異と赤くなったテストの ID の対応を `.cache/mutations-task7.json` に残す (コントローラが同じ変異を再生する)。変異: `sanitize` のメンションの無害化を外す、表の区切りのエスケープを外す、`should_post` の名義の照合を外す
- [ ] Step 5: コントローラが fmt とテストの緑を確かめ、manifest を再生成してコミットする

### Task 8: reusable workflow の生成と配線のテスト

Files:
- Create: `tech-briefing/tech_briefing/workflow_def.py`、`.github/workflows/tech-briefing-triage.yml` (生成物)
- Modify: `.github/workflows/ci.yml` (readme-drift の job に `python3 tech-briefing/bin/gen_workflow.py --check` の step を足す)、`.pre-commit-config.yaml` (同じ検査の hook を足す)
- Test: `tech-briefing/tests/test_workflow_def.py`

Interfaces:
- Produces: `workflow() -> dict`、`render(doc: dict) -> str` (`json.dumps(doc, indent=2, ensure_ascii=False)` に末尾の改行)、`bin/gen_workflow.py` の `main(argv: list[str]) -> int` (`--check` は生成物と一致で0、不一致で1、`--write` は書き出す)

- [ ] Step 1: 失敗するテストを書く
  - job は `select` `triage` `post` の3つで、権限の表が `select: {contents: read, issues: read}`、`triage: {contents: read}`、`post: {contents: read, issues: write}`、top-level が `{}`
  - `secrets.ANTHROPIC_API_KEY` を参照するのは `triage` だけで、`triage` は `environment: tech-briefing` を持ち、`on.workflow_call.secrets.ANTHROPIC_API_KEY.required` が真
  - どの step の `run:` にも `${{` が無い (値は `env:` で渡す)
  - `uses:` は全部 `@` の後が40桁の16進
  - `post` は `if:` に `always()` と、select の結果が `triage` `unexpected` `input-too-large` のどれかの条件を持つ
  - `concurrency` を持つ
  - 共通の仕組みのコードを checkout する各 job で、`job.workflow_repository` と `job.workflow_sha` が空なら非0で止まる step が checkout より前にある
  - 生成物は JSON の文書として書く (YAML 1.2 は JSON を含むので GitHub の YAML として読める。鍵 `on` も文字列のまま残る)。`json.loads(生成物) == workflow()` で読み戻して一致を見る
  - `bin/gen_workflow.py --check` が一致で0、1文字変えた生成物で1を返す
- [ ] Step 2: 落ちることを確かめる
- [ ] Step 3: 実装し、`--write` で生成物を書く。job 間の受け渡しは actions/upload-artifact と download-artifact (SHA で固定)。共通の仕組みのコードの checkout は `repository: ${{ job.workflow_repository }}` と `ref: ${{ job.workflow_sha }}` で行う (docs.github.com の contexts に、reusable workflow が自分のソースを checkout する用途の例がある。GitHub Enterprise Server では使えない。2026-11-24 に原文で確かめた)。どちらかが空なら止まる step を checkout の前に置く。実物で届くかは Issue のタスクに残す
- [ ] Step 4: PASS を確かめ、`python3 tech-briefing/bin/gen_workflow.py --check` が rc 0、生成物を1文字変えると rc 1 になることを確かめる。コントローラが境界で1回だけ、実物の YAML のパーサ (`uv run --with pyyaml`) で生成物を読み、`json.loads` の結果と一致すること (鍵 `on` が真偽値に化けないこと) を確かめる
- [ ] Step 4b: 変異注入。下の変異を1つずつ入れ、狙ったテストだけが赤くなることを確かめ、変異と赤くなったテストの ID の対応を `.cache/mutations-task8.json` に残す (コントローラが同じ変異を再生する)。変異: `triage` の権限に `issues: write` を足す、鍵を `post` でも参照する、`run:` に式を直に書く、空の SHA で止まる step を外す
- [ ] Step 5: コントローラが fmt とテストの緑を確かめ、manifest を再生成してコミットする

### Task 9: アダプタと入口のスクリプトと境界のテスト

Files:
- Create: `tech-briefing/adapters/anthropic_client.py`、`github_api.py` (urllib と GITHUB_TOKEN。ページを全部読む)、`fetcher.py` (urllib)、`yaml_loader.py`、`tech-briefing/bin/run_select.py`、`run_triage.py`、`run_post.py` (PEP 723 の `# /// script` で依存と版を固定。anthropic と PyYAML は公開から2週間以上の版)
- Test: `tech-briefing/tests/test_boundaries.py`、`tech-briefing/tests/test_fakes_contract.py`

Interfaces:
- Consumes: Task 2〜8 の全部
- Produces: 入口のスクリプトの引数と環境変数 (Task 8 の workflow が渡すもの) と、artifact のファイル名 `input.json` `report.json` `failure.json` `tool-log.json`

- [ ] Step 1: 失敗するテストを書く
  - `tech_briefing/` のどのモジュールも、ast で読んだ import に `adapters`・`anthropic`・`yaml` を持たない
  - テストのファイルのどれも `adapters` と `bin` を import しない
  - `tech-briefing/` の下のどの `.py` の stem も `sys.stdlib_module_names` に入らない
  - `bin/` の各スクリプトが PEP 723 の block を持ち、依存の版が `==` で固定されている
  - Fake の側の契約: `FakeGitHub` が100件を超えるコメントをページに分けて返す設定でも、`decide` が全件を見る (アダプタの `comments` が全ページを返す約束を Fake に持たせる)。`FakeMessages` の応答の形が Task 6 のループの前提 (content の block の type、stop_reason、usage の鍵) と一致する
- [ ] Step 2: 落ちることを確かめる
- [ ] Step 3: アダプタと入口のスクリプトを書く。入口は Protocol の実物を組み立てて本体を呼ぶだけで、判定を持たない
- [ ] Step 4: PASS を確かめ、`python3 scripts/run-python-tests.py` と `pre-commit run --all-files` が緑であることを確かめる
- [ ] Step 5: コントローラが fmt とテストの緑を確かめ、manifest を再生成してコミットする

### Task 10: Issue に残りを書く

Files:
- Modify: `docs/issues/ISSUE-113_*/issue.md`

- [ ] Step 0: spec の「テスト」の節の各項目と、それを固定するテスト ID の対応表を作り、PR の本文に載せる
- [ ] Step 1: タスクの「実装し、テストで固定する」に印を付け、人の手が要る残りをタスクとして足す: 鍵と environment と variable とワークスペースの用意、契約テストの実物の側、入口のスクリプトとアダプタの実物での走行 (select だけの dry-run から)、有効にする前のテスト、導入の手順の skill、spec の「実装のときに確かめること」の各項目
- [ ] Step 2: コミットする
