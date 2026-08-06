# Windows 側のブートストラップ

新しい Windows VM に SSH 経路を作る手順。ゲストに GUI で触らず、ホストの `prlctl exec` だけで完了する (Parallels Desktop 26.4.0 + Windows 11 ARM で実測)。

VM 1 台につき 1 回しか要らないので `SKILL.md` からは分離してある。

`prlctl exec` は **NT AUTHORITY\SYSTEM** かつ Administrators として走るので、管理者権限が要る操作もそのまま通る。

## 0. `prlctl exec` を通す

隔離が有効だと `prlctl exec` は "Unable to open new session in this virtual machine" で失敗する。

```bash
prlctl set "<vm>" --isolate-vm off
prlctl exec "<vm>" cmd.exe /c ver
```

**コマンドはトークンを分割して渡す。** `prlctl exec "<vm>" "cmd.exe /c ver"` のようにプログラム名まで 1 文字列に含めると、エラーも出さず exit 2 で無出力のまま失敗する。

## 1. OpenSSH Server を導入して起動する

```bash
prlctl exec "<vm>" powershell -NoProfile -Command "Add-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0'"
prlctl exec "<vm>" powershell -NoProfile -Command "Set-Service -Name sshd -StartupType Automatic; Start-Service sshd"
```

## 2. 公開鍵を置く

ログインさせるユーザーが Administrators のメンバーなら、鍵は `~/.ssh/authorized_keys` ではなく `C:\ProgramData\ssh\administrators_authorized_keys` に置く (既定の `sshd_config` に `Match Group administrators` があるため)。ACL は SYSTEM と Administrators だけに絞る。他に書ける主体があると sshd がファイルを無視する。

```bash
prlctl exec "<vm>" powershell -NoProfile -Command "[IO.File]::WriteAllText('C:\ProgramData\ssh\administrators_authorized_keys','<公開鍵1行>' + [Environment]::NewLine); icacls.exe 'C:\ProgramData\ssh\administrators_authorized_keys' /inheritance:r /grant '*S-1-5-32-544:F' /grant '*S-1-5-18:F'"
```

ACL は日本語 Windows でもグループ名がローカライズされうるので、名前ではなく SID (`*S-1-5-32-544` = Administrators、`*S-1-5-18` = SYSTEM) で指定する。

## 3. ファイアウォールを開ける

OpenSSH Server の導入で規則 `OpenSSH-Server-In-TCP` は作られるが、**Private プロファイル限定**で入る。Parallels の共有ネットワークは Windows から Public と判定されるため、そのままでは通らない。

ネットワーク全体を Private に格下げすると探索や共有まで一括で緩むので、規則側だけを広げて送信元を Parallels のサブネットに限定する。

サブネットはホスト側のブリッジアドレスから確認する。

```bash
ifconfig | grep -B5 "10.211.55" | grep -E "^bridge|inet "
```

得られたネットワークを規則へ渡す (既定は `10.211.55.0/24`、ホストは `.2`)。

```bash
prlctl exec "<vm>" powershell -NoProfile -Command "Set-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -Profile Any -RemoteAddress '<subnet>'"
```

## 4. pwsh(7) を入れる

`winvm health` は pwsh(7) を必須とする。

```bash
ssh <alias> "winget install --id Microsoft.PowerShell --source winget --accept-package-agreements --accept-source-agreements --silent --disable-interactivity"
```

Microsoft Store (MSIX) 版が入っている場合、実体は `C:\Program Files\WindowsApps\...` に置かれ `C:\Program Files\PowerShell\7\pwsh.exe` は存在しない。それでも `%LOCALAPPDATA%\Microsoft\WindowsApps\pwsh.exe` の alias 経由で SSH セッションからも起動できる (実測)。**`C:\Program Files\PowerShell` の有無で導入判定しない。**

## 5. git を入れる

`winvm run` は VM 側の git を使う。

```bash
ssh <alias> "winget install --id Git.Git --source winget --accept-package-agreements --accept-source-agreements --silent --disable-interactivity"
```

## 6. 確認する

```bash
winvm doctor --vm "<vm>" --host <alias>
```

全項目が `[ OK ]` になれば完了。`[FAIL]` が出たら `troubleshooting.md` を参照する。
