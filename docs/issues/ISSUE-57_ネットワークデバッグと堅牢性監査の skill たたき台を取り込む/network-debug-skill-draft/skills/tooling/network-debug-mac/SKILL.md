---
name: network-debug-mac
description: macOS で通信そのものを観測して、HTTP API の応答、TLS ハンドシェイクの失敗、接続エラー、意図しない外向きリクエストを切り分ける。ログやコードからの推測で原因が決まらず、実際に流れているものを見る必要があるときに使う。chrome-devtools-mcp で接続できる Chrome のトラフィックだけは対象外で chrome-devtools-debugger を使う。
allowed-tools: Bash(lsof -i*) Bash(nettop *) Bash(scutil --dns) Bash(tshark *) Bash(mitmdump *) Bash(ls -l /dev/bpf*)
---

# network-debug-mac

## 責務の境界

chrome-devtools-mcp で接続できる Chrome のトラフィックは `chrome-devtools-debugger` を使う。**それ以外はすべてこの skill が見る。**

- それ以外のブラウザ (Safari / Firefox / Arc / Electron アプリ)
- 非ブラウザのプロセス、CLI、自作のサーバ、ネイティブアプリ、バックグラウンドの daemon
- HTTP より下のレイヤー (TCP / TLS / DNS)

除外の基準は「ブラウザかどうか」ではなく「**CDP で繋げるかどうか**」である。
「ブラウザは対象外」と解釈すると Safari / Firefox / Electron がどちらの skill の担当でもなくなる。それらは CDP で繋げないのでここが見る。

観測して切り分けるところまでが責務。修正方針の決定は含まない。

## レイヤーの選択

上から順に試す。**下へ降りるのはコストが上がる（1で答えが出るなら2降りない、等）**

1. 「どこへ繋いでいるか」を知りたいだけ
  - Means: `lsof` / `nettop` / `scutil`
  - Ref: 下記「まず確認する」
2. HTTP(S) のリクエストとレスポンスの中身
  - Means: `mitmproxy`
  - Ref: `references/http.md`
3. HTTP 以前で失敗 (TCP reset、TLS handshake、DNS、非 HTTP プロトコル)
  - Means: `tcpdump` + `tshark`
  - Ref: `references/packet.md`
4. プロキシを挟めないクライアントの TLS 中身
  - Means: `SSLKEYLOGFILE` + `tshark`
  - Ref: `references/tls.md`

2 で大半が済む。3 へ降りるのは「HTTP のリクエストが 1 つも観測できない」か「接続自体が確立しない」ときに限る。

## 対象の種別を先に決める

レイヤー 2 に入る前に、対象が CLI か GUI かを決める。**ここで手段が変わるので、飛ばすと環境変数で空振りしてから気づくことになる。**

- Target: CLI / 自作サーバ / スクリプト
  - プロキシの通し方: 環境変数 (`HTTP_PROXY` / `HTTPS_PROXY`) → mitmproxy regular モード
- Target:  GUI アプリ (Safari / Firefox / Electron / ネイティブ)
  - プロキシの通し方: `mitmdump --mode local:<プロセス名>` （ **初回に人間による承認が必要**）

macOS の GUI アプリは `HTTPS_PROXY` を見ない (CFNetwork 経由でシステムのネットワーク設定を読む)。したがって GUI アプリでは regular モードが第一候補にならず、local モードが第一候補になる。

local モードは初回にネットワーク拡張の承認ダイアログが出る。**エージェントは自動化できないので、GUI アプリを対象にすると決めた時点で人間に承認を依頼する。** 承認前に起動しても黙って何も捕れないため、トラフィックが 0 件のときはまずここを疑う。

Firefox だけは証明書ストアが独立しているので追加の手当てが要る。
`references/http.md` の「対象別の手当て」を読む。

## まず確認する

キャプチャを始める前にこの 3 つを見る。ここで解決することが多く、権限も要らない。

