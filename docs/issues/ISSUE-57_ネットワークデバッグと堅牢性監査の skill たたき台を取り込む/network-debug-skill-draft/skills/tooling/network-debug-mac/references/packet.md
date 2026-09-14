# パケットレイヤ: tcpdump で採る、tshark で読む

HTTP レイヤ (`references/http.md`) で見えない、あるいは接続自体が
確立しないときに降りてくる。

- `tcpdump` は macOS に同梱されているので追加インストール不要 (ただし `pktap` を使う経路は sudo が要る)
- `tshark` は `brew install wireshark` (CLI のみ。GUI は `--cask wireshark-app`)

**採取と解析を分ける。** ターミナル出力を目で読むのではなく、pcapng に落として
tshark で問い合わせる。この形なら結果を機械的に扱える。

## 採取

**経路が 2 つあり、sudo の有無で変わる。** どちらを使えるかは `SKILL.md` の
「前提の確認」で判定してから来る。

### sudo なし (既定)

chmodbpf で `/dev/bpf*` が読めれば、通常のインターフェース名を指定して採れる。

```bash
tcpdump -i en0 -w /tmp/cap.pcapng
tcpdump -i lo0 -w /tmp/lo.pcapng     # localhost はこちら
```

インターフェースをまたぐ同時取得はできないので、両方要るなら 2 つ走らせる。
後片付けの対象が 2 つになるので `SKILL.md` の「後片付け」を必ず通す。

### sudo あり (pktap)

`pktap` 疑似インターフェースを使うと、複数インターフェースの同時取得と
プロセス単位のフィルタができる。**ただし疑似インターフェースの作成に root 権限が要る。**

```bash
# 複数インターフェース同時。localhost を含めるには lo0 が必要
sudo tcpdump -i pktap,lo0,en0 -w /tmp/cap.pcapng

# 全インターフェース (loopback と tunnel も含む)
sudo tcpdump -i pktap,all -w /tmp/cap.pcapng

# プロセス単位 (-Q は Apple 拡張のメタデータフィルタ)
sudo tcpdump -i pktap,all -Q "proc = 'node'" -w /tmp/node.pcapng
sudo tcpdump -i pktap,en0 -Q "( if=en0 and proc=curl ) || (if != en0 and dir=in)"
```

`/dev/bpf*` が読めることと pktap が使えることは別である。chmodbpf を入れても
sudo なしで pktap を指定すると採取そのものが始まらない:

```
tcpdump: ioctl(SIOCIFCREATE): Operation not permitted
```

これは見て分かる失敗なので、下記の `-Q` の黙殺とは扱いが違う。

`pktap` を使うと自動的に pcapng 形式になり、パケットにプロセス名などの
メタデータが付く (`-k` で表示)。

TODO: sudo が使える環境で `-Q` のフィルタ式を 1 度通す。man tcpdump の
PACKET METADATA FILTER 節が canonical で、`proc` 以外に `if` / `dir` / `pid` / `svc`
が使える。

### -Q を通常インターフェースに付けない

**pktap 以外で `-Q proc=` を指定すると、エラーも警告も出ないまま無視される。**
実測 (macOS 26.6, tcpdump on `lo0`) — 存在しないプロセス名なので 0 件になるはずが:

```
$ tcpdump -i lo0 -c 3 -Q "proc = 'zzz_no_such_proc'"
3 packets captured     # フィルタが効いていない
```

「そのプロセスの通信だけを見た」つもりで全件を見ることになる。プロセス単位で
絞るなら pktap (= sudo) を使うか、絞るのを諦めて BPF フィルタ (ポート / ホスト)
で代替する。

### 採取量を抑える

長時間まわすときはリングバッファで上限を切る。放置でディスクを埋めない。

```bash
tcpdump -i en0 -w /tmp/cap.pcapng -C 100 -W 5   # 100MB x 5 ファイルで循環
```

BPF フィルタで入口を絞るのも有効 (採取量そのものが減る):

```bash
tcpdump -i en0 'tcp port 443 and host example.com' -w /tmp/cap.pcapng
```

## 解析

```bash
# 会話サマリ。まずこれで全体像を見る
tshark -r /tmp/cap.pcapng -q -z conv,tcp

# 必要なフィールドだけ CSV で出す
tshark -r /tmp/cap.pcapng -T fields \
  -e frame.time_relative -e ip.src -e ip.dst -e tcp.flags.str -e http.host \
  -E separator=, -E header=y

# JSON。構造ごと扱いたいとき (出力が大きいので必ず表示フィルタと併用する)
tshark -r /tmp/cap.pcapng -Y 'http.response.code >= 400' -T json
```

## よく使う表示フィルタ

`-Y` に渡す。BPF フィルタ (採取時) とは文法が別物なので混ぜない。

| 目的 | フィルタ |
|---|---|
| TCP の異常終了 | `tcp.flags.reset == 1` |
| 再送 | `tcp.analysis.retransmission` |
| TLS handshake の失敗 | `tls.alert_message` |
| ClientHello の SNI | `tls.handshake.extensions_server_name` |
| DNS の応答エラー | `dns.flags.rcode != 0` |
| HTTP エラー応答 | `http.response.code >= 400` |

## 症状別の入口

| 症状 | 見るもの |
|---|---|
| 接続できない | SYN に対する応答。RST が返るか、応答が無い (SYN 再送) か |
| 途中で切れる | `tcp.flags.reset == 1` の送信元。どちら側が切ったか |
| TLS で失敗 | `tls.alert_message` と、直前の ClientHello の SNI / 提示された証明書 |
| 名前解決が怪しい | `dns` で実際に引かれた名前と返った A レコード |
| 遅い | `-z conv,tcp` の duration と、`tcp.analysis.retransmission` の有無 |

## 落とし穴

- **localhost は `lo0` が必要。** `-i en0` だけでは見えない
- `pktap` 使用時 (sudo) は promiscuous モードにならない。他ホストの通信は見えない
  (通常のデバッグでは問題にならない)
- HTTP/2 と gRPC はテキスト出力では読めない。pcapng + tshark 前提で進める
- 暗号化された本文は当然読めない。中身が必要なら
  `references/http.md` か `references/tls.md` へ戻る
