# コマンドをスクリプトへ切り出す判断

初版は散文 + snippet のみで、`.sh` / `.py` を持たない。その判断の根拠と、
将来切り出すときの条件・コスト・形をここに置く。

## 判断の基準

`agentic-coding-tools` で既にスクリプトを持つ 2 つ (`markdown-to-pdf/scripts/render.py`、
`in-repo-issue/scripts/issue-id.py`) に共通する性質が基準になる。

1. **スクリプトが canonical を持ち、散文が再掲しない**
   (`in-repo-issue/SKILL.md` は識別子の形も採番規則も書かず「canonical は同スクリプト」
   とだけ書く)
2. **決定的である**。入力から出力が一意に決まり、途中に人の判断が無い
3. **人間や CI も走らせる**。`issue-id.py` を `cp` でプロジェクトへ配るのはこのため

CLAUDE.md の [MUST]「規約は散文ではなく検査に落とす」の、skill 側での実装にあたる。

## この skill の中身を基準で分類する

- **判断があるもの → snippet のまま置く**
  - 採取のインターフェース、BPF フィルタ、表示フィルタ、出力の読み方
  - パラメータが内容の全部で、かつ出力を見てから次を決める。スクリプトに包むと
    `capture.sh --iface en0 --filter ...` のように、文書化された方言 (tcpdump) の上に
    文書化されていない方言を足すだけになる
  - reference にフラグを直書きする利点は、エージェントがそれを読んで未知の組み合わせを
    作れること。スクリプト化はこれを奪う
- **決定的で結果が真偽値のもの → スクリプト向き**
  - 該当するのは「前提の確認」だけ。パラメータが無く、出力が判定で、人間も走らせたい
- **中間**
  - 後片付け。列挙 (`pgrep`) は決定的だが、kill は確認が要るので完全自動化しない

**スクリプト化は「決定的に実行される」ためではない。** この skill が文書化した失敗
(承認漏れ、GUI の環境変数、`-Q` の黙殺、pktap の権限) はどれもコマンドの書き間違いでは
なく環境の差異で、包んでも直らない。中で環境を検査するなら直る。だから切り出す候補は
検査だけになる。

## 切り出す閾値

「前提の確認」が実質 2 行 (`command -v` のループと `ls -l /dev/bpf0`) である限り
切り出さない。次のどれかが起きたら切り出す。

- 検査が分岐を持ち始めた (pktap の可否、local モードの承認済み状態、CA の有効期限、
  Firefox プロファイルの証明書ストア)
- 同じ検査を別の場所へ 3 回書いた
- テストを書きたくなった (repo は `unittest` 縛りで `run-python-tests.py` がある)

目安として 20 行を超えたら散文で持つのは無理。

## 切り出すときのコスト

**`${CLAUDE_SKILL_DIR}` が未設定の経路ではスクリプトへ辿り着けない。**
これは `in-repo-issue/SKILL.md` が既に踏んで文書化している罠と同じものである。

```
スクリプトに辿り着けなかった場合 (`${CLAUDE_SKILL_DIR}` が未設定だとパスが
`/scripts/issue-id.py` になる) は python の `No such file or directory` が出る
```

skill として invoke された経路では展開されるが、git hook や CI runner は skill を
読み込まない。そこから同じ検査を走らせたいなら、配布先へ copy する形が併せて要る
(`in-repo-issue` が `scripts/issue-id.py` をプロジェクトへ配っているのはこのため)。
スクリプトを持つと置き場所が 2 つになる、というのがここでのコストである。

SKILL.md 側に但し書きを置いて劣化運転を許す形もある:

```markdown
`${CLAUDE_SKILL_DIR}` が未設定の環境ではスクリプトへ辿り着けない
(`No such file or directory`)。その場合は下の 2 行を直接実行して同じ判定をする。
```

得られる利益は `allowed-tools` の絞り込みである。
`Bash(${CLAUDE_SKILL_DIR}/scripts/preflight.sh)` の 1 パターンで済み、
今の `Bash(tshark *)` より権限が狭くなる。

## preflight のたたき台

**まだ配線しない。** 上の閾値に達したときの形を見えるようにしておくためのもの。
ここから `scripts/preflight.sh` へ移す。

```bash
#!/usr/bin/env bash
# network-debug-mac の着手前チェック。
# 各レイヤーの経路が通るかを判定し、足りないものと依頼内容を stdout へ出す。
# 終了コード: 0 = 全経路が通る / 1 = 使えない経路がある (出力を読む)
#
# プロンプトを出さないこと。`sudo -n` を使い、対話で止まる経路を作らない。
# 止まる検査はエージェントから呼べず、呼べない検査は書いていないのと同じになる。
set -uo pipefail
degraded=0

for c in mitmdump tshark jq; do
    command -v "$c" > /dev/null && continue
    degraded=1
    case "$c" in
        mitmdump) echo "missing: mitmdump -- レイヤー2が使えない。依頼: brew install mitmproxy" ;;
        tshark)   echo "missing: tshark -- レイヤー3の解析が使えない (採取は可)。依頼: brew install wireshark" ;;
        jq)       echo "missing: jq -- HAR 解析は python3 -m json.tool で代替する (依頼不要)" ;;
    esac
done

if [ -r /dev/bpf0 ]; then
    echo "ok: /dev/bpf0 readable -- sudo なしで通常インターフェースを採取できる"
else
    degraded=1
    echo "missing: /dev/bpf0 が読めない -- レイヤー3,4 が使えない。依頼: brew install --cask wireshark-chmodbpf"
fi

# pktap の作成は /dev/bpf* の読み取りとは別の権限で、chmodbpf では外れない。
if sudo -n true 2> /dev/null; then
    echo "ok: sudo 可 -- pktap (複数IF同時 / -Q プロセスフィルタ) が使える"
else
    degraded=1
    echo "degraded: sudo 不可 -- pktap が使えない。-i en0 / -i lo0 で個別に採る。-Q proc= は黙って無視されるので使わない"
fi

if [ -r "$HOME/.mitmproxy/mitmproxy-ca-cert.pem" ]; then
    echo "ok: mitmproxy CA あり"
else
    echo "info: mitmproxy CA が無い -- mitmdump を一度起動すれば生成される"
fi

exit "$degraded"
```

`sudo -n true` は資格情報が無いとき**プロンプトを出さずに** 1 を返す
(実測: `sudo: a password is required` / exit 1)。stderr は捨てる。

TODO: 切り出すときに、上の閾値に挙げた検査 (承認済み状態、CA の有効期限、
Firefox の証明書ストア) を足す。足した時点でテストを書く。
