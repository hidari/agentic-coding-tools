---
status: open
---

# fix: VM の pwsh probe が偽陽性で exec と health が実機で動かない

## 背景

PR #3 のマージ前レビューで発見した。**実機で確認済み**。

`winvm exec` と `winvm health` は `where pwsh >nul 2>nul` で pwsh(7) の有無を確かめてから
`.ps1` を転送して実行する。この probe が通るのに、本実行が失敗する状態が存在する。

### 実測 (2026-08-10、relay-winvm)

```
$ ssh relay-winvm "where pwsh"
C:\Users\sho\AppData\Local\Microsoft\WindowsApps\pwsh.exe

$ ssh relay-winvm 'pwsh -NoProfile -Command "Write-Output PWSHOK"'
(CP932 28 バイト) -> 「アクセスが拒否されました。」

$ ssh relay-winvm 'if exist "C:\Program Files\PowerShell\7\pwsh.exe" (echo FOUND) else (echo NOTFOUND)'
NOTFOUND
```

PATH にあるのは Microsoft Store の実行エイリアス (stub) だけで、pwsh 7 本体は入っていない。
stub は非対話セッションから叩くとアクセス拒否される。

### 何が問題か

問題は 2 層ある。

1. **VM の状態**: pwsh 7 が入っていないので `exec` と `health` が動かない
2. **probe の設計**: `where` は「PATH にあるか」しか見ていない。起動できるかは見ていない。
   そのため probe は通り、親切な `PWSH_PROBE_ERROR` (「pwsh 未導入の可能性、
   winget install で導入する」) は**表示されない**。利用者に届くのはモジバケした
   アクセス拒否メッセージだけになる

probe が buy している情報は SSH 到達性と PATH 有無に限られ、その両方は本実行の失敗からも
同じだけ得られる。つまり現状の probe は 1 往復を払って何も守っていない。

### 影響範囲

`exec` は PR #3 で追加した新機能で、**live smoke が行われていない**。Issue #1 の対応記録に
ある実機 A/B は `winvm run` (cmd.exe 経路) で行われたもので、pwsh 経路は通っていない。

`health` は既存機能だが同じ probe を使うため同じ状態にある。

## タスク

- [ ] VM に pwsh 7 を導入する (`winget install --id Microsoft.PowerShell`)。Store の
      alias stub ではなく `C:\Program Files\PowerShell\7\pwsh.exe` が入ることを確認する
- [ ] 導入後に `winvm exec` の full chain を live smoke する
      (成功ケース / リモート非 0 の伝搬 / パイプを含むコマンド / 後始末が消えること)
- [ ] `winvm health` も同様に live smoke する
- [ ] probe を「PATH にあるか」から「起動できるか」へ変える。案: `pwsh -NoProfile
      -Command "exit 0"` の exit code を見る。往復数は変わらず、判定の意味だけが変わる
- [ ] alias stub が PATH にある状態を再現できるなら、その状態で probe が落ちることを
      テストで pin する (再現できないなら probe の argv だけ pin して理由を書く)
- [ ] `PWSH_PROBE_ERROR` の文面に「Store の alias stub は使えない」ことを足すか検討する

## 関連

- `skills/devops/windows-vm-verification/winvm.py` — `pwsh_probe_command` (663 行)、
  `PWSH_PROBE_ERROR`、`cmd_health`、`cmd_exec`
- Issue [#7](../7_リモート往復数を減らす/issue.md) — probe を本実行に畳む案があり、
  本 Issue と設計が結合する。先に本 Issue を解決すること
