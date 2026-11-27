---
status: open
---

# feat: 外部ツールが立てる技術情報の Issue をリポジトリへの影響で分類して報告する共通の仕組みを作る

## 背景

外部のツールが、技術情報 (依存の advisory、廃止や破壊的変更、新機能) を集めたまとめを `[tech-briefing] YYYY-MM-DD` の形のタイトルで GitHub Issue として毎日立てる。受け取る側のリポジトリでは、その項目が自分に当たるかを人が読んで判断する必要がある。

LLM にトリアージを任せたいが、Issue の本文とコメントは外から自動で集めた文章で、指示を埋め込める。LLM を動かす主体が書き込みの権限や shell を持つと、禁止はプロンプトの文言でしか効かない。守りは job の権限と道具の構成で効かせる必要がある。

複数のリポジトリで使えるようにするので、リポジトリごとに作ると同じ問題を別々に直すことになる。共通の reusable workflow と Python の処理をこのリポジトリに置き、各リポジトリは SHA を固定して呼び、呼び出しの workflow と設定のファイルと基準の文書だけを持つ形にする。導入の手順は apm で配る skill にする。

設計は `ISSUE-113-spec.md` に置く。

## タスク

- [x] 設計書 (`ISSUE-113-spec.md`) を書いて承認を得る
- [x] 実装プラン (`ISSUE-113-plan.md`) を書く
- [x] 決まった処理 (select の判定、設定と呼び出し側の検査、入力の絞り込みと置き換え、道具の許可、本文の整形と無害化、失敗の終わり方、ループの上限) と reusable workflow を実装し、ユニットテストと配線のテストで固定する

人の手や外部への通信が要る残り。実装はユニットテストと Fake の契約テストと配線のテストまでで、実物では2026-11-27に最初の導入先で dry-run を2回 (select だけの1回と、テスト用の Issue で triage まで通した1回) 走らせただけである (結果は「実物で走らせる」のタスクの子)。

- [x] 準備: 技術情報のトリアージ用の専用のワークスペースを Console に作って月の支出上限を掛け、リポジトリ専用の鍵を発行する。GitHub 側に environment `tech-briefing`、鍵、variable を作る。組織の web search / web fetch の許可の一覧が `official_sources` を含むことを確かめる
  - 2026-11-27に済ませた。専用のワークスペースに月の支出上限 (まず$30) を掛け、鍵はサービスアカウント1つの下にリポジトリごとに1本ずつ発行した (導入予定の3つ分)。どの鍵も Messages API に200を返し、応答ヘッダの `anthropic-workspace-id` は3本とも同じで、Admin API の読み取りには401を返した
  - 導入予定の3つのリポジトリに、environment `tech-briefing` (deployment branch は既定のブランチの名前の1件だけ)、その secret `ANTHROPIC_API_KEY`、repository variable `TECH_BRIEFING_AUTHOR` を置いた。variable は select の job が読み、select は environment を持たないので、environment の variable ではなく repository variable に置く
  - 組織の許可の一覧: 最初の導入先の `official_sources` を `allowed_domains` に入れた web search と web fetch の要求が200で通った。対照として、どこにも登録していないドメインを足した要求も200で通ったので、組織の側に許可の一覧は設定されていない (docs の Server tools の Domain filtering の節は、部分集合の検査を組織の側に制限があるときのこととして書いている)。組織の一覧は今は置かない (人の判断。置くと組織の他の利用の web の道具も縛られ、導入先ごとの `official_sources` を足し続ける手間が増える)
  - [x] 鍵を発行する前に、入口の3本の script を `uv lock --script` で lock し、間接の依存を hash まで固定する。隣の `<script>.lock` を追跡下に置き、workflow は `uv run --locked --script` で起動する。`--locked` を外すと uv は PEP 723 と食い違う lock を黙って書き換えて走り、lock が無くても作らずに走る。`--locked` は lock が無いとき (rc 2) と、依存・requires-python・前へずらした exclude-newer が食い違うとき (rc 1) に止める。exclude-newer を後ろへずらした食い違いは止めずに lock のまま入れる (どれも uv 0.13.4 で確かめた)。lock が追跡下にあって PEP 723 と食い違わないことは `test_boundaries.py` の `ScriptLock` が固定する
