# HTTP(S) レイヤ: mitmproxy

TLS を復号して中身を読めるので、HTTP のデバッグはパケットキャプチャより先にこれを使う。

```bash
brew install mitmproxy
```

## モードの選択

**どちらが第一候補かは対象で変わる。** `SKILL.md` の「対象の種別を先に決める」で
判定してからここへ来る。

- regular (既定)
  - 第一候補になる対象: CLI / 自作サーバ / スクリプト。環境変数でプロキシを通せるもの
  - 権限: 不要
- `--mode local`
  - 第一候補になる対象: GUI アプリ。環境変数を見ないもの
  - 権限: 初回に人間の承認が必要

macOS の GUI アプリは `HTTPS_PROXY` を読まない (CFNetwork 経由でシステムの
ネットワーク設定を見る)。GUI アプリに対して環境変数で通そうとすると、
エラーも出ずにトラフィックが 0 件になる。

## 対象別の手当て

- CLI / 自作サーバ
  - プロキシ: 環境変数 (regular)
  - 証明書: 環境変数で指定 (下記「証明書を信頼させる」)
  - SSLKEYLOGFILE: ランタイム次第
- Safari
  - プロキシ: `--mode local:Safari`
  - 証明書: システムキーチェーン
  - SSLKEYLOGFILE: 非対応
- Firefox
  - プロキシ: `--mode local:firefox` または Firefox 自身のプロキシ設定
  - 証明書: **独自ストア**。下記の手当てが必要
  - SSLKEYLOGFILE: 対応
- Electron アプリ (Slack / Discord / VS Code など)
  - プロキシ: `--mode local:<プロセス名>`
  - 証明書: システムキーチェーン
  - SSLKEYLOGFILE: 基本的に非対応

Chrome は `chrome-devtools-debugger` の担当なのでここには無い。CDP で繋がない
Chromium ベースのアプリ (Electron / Arc) は GUI アプリとして扱う。

**Firefox の証明書ストアは独立している。** システムキーチェーンへ CA を入れても
効かないため、「証明書を入れたのに TLS エラーが出る」で止まる。どちらかを採る:

- `about:config` で `security.enterprise_roots.enabled` を `true` にして
  システムのルートを信頼させる
- 設定の「証明書を表示」から `~/.mitmproxy/mitmproxy-ca-cert.pem` を手動インポート

TODO: どちらの手順を標準にするか、実際に 1 度通してから決める。

## regular モード

プロキシを立て、対象に環境変数で通す。sudo も GUI 操作も要らない。

```bash
mitmdump -q --listen-port 8080 --set hardump=/tmp/flows.har &

HTTP_PROXY=http://127.0.0.1:8080 \
HTTPS_PROXY=http://127.0.0.1:8080 \
SSL_CERT_FILE=~/.mitmproxy/mitmproxy-ca-cert.pem \
  <対象のコマンド>
```

`hardump` は**終了時**に書かれる。途中で読もうとしても空なので、
対象の実行が終わったら `mitmdump` を止めてから HAR を読む。

stdout に出すこともできる:

```bash
mitmdump -q --set hardump=-
```

## local モード

GUI アプリが対象のとき。プロセス単位で透過的に横取りする。

```bash
mitmdump --mode local:<プロセス名>    # 対象を限定する
mitmdump --mode local:'!<プロセス名>' # 対象を除外する
mitmdump --mode local:<PID>
```

初回にネットワーク拡張の承認ダイアログが出る。**人間に承認を依頼してから進む。**
承認前に起動しても黙って何も捕れないので、トラフィックが 0 件のときは
まずここを疑う。

対象を絞らずに `--mode local` 単独で起動すると、開発機の全通信が
プロキシを経由する。ノイズが増えるだけでなく後片付けの取り残しも起きやすいので、
プロセス名か PID で必ず絞る。

## HAR の読み方

HAR は JSON なので `jq` でそのまま解析できる。エージェントが結果を機械的に
扱えるのがこの経路の利点。

```bash
# ステータスと URL の一覧
jq -r '.log.entries[] | "\(.response.status) \(.request.method) \(.request.url)"' /tmp/flows.har

# 4xx/5xx だけ
jq '.log.entries[] | select(.response.status >= 400)' /tmp/flows.har

# 遅いリクエスト上位
jq -r '.log.entries | sort_by(-.time) | .[] | "\(.time|round)ms \(.request.url)"' /tmp/flows.har
```

## 証明書を信頼させる (CLI / 自作サーバ)

ランタイムごとに見る環境変数が違う。ここを外すと TLS エラーだけが出て
「プロキシが動いていない」と誤診しやすい。

- curl / OpenSSL 系: `SSL_CERT_FILE`
- Node.js: `NODE_EXTRA_CA_CERTS`
- Python requests: `REQUESTS_CA_BUNDLE`
- Go: `SSL_CERT_FILE`

TODO: 実際に使うランタイムで 1 度通して確認する。上表は一般的な対応で、
ライブラリが独自の証明書ストアを持つ場合は当たらない (Firefox がその例)。

システムのキーチェーンへ入れる方法もあるが、**この skill では採らない**。
環境変数はそのプロセス限りで済み、後片付けが要らない。

## フィルタと再生

流量が多いときは絞る。local モードで GUI アプリを見るときは特に効く。

```bash
mitmdump -q --set hardump=/tmp/api.har '~u /api/'      # URL に /api/ を含む
mitmdump -q --set hardump=/tmp/h.har '~d example.com'  # ホスト一致
```

保存した flow は読み直せる。HAR も native flow も拡張子ではなく内容で判別される。

```bash
mitmdump -nr /tmp/flows.har            # 読み直して整形表示
mitmdump -nr /tmp/flows.har -w /tmp/filtered.mitm '~u /api/'
```
