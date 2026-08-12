---
status: open
---

# docs: prlctl exec の呼び出し形式とゲスト出力の文字コードが未文書

## 背景

SSH が繋がらない状況で `prlctl exec` を代替チャネルとして使ったときに踏んだ。

`prlctl exec` はコマンドを argv 形式で渡す必要がある。単一文字列で渡すと
**エラーではなく空の出力と exit 2** で返る。

```bash
# 期待どおり動く
prlctl exec "<vm>" cmd.exe /c ver
# -> Microsoft Windows [Version ...]   (exit 0)

# 静かに壊れる
prlctl exec "<vm>" 'ver'
# -> (出力なし)                        (exit 2)
```

実際にこの形でレジストリの Uninstall キーを `reg query ... /f <アプリ名>` で引いて空が返り、
「対象アプリは VM に入っていない」と読みかけた。エラーが出ないので誤りに気づけず、
対照 (必ず出力があるはずの `ver`) を取って初めて「クエリの組み方が違う」と分かった。
偽陰性なので、何度引いても同じ答えが返って確信だけが強まる種類の失敗になる。

あわせて、ゲストの出力は ja-JP 環境では CP932 で返る。macOS 側で読むには
`iconv -f CP932 -t UTF-8` を通す必要がある。`tr` などのバイト単位ツールに直接渡すと
`Illegal byte sequence` で落ちる (実際に `tr -d '\r'` で落ちた)。

`winvm.py` 内部の CP932 デコードは [Issue #1](../closed/1_winvm%20が%20VM%20の%20CP932%20出力で%20UnicodeDecodeError%20になる/issue.md)
で対処済みだが、そこで直ったのは winvm を経由する経路だけである。SSH が落ちていて
`prlctl exec` を直接叩く場面はまさに winvm を経由しないため、この対処の外側にある。

## 現状

SKILL.md に `prlctl exec` の記述は 2 箇所あるが、いずれも呼び出し形式には触れていない。

- 「`host isolation` … on だと `prlctl exec` が通らない」
- 「`prlctl exec` … 実際に `cmd.exe /c ver` をゲストで実行できるか」

後者は doctor の内部実装として argv 形式を使っているが、読み手が「自分で叩くときも
この形式でなければならない」と読み取れる書き方にはなっていない。

## タスク

- [ ] SKILL.md に `prlctl exec` を直接使うときの注意を追記する
  - [ ] argv 形式で渡すこと。単一文字列形式は空 + exit 2 で静かに失敗すること
  - [ ] 「見つからない」という結論を出す前に、必ず出力があるコマンドを対照に置くこと
  - [ ] ゲスト出力は ja-JP で CP932。`iconv -f CP932 -t UTF-8` を通すこと (`tr` は不正バイト列で落ちる)
- [ ] SSH が使えない状況での代替チャネルとして `prlctl exec` を使う、という位置づけを書く
      (`winvm exec` は SSH 前提なので、SSH が落ちている診断中には使えない)

## 関連

- [Issue #1 (closed): winvm が VM の CP932 出力で UnicodeDecodeError になる](../closed/1_winvm%20が%20VM%20の%20CP932%20出力で%20UnicodeDecodeError%20になる/issue.md)
  — winvm 内部のデコードはこちらで対処済み。本 Issue は winvm を経由しない直接呼び出しの経路。
- [Issue #9: doctor が APIPA アドレスを健全と判定する](../9_doctor%20が%20APIPA%20アドレスを健全と判定する/issue.md)
  — 同じ調査中に見つかった。SSH が落ちている間の診断で `prlctl exec` が唯一のチャネルになる。
