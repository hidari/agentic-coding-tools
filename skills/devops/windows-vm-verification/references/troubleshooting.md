# Windows VM 検証 — トラブルシューティング

繋がらないときはまず `winvm doctor --vm <id> --host <alias>` を実行する。下の表の症状のうちホスト側で観測できるものは doctor が観測値付きで出す。

| 症状 | 原因 | 対処 |
|---|---|---|
| `prlctl exec` が "Unable to open new session in this virtual machine" | VM がホストから隔離されている (`config.pvs` の `<IsolatedVm>1</IsolatedVm>`) | `prlctl set "<vm>" --isolate-vm off`。エラー文は「起動未完了」「Tools が古い」も挙げるが、Tools のバージョンが Parallels Desktop 本体と一致していてデスクトップまで起動しているなら隔離が原因 |
| `prlctl exec` が exit 2 で何も出さない | コマンドをプログラム名ごと 1 文字列で渡している | トークンを分割して渡す。`prlctl exec "<vm>" cmd.exe /c ver` は通り、`prlctl exec "<vm>" "cmd.exe /c ver"` は黙って失敗する |
| `winvm resolve-ip` が "VM が見つかりません" | 名前の不一致 (部分一致はしない) | エラーに出る登録済み名の一覧から選ぶか `prlctl list -a` で確認する。UUID でも指定できる |
| `winvm resolve-ip` が "IP を解決できません (status=stopped)" | VM が停止している | `prlctl start "<vm>"` で起動してから再実行 |
| SSH が connect timeout する (sshd は Running) | ファイアウォール規則 `OpenSSH-Server-In-TCP` が Private プロファイル限定で、Parallels の共有ネットワークは Public 判定。ただし doctor の IP が `169.254.x.x` なら原因は DHCP 側で、下の APIPA の節が該当する (同じ症状を出すので先に IP 行を見る) | `Set-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -Profile Any -RemoteAddress '10.211.55.0/24'`。ネットワーク全体を Private に落とす方法もあるが探索と共有まで緩むので規則側を広げる |
| SSH が Permission denied (公開鍵は配置済み) | ログインユーザーが Administrators のメンバーで、鍵が `~/.ssh/authorized_keys` にある | `C:\ProgramData\ssh\administrators_authorized_keys` に置き、ACL を SYSTEM と Administrators だけに絞る (`icacls ... /inheritance:r /grant '*S-1-5-32-544:F' /grant '*S-1-5-18:F'`)。他に書ける主体があると sshd はファイルを無視する |
| SSH banner exchange timeout | VM 起動直後で sshd がまだ起動していない | 30〜60 秒待ってから再接続。`ssh -o ConnectTimeout=60 <alias>` で待機時間を伸ばす |
| SSH known_hosts mismatch | VM 再構築で HostKey が変わった | `~/.ssh/known_hosts` から `HostKeyAlias` に対応するエントリを削除。`StrictHostKeyChecking accept-new` 設定済みであれば次回接続時に自動登録される |
| PowerShell 出力が文字化け | cmd.exe 経由の cp932 エンコード問題 | `winvm health` / `winvm run` は `[Console]::OutputEncoding = UTF8` を自動設定済み。手動実行時は同様の設定を追加する |
| 転送した `.ps1` が「スクリプトの実行が無効になっている」で弾かれる | WinPS 5.1 の ExecutionPolicy が Restricted | `pwsh`(7) で実行する。pwsh は RemoteSigned で、scp 転送物には Mark-of-the-Web が付かないため通る。`-ExecutionPolicy Bypass` で上書きしない |
| `winvm health` が "pwsh(7) を確認できませんでした" で停止 | VM 未起動 / SSH 未到達、または pwsh(7) 未導入 | `winvm doctor --vm <id> --host <alias>` で切り分ける。到達できて pwsh 未導入なら `winget install --id Microsoft.PowerShell` |
| `C:\Program Files\PowerShell` が無いのに winget は「導入済み」と言う | Microsoft Store (MSIX) 版が入っている | 実体は `C:\Program Files\WindowsApps\Microsoft.PowerShell_<ver>_arm64__8wekyb3d8bbwe\`。`%LOCALAPPDATA%\Microsoft\WindowsApps\pwsh.exe` の alias 経由で SSH からも起動できるので MSI を入れ直す必要はない。パスの有無で導入判定しないこと |
| cmd.exe の `for %C in (...) do @(... >nul 2>nul && ...)` が全件「見つからない」を返す | 括弧内でリダイレクトの解釈が変わり `where` の stderr が漏れている | 検査には PowerShell の `Get-Command` を使う。あるいは 1 コマンドずつ実行する。必ず見つかるはずの対照 (`powershell` など) を混ぜておくと、検査自体の故障に気づける |

## VM が APIPA になる (DHCP 応答なし)

`winvm doctor` の IP が `169.254.x.x` を返したら DHCP に失敗している。この帯は DHCP から応答が無かったときに OS が自分で振る自己割り当てアドレスで、値が取れていてもネットワークは無い。疑うのは VM 内部ではなくホスト側の Parallels の NAT/DHCP。

実際に踏んだときの原因は、NAT/DHCP デーモン `prl_naptd` がソケットを 1 つも持たないまま生き続けていたこと。プロセス自体は生きているので `ps` には見え、watchdog (`watchdog start 60 20 ... prl_naptd start`) もプロセスの生死しか見ないため、壊れた状態が安定して維持され続けた (実測で 2 日間)。

```bash
# DHCP を誰も listen していないことを確かめる
sudo lsof -nP -iUDP:67

