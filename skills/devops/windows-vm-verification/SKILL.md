---
name: windows-vm-verification
description: Parallels Desktop 上の Windows 検証 VM を繋ぐ/調べる/検証する generic CLI (winvm)。SSH 越しの NTFS/health 確認、cfg(windows) コードの remote 検証 (ローカル変更を scp 同期して remote コマンド実行)、prlctl からの IP 解決、繋がらないときのホスト側診断 (doctor) を扱う。Parallels Desktop の Windows VM を操作・検証する時に使う。
---

# Windows VM 検証スキル (winvm)

## いつ使うか

- SSH 経由で VM の NTFS 健全性・開発ツールチェーンを確認したい
- macOS 側の変更を Windows VM に同期して `cfg(windows)` コードを検証したい
- VM に繋がらず、原因がホスト側 (VM 未起動 / 隔離設定 / Tools) かゲスト側かを切り分けたい
- IP が変わって SSH 接続先が不明になった

## winvm CLI 概要

`winvm.py` は uv で実行する単一ファイル CLI。設定は **環境変数**でも **引数**でも渡せ、引数が優先する。

| 環境変数 | 対応引数 | 意味 |
|---|---|---|
| `WINVM_VM` | `--vm` | Parallels の VM 名または UUID (`prlctl list -a` で確認) |
| `WINVM_HOST` | `--host` | SSH ホスト名 (ssh config alias) |
| `WINVM_REPO` | `--repo` | VM 上のリポジトリパス。`/` 区切りで可（内部で `\` に変換。例 `C:/Users/user/repo`） |
| `WINVM_BASE` | `--base` | 差分基点ブランチ/コミット |

VM の指定は名前でも UUID でも通る。`prlctl` が受け付ける識別子と同じ集合に揃えてあるので、`winvm` と `prlctl` を混ぜて使っても指す VM がずれない。名前は完全一致で、部分一致はしない。

## サブコマンド

### `resolve-ip`

```
winvm resolve-ip --vm <名前 or UUID>
```

`prlctl list -a -f -j` の JSON から該当 VM の `ip_configured` を読み、IPv4 を標準出力に出す。解決できなければ非 0 終了。

失敗時は理由を区別して出す（VM 未登録なら登録済みの名前一覧、停止中なら `status=stopped` と起動コマンド）。

### `doctor`

```
winvm doctor --vm <名前 or UUID> [--host <alias>]
```

VM が使える状態かをホスト側から観測する。各項目は **判定だけでなく観測値**を出す（「緑だから健全」ではなく「何をどう観測してその判定か」を読めるようにするため）。

| 項目 | 見ているもの |
|---|---|
| `VM` | `prlctl list -a -f -j` に該当があるか。名前と UUID |
| `status` | `running` かどうか |
| `IP` | `ip_configured` から取れた IPv4 |
| `Parallels Tools` | `prlctl list -i` の `GuestTools: state=... version=...` |
| `host isolation` | バンドル内 `config.pvs` の `<IsolatedVm>`。**on だと `prlctl exec` が通らない** |
| `prlctl exec` | 実際に `cmd.exe /c ver` をゲストで実行できるか |
| `ssh <alias>` | `--host` 指定時のみ。SSH が張れるか |

`[ -- ]` は「確認できなかった」で、OK でも NG でもない。読めなかったことを「健全」に読み替えない。

FAIL があれば exit 1、無ければ exit 0。

### `health`

```
winvm health --host <alias> [--repo <winpath>] [--check-tools node,cargo,...]
```

SSH 越しに以下を確認する:

- NTFS ボリューム健全性 (`chkdsk` 相当)
- dirty bit の有無
- NTFS 破損イベント (Windows Event Log)
- 予期しないシャットダウンイベント
- （オプション）`--check-tools` に渡した任意のコマンド名（例 `node,pnpm,cargo,rustc`）の存在確認とバージョン
- （オプション）リポジトリの HEAD

**実装の注意点**:

- PowerShell スクリプトは `scp` で転送し pwsh(7) の `-File` で実行する。pwsh は RemoteSigned かつ scp 転送物に Mark-of-the-Web が付かないため `-ExecutionPolicy Bypass` 無しで実行できる（WinPS 5.1 の Restricted を Bypass で上書きする多層防御の穴を避ける）
- pwsh は必須。同じ `.ps1` を `powershell`(5.1) に渡すと ExecutionPolicy で弾かれる（実測）
- `-EncodedCommand` は cmd.exe の 8191 文字制限に引っかかる長いコマンドで失敗するため使用しない
- 出力文字化けを防ぐため `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8` をスクリプト冒頭に設定する（SSH 越しの `OutputEncoding` は pwsh でも既定 shift_jis のため PS バージョン非依存で必要）
- ラベルは ASCII にして cp932 の問題を回避する

### `run`

```
winvm run --host <alias> --repo <winpath> [--base <ref>] [--skip-when-no-changes] -- <remote cmd>
```

macOS 上のローカル変更を Windows VM に同期して remote コマンドを実行する。`cfg(windows)` 対象コードの検証に使う。

1. VM の現在 HEAD を差分基点として計算（ローカルで解決できなければ `--base`）
2. VM を git でプリスティン状態にリセット（`git checkout -- . && git clean -fd`）
3. ローカルで削除・リネームされたファイルを VM 側でも削除（`--no-renames --diff-filter=D` で列挙。reset は VM HEAD の tracked ファイルを復元するため、明示削除しないと stale ファイルが tsc/cargo に拾われ偽陰性になる）
4. `git delta`・working tree・untracked の変更ファイルを `scp` で同期（親ディレクトリは自動作成）
5. 指定のリモートコマンドを実行（カレントディレクトリは `--repo` に自動 `cd` 済み。リモートコマンド側で `cd` 不要）

`--skip-when-no-changes`: 差分がない場合はスキップ（CI 的ユースケース）

`--base <ref>`: VM の HEAD をローカルで解決できないときのフォールバック差分基点（既定 `main`）。通常は VM の現在 HEAD を自動基点にするので指定不要。

## 接続セットアップ

`~/.ssh/config` に `ProxyCommand` として `winvm resolve-ip` を組み込むと、SSH クライアントが接続のたびに現在の IP を解決する。最小形は次の 1 行（`<vm>` は VM 名か UUID に置換。`User` / `IdentityFile` / `HostKeyAlias` を含む完全形は `references/ssh-config.template`）:

```
ProxyCommand sh -c 'exec nc "$(winvm resolve-ip --vm "<vm>")" 22'
```

### 初回セットアップ手順

1. `winvm.py` を PATH が通った場所に配置（または `winvm` シェル関数/alias を設定）

   ```bash
   # 例: uv で実行する関数を .zshrc などに追加
   winvm() { uv run /path/to/winvm.py "$@"; }
   ```

2. SSH 鍵ペアを生成（既存のものがあれば流用可）

   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_winvm -C "winvm"
   ```