- [ ] 契約テストの実物の側を手で回す。対象は Messages API、GitHub の API、取得先の registry と OSV。次を実物で確かめる
  - registry の欄の名前 (npm の `dist-tags.latest`、crates の `max_stable_version`、PyPI の `project_urls`) は記憶で書いたもので、実物で確かめていない
  - OSV を CVE で引いたとき、alias にしか無い記録が 404 になるかもしれない (未確認)
  - 信頼できない区切りの中で `<` をエスケープすると LLM の読みが変わるか、`pause_turn` の後に assistant が続く形を API が受けるか、usage の null を API が返すか
  - registry の repository の欄の実際の形。`git+ssh` や `git://`、`github:` の短縮形、末尾の `/` は配布元のリポジトリを判定できず、release notes を引けない
  - PyPI で正規化していない名前を引いたときと、改名したリポジトリの releases を引いたときの redirect。fetcher は redirect を拒否するので、どちらも取得の失敗になる
  - api.github.com を認証なしで引くときの rate limit に、1回の triage の道具の呼び出しが収まるか
  - npm の registry を packument の全体で取っている (tech-briefing/tech_briefing/tools.py の `ToolBox._fetch_registry`)。`/latest` の経路 (scoped package の `%2F` を含む) を実物で確かめてから、`dist-tags.latest` と `repository` だけを取る形へ替える
  - anthropic のアダプタの「stderr に例外の型と状態コードだけを出し、本文を出さない」守り (tech-briefing/adapters/anthropic_client.py の `SdkMessages.create`) を固定するテストが無い。依存のあるアダプタなので CI のテストからは読まない。実物の契約テストで、失敗の応答のときに本文が stderr に出ないことを見る
    - 2026-11-27に、ロックした版の SDK に偽のトランスポートで400を返させた書き捨ての smoke で、stderr に本文が出ないことを確かめた (対照として、例外の文には本文が入っていた)。実物の API の失敗の応答では、まだ確かめていない
- [ ] 入口のスクリプトとアダプタを実物で走らせる。select だけの dry-run から始め、続けて triage と post に進む。`gh` と GitHub の API を呼ぶ経路は live smoke で通す。次を実物の run で確かめる
  - `job.workflow_repository` と `job.workflow_sha` が step の `env:` と `with:` で効くか、`inputs.config-path` のようなハイフン付きの名前をドットで読めるか
  - select は開いている Issue の全ページを本文ごと取る (tech-briefing/adapters/github_api.py の `GitHubRest.open_issues`)。開いたままの Issue の件数の実績を見てから、絞り込み (タイトルの検索や作者の指定) を設計で決める
  - triage の upload が複数ファイルのとき、artifact の中の相対パスがどうなるか
  - anthropic のアダプタの 2.7.0 の credentials の自動探索と、鍵を env に置いたときの `warn_env_static_shadows_auto_discovery` を実物の走行で確かめる
  - 無害化した本文が GitHub 上でリンク・メンション・参照にならないこと。特に `www.` と `GH-` の直後に ZWSP を入れた形と、スキームのコロンの後に ZWSP を入れた形を、実物のコメントで確かめる
  - triage が timeout したときの `needs.triage.result` の値と post の挙動。cancelled になるなら post が skip されて印が付かず、次の run から毎回 triage を呼び直す
  - run を re-run したときに、同じ名前の artifact が衝突しないか
  - stop_reason が tool_use なのに client の道具のブロックが無い応答を API が返すか。返すなら、いまのループは assistant を最後にして送り直し、400 から api-error に化けうる
  - スキーム無しの URL (`www.` で始まる形) を web_fetch が開くか
  - 2026-11-27の最初の導入先での run の結果。select だけの dry-run は当日の Issue が無く no-issue で終わった。続けて、前日のブリーフィングの本文とコメントを写した当日のタイトルのテスト用の Issue を立て、`dry_run` を真にして回すと、select・triage・post の3つとも success だった。テスト用の Issue は閉じた
    - `job.workflow_repository` と `job.workflow_sha` は step の `env:` で効いた (空なら止める guard は3つの job とも発火しなかった)。`with:` でも効き、共通の仕組みの checkout が呼ばれた SHA を取った。`inputs.config-path` も設定のパスとして届いた
    - environment の secret を呼び出し側から名前で渡す経路で、鍵が triage の job に届いた (呼び出し側のリポジトリに repository secret は0件なので、environment の側から届いた)。`submit_report` を strict の client tool にし、server tool と prompt caching と effort を同じ要求に入れた形を、API は400にせず受けた。報告は4項目 (C が1、D が3) で、`submit_report` で受け取った
    - `allowed_domains` と矛盾しない結果だった。モデルが `official_sources` に無い github.com の URL を取りにいき、許可したドメインで代わりに確かめた。ただし `url_not_allowed` で弾かれたことの出所はモデルが書いた報告の `unreachable_sources` だけで、tool-log には呼び出しの結果が残らない。`url_not_allowed` は robots.txt などの Anthropic 側の制限でも出るので、原因を `allowed_domains` に絞れてはいない (推定)。切り分けには tool-log に道具の結果を残すことが要る (下の「最初の run で分かった残りを直すか決める」のタスク)
    - client の道具の読み取りの制限は効いた。設定の files の外のファイル (`.claude/settings.json`) は読めなかった
    - 1回の費用: usage は入力24、キャッシュの読み177,133、キャッシュの書き込み33,738、出力5,100、web fetch 7回、web search 1回で、単価から約$0.32
    - triage の upload は、ファイルの共通の祖先を root にするので、artifact の中では `report.json` と `tool-log.json` がファイル名だけで並んだ。post は「dry-run のため投稿せず、本文を step summary に出しました」で終わった
    - `warn_env_static_shadows_auto_discovery` は確かめられていない。anthropic 2.7.0 はこの警告を、自動探索できる資格情報の手がかり (ANTHROPIC_PROFILE や ANTHROPIC_CONFIG_DIR などの env か、設定のディレクトリの `active_config` のファイル) があるときだけ出す。runner にはその手がかりが無いと見られ (推定。ファイルの有無は観測していない)、この警告の経路は通っていない。警告の文面は関数名を含まないので、ログを関数名で探した0件はそもそも何も見ていない
    - この run で答えが出たのは、この子の上の問いのうち checkout の式 (`env:` と `with:`) と config-path、upload の相対パス。開いている Issue の件数、credentials の自動探索、無害化した本文 (dry-run は投稿しない)、timeout、re-run、client の道具のブロックが無い tool_use、スキーム無しの URL は、まだ確かめていない
