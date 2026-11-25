---
status: open
---

# feat: 外部ツールが立てる技術情報の Issue をリポジトリへの影響で分類して報告する共通の仕組みを作る

## 背景

外部のツールが、技術情報 (依存の advisory、廃止や破壊的変更、新機能) を集めたまとめを `[tech-briefing] YYYY-MM-DD` の形のタイトルで GitHub Issue として毎日立てる。受け取る側のリポジトリは、その項目が自分に当たるかを毎回人が読んで判断している。

LLM にトリアージを任せたいが、Issue の本文とコメントは外から自動で集めた文章で、指示を埋め込める。LLM を動かす主体が書き込みの権限や shell を持つと、禁止はプロンプトの文言でしか効かない。守りは job の権限と道具の構成で効かせる必要がある。

同じ仕組みを複数のリポジトリで使うので、リポジトリごとに作ると同じ問題を別々に直すことになる。共通の reusable workflow と Python の処理をこのリポジトリに置き、各リポジトリは SHA を固定して呼び、呼び出しの workflow と設定のファイルと基準の文書だけを持つ形にする。導入の手順は apm で配る skill にする。

この段は報告だけで、自動の修正・PR・マージの段の土台になる。設計は `ISSUE-113-spec.md` に置く。

## タスク

- [x] 設計書 (`ISSUE-113-spec.md`) を書いて承認を得る
- [x] 実装プラン (`ISSUE-113-plan.md`) を書く
- [x] 決まった処理 (select の判定、設定と呼び出し側の検査、入力の絞り込みと置き換え、道具の許可、本文の整形と無害化、失敗の終わり方、ループの上限) と reusable workflow を実装し、ユニットテストと配線のテストで固定する

人の手や外部への通信が要る残り。実装はユニットテストと Fake の契約テストと配線のテストまでで、実物での走行はまだ一度もしていない。

- [ ] 準備: 技術情報のトリアージ用の専用のワークスペースを Console に作って月の支出上限を掛け、リポジトリ専用の鍵を発行する。GitHub 側に environment `tech-briefing`、鍵、variable を作る。組織の web search / web fetch の許可の一覧が `official_sources` を含むことを確かめる
  - 鍵を発行する前に、入口の3本の script を `uv lock --script` で lock し (`<script>.lock` を作ってコミットし、起動はそれを使う)、間接の依存を hash まで固定する。いまは PEP 723 の `[tool.uv]` の `exclude-newer` で解決の日付を絞っただけで、それが間接の依存に効くことは uv の docs に明記が無い
- [ ] 契約テストの実物の側を手で回す。対象は Messages API、GitHub の API、取得先の registry と OSV。次を実物で確かめる
  - registry の欄の名前 (npm の `dist-tags.latest`、crates の `max_stable_version`、PyPI の `project_urls`) は記憶で書いたもので、実物で確かめていない
  - OSV を CVE で引いたとき、alias にしか無い記録が 404 になるかもしれない (未確認)
  - 信頼できない区切りの中で `<` をエスケープすると LLM の読みが変わるか、`pause_turn` の後に assistant が続く形を API が受けるか、usage の null を API が返すか
  - registry の repository の欄の実際の形。`git+ssh` や `git://`、`github:` の短縮形、末尾の `/` は配布元のリポジトリを判定できず、release notes を引けない
  - PyPI で正規化していない名前を引いたときと、改名したリポジトリの releases を引いたときの redirect。fetcher は redirect を拒否するので、どちらも取得の失敗になる
  - api.github.com を認証なしで引くときの rate limit に、1回の triage の道具の呼び出しが収まるか
- [ ] 入口のスクリプトとアダプタを実物で走らせる。select だけの dry-run から始め、続けて triage と post に進む。`gh` と GitHub の API を呼ぶ経路は live smoke で通す。次を実物の run で確かめる
  - `job.workflow_repository` と `job.workflow_sha` が step の `env:` と `with:` で効くか、`inputs.config-path` のようなハイフン付きの名前をドットで読めるか
  - triage の upload が複数ファイルのとき、artifact の中の相対パスがどうなるか
  - anthropic のアダプタは一度も実行していない。2.7.0 の credentials の自動探索と、鍵を env に置いたときの `warn_env_static_shadows_auto_discovery` を実物の走行で確かめる
  - 無害化した本文が GitHub 上でリンク・メンション・参照にならないこと。特に `www.` と `GH-` の直後に ZWSP を入れた形と、スキームのコロンの後に ZWSP を入れた形を、実物のコメントで確かめる
  - triage が timeout したときの `needs.triage.result` の値と post の挙動。cancelled になるなら post が skip されて印が付かず、次の run から毎回 triage を呼び直す
  - run を re-run したときに、同じ名前の artifact が衝突しないか
  - stop_reason が tool_use なのに client の道具のブロックが無い応答を API が返すか。返すなら、いまのループは assistant を最後にして送り直し、400 から api-error に化けうる
  - スキーム無しの URL (`www.` で始まる形) を web_fetch が開くか
