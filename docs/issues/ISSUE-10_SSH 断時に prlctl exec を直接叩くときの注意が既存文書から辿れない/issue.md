---
status: open
---

# docs: SSH 断時に prlctl exec を直接叩くときの注意が既存文書から辿れない

## 背景

SSH が繋がらない状況で `prlctl exec` を代替チャネルとして使ったときに踏んだ。

`prlctl exec` はコマンドを argv 形式で渡す必要がある。プログラム名まで 1 文字列に含めると
**エラーではなく空の出力と exit 2** で返る。

```bash
# 期待どおり動く
prlctl exec "<vm>" cmd.exe /c ver
# -> Microsoft Windows [Version ...]   (exit 0)

# 静かに壊れる
prlctl exec "<vm>" "cmd.exe /c ver"
# -> (出力なし)                        (exit 2)
```

実際にこの形でレジストリの Uninstall キーを `reg query ... /f <アプリ名>` で引いて空が返り、
「対象アプリは VM に入っていない」と読みかけた。エラーが出ないので誤りに気づけず、
対照 (必ず出力があるはずの `cmd.exe /c ver`) を取って初めて「クエリの組み方が違う」と分かった。
偽陰性なので、何度引いても同じ答えが返って確信だけが強まる種類の失敗になる。

あわせて、ゲストの出力は ja-JP 環境では CP932 で返る。macOS 側で読むには
`iconv -f CP932 -t UTF-8` を通す必要がある。`tr` などのバイト単位ツールに直接渡すと
`Illegal byte sequence` で落ちる (実際に `tr -d '\r'` で落ちた)。

[ISSUE-1](../closed/ISSUE-1_winvm%20が%20VM%20の%20CP932%20出力で%20UnicodeDecodeError%20になる/issue.md)
が直したのは `winvm.py` が外部コマンドの出力を読む経路で、対処は UTF-8 decode +
`errors="replace"` である。落ちなくはなったが日本語は化けたままで、復号はしていない。
ホスト側で `prlctl exec` の出力を直接読む経路は、そもそもこの対処の外側にある。

## 現状

呼び出し形式そのものは既に 3 箇所に書かれている。**それでも踏んだ**ので、本 Issue は
「未文書」ではなく「導線が無い」問題として扱う。

- `references/windows-bootstrap.md` — プログラム名まで 1 文字列に含めると exit 2 で無出力
- `references/troubleshooting.md` — 症状表に同内容 (通る例と失敗する例つき)
- `winvm.py` の `prlctl_exec_argv` docstring — 同じ実測 (Parallels Desktop のバージョンつき)

SKILL.md 本文にも `prlctl exec` への言及はあるが、いずれも呼び出し形式には触れておらず、
上の 3 箇所への導線にもなっていない。

- 「`host isolation` … on だと `prlctl exec` が通らない」
- 「`prlctl exec` … 実際に `cmd.exe /c ver` をゲストで実行できるか」
- セットアップ手順の「ホストの `prlctl exec` だけで完結する」

このうち doctor の項目は、実際に `cmd.exe /c ver` を実行して観測値ごと出す。つまり
doctor 自身が「必ず出力があるコマンドの対照」を体現しているのだが、読み手がそう使えると
気づける書き方にはなっていない。

本当に未文書なのは次の 2 点に絞られる。

- ホスト側で出力を読むときの文字コード (`iconv` が要る / `tr` は不正バイト列で落ちる)。
  `references/troubleshooting.md` の既存の文字化け行は、ゲスト側で
  `[Console]::OutputEncoding` を設定する別経路の話で、この経路は覆っていない
- SSH が落ちている診断中の代替チャネルという位置づけ

## タスク

追記先は `references/troubleshooting.md` とする。症状と対処の canonical はそちらで、
SKILL.md のトラブルシューティング節はそこへのポインタしか持たない。

- [ ] `references/troubleshooting.md` に、ホスト側で `prlctl exec` の出力を読むときの
      文字コードの行を足す (`iconv -f CP932 -t UTF-8` を通す。`tr` は不正バイト列で落ちる)
- [ ] 既存の argv 形式の行に、SSH 断時に直接叩く場面という文脈と、「見つからない」と
      結論する前に `cmd.exe /c ver` を対照に置くことを足す。同じ事実を 4 箇所目として書かない
