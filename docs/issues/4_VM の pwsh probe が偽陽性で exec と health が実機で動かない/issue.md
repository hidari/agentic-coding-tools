---
status: open
---

# fix: VM の pwsh probe が偽陽性で exec と health が実機で動かない

## 背景

PR #3 のマージ前レビューで発見した。**実機で確認済み**。

`winvm exec` と `winvm health` は `where pwsh >nul 2>nul` で pwsh(7) の有無を確かめてから
`.ps1` を転送して実行する。この probe が通るのに、本実行が失敗する状態が存在する。

### 実測 (2026-08-10、example-vm)

```
$ ssh example-vm "where pwsh"
C:\Users\<name>\AppData\Local\Microsoft\WindowsApps\pwsh.exe

$ ssh example-vm 'pwsh -NoProfile -Command "Write-Output PWSHOK"'
(CP932 28 バイト) -> 「アクセスが拒否されました。」

$ ssh example-vm 'if exist "C:\Program Files\PowerShell\7\pwsh.exe" (echo FOUND) else (echo NOTFOUND)'
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

### 影響範囲 (発見時点)

`exec` は PR #3 で追加した新機能で、発見時点では **live smoke が行われていなかった**。
Issue #1 の対応記録にある実機 A/B は `winvm run` (cmd.exe 経路) で行われたもので、
pwsh 経路は通っていなかった。`health` は既存機能だが同じ probe を使うため同じ状態にあった。

live smoke は本 Issue の調査中に実施した (下の調査記録を参照)。残っているのは probe の
設計問題と、VM を作り直したときの再発防止である。

## 調査記録 (2026-08-10): 原因は MSIX 形式だった

「未導入」と書いたのは誤りだった。pwsh 7.6.4 は入っていたが **MSIX (Store 形式)** で、
SSH の非対話セッションから起動できない形だった。

```
$ winget list --id Microsoft.PowerShell
PowerShell  Microsoft.PowerShell  7.6.4.0  winget

$ powershell -NoProfile -Command "Get-AppxPackage -Name Microsoft.PowerShell"
InstallLocation : C:\Program Files\WindowsApps\Microsoft.PowerShell_7.6.4.0_arm64__8wekyb3d8bbwe
```

- 対話セッション (昇格) では起動する。SSH からは alias 経由で「アクセスが拒否されました」、
  実体パス直打ちで「指定されたプログラムは実行できません」
- SSH セッションは **昇格済み** (High Mandatory Level / Administrators) なので、権限では
  なく MSIX のパッケージ活性化コンテキストが原因
- `winget install --id Microsoft.PowerShell --installer-type msi` は ARM64 では
  `No applicable installer found`。winget 経由では MSI が入らない

### 対応

GitHub Releases の `PowerShell-7.6.4-win-arm64.msi` を `winvm push` で投入し `msiexec` で
導入した。`C:\Program Files\PowerShell\7\pwsh.exe` が配置され、PATH でも WindowsApps より
先に来るため素の `pwsh` が SSH から解決・起動できるようになった。

**多重化された SSH の PATH は古いまま**なので、導入直後の `where pwsh` は依然 alias を
返す。`ssh -O exit <host>` で ControlMaster を落として張り直すと正しく解決する。

### live smoke の結果 (全 6 件 OK)

| ケース | 結果 |
|---|---|
| `exec` 成功 (cmdlet) | exit 0、出力が返る |
| `exec` 非 0 の伝搬 (`cmd /c exit 7`) | exit 7 |
| `exec` パイプが cmd に食われない | exit 0 |
| `exec` クォート付き引数が落ちない | exit 0、`a b  c` が原文で残る |
| `exec` 日本語エラー (CP932) で落ちない | exit 1 で正常な失敗報告 |
| `health` | exit 0 |

あわせて `push` (108 MB + 親ディレクトリ自動作成 + サイズ照合)、`pull` (SHA256 が原本と
一致)、`screenshot` (1.6 MB PNG + 親ディレクトリ自動作成) も実機で通した。

一時 `.ps1` の後始末も確認した。VM に残っていたのは修正前の固定名 `winvm_exec.ps1` だけで、
一意名になった今回の実行分は 1 つも残っていない。

## タスク

- [x] VM の pwsh を SSH から使える形にする (MSI を導入)
- [x] `winvm exec` の full chain を live smoke する
- [x] `winvm health` を live smoke する
- [ ] probe を「PATH にあるか」から「起動できるか」へ変える。案: `pwsh -NoProfile
      -Command "exit 0"` の exit code を見る。往復数は変わらず、判定の意味だけが変わる
- [ ] `PWSH_PROBE_ERROR` の文面に「Store の alias stub は使えない」ことを足す。
      MSI 導入前の VM では probe が通ってしまい、この文面自体が表示されなかった
- [ ] VM を作り直したときに同じ状態へ戻らないようにする。`references/windows-bootstrap.md` が
      `winget install --id Microsoft.PowerShell` を案内しており、ARM64 ではこれで MSIX が入る
      ので同じ問題が再発する。MSI を明示的に入れる手順へ変える
- [ ] 実測に反証されている記述を訂正する。`references/windows-bootstrap.md` と
      `references/troubleshooting.md` が「`%LOCALAPPDATA%\Microsoft\WindowsApps\pwsh.exe` の
      alias 経由で SSH セッションからも起動できる」と書いており、前者には「(実測)」まで付いて
      いる。本 Issue の実測 (非対話セッションからはアクセス拒否。上の調査記録) が反証している。
      実測を名乗る記述は疑われないぶん、残すと次に踏んだ人を確実に誤誘導する

## 関連

- `skills/devops/windows-vm-verification/winvm.py` — `pwsh_probe_command`、
  `PWSH_PROBE_ERROR`、`cmd_health`、`cmd_exec`
- Issue [#7](../7_リモート往復数を減らす/issue.md) — probe を本実行に畳む案があり、
  本 Issue と設計が結合する。先に本 Issue を解決すること
- Issue [#9 (closed)](../closed/9_doctor%20が%20APIPA%20アドレスを健全と判定する/issue.md) — 同種の欠陥。
  あちらは doctor が APIPA アドレス (DHCP 失敗) を OK と判定する件で、どちらも
  「検査が通ること」を「機能していること」の証拠として扱ってしまう類型。
  対象のコードは別 (pwsh probe と IP チェック) なので独立に直せる
