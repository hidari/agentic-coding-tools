# Windows VM 検証 — トラブルシューティング

| 症状 | 原因 | 対処 |
|---|---|---|
| VM 起動時 "ディレクトリが空ではありません" | `*.lck` ロックディレクトリが残留 (stale disk lock) | `vmware-vmx` プロセスが停止していることを確認してから `winvm recover --vmx <bundle>.vmx --dry-run` で対象確認 → `winvm recover --vmx <bundle>.vmx` で除去 |
| `nc` が "Host is down" | stale lease (VM 未起動、または別 IP が払い出された) | VM を起動してから `winvm resolve-ip` を再実行して IP を確認。接続が続くようなら leases ファイルを直接確認: `cat /var/db/vmware/vmnet-dhcpd-vmnet8.leases` |
| SSH banner exchange timeout | VM 起動直後で sshd がまだ起動していない | 30〜60 秒待ってから再接続。VM ブート完了を待つか、`ssh -o ConnectTimeout=60 <alias>` で待機時間を伸ばす |
| SSH known_hosts mismatch | VM 再構築で HostKey が変わった | `~/.ssh/known_hosts` から `HostKeyAlias` に対応するエントリを削除。`StrictHostKeyChecking accept-new` 設定済みであれば次回接続時に自動登録される |
| PowerShell 出力が文字化け | cmd.exe 経由の cp932 エンコード問題 | `winvm health` / `winvm run` は `[Console]::OutputEncoding = UTF8` を自動設定済み。手動で PowerShell を実行する場合は同様の設定を追加する |
| `winvm health` が "pwsh(7) を確認できませんでした" で停止 | VM 未起動 / SSH 未到達、または VM に pwsh(7) 未インストール・PATH 未通し | まず VM 起動と SSH 到達を確認 (`winvm resolve-ip` / `ssh <alias> hostname`)。到達できて pwsh 未導入なら `winget install --id Microsoft.PowerShell` で導入し PATH を通す。`winvm health` は WinPS 5.1 の Restricted を `-ExecutionPolicy Bypass` で回避せず pwsh を必須とする |
| `winvm recover` が拒否される | `vmware-vmx` プロセスが実行中 | `pgrep -l vmware-vmx` で確認し、VMware Fusion から VM を完全停止してから再実行 |