# 対照。正常なら mDNSResponder が返る
sudo lsof -nP -iUDP:5353
```

対照は必ず並べること。`sudo` 無しでは他ユーザのソケットが見えず必ず空になるので、対照が無いと「壊れている」と「そもそも見えていない」を区別できない。

対照と同じ `-i` 形で引くこと。`-p <pid>` 形にすると判定が「cwd や txt の行に混じって UDP 行が無いこと」という absence 検査になり、さらに pid を取り違えたときの出力が「ソケットを持っていない」ときと同じ空になる (存在しない pid はヘッダ行すら出ないことを実測)。

UDP:67 が空だったら、落とす相手を `pgrep -fl prl_naptd` で特定して `sudo kill <pid>` する。watchdog が 60 秒以内に起動し直して DHCP が復旧する。

診断が難しいのは、他の観測点が揃って「正常」を返すため。

- `prlsrvctl net info Shared` は正常に見える。`NAT server:` 行が空なのは正常時もそうなので、サービス停止の根拠にならない (ここを誤読した)
- ホスト側のブリッジは UP で Parallels adapter の IP を保持し、メンバーに VM の tap もいる
- `ps` に `prl_naptd` が見える (生きてはいる)
- アダプタを `--device-disconnect` / `--device-connect` すると bridge の address cache にゲスト MAC が載る (フレームは届いている)

一時退避として bridged (`prlctl set <vm> --device-set net0 --type bridged --iface <host if>`) へ切り替える手もあるが、共有ネットワークを離れると上の表の SSH 関連の行が前提にしている条件 (ファイアウォール規則のスコープと Windows によるネットワークの分類) がどちらも崩れ、その後始末が付いてくる。真因を直す方が早い。

## Parallels に無い失敗モード

旧環境 (VMware Fusion) には、バンドル内に `*.lck` ディレクトリが残ると「ディレクトリが空ではありません」で VM が起動しなくなる失敗モードがあり、それを除去する `recover` サブコマンドがあった。

Parallels の `.pvm` バンドルには対応するものが無い（バンドル直下にあるのは 0 バイトの `vm.lock` ファイル 1 つで、`lsof` でも保持プロセスが見えない）。VM の生存は per-VM の `prl_vm_app --uuid {UUID}` プロセスが表す。固まった VM を落とす手段は Parallels 自身が `prlctl stop <vm> --kill` として持っている。

そのため `recover` は移植せず削除した。同等の復旧が要る場面では `prlctl stop <vm> --kill` を使う。
