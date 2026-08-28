---
status: closed
---

# improve: resolve-ip が APIPA をそのまま返すとき利用者に何も知らせない

## 背景

[ISSUE-9](../ISSUE-9_doctor%20が%20APIPA%20アドレスを健全と判定する/issue.md) で doctor の IP チェックが
APIPA (`169.254.0.0/16`) を FAIL 側へ倒るようにしたが、同じ `pick_ipv4` を使う `cmd_resolve_ip` は
手を付けていない。あちらは「Parallels が報告した IP をそのまま返す」観測に徹する契約なので、
判定を混ぜると ProxyCommand 経由の接続の失敗様式まで変わる、というのが ISSUE-9 での判断だった。

その判断自体は変えない。ただし記録していたのは「判定を入れて exit 1 にする」対「純粋な観測に
徹する」の二択だけで、**stdout と exit code を一切変えずに stderr へ 1 行出す**という中間の形を
評価していなかった。

`references/ssh-config.template` の ProxyCommand は接続のたびに `winvm resolve-ip` を呼ぶ。
APIPA を掴んでいる間は、その値が ssh の接続先になって不透明にタイムアウトする。これは
ISSUE-9 が記録した誤診の入口 (「SSH が connect timeout する」から調べ始めてしまう) が
そのまま再演されることを意味する。doctor を叩くことを知っている人だけが真因に届く。

## 現状

`skills/devops/windows-vm-verification/winvm.py` の `cmd_resolve_ip` は `pick_ipv4` の戻り値を
そのまま `print` して 0 を返す。APIPA と正常な IP を区別する箇所は無い。

## 提案

stdout と exit code は不変のまま、APIPA を検知したときだけ stderr へ 1 行出す。

- ProxyCommand の中で動くコマンドの stderr は、ssh の呼び出し元にそのまま表示される (実測)。
  接続を待っている人の目に、待っている最中に届く
- stdout を汚さないので、`$(winvm resolve-ip ...)` で値を取るスクリプトは影響を受けない
- exit code を変えないので ProxyCommand の失敗様式も変わらない (exit 1 にすると nc が空文字を
  掴んで即死し、今と別の壊れ方になる。これは ISSUE-9 で棄却済み)
- 文面は doctor を見ろというポインタだけにする。DHCP の話を再掲すると doctor の hint と
  二重管理になる

## タスク

- [x] `test_winvm.py` に「resolve-ip が APIPA のとき stdout は IP のみ・exit 0 のまま、
      stderr に警告が出る」テストを追加する。stdout に警告が混ざらないことを negative case で pin する
- [x] 追加したテストが現行実装で赤くなることを確認する
- [x] `cmd_resolve_ip` に警告を実装する。接頭辞は `警告:` (終端しない通知。規約は
      `StderrMessagePrefix` テストが canonical)
- [x] 変異注入で確認する: 警告の分岐を外すと追加したテストが赤くなること。
      あわせて警告を stdout 側へ出す変異で、stdout を pin した assertion が赤くなること
- [x] ControlMaster を使っている場合に警告が何回出るかを実機で確認する。毎接続で出ると
      うるさいので、master 確立時の 1 回で済むなら現状のままでよい

## 実測 (2026-08-28)

### ProxyCommand の stderr は呼び出し元に出る

提案が「(実測)」として引いていた前提を自分で再現した。存在しない host へ
`ProxyCommand=sh -c 'echo <目印> >&2; exit 1'` で繋ぐと、目印が ssh の呼び出し元の端末に出る。
警告が人の目に届く経路はこれで確定した。

### ControlMaster 下では master 確立時の 1 回だけ

`ControlMaster auto` / `ControlPersist 60s` で同一 host へ 3 回続けて接続し、ProxyCommand が
起動した回数をログの行数で数えた。**3 接続に対して 1 回**。警告が接続のたびに出ることはなく、
抑制機構は要らない。ssh-config.template のコメントが「prlctl の起動も 1 回で済む」と
書いていたのと一致する。

測ったのは ssh 自身の多重化の意味論なので、接続先には認証が通る任意の host を使った
(APIPA を再現するには VM 内で DHCP を壊す必要があり、そこまでしても測る対象は変わらない)。

### live smoke

実 `prlctl` を通す経路 (subprocess 起動 → JSON パース → VM 引き当て) は停止中 VM と
未登録名の 2 つで通した。IP が返る成功パスは VM が停止中のため未実行で、そこは
`FakeRunner` を通したユニットテストが覆っている。

### 変異注入

7 件を 1 件ずつ隔離して当て、全て KILLED。baseline が緑であることを先に確認している。

| 変異 | 赤くなったテスト |
| --- | --- |
| 警告の分岐を殺す | resolve-ip の APIPA テスト |
| 警告を stdout へ出す | 同上 (stdout の exact 比較) |
| 判定を常に真にする | 正常系が無警告であることの対照 |
| 判定を常に偽にする | resolve-ip と **doctor の両方** |
| 帯を link-local から private へすり替える | 正常系の対照 |
| 警告から VM 名を落とす | resolve-ip の APIPA テスト |
| 警告へ doctor の説明を写す | 同上 (二重管理の検査) |

「判定を常に偽にする」で doctor 側も同時に赤くなることが、抽出した `is_apipa` が
両方の呼び出し元で本当に共有されている証拠になっている。

## 関連

- `skills/devops/windows-vm-verification/winvm.py` — `cmd_resolve_ip`、`pick_ipv4`
- `skills/devops/windows-vm-verification/references/ssh-config.template` — ProxyCommand が
  接続のたびに resolve-ip を呼ぶ
- [ISSUE-9 (closed): doctor が APIPA アドレスを健全と判定する](../ISSUE-9_doctor%20が%20APIPA%20アドレスを健全と判定する/issue.md)
  — 本 Issue の親にあたる判断。doctor 側は解決済みで、残っているのは resolve-ip 経路だけ
- [ISSUE-3: VM 引き当ての前段が複数のサブコマンドに逐語で複製されている](../../ISSUE-3_VM%20引き当ての前段が複数のサブコマンドに逐語で複製されている/issue.md)
  — `cmd_resolve_ip` を触る点で作業範囲が重なる。先に ISSUE-3 を片付けると本 Issue の変更点が 1 箇所で済む
