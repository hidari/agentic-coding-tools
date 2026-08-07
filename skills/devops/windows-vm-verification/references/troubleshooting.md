# Windows VM 検証 — トラブルシューティング

繋がらないときはまず `winvm doctor --vm <id> --host <alias>` を実行する。下の表の症状のうちホスト側で観測できるものは doctor が観測値付きで出す。

| 症状 | 原因 | 対処 |
|---|---|---|
| `prlctl exec` が "Unable to open new session in this virtual machine" | VM がホストから隔離されている (`config.pvs` の `<IsolatedVm>1</IsolatedVm>`) | `prlctl set "<vm>" --isolate-vm off`。エラー文は「起動未完了」「Tools が古い」も挙げるが、Tools のバージョンが Parallels Desktop 本体と一致していてデスクトップまで起動しているなら隔離が原因 |
| `prlctl exec` が exit 2 で何も出さない | コマンドをプログラム名ごと 1 文字列で渡している | トークンを分割して渡す。`prlctl exec "<vm>" cmd.exe /c ver` は通り、`prlctl exec "<vm>" "cmd.exe /c ver"` は黙って失敗する |
| `winvm resolve-ip` が "VM が見つかりません" | 名前の不一致 (部分一致はしない) | エラーに出る登録済み名の一覧から選ぶか `prlctl list -a` で確認する。UUID でも指定できる |
| `winvm resolve-ip` が "IP を解決できません (status=stopped)" | VM が停止している | `prlctl start "<vm>"` で起動してから再実行 |
| SSH が connect timeout する (sshd は Running) | ファイアウォール規則 `OpenSSH-Server-In-TCP` が Private プロファイル限定で、Parallels の共有ネットワークは Public 判定 | `Set-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -Profile Any -RemoteAddress '10.211.55.0/24'`。ネットワーク全体を Private に落とす方法もあるが探索と共有まで緩むので規則側を広げる |
| SSH が Permission denied (公開鍵は配置済み) | ログインユーザーが Administrators のメンバーで、鍵が `~/.ssh/authorized_keys` にある | `C:\ProgramData\ssh\administrators_authorized_keys` に置き、ACL を SYSTEM と Administrators だけに絞る (`icacls ... /inheritance:r /grant '*S-1-5-32-544:F' /grant '*S-1-5-18:F'`)。他に書ける主体があると sshd はファイルを無視する |
| SSH banner exchange timeout | VM 起動直後で sshd がまだ起動していない | 30〜60 秒待ってから再接続。`ssh -o ConnectTimeout=60 <alias>` で待機時間を伸ばす |
| SSH known_hosts mismatch | VM 再構築で HostKey が変わった | `~/.ssh/known_hosts` から `HostKeyAlias` に対応するエントリを削除。`StrictHostKeyChecking accept-new` 設定済みであれば次回接続時に自動登録される |
| PowerShell 出力が文字化け | cmd.exe 経由の cp932 エンコード問題 | `winvm health` / `winvm run` は `[Console]::OutputEncoding = UTF8` を自動設定済み。手動実行時は同様の設定を追加する |
| 転送した `.ps1` が「スクリプトの実行が無効になっている」で弾かれる | WinPS 5.1 の ExecutionPolicy が Restricted | `pwsh`(7) で実行する。pwsh は RemoteSigned で、scp 転送物には Mark-of-the-Web が付かないため通る。`-ExecutionPolicy Bypass` で上書きしない |
| `winvm health` が "pwsh(7) を確認できませんでした" で停止 | VM 未起動 / SSH 未到達、または pwsh(7) 未導入 | `winvm doctor --vm <id> --host <alias>` で切り分ける。到達できて pwsh 未導入なら `winget install --id Microsoft.PowerShell` |
| `C:\Program Files\PowerShell` が無いのに winget は「導入済み」と言う | Microsoft Store (MSIX) 版が入っている | 実体は `C:\Program Files\WindowsApps\Microsoft.PowerShell_<ver>_arm64__8wekyb3d8bbwe\`。`%LOCALAPPDATA%\Microsoft\WindowsApps\pwsh.exe` の alias 経由で SSH からも起動できるので MSI を入れ直す必要はない。パスの有無で導入判定しないこと |
| cmd.exe の `for %C in (...) do @(... >nul 2>nul && ...)` が全件「見つからない」を返す | 括弧内でリダイレクトの解釈が変わり `where` の stderr が漏れている | 検査には PowerShell の `Get-Command` を使う。あるいは 1 コマンドずつ実行する。必ず見つかるはずの対照 (`powershell` など) を混ぜておくと、検査自体の故障に気づける |

## Parallels に無い失敗モード

旧環境 (VMware Fusion) には、バンドル内に `*.lck` ディレクトリが残ると「ディレクトリが空ではありません」で VM が起動しなくなる失敗モードがあり、それを除去する `recover` サブコマンドがあった。

Parallels の `.pvm` バンドルには対応するものが無い（バンドル直下にあるのは 0 バイトの `vm.lock` ファイル 1 つで、`lsof` でも保持プロセスが見えない）。VM の生存は per-VM の `prl_vm_app --uuid {UUID}` プロセスが表す。固まった VM を落とす手段は Parallels 自身が `prlctl stop <vm> --kill` として持っている。

そのため `recover` は移植せず削除した。同等の復旧が要る場面では `prlctl stop <vm> --kill` を使う。
