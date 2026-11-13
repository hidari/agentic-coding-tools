---
status: closed
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

### 確かめたこと (2026-11-13、agent-browser 0.39.1)

ポートだけ違うローカルの 2 origin を立てた。A は Basic 認証が要り、ページから B へ CORS の fetch (PUT)、script、img を出す。B は preflight で `authorization` を要求されたら 403 を返す。届いたヘッダはサーバの側で記録した。資格情報はダミーの値を使った。

- (1) `set credentials` は、B の script と img にも正しい資格情報の Authorization を実際に送った。報告者の推測 (第三者のホストへ送られた可能性) は、送信まで確かめられた。CORS の preflight の要求ヘッダには `authorization` が載って 403 になり、ページの fetch は失敗した (資格情報なしの対照は A が 401 を返すだけで、B へは何も届かない)
- (1) `open <url> --headers '{"Authorization":"Basic ..."}'` は、scheme・host・port が完全に一致する origin にだけ付いた。B にも、`sub.localhost` にも、ホスト名違いの `127.0.0.1` にも、A から B への 302 の先にも付かなかった。同じタブでのクリックや、`--headers` を付けない後の `open` でも A には付いたままだった。CORS の fetch は preflight 204、PUT 200 で通った
- (1) ページが `target="_blank"` で開いたタブには `--headers` が届かなかった。そのタブの document は Authorization 無しで 401 になった。このとき network の射影 (元のセッションのバッファ) にその 401 は入らず、引数なしの `tab close` は active になった新しいタブを閉じた。agent-browser のヘルプは `tab new` と `click --new-tab` で開いたタブがヘッダを引き継ぐと書くが、そちらは確かめていない
- (1) 間違った資格情報のときも `open` は rc 1 で、射影には `Document` の 401 が出た
- (1) `--headers` に渡す JSON が壊れていると、`open` は rc 1 で、エラー文に入力 (base64 の値) をそのまま出した。正しい JSON のときは、素の出力にも `--json` の出力にも出なかった
- (2) `network requests --json` は、各リクエストの `headers` と `responseHeaders` を丸ごと持つ。`set credentials` のときは、B 宛ての 3 件を含む 6 件に Authorization の値がそのまま載った。`{status, method, url, resourceType}` へ射影すると 0 件になり、検知の分類表に要る項目はこの 4 つで足りる
- (4) 作り直しは再現しなかった。条件の切り分けは ISSUE-110 へ移した
- 直した手順書で live smoke を回した。explorer 役の agent 2 体が手順書を読み、同じ 2 origin に 2 section を並列で探索した。サーバに届いた 64 件のうち、対象の origin の 30 件は全部 Authorization 付きで、別 origin の 32 件と、ホスト名違いの 2 件は全部付いていなかった。2 体の出力に base64 の値とパスワードの平文は 0 件 (対照に使った資格情報のファイルでは 1 件)。作業用のディレクトリは 2 体とも空のまま終わった
- レビューの指摘を当てた後の手順書で、smoke をもう一度回した。サーバに届いた 64 件の内訳は 1 回目と同じで (対象の origin の 30 件だけが Authorization 付き)、2 体の出力と作業用のディレクトリに資格情報は 0 件だった。2 体とも、400 以上だけに絞った射影が空のとき「見たうえで無かった」と言えないことを指摘したので、射影はバッファの件数 (`seen`) を対照として一緒に出す形にした。正常の読み込みで `seen` 7、空にした直後で 0、資格情報なしで 1 と `Document` の 401 を返すことを確かめた
- mobile の section の手順 (認証の `open` の後に `set device` を打ち、`open` のたびに打ち直す) でも、遷移とクリックの先の A へのリクエストは全部 Authorization 付きだった

### 報告者の提案

- monkey-explorer-agent の手順に、network のダンプは jq などで status / method / URL に絞ってから出し、ヘッダを出さないことを明記する (2)
- dispatcher が explorer ごとに専用の scratch のサブディレクトリを渡す (3)
- Basic 認証を origin で絞って付ける方法に変える。できないなら、CORS に頼る機能はカバーできないことと、第三者のホストへ送られうることを profile の文書に書く (1)

## タスク

- [x] (1) `agent-browser` に資格情報を origin で絞って付ける手段があるかを、実物で確かめる。対象の origin へのリクエストと、別の origin へのリクエストの両方を観測する (preflight の要求ヘッダと、実際に送られたヘッダを分けて見る)
- [x] (1) 絞れるならその手段へ替える。絞れないなら、CORS に頼る機能はカバーできないことと、第三者のホストへ送られうることを、profile の schema と文書に書く
- [x] (2) explorer の手順で、network の出力をヘッダを含まない形に絞ってから読む。検知の分類表が使う項目 (status / method / URL) だけで足りることを確かめる
- [x] (3) dispatcher が explorer ごとに専用の scratch の置き場を渡し、explorer はそこだけに書く
- [x] (4) ブラウザが作り直されたことをハーネスの側で検知し、section を止める (Basic 認証の section で、`BASE_URL` の `Document` が 401 になったら打ち切る)
- [x] (4) 作り直しの条件の切り分けと、Basic 認証の無い section での検知を ISSUE-110 へ移す

## 関連

- ISSUE-40: Phase 1 のログイン手順 (`AUTH_RECIPE`) と submit 禁止の衝突。同じ Phase 1 の手順を触る
- ISSUE-34: profile の対象の allowlist が無い。(1) の「どの origin へ資格情報を付けてよいか」と同じ材料が要る
- ISSUE-110: (4) から切り出した、作り直しの条件と Basic 認証の無い section での検知
- ISSUE-111: この直しの live smoke で見つかった、手順書が決めていない検知と探索の判断
- ISSUE-112: この直しのレビューから出た、資格情報と network の読み方を同梱のスクリプトへ寄せる提案
