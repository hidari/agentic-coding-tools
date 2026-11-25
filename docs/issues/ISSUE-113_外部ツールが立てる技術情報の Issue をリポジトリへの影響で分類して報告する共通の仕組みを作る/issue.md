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
- [ ] 契約テストの実物の側を手で回す。対象は Messages API、GitHub の API、取得先の registry と OSV。次を実物で確かめる
  - registry の欄の名前 (npm の `dist-tags.latest`、crates の `max_stable_version`、PyPI の `project_urls`) は記憶で書いたもので、実物で確かめていない
  - OSV を CVE で引いたとき、alias にしか無い記録が 404 になるかもしれない (未確認)
  - 信頼できない区切りの中で `<` をエスケープすると LLM の読みが変わるか、`pause_turn` の後に assistant が続く形を API が受けるか、usage の null を API が返すか
- [ ] 入口のスクリプトとアダプタを実物で走らせる。select だけの dry-run から始め、続けて triage と post に進む。`gh` と GitHub の API を呼ぶ経路は live smoke で通す。次を実物の run で確かめる
  - `job.workflow_repository` と `job.workflow_sha` が step の `env:` と `with:` で効くか、`inputs.config-path` のようなハイフン付きの名前をドットで読めるか
  - triage の upload が複数ファイルのとき、artifact の中の相対パスがどうなるか
  - anthropic のアダプタは一度も実行していない。2.7.0 の credentials の自動探索と、鍵を env に置いたときの `warn_env_static_shadows_auto_discovery` を実物の走行で確かめる
- [ ] 有効にする前のテストを、spec の「有効にする前のテスト」のとおり、最初に導入するリポジトリで回す。基準の run と入力の範囲、2回目と対照、injection (道具の制限の確かめを含む) の順。ブリーフィングが投稿を始める前に行う
- [ ] 導入の手順の skill を apm で配る形で書く。有効にする前のテストを最初のリポジトリで1回回したあとに書く
- [ ] spec の post の job の節を直す。`always()` と書いているところを `!cancelled()` にする。意図は「失敗を越えて走るが、取り消しは尊重する」で、`always()` は run の取り消しの後も投稿しうる。実装は `!cancelled()` になっている。spec は今回は直さず、スナップショットとして残してこのタスクで追う
- [ ] spec の「実装のときに確かめること」を片付ける。解消した項目は解消として spec に書き、残る項目は実物の run で確かめる
  - 解消: `job.workflow_repository` と `job.workflow_sha` は実在する (docs.github.com の原文で確認。GitHub Enterprise Server では使えない)。ただし step の `env:` と `with:` で効くかは上の実物の run で確かめる
  - 解消: environment の鍵は、呼び出し側が名前で渡せば呼ばれた側の job に届く (docs.github.com の reuse-workflows に "You can pass a secret by name even if it only exists in the environment" と "the environment secret takes precedence" とある)
  - 方法は決定、実物での確かめが残る: reusable workflow の中で呼ばれた SHA と同じコードを checkout する方法は `job.workflow_repository` と `job.workflow_sha` を使う形で、どちらかが空なら checkout の前に止める guard がある (tech-briefing/tech_briefing/workflow_def.py:64-82)。呼び出しの workflow のファイルは `github.workflow_ref` から特定する (workflow_def.py:140-142、tech-briefing/bin/run_select.py:29)。効くかどうかは上の「実物で走らせる」のタスクの子項目と同じ
  - 残り: web_fetch が許可したドメインからほかのドメインへのリダイレクトを追うか。実装は `allowed_domains` に `official_sources` をそのまま渡すだけで、件数を絞る処理も上限の扱いも持たない (tech-briefing/tech_briefing/tools.py:213、tools.py:222)。リダイレクトと件数の上限は docs か実物で確かめる
  - 方法は決定、実物での確かめが残る: `submit_report` は strict の client tool にし (tech-briefing/tech_briefing/tools.py:95)、server tool と prompt caching を同じ要求に入れ (tech-briefing/tech_briefing/triage.py:107-112)、`output_config` は `effort` だけで `format` を使わない (triage.py:101)。この形を API が400にしないかは実物の run で確かめる
  - 方法は決定、実物での確かめが残る: advisory は OSV の `/v1/vulns/<ID>` を引き (tech-briefing/tech_briefing/tools.py:413)、リリースノートは GitHub の REST の releases を `Authorization` を付けずに引く (tools.py:387)。GHSA / CVE / RUSTSEC を実際に引けるか、認証なしで回せるかは上の実物の契約テストで確かめる
  - 残り: web search の dynamic filtering を持つ最新の版の名前。実装は `web_search_20260209` を使っている (tech-briefing/tech_briefing/constants.py:21) が、それが最新かどうかは docs で確かめる

## 関連

なし