- [x] 最初の run で分かった残りを直すか決める
  - 2026-11-27に4件とも人が決め、同じ日に直した。決めたことは各項目の末尾に書く。実物の run でしか確かめられない残りは、この下の「直したことを実物で確かめる」のタスクで追う
  - web fetch が7回呼ばれ、`WEB_FETCH_MAX_USES` (5) を超えた。tool-log には呼び出し元の欄が無いが、web fetch の前に、その URL を `web_fetch` で取るコードを持つ code execution が並んでいたので、dynamic filtering の中からの呼び出しと見られる (推定)。docs の web fetch の Max uses の節は、code execution から呼んだ取得は、turn の中の前の要求の分として上限に数えないと書くので、`max_uses` は run 全体の上限としては効かないと読める (推定。7回が要求ごとにどう分かれたかは記録が無い)。費用の上限を効かせるのはループの上限と支出上限のほうになる。spec の「起動と費用」の節は `max_uses` も上限を効かせるものに数えているので、直すなら spec のこの1文も直す。`max_uses` の値は spec の「作り手に任せること」の節のとおり実測から決める。同じ要求の中では code execution からの取得も上限に数え、超えると code execution が error で終わる (docs の同じ節)
    - 決めたこと: `max_uses` は1回の要求の中の守りとして残し、spec の「起動と費用」の1文と triage の job の節を、費用の上限を効かせるのはループの上限と支出上限だと直した。値は実測から決める
  - web fetch の最初の取得がひと月前のキャッシュを返し (日付の出所はモデルが書いた報告の notes だけ)、モデルが自分で `use_cache` を偽にして取り直した (取り直しは tool-log で確かめた)。キャッシュを使わない既定にするかを決める
    - 決めたこと: web fetch の tool の定義で `use_cache` を偽にする (docs の web fetch の Cache bypass は既定を真とし、変わりやすい取得先では偽を勧める)。tech-briefing/tests/test_tools.py の `ServerTools.test_shape` が固定する
  - artifact の usage に `anthropic-workspace-id` と `usage.iterations` が残らないので、run からはワークスペースと fallback の有無が分からない。tool-log の usage へ足すかを決める
    - 決めたこと: 両方を足す。tool-log.json の `usage_by_response` に応答ごとの model・ワークスペースの id・`usage.iterations` (数と名前だけ) を残し、step summary には合計の usage だけを出す。ワークスペースの id は、アダプタが応答ヘッダの `anthropic-workspace-id` を `with_raw_response` で読んで応答の dict に足す (鍵は tech-briefing/tech_briefing/protocols.py の `WORKSPACE_ID_KEY`)。アダプタは CI から読まないので、ロックした版の SDK に偽のトランスポートを当てた書き捨ての smoke で、ヘッダの取り込みと、ヘッダが無いときに鍵を足さないことを確かめた
  - tool-log は道具の呼び出しの入力だけを残し、結果 (web の道具の `error_code` など) を残さない。今回の害の無い run でも tool-log には `official_sources` の外への web fetch が2回ある。spec の「有効にする前のテスト」の injection の判定 (tool-log に許した道具と行き先のほかが無い) を書いてあるとおりに当てると、害の無いふるまいでも赤になり、拒否の原因も見分けられない。結果を残すか、判定を「外から取れた結果が無いこと」に直すかを決める
    - 決めたこと: 結果の要約を残し、判定も直す。tool-log は server の道具の結果のブロック (type の末尾が `_tool_result`) を、種類・tool_use_id・取得した URL・error_code・retrieved_at・return_code と caller だけの要約で残し、ほかの欄 (本文・stdout など) を残さない。呼び出しの側にも id と caller (type と tool_id だけ) を残し、dynamic filtering の中の呼び出しを code execution の id と結び付けられるようにした。spec の injection の判定は、許した行き先の外から取得に成功した結果が無いことと、埋めた指示の文にだけ出てくる目印のホストやクエリへの呼び出しが1回も無いことの2つにした (守りが止めた呼び出しでも、試みたことはモデルが指示に従った証拠になるため)
