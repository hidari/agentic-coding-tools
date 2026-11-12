---
status: open
---

# fix: web-monkey-qa のハーネスが資格情報を対象の外へ出し並列の explorer が干渉する

## 背景

出所は別のリポジトリのセッションからの依頼 (2026-11-12)。HTTP Basic 認証の背後にある staging に対して、3 つの section を 1 つの wave で並列に dispatch し、explorer 3 体の報告から次の 4 つを観測した。どれも報告者の実測で、このリポジトリではまだ再現していない。

1. `agent-browser` の `set credentials` が、Basic 認証の Authorization を対象の origin 以外にも付けようとする。対象になったのは、オブジェクトストレージの presigned URL、CDN、第三者の解析用スクリプトのホスト。CORS の preflight の `Access-Control-Request-Headers` に `authorization` が載り、ストレージは 403 を返した (authorization が無い対照は 204)
   - アップロードのように CORS に頼る機能は、このハーネスでは確かめられない。アプリが偽の失敗を表示する
   - 第三者のホストへ資格情報が送られた可能性がある。送信そのものは確かめていない (報告者の推測)
2. explorer が `agent-browser network requests --json` を加工せずに出すと、リクエストの Authorization ヘッダ (Basic の Base64) が subagent のコンテキストに載る。3 体中 3 体が 1 回ずつ踏んだ。ファイルには書いていない
3. 並列の explorer が同じ scratchpad を共有し、同じ名前の検知スクリプトを上書きしあって、別の explorer のセッションの console と network のバッファを消した
4. `agent-browser` (報告では 0.39.1) のブラウザが、`press Escape` の直後などに作り直され、cookie と Basic 認証が消える。7 回中 3 回で、再現の条件は取れていない

### 今の手順書が前提にしていること (2026-11-12 に確かめた)

- `agents/monkey-explorer-agent.md` は、`HTTP_AUTH_USERNAME_ENV` / `HTTP_AUTH_PASSWORD_ENV` を「origin 全体の Basic 認証」として扱い、最初の `open` の前に `set credentials` を打つよう指示している。資格情報を origin で絞る手順は無い
- 値をコンテキストへ入れない指示は環境変数の展開についてだけある。`network requests --json` の出力を絞る指示は無く、分類表の http-4xx / http-5xx も `network requests --json` を検知の手段にしている
- ブラウザのセッションは `--session monkey-<SECTION>` で分けているが、scratch の置き場を explorer ごとに分ける指示は無い

### 報告者の提案

- monkey-explorer-agent の手順に、network のダンプは jq などで status / method / URL に絞ってから出し、ヘッダを出さないことを明記する (2)
- dispatcher が explorer ごとに専用の scratch のサブディレクトリを渡す (3)
- Basic 認証を origin で絞って付ける方法に変える。できないなら、CORS に頼る機能はカバーできないことと、第三者のホストへ送られうることを profile の文書に書く (1)

## タスク

- [ ] (1) `agent-browser` に資格情報を origin で絞って付ける手段があるかを、実物で確かめる。対象の origin へのリクエストと、別の origin へのリクエストの両方を観測する (preflight の要求ヘッダと、実際に送られたヘッダを分けて見る)
- [ ] (1) 絞れるならその手段へ替える。絞れないなら、CORS に頼る機能はカバーできないことと、第三者のホストへ送られうることを、profile の schema と文書に書く
- [ ] (2) explorer の手順で、network の出力をヘッダを含まない形に絞ってから読む。検知の分類表が使う項目 (status / method / URL) だけで足りることを確かめる
- [ ] (3) dispatcher が explorer ごとに専用の scratch の置き場を渡し、explorer はそこだけに書く
- [ ] (4) ブラウザが作り直される条件を切り分ける。ハーネスの側で検知できるなら、検知したら section を止める (cookie と認証が消えたまま探索を続けると、全ページが 4xx の所見になる)

## 関連

- ISSUE-40: Phase 1 のログイン手順 (`AUTH_RECIPE`) と submit 禁止の衝突。同じ Phase 1 の手順を触る
- ISSUE-34: profile の対象の allowlist が無い。(1) の「どの origin へ資格情報を付けてよいか」と同じ材料が要る