- [ ] SKILL.md から上記へ辿れる導線を作る。SSH 前提なのは `winvm exec` であって winvm 全体
      ではない (`resolve-ip` / `doctor` / `screenshot` は prlctl だけで動く) ことが分かる形にする
- [ ] 文字コードの注意を Windows 固有として書く。macOS ゲストは UTF-8 で返すため、
      同じ注意を両 skill へ複製しない

## 2 度目の観測 (macOS ゲスト)

ISSUE-51 の作業中に、同じ根を macOS ゲストで踏んだ。SSH がまだ無い段階の踏み台として
`prlctl exec` を使い、公開鍵を配置しようとした場面である。

本 Issue が「未実測」としていたクォート意味論は、この過程で実測できた。**ゲスト側で argv が
再分割される。**

| 試行 | 結果 |
|---|---|
| `prlctl exec "<vm>" sh -c 'echo hello > /tmp/probe.txt'` | ファイルは作られるが中身は**改行 1 バイト**。`echo hello` の引数が失われ、`sh -c 'echo'` 相当が実行された |
| `cat key.pub \| prlctl exec "<vm>" tee -a <path>` | `PrlJob_GetRetCode: Invalid argument` (stdin パイプが通らない) |
| `prlctl exec "<vm>" mv /tmp/x /etc/x` | `PrlJob_GetResult: Invalid argument` (特権パスへの書き込み) |

引数が単純なもの (`mkdir -p <path>` / `chown -R <user>:<group> <path>` / `chmod 700 <path>`)
は通った。トークン数が少なくリダイレクトを含まないコマンドだけが安全と考えてよい。

**壊れ方が Windows 側と違う。** 本 Issue の背景が記録している Windows の症状は「空の出力と
exit 2」で、少なくとも「何も返ってこなかった」ことは分かる。macOS ゲストでは**エラーに
ならず部分的に実行される**。ファイルはできるので成功に見え、中身を確認しない限り気づけない。
偽陰性としてはこちらの方が深い。

なお macOS ゲストの出力は UTF-8 なので、本 Issue が挙げるもう 1 つの未文書項目 (`iconv` が
要る / `tr` が不正バイト列で落ちる) は macOS 側では出ない。文字コードの注意は Windows
固有として書ける。

### 「採らなかった案」の条件について

下の節は「同じ罠を再度踏んだらコード側へ移す」と条件を明記している。今回が 2 度目にあたり、
先送りの根拠だった「クォート意味論は未実測」も上の表で解消された。判断そのものは変えずに
事実だけを置くが、再検討の材料は揃っている。

## 検討して採らなかった案

winvm 側に prlctl 経由の exec 口を足してコードで吸収する案がある。argv 形式・CP932・
exit 2 の静かな失敗はいずれも機械的に吸収でき、吸収すればテストで pin できて散文と違い
drift しない。文書化済みの状態で踏んだという事実は、この経路に対する散文の検証力が弱いことの
一次証拠でもある。

それでも今回は採らない。外部コマンドを組み立てるサブコマンドはこのリポジトリの規約上
live smoke の完走が要り、prlctl が argv をゲスト側でどう再結合するかのクォート意味論は
未実測である。緊急診断で 1 回使った経路に対しては先行投資が大きい。同じ罠を再度踏んだら
コード側へ移す。

## 関連

- `skills/devops/windows-vm-verification/references/troubleshooting.md` — 追記先。
  `references/windows-bootstrap.md` と `winvm.py` の `prlctl_exec_argv` docstring が
  同じ実測を持つので、canonical をどこに置くかを決めてから書くこと
- [ISSUE-1 (closed): winvm が VM の CP932 出力で UnicodeDecodeError になる](../closed/ISSUE-1_winvm%20が%20VM%20の%20CP932%20出力で%20UnicodeDecodeError%20になる/issue.md)
- [ISSUE-9 (closed): doctor が APIPA アドレスを健全と判定する](../closed/ISSUE-9_doctor%20が%20APIPA%20アドレスを健全と判定する/issue.md)
  — 同じ調査中に見つかった
- ISSUE-51 — macOS VM 版の skill。その作業中に同じ根を 2 度目に踏み、未実測だったクォート
  意味論を実測した (上の「2 度目の観測」節)
