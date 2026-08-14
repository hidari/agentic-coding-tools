---
name: windows-vm-verification
description: Parallels Desktop 上の Windows 検証 VM を繋ぐ/調べる/検証する generic CLI (winvm)。SSH 越しの NTFS/health 確認、cfg(windows) コードの remote 検証 (ローカル変更を scp 同期して remote コマンド実行)、prlctl からの IP 解決、繋がらないときのホスト側診断 (doctor)、画面のスクリーンショット (screenshot)、任意ファイルの転送 (push/pull)、クォート/パイプ安全な任意 pwsh コマンド実行 (exec) を扱う。Parallels Desktop の Windows VM を操作・検証する時に使う。
---

# Windows VM 検証スキル (winvm)

## いつ使うか

- SSH 経由で VM の NTFS 健全性・開発ツールチェーンを確認したい
- macOS 側の変更を Windows VM に同期して `cfg(windows)` コードを検証したい
- VM に繋がらず、原因がホスト側 (VM 未起動 / 隔離設定 / Tools) かゲスト側かを切り分けたい
- IP が変わって SSH 接続先が不明になった
- GUI アプリの QA で VM の画面を目視したい (`screenshot`)
- CI が焼いた MSI などの任意ファイルを VM と往復させたい (`push` / `pull`)
- パイプやクォートを含む任意の PowerShell コマンドを VM で実行したい (`exec`)

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

`prlctl list -a -i -j` の JSON から該当 VM の `Network.ipAddresses` を読み、`type` が `ipv4` のエントリを標準出力に出す。解決できなければ非 0 終了。

失敗時は理由を区別して出す（VM 未登録なら登録済みの名前一覧、停止中なら `status=stopped` と起動コマンド）。

### `doctor`

```
winvm doctor --vm <名前 or UUID> [--host <alias>]
```

VM が使える状態かをホスト側から観測する。各項目は **判定だけでなく観測値**を出す（「緑だから健全」ではなく「何をどう観測してその判定か」を読めるようにするため）。

| 項目 | 見ているもの |
|---|---|
| `VM` | `prlctl list -a -i -j` に該当があるか。名前と UUID |
| `status` | `running` かどうか |
| `IP` | `Network.ipAddresses` の `type: ipv4` エントリ。**APIPA (link-local) は DHCP 失敗なので FAIL** |
| `Parallels Tools` | 同じレコードの `GuestTools: state=... version=...` |
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

**制約**: `--` の後のリモートコマンドは argv を空白で連結して組み立てるため、クォートが落ちる。`|` や `&` を含めると cmd.exe 側のシェル演算子として解釈されるので、複雑なコマンドは `winvm exec` を使う（`.ps1` 化・転送・後始末を自動でやる）。

### `screenshot`

```
winvm screenshot --vm <名前 or UUID> --out <macOS 側のパス>
```

`prlctl capture` でホスト側から VM の画面を PNG に撮る。保存先の親ディレクトリは自動作成し、撮れたファイルが 0 バイトでないことまで確認してから保存先とサイズを報告する（rc 0 を「撮れた」と読み替えない）。VM が停止中なら `status=stopped` と起動コマンドを出して非 0 終了。

**SSH 方式へ「改善」しないこと**。Windows の SSH セッションは session 0 で、対話ユーザーのデスクトップは別セッションにある。SSH 越しに PowerShell でスクリーンキャプチャを撮るとこのセッション分離のため黒画面になる。`prlctl capture` はホスト側から VM の画面を直接撮るので分離の影響を受けない（`-f,--file` は prlctl 26.4.1 の `capture --help` で実測した唯一のオプション）。

### `push` / `pull`

```
winvm push --host <alias> <local path> <remote path>
winvm pull --host <alias> <remote path> <local path>
```

任意ファイル 1 個を scp で転送する。CI が焼いた MSI の投入や VM 側の成果物・ログの回収に使う（`run` は git 差分の同期が前提なので任意ファイルには使えない）。

- リモートパスは `/` 区切りで書ける（`--repo` と同じ扱い。cmd.exe に渡す箇所だけ内部で `\` に変換）
- 転送先の親ディレクトリ（push はリモート、pull はローカル）は自動作成
- 転送後にサイズを照合し、一致しなければ非 0 終了（scp の rc 0 を「完了」と読み替えない）
- リモートのサイズ問い合わせは不在を ASCII の目印に固定してある（値は `winvm.py` の `REMOTE_MISSING_MARK` が canonical）。素の `for %I ... %~zI` は対象不在で裸の `echo` に落ち、cmd が「ECHO は \<ON\> です。」を CP932 で返す（実測）ため、localized な出力を判定に混ぜない

### `exec`

```
winvm exec --host <alias> -- <pwsh コマンド>
```

任意の PowerShell コマンドを VM で実行する。コマンドを一時 `.ps1` に書き出して `scp` で転送し、pwsh(7) の `-File` で実行して終了後に削除する（`health` と同じ経路）。コマンドはシェルを経由せずファイルへ書かれるので、`run` の「argv を空白連結するためクォートが落ちる」制約が無く、パイプ（`|`）も cmd.exe に解釈されない。

- リモートの exit code をそのまま winvm の exit code として返す。`pwsh -File` はスクリプトが exit しないと native コマンドの失敗を 0 に潰すため、生成する `.ps1` の末尾で `$LASTEXITCODE`（native）を優先しつつ cmdlet の失敗（`$?` が偽）も非 0 へ倒して明示的に exit する
- 転送した一時 `.ps1` は実行が失敗しても削除する（ローカル・リモートとも）
- 出力文字化け対策の `[Console]::OutputEncoding = UTF8` は `health` と同じく生成スクリプトの冒頭で設定する

## 接続セットアップ

`~/.ssh/config` に Host エントリを 1 つ足す。IP は `ProxyCommand` から `winvm resolve-ip` を呼んで接続のたびに解決するので、IP が変わっても設定を直す必要がない。エントリの全体は `references/ssh-config.template` にある（そのまま写して `<alias>` / `<vm>` / `<user>` を置換する）。

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

3. Windows 側を整える（`references/windows-bootstrap.md`。OpenSSH Server の導入・鍵の配置・ファイアウォール・pwsh(7) と git の導入まで、ホストの `prlctl exec` だけで完結する）

4. `references/ssh-config.template` を写して `~/.ssh/config` に Host エントリを追加

5. 接続確認

   ```bash
   winvm doctor --vm "<vm>" --host <alias>
   ```

## トラブルシューティング

繋がらないときはまず `winvm doctor --vm <id> --host <alias>` を実行する。症状と対処の一覧は `references/troubleshooting.md` にある。