- [ ] 有効にする前のテストを、spec の「有効にする前のテスト」のとおり、最初に導入するリポジトリで回す。基準の run と入力の範囲、2回目と対照、injection (道具の制限の確かめを含む) の順。ブリーフィングが投稿を始める前に行う
  - 基準の run と2回目は導入するリポジトリごとに1回ずつ、injection は共通の仕組みの SHA ごとに1回回す
  - 終わったらテスト用に立てた Issue を閉じる
- [ ] 導入の手順の skill を apm で配る形で書く。有効にする前のテストを最初のリポジトリで1回回したあとに書く
- [ ] 拒否のドメインの一覧と files の glob の残りを直す
  - 拒否のドメインの一覧を広げる。いまは blogspot.com・medium.com・readthedocs.io・web.archive.org・translate.goog・s3.amazonaws.com・storage.googleapis.com・codeberg.org が通る。導入の skill に「一覧は網羅ではない」と書く
  - 設定の files の glob の `*` が `/` をまたぐ (fnmatch の挙動)。導入の skill と設定の説明にそう書くか、`/` をまたがない照合に替える
- [ ] コードの小さな残りを直す
  - select が GitHub の API の失敗 (URLError・TimeoutError・HTTPException) で `result=` と step summary を出さずに traceback で終わる。入口で捕まえて原因を1行出す。select の結果の値を足すかもここで決める
  - max_tokens などで止まって no-report になったときに、最後の stop_reason を内部ログに出す
  - `pause_turn` の応答に client の tool_use が混ざる形のテストを足す
  - select の upload の step の `if:` を固定するテストを足す
- [ ] spec の post の job の節を直す。`always()` と書いているところを `!cancelled()` にする。意図は「失敗を越えて走るが、取り消しは尊重する」で、`always()` は run の取り消しの後も投稿しうる。実装は `!cancelled()` になっている。spec は今回は直さず、スナップショットとして残してこのタスクで追う
- [ ] spec の「実装のときに確かめること」を片付ける。解消した項目は解消として spec に書き、残る項目は実物の run で確かめる
  - 解消: `job.workflow_repository` と `job.workflow_sha` は実在する (docs.github.com の原文で確認。GitHub Enterprise Server では使えない)。ただし step の `env:` と `with:` で効くかは上の実物の run で確かめる。docs の原文だけが根拠で、実物の run では確かめていない
  - 解消: environment の鍵は、呼び出し側が名前で渡せば呼ばれた側の job に届く (docs.github.com の reuse-workflows に "You can pass a secret by name even if it only exists in the environment" と "the environment secret takes precedence" とある)。docs の原文だけが根拠で、実物の run では確かめていない
  - 方法は決定、実物での確かめが残る: reusable workflow の中で呼ばれた SHA と同じコードを checkout する方法は `job.workflow_repository` と `job.workflow_sha` を使う形で、どちらかが空なら checkout の前に止める guard がある (tech-briefing/tech_briefing/workflow_def.py の `_guard_step`)。呼び出しの workflow のファイルは `github.workflow_ref` から特定する (workflow_def.py の `_select_job` の env の `TB_CALLER_WORKFLOW_REF` と、tech-briefing/bin/run_select.py の `main` が呼ぶ `jobio.caller_workflow_path`)。効くかどうかは上の「実物で走らせる」のタスクの子項目と同じ
  - 残り: web_fetch が許可したドメインからほかのドメインへのリダイレクトを追うか。実装は `allowed_domains` に `official_sources` をそのまま渡すだけで、件数を絞る処理も上限の扱いも持たない (tech-briefing/tech_briefing/tools.py の `ToolBox.server_tools`)。リダイレクトと件数の上限は docs か実物で確かめる
  - 方法は決定、実物での確かめが残る: `submit_report` は strict の client tool にし (tech-briefing/tech_briefing/tools.py の `_tool`)、server tool と prompt caching を同じ要求に入れ、`output_config` は `effort` だけで `format` を使わない (どちらも tech-briefing/tech_briefing/triage.py の `build_params`)。この形を API が400にしないかは実物の run で確かめる
  - web search と `output_config.format` を同じ要求に入れると400になるかの問いは、format を使わない設計にしたので確かめない
  - 方法は決定、実物での確かめが残る: advisory は OSV の `/v1/vulns/<ID>` を引き (tech-briefing/tech_briefing/tools.py の `ToolBox._advisory`)、リリースノートは GitHub の REST の releases を `Authorization` を付けずに引く (tools.py の `ToolBox._release_notes`)。GHSA / CVE / RUSTSEC を実際に引けるか、認証なしで回せるかは上の実物の契約テストで確かめる
  - 残り: web search の dynamic filtering を持つ最新の版の名前。実装は `web_search_20260209` を使っている (tech-briefing/tech_briefing/constants.py の `WEB_SEARCH_TOOL`) が、それが最新かどうかは docs で確かめる

## 関連

なし