3. Windows 側を整える（下の「Windows 側のブートストラップ」）

4. `references/ssh-config.template` を参考に `~/.ssh/config` に Host エントリを追加

5. 接続確認

   ```bash
   winvm doctor --vm "<vm>" --host <alias>
   ```

## Windows 側のブートストラップ

新しい Windows VM に SSH 経路を作る手順。ゲストに GUI で触らず、ホストの `prlctl exec` だけで完了する（Parallels Desktop 26.4.0 + Windows 11 ARM で実測）。

`prlctl exec` は **NT AUTHORITY\SYSTEM** かつ Administrators として走るので、管理者権限が要る操作もそのまま通る。

### 0. `prlctl exec` を通す

隔離が有効だと `prlctl exec` は "Unable to open new session in this virtual machine" で失敗する。

```bash
prlctl set "<vm>" --isolate-vm off
prlctl exec "<vm>" cmd.exe /c ver
```

**コマンドはトークンを分割して渡す。** `prlctl exec "<vm>" "cmd.exe /c ver"` のようにプログラム名まで 1 文字列に含めると、エラーも出さず exit 2 で無出力のまま失敗する。

### 1. OpenSSH Server を導入して起動する

```bash
prlctl exec "<vm>" powershell -NoProfile -Command "Add-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0'"
prlctl exec "<vm>" powershell -NoProfile -Command "Set-Service -Name sshd -StartupType Automatic; Start-Service sshd"
```

### 2. 公開鍵を置く

ログインさせるユーザーが Administrators のメンバーなら、鍵は `~/.ssh/authorized_keys` ではなく `C:\ProgramData\ssh\administrators_authorized_keys` に置く（既定の `sshd_config` に `Match Group administrators` があるため）。ACL は SYSTEM と Administrators だけに絞る（他に書ける主体があると sshd がファイルを無視する）。

```bash
prlctl exec "<vm>" powershell -NoProfile -Command "[IO.File]::WriteAllText('C:\ProgramData\ssh\administrators_authorized_keys','<公開鍵1行>' + [Environment]::NewLine); icacls.exe 'C:\ProgramData\ssh\administrators_authorized_keys' /inheritance:r /grant '*S-1-5-32-544:F' /grant '*S-1-5-18:F'"
```

ACL は日本語 Windows でもグループ名がローカライズされうるので、名前ではなく SID (`*S-1-5-32-544` = Administrators、`*S-1-5-18` = SYSTEM) で指定する。

### 3. ファイアウォールを開ける

OpenSSH Server の導入で規則 `OpenSSH-Server-In-TCP` は作られるが、**Private プロファイル限定**で入る。Parallels の共有ネットワークは Windows から Public と判定されるため、そのままでは通らない。

ネットワーク全体を Private に格下げすると探索や共有まで一括で緩むので、規則側だけを広げて送信元を Parallels のサブネットに限定する:

```bash
prlctl exec "<vm>" powershell -NoProfile -Command "Set-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -Profile Any -RemoteAddress '10.211.55.0/24'"
```

サブネットは `ifconfig | grep 10.211.55` などでホスト側のブリッジアドレスから確認する（既定は `10.211.55.0/24`、ホストは `.2`）。

### 4. pwsh(7) を入れる

`winvm health` は pwsh(7) を必須とする。

```bash
ssh <alias> "winget install --id Microsoft.PowerShell --source winget --accept-package-agreements --accept-source-agreements --silent --disable-interactivity"
```

Microsoft Store (MSIX) 版が入っている場合、実体は `C:\Program Files\WindowsApps\...` に置かれ `C:\Program Files\PowerShell\7\pwsh.exe` は存在しない。それでも `%LOCALAPPDATA%\Microsoft\WindowsApps\pwsh.exe` の alias 経由で SSH セッションからも起動できる（実測）。**`C:\Program Files\PowerShell` の有無で導入判定しない。**

### 5. git を入れる

`winvm run` は VM 側の git を使う。

```bash
ssh <alias> "winget install --id Git.Git --source winget --accept-package-agreements --accept-source-agreements --silent --disable-interactivity"
```

## トラブルシューティング

詳細は `references/troubleshooting.md` を参照。