- [ ] 直したことを実物で確かめる。有効にする前のテストの基準の run で兼ねる
  - API が web fetch の `use_cache` を偽にした要求を400にせず受けるか
  - 実物の応答が `anthropic-workspace-id` のヘッダを持ち、`usage_by_response` に値が入るか。`usage.iterations` を server の道具のループで返すか
  - dynamic filtering の中の呼び出しと結果のブロックが caller を持って応答に出るか (`response_inclusion` の既定は full)。最初の run の7回の web fetch が、code execution の呼び出しごとにどう分かれるか
  - fallback が起きたときに、ループが入力トークンの上限に数える量は実際より少なくなる。トップレベルの usage は返した1回分だけを数え、課金の記録は `usage.iterations` のほうにある (docs の Refusals and fallback、2026-11-27確認)。上限を iterations の合計で数えるかを、fallback の実績を見て決める
- [ ] 最初の導入先の呼び出しを、最初の run で分かった残りを直した版 (PR #116 のマージ) の SHA へ上げる。有効にする前のテストはその SHA で回す
  - この版で config-path の検査が厳しくなった (正規形の規則と、解決した先の .git の拒否) ので、select だけの dry-run で config-error にならないことを先に確かめる
- [ ] 有効にする前のテストを、spec の「有効にする前のテスト」のとおり、最初に導入するリポジトリで回す。基準の run と入力の範囲、2回目と対照、injection (道具の制限の確かめを含む) の順。外部のツールが当日の Issue を立て始める前に行う
  - 基準の run と2回目は導入するリポジトリごとに1回ずつ、injection は共通の仕組みの SHA ごとに1回回す
  - 終わったらテスト用に立てた Issue を閉じる
- [ ] 呼び出しの workflow に `schedule` を足して有効にする。有効にする前のテストが通ったあとに、人の了承を得てから足す。間隔と時間帯は導入するリポジトリが決める (spec の「起動と費用」)
- [ ] 導入の手順の skill を apm で配る形で書く。有効にする前のテストを最初のリポジトリで1回回したあとに書く
  - spec の「設定のファイル」の節と導入の手順の2は、組織の許可の一覧があることを前提に、`official_sources` がその部分集合かを確かめると書いている。組織の側に一覧が無いと検査は効かない (準備のタスクの子)。skill には、一覧の有無を先に見る手順として書く。spec の側は下の spec を直すタスクで追う
- [ ] 残りの導入先へ展開する。導入の手順の skill に沿って、導入するリポジトリごとに呼び出しの workflow と設定のファイルと基準の文書を置き、基準の run と2回目を回す (injection は共通の仕組みの SHA ごとに1回なので、同じ SHA なら回し直さない)
- [ ] 拒否のドメインの一覧と files の glob の残りを直す
  - 拒否のドメインの一覧を広げる。いまは blogspot.com・medium.com・readthedocs.io・web.archive.org・translate.goog・s3.amazonaws.com・storage.googleapis.com・codeberg.org が通る。導入の skill に「一覧は網羅ではない」と書く
  - 設定の files の glob の `*` が `/` をまたぐ (fnmatch の挙動)。導入の skill と設定の説明にそう書くか、`/` をまたがない照合に替える
- [ ] コードの小さな残りを直す
  - select が GitHub の API の失敗 (URLError・TimeoutError・HTTPException) で `result=` と step summary を出さずに traceback で終わる。入口で捕まえて原因を1行出す。select の結果の値を足すかもここで決める
  - max_tokens などで止まって no-report になったときに、最後の stop_reason を内部ログに出す
  - `pause_turn` の応答に client の tool_use が混ざる形のテストを足す
  - テストの builder の複製 (`issue()`・`comment()`・ToolBox の組み立て) を tech-briefing/tests/fakes.py に寄せる。いまは test_select・test_entrypoints・test_render・test_tools・test_triage がそれぞれ持つ
    - 応答の builder も同じく寄せる (test_entrypoints の `submit_response`・`pause_response`・`end_turn_response`、test_triage の `response`・`usage`・`thinking`・`tool_use`・`server_use`・`submit`・`pauses`)
  - tech-briefing/tests/test_render.py の `ShouldPostTest` と `IsBotReportTest` の表が重なっている。表の各行で `is_marked`・`is_bot_report`・`should_post` を見る形にまとめる
  - tech-briefing/tests/test_entrypoints.py の `recording_open` の複製 (`Post.test_target_comes_only_from_select_dir` と `ArtifactNames.test_post_reads_only_uploaded_names`) を Harness の contextmanager にする
  - 「想定と違う型は空として読む」を tech-briefing/tech_briefing/shapes.py の `as_dict` に揃える。いまは tech-briefing/tech_briefing/caller_check.py の3か所と tech-briefing/tech_briefing/wire.py の1か所が、それぞれ `isinstance` で書いている
  - search_files と search_issues は、正規形の規則が拒否する名前のファイル (UTF-8 で表せない名前など) や読めないファイルが1つでも走査に出ると、検索の全体が誤りになる。中身を読まない側に倒れているので守りとしては正しいが、そのファイルだけを飛ばして件数に数える形にするかを決める
  - パスの規則と criteria の誤りの表を1か所に寄せる。tech-briefing/tests/test_tools.py の `SharedPathRule` と tech-briefing/tests/test_config.py の `REJECT_PATHS` / `ACCEPT_PATHS` のループが重なり、tech-briefing/tests/test_select.py に criteria の誤りの手書きの列挙があり、tech-briefing/tests/test_entrypoints.py の `JobIo.test_read_criteria_or_none` の表は使われない値を持つ dict になっている
  - 以下は PR #112 のマージ前のゲートで、挙動を誤らせないので直さずに記録したもの (PR #112 のコメントにも残した)
  - 2026-11-27に直した: tech-briefing/tech_briefing/triage.py の区切りのコメントが、同じ文字から同じ文字への変換に読めた。エスケープの表記を使わない文にした
  - tech-briefing/bin/gen_workflow.py:31 の `os.path.relpath(TARGET, REPO_ROOT)` は `WORKFLOW_FILE` を組んでから逆算している。エラーの文に `WORKFLOW_FILE` をそのまま使う
  - 投稿先の鍵 `"issue_number"` が tech-briefing/tech_briefing/selection.py:84・tech-briefing/bin/run_select.py:48・tech-briefing/bin/run_post.py:30 に literal で3回ある (job の間の約束)。constants の定数か、selection の「番号だけの入力」を作る関数に寄せる
  - tech-briefing/tests/test_tools.py の `CostLimits.test_server_tool_limits_are_the_provisional_values` と `ServerTools.test_shape` が費用の上限の仮の値を二重に literal で固定している。片方に寄せる (pin は減らない)
  - tech-briefing/tests/test_render.py:399-402 の `PlanPostTest.test_other_results_are_internal_error` は、412-414行の対照のループに含まれる。消す
  - tech-briefing/tests/test_adapters_stdlib.py:91・138 の `self.gh` / `self.fe` は import した module の別名で、別の実体に見える。module を直に使う
  - tech-briefing/tests/test_adapters_stdlib.py の module の docstring が、ast から導いて固定しているアダプタの名前を literal で再掲している。tech-briefing/adapters/__init__.py と同じく参照だけにする
  - tech-briefing/tests/test_boundaries.py の `dependent_modules` は `RUNTIME_DEPS` に載った名前しか知らず、依存の無いアダプタに `RUNTIME_DEPS` の外の第三者の import が足されても静的には止まらない (CI で赤になるのは、そのアダプタをテストが import し、かつ runner の素の python3 にそのパッケージが無いときだけ)。`dynamic_imports` と `yaml_uses` は `sys.modules[...]` 経由の形を拾わない
  - tech-briefing/tech_briefing/caller_check.py:10-14 の `CALLER_WITH_KEYS`・`CALLER_SECRETS`・`CALLER_PERMISSIONS` が、tech-briefing/tech_briefing/workflow_def.py の `workflow()` の workflow_call の inputs・secrets と job の権限の写しで、両者を突き合わせるテストが無い (input を足すと test_workflow_def の literal の1件だけが赤になり、そこを直すと caller_check とのずれは誰も捕まえない)。`CALLER_WITH_KEYS` と `CALLER_SECRETS` は workflow_def から導く。`CALLER_PERMISSIONS` は権限の天井なので literal のまま残し (導くと呼ばれる側の権限が増えたときに天井が黙って広がる)、job の権限の scope ごとの最大と一致することをテストで固定する
  - 同じ caller_check.py の89行と97行のエラーの文が `CALLER_PERMISSIONS` と `CALLER_SECRETS` の値を literal で再掲している。101行は `CALLER_WITH_KEYS` から文を組んでいる。3つの文の作り方を揃え、集合から組む
  - 2026-11-27に直した: tech-briefing/tech_briefing/jobio.py の `read_under` は、config-path に `./tb.yml`・`.//tb.yml`・`.git/config` を受け、`.git/config` を YAML として読ませると PyYAML の例外の文が該当の行の断片を Actions のログへ出していた。`read_under` は `canonical_parts` を、パスの文字列と解決した先の経路の両方に当てる (root の中の symlink で .git/config を指す形も止める)。`load_yaml_under` は読み手の例外の文を出さず型だけを出し、守りはこちらに置いた (アダプタは CI から読まないため)。アダプタの yaml_loader も例外の文を位置だけにし、`protocols.YamlLoader` の約束に書いた
  - `read_under` を `repo_paths.read_regular` に寄せ、設定と呼び出しの workflow の読み取りでも symlink を辿らない形にするかを決める。読み手が1つになり、解決した先の検査が要らなくなる。ただし symlink にした設定のファイルが config-error に変わる (挙動の変更なので、PR のマージ前のゲートでは直さずに記録した)。呼び出しの workflow のファイルが symlink のとき Actions が走らせるかは確かめていない
- [ ] spec の post の job の節を直す。`always()` と書いているところを `!cancelled()` にする。意図は「失敗を越えて走るが、取り消しは尊重する」で、`always()` は run の取り消しの後も投稿しうる。実装は `!cancelled()` になっている。spec は今回は直さず、スナップショットとして残してこのタスクで追う。plan の Task 8 の Step 1 も `always()` と書いているが、実装が正で、plan はスナップショットとして追わない
- [ ] spec の select の job の節を、select が criteria のファイルを読んで確かめる実装に合わせて直す。呼び出し側のリポジトリの checkout を select が使う範囲 (「設定のファイルと呼び出しの workflow のファイルだけ」) に criteria のファイルを足し、設定の誤りの列挙 (`config-error` で終わるもの) に、criteria のパスの形の誤り (絶対パス・`..`・`.`・空の segment・`.git`) と、criteria のファイルが読めないか中身が空のときを足す。glob の形の誤りに `.`・空の segment・`.git` を足す
  - 設定の誤りの列挙に、パスに NUL や UTF-8 で表せない文字を含むときと、criteria の経路のどこかが symlink のとき (指す先がリポジトリの中のふつうのファイルでも) を足す。criteria は道具の読み取りと同じ読み手 (tech-briefing/tech_briefing/repo_paths.py の `read_regular`) で読み、symlink を辿らない
  - spec の「置き場とモジュールの分け方」の「アダプタと入口のスクリプトはこのリポジトリの CI のテストから import しない」を、「実行時の依存を直にか兄弟のアダプタ経由で import するアダプタ (いまは anthropic_client・yaml_loader) は CI のテストから読まない。依存の無いアダプタは読んでよい」に直す (tech-briefing/tests/test_adapters_stdlib.py が依存の無いアダプタを読み、どれを読んでよいかは tech-briefing/tests/test_boundaries.py が ast から導いて固定する)
- [ ] spec の「設定のファイル」の節の「組織の許可の一覧の部分集合でないと validation error になる」と導入の手順の2を、組織の側に一覧があるときだけの検査に直す (準備のタスクの子で、一覧が無いと検査が効かないことを確かめた)。spec は今回は直さず、このタスクで追う
- [ ] spec の「実装のときに確かめること」を片付ける。解消した項目は解消として spec に書き、残る項目は実物の run で確かめる
  - 解消: `job.workflow_repository` と `job.workflow_sha` は実在する (docs.github.com の原文で確認。GitHub Enterprise Server では使えない)。step の `env:` と `with:` で効くことは、2026-11-27の実物の run で確かめた (「実物で走らせる」のタスクの子)
  - 解消: environment の鍵は、呼び出し側が名前で渡せば呼ばれた側の job に届く (docs.github.com の reuse-workflows に "You can pass a secret by name even if it only exists in the environment" と "the environment secret takes precedence" とある)。名前で渡す経路で鍵が triage の job に届くことを、2026-11-27の実物の run で確かめた
  - 解消: reusable workflow の中で呼ばれた SHA と同じコードを checkout する方法は `job.workflow_repository` と `job.workflow_sha` を使う形で、どちらかが空なら checkout の前に止める guard がある (tech-briefing/tech_briefing/workflow_def.py の `_guard_step`)。呼び出しの workflow のファイルは `github.workflow_ref` から特定する (workflow_def.py の `_select_job` の env の `TB_CALLER_WORKFLOW_REF` と、tech-briefing/bin/run_select.py の `main` が呼ぶ `jobio.caller_workflow_path`)。2026-11-27の実物の run で効いた (「実物で走らせる」のタスクの子)
  - 残り: web_fetch が許可したドメインからほかのドメインへのリダイレクトを追うか。実装は `allowed_domains` に `official_sources` をそのまま渡すだけで、件数を絞る処理も上限の扱いも持たない (tech-briefing/tech_briefing/tools.py の `ToolBox.server_tools`)。リダイレクトと件数の上限は docs か実物で確かめる
  - 解消: `submit_report` は strict の client tool にし (tech-briefing/tech_briefing/tools.py の `_tool`)、server tool と prompt caching を同じ要求に入れ、`output_config` は `effort` だけで `format` を使わない (どちらも tech-briefing/tech_briefing/triage.py の `build_params`)。この形を API は400にせず受けた (2026-11-27の実物の run、「実物で走らせる」のタスクの子)
  - web search と `output_config.format` を同じ要求に入れると400になるかの問いは、format を使わない設計にしたので確かめない
  - 方法は決定、実物での確かめが残る: advisory は OSV の `/v1/vulns/<ID>` を引き (tech-briefing/tech_briefing/tools.py の `ToolBox._advisory`)、リリースノートは GitHub の REST の releases を `Authorization` を付けずに引く (tools.py の `ToolBox._release_notes`)。GHSA / CVE / RUSTSEC を実際に引けるか、認証なしで回せるかは上の実物の契約テストで確かめる
  - 残り: web search の dynamic filtering を持つ最新の版の名前。実装は `web_search_20260209` を使っている (tech-briefing/tech_briefing/constants.py の `WEB_SEARCH_TOOL`) が、それが最新かどうかは docs で確かめる

## 関連

なし