```bash
lsof -i -P -n            # プロセス別の接続一覧。接続先の特定はこれで足りる
nettop -P -l 1           # プロセス別の通信量スナップショット
scutil --dns             # DNS 設定。名前解決が疑わしいとき
```

単発の HTTP を見たいだけなら、キャプチャより先にこれを試す:

```bash
curl -v --trace-ascii - https://example.com
```

## 前提の確認

レイヤーを決めたら、**その経路が通るかを着手前に見る。** 足りないものを人間に依頼するのは着手前であって、途中で詰まってからではない。不足はエラーではなく「トラフィック 0 件」や「フィルタが効いていない結果」として現れるため、走らせた後では不足と別の原因を切り分けられない。

```bash
for c in mitmdump tshark jq; do command -v "$c" >/dev/null || echo "missing: $c"; done
ls -l /dev/bpf0
```

`tcpdump` / `lsof` / `nettop` / `scutil` は macOS 同梱なので確認しない。

- `mitmdump` が無い: レイヤー 2 が使えない
  - `brew install mitmproxy` を**人間に依頼する**
  - 依頼できない場合、対象が `SSLKEYLOGFILE` に対応していればレイヤー 4 へ迂回できる
- `tshark` が無い: レイヤー 3 の**解析**ができない (採取はできる)
  - `brew install wireshark` を**人間に依頼する**
  - 暫定的には `tcpdump -r <file>` で読める。表示フィルタは使えない
- `jq` が無い: HAR の解析に使う
  - 依頼不要。`python3 -m json.tool` で代替する
- `/dev/bpf0` が自分のユーザーで読めない: レイヤー 3, 4 が使えない
  - `brew install --cask wireshark-chmodbpf` を**人間に依頼する**
  - この skill の中で sudo を前提にしない (毎回のパスワード入力で手順が止まる)
- `sudo` が使えない: `pktap` が使えない
  - `/dev/bpf*` の読み取りと pktap 疑似インターフェースの**作成**は別の権限で、chmodbpf が外すのは前者だけ。`/dev/bpf0` が読めても pktap は通らない
  - 影響: 複数インターフェースの同時取得と `-Q` プロセスフィルタが使えない
  - 対応: `-i en0` / `-i lo0` で個別に採る。`-Q proc=` は**使わない** (下記「落とし穴」)

`mitmproxy` を使う場合は CA 証明書の存在も確認する:

```bash
ls ~/.mitmproxy/mitmproxy-ca-cert.pem
```

無ければ `mitmdump` を一度起動すれば生成される。

## 落とし穴

- **localhost 間の通信は `-i lo0` (または `pktap,lo0`) でないと見えない。** ローカルのサーバとクライアントをデバッグするときに必ず引っかかる
- HTTP/2 と gRPC は tcpdump のテキスト出力では読めない。pcapng に落として tshark で開く
- Electron アプリは Chromium だが CDP で繋いでいないので `chrome-devtools-debugger` では見えない。GUI アプリとして扱う
- **通常インターフェースに `-Q proc=` を付けても黙って無視される。** エラーも警告も出ず、フィルタされていない結果が返る。プロセス単位で絞るには pktap (= sudo) が要る

## 後片付け

バックグラウンドで起動したキャプチャは**必ず止める**。放置するとディスクを埋め、プロキシ設定が残ったままになる。

```bash
# 起動したものを確認してから止める。pkill を確認なしで打たない
pgrep -lf 'mitmdump|tshark|tcpdump'
```

作業の最後に、環境変数で通したプロキシ設定がそのシェル限りだったことを確認する。
`~/.zshrc` などへ書き込む形でプロキシを通してはいけない。システムのネットワーク設定を書き換える形も採らない (後片付けが漏れる)。

## Reference

- `references/http.md`: mitmproxy の 2 つのモード、対象別の手当て、HAR 出力、フィルタ
- `references/packet.md`: tcpdump での採取 (sudo の有無で経路が変わる)、tshark での解析
- `references/tls.md`: SSLKEYLOGFILE による復号。プロキシを挟まない経路
