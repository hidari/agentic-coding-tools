# TLS の復号: SSLKEYLOGFILE

プロキシを挟まずに暗号化された通信の中身を読む経路。

## mitmproxy との使い分け

- mitmproxy
  - 通信経路: 書き換える (中継する)
  - 証明書の信頼: 必要
  - 見えるもの: HTTP セマンティクス
  - 使う場面: HTTP のデバッグ全般
- SSLKEYLOGFILE
  - 通信経路: 変えない
  - 証明書の信頼: 不要
  - 見えるもの: パケットと HTTP の両方
  - 使う場面: 証明書ピンニング、プロキシ非対応、HTTP 以外の TLS

**通常は mitmproxy を使う。** こちらを選ぶのは、証明書を信頼させられない
(ピンニングしている) か、経路を変えると再現しなくなる問題を追うとき。

## 手順

鍵をファイルへ書き出しながら通信し、同時にパケットを採って、
tshark へ鍵を渡す。

```bash
# 1. キャプチャを開始 (pktap は sudo が要るので既定は通常インターフェース)
tcpdump -i en0 -w /tmp/tls.pcapng &

# 2. 鍵をログしながら対象を実行
SSLKEYLOGFILE=/tmp/keys.log <対象のコマンド>

# 3. キャプチャを止めてから、鍵を渡して読む
tshark -r /tmp/tls.pcapng -o tls.keylog_file:/tmp/keys.log -Y http2
```

## 対応しているクライアント

`SSLKEYLOGFILE` を見るのはランタイム側の実装であり、すべてが対応しているわけではない。

- curl: OpenSSL / GnuTLS ビルドのもの。macOS 同梱版は要確認
- Chrome / Firefox: 対応。ただしブラウザは `chrome-devtools-debugger` が先
- Go: 標準では無効。`tls.Config.KeyLogWriter` を書く必要がある
- Node.js: `--tls-keylog=<file>` フラグ
- Python: ssl モジュールで `SSLContext.keylog_filename`

TODO: 対象のランタイムで鍵が実際に書かれることを、キャプチャの前に確認する。
`/tmp/keys.log` が空のまま進めると「復号できない」原因の切り分けに時間を取られる。

## 後片付け

**鍵ログは機密である。** そのセッションの通信をすべて復号できるため、以下を守り、使用後に絶対に削除すること。

- リポジトリへコミットしない
- デバッグレポートへ添付しない
- git 管理下のディレクトリへ書き出さない
