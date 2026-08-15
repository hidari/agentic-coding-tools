---
status: open
---

# improve: resolve-ip が APIPA をそのまま返すとき利用者に何も知らせない

## 背景

[Issue #9](../closed/ISSUE-9_doctor%20が%20APIPA%20アドレスを健全と判定する/issue.md) で doctor の IP チェックが
APIPA (`169.254.0.0/16`) を FAIL 側へ倒るようにしたが、同じ `pick_ipv4` を使う `cmd_resolve_ip` は
手を付けていない。あちらは「Parallels が報告した IP をそのまま返す」観測に徹する契約なので、
判定を混ぜると ProxyCommand 経由の接続の失敗様式まで変わる、というのが Issue #9 での判断だった。

その判断自体は変えない。ただし記録していたのは「判定を入れて exit 1 にする」対「純粋な観測に
徹する」の二択だけで、**stdout と exit code を一切変えずに stderr へ 1 行出す**という中間の形を
評価していなかった。

`references/ssh-config.template` の ProxyCommand は接続のたびに `winvm resolve-ip` を呼ぶ。
APIPA を掴んでいる間は、その値が ssh の接続先になって不透明にタイムアウトする。これは
Issue #9 が記録した誤診の入口 (「SSH が connect timeout する」から調べ始めてしまう) が
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
  掴んで即死し、今と別の壊れ方になる。これは Issue #9 で棄却済み)
- 文面は doctor を見ろというポインタだけにする。DHCP の話を再掲すると doctor の hint と
  二重管理になる

## タスク

- [ ] `test_winvm.py` に「resolve-ip が APIPA のとき stdout は IP のみ・exit 0 のまま、
      stderr に警告が出る」テストを追加する。stdout に警告が混ざらないことを negative case で pin する
- [ ] 追加したテストが現行実装で赤くなることを確認する
- [ ] `cmd_resolve_ip` に警告を実装する。接頭辞は `警告:` (終端しない通知。規約は
      `StderrMessagePrefix` テストが canonical)
- [ ] 変異注入で確認する: 警告の分岐を外すと追加したテストが赤くなること。
      あわせて警告を stdout 側へ出す変異で、stdout を pin した assertion が赤くなること
- [ ] ControlMaster を使っている場合に警告が何回出るかを実機で確認する。毎接続で出ると
      うるさいので、master 確立時の 1 回で済むなら現状のままでよい

## 関連

- `skills/devops/windows-vm-verification/winvm.py` — `cmd_resolve_ip`、`pick_ipv4`
- `skills/devops/windows-vm-verification/references/ssh-config.template` — ProxyCommand が
  接続のたびに resolve-ip を呼ぶ
- [Issue #9 (closed): doctor が APIPA アドレスを健全と判定する](../closed/ISSUE-9_doctor%20が%20APIPA%20アドレスを健全と判定する/issue.md)
  — 本 Issue の親にあたる判断。doctor 側は解決済みで、残っているのは resolve-ip 経路だけ
- [Issue #3: VM 引き当ての前段が複数のサブコマンドに逐語で複製されている](../ISSUE-3_VM%20引き当ての前段が複数のサブコマンドに逐語で複製されている/issue.md)
  — `cmd_resolve_ip` を触る点で作業範囲が重なる。先に #3 を片付けると本 Issue の変更点が 1 箇所で済む
