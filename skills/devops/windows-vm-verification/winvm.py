#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""windows-vm-verification: VMware Fusion 上の Windows VM を繋ぐ/直す/調べる/検証する generic CLI。"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path


def extract_mac_from_vmx(vmx_text: str) -> str | None:
    """`.vmx` 本文から NIC MAC を小文字で返す (generatedAddress 優先、無ければ address)。"""
    for key in ("ethernet0.generatedAddress", "ethernet0.address"):
        m = re.search(rf'^\s*{re.escape(key)}\s*=\s*"([^"]+)"', vmx_text, re.MULTILINE)
        if m:
            return m.group(1).strip().lower()
    return None


def parse_latest_lease_ip(leases_text: str, mac: str) -> str | None:
    """同一 MAC の最後の lease ブロックの IP を返す (lease は時系列追記、最後が現在値)。"""
    mac = mac.lower()
    current_ip: str | None = None
    latest: str | None = None
    for line in leases_text.splitlines():
        s = line.strip()
        if s.startswith("lease "):
            current_ip = s.split()[1]
        elif s.startswith("hardware ethernet"):
            m = s.split()[2].rstrip(";").lower()
            if m == mac:
                latest = current_ip
    return latest


DEFAULT_LEASES = "/var/db/vmware/vmnet-dhcpd-vmnet8.leases"


def _env_or(arg_value: str | None, env_key: str, default: str | None = None) -> str | None:
    return arg_value or os.environ.get(env_key) or default


def cmd_resolve_ip(args: argparse.Namespace) -> int:
    vmx = _env_or(args.vmx, "WINVM_VMX")
    leases = _env_or(args.leases, "WINVM_LEASES", DEFAULT_LEASES)
    if not vmx:
        print("error: --vmx (または WINVM_VMX) が必要です", file=sys.stderr)
        return 2
    try:
        mac = extract_mac_from_vmx(Path(vmx).read_text(encoding="utf-8", errors="replace"))
    except OSError as e:
        print(f"error: {vmx} を開けません: {e}", file=sys.stderr)
        return 1
    if not mac:
        print(f"error: .vmx から MAC を導出できません: {vmx}", file=sys.stderr)
        return 1
    try:
        ip = parse_latest_lease_ip(Path(leases).read_text(encoding="utf-8", errors="replace"), mac)
    except OSError as e:
        print(f"error: {leases} を開けません: {e}", file=sys.stderr)
        return 1
    if not ip:
        print(f"error: MAC {mac} に一致する lease がありません (VM 未起動?)", file=sys.stderr)
        return 1
    print(ip)
    return 0


def files_to_sync(branch_delta: str, working_delta: str, untracked: str) -> list[str]:
    """行をトリム/空行除去/重複排除/安定ソート."""
    s: set[str] = set()
    for block in (branch_delta, working_delta, untracked):
        for line in block.splitlines():
            t = line.strip()
            if t:
                s.add(t)
    return sorted(s)


def files_to_delete(branch_deleted: str, working_deleted: str) -> list[str]:
    """diff_base..HEAD と working tree で削除されたファイルの和集合 (トリム/空行除去/重複排除/安定ソート)。

    scp は追加/上書きしかできず、reset (`git checkout -- .`) は VM HEAD の tracked ファイルを
    復元するため、ローカルで削除 (rename 含む) されたファイルは明示的に VM 側で消す必要がある。
    消し漏れると VM 側 tsc/cargo が stale ファイルを拾い偽陰性になる。"""
    s: set[str] = set()
    for block in (branch_deleted, working_deleted):
        for line in block.splitlines():
            t = line.strip()
            if t:
                s.add(t)
    return sorted(s)


def to_windows_path(path: str) -> str:
    r"""`/` を `\` に変換."""
    return path.replace("/", "\\")


def resolve_diff_base(vm_head: str, vm_head_known: bool, fallback: str) -> str:
    """vm_head_known なら vm_head を返す、さもなければ fallback を返す."""
    return vm_head if vm_head_known else fallback


def parent_mkdir_commands(repo_win: str, files: list[str]) -> list[str]:
    """各ファイルの親ディレクトリを作る cmd コマンドのリストを返す (1 親 1 コマンド)。
    cmd の `if ... & if ...` 連結は最初の if が偽だと連鎖全体が束縛され実行されないため、
    親ごとに独立コマンドとして発行する (連結バグ回避)。"""
    parents: set[str] = set()
    for f in files:
        parent = str(Path(f).parent)
        if parent and parent != ".":
            parents.add(f"{repo_win}\\{to_windows_path(parent)}")
    return [f'if not exist "{p}" mkdir "{p}"' for p in sorted(parents)]


def remote_delete_commands(repo_win: str, files: list[str]) -> list[str]:
    """削除ファイルを VM 側で消す cmd コマンドのリスト (1 ファイル 1 独立コマンド)。
    parent_mkdir_commands と同じ理由で `&` 連結せず独立発行する。"""
    return [
        f'if exist "{repo_win}\\{to_windows_path(f)}" del /f /q "{repo_win}\\{to_windows_path(f)}"'
        for f in sorted(files)
    ]


def remote_reset_command(repo_win: str) -> str:
    return f'cd /d "{repo_win}" && git checkout -- . && git clean -fd'


def remote_exec_command(repo_win: str, remote_cmd: str) -> str:
    return f'cd /d "{repo_win}" && {remote_cmd}'


def remote_command_from_args(remote: list[str]) -> str | None:
    """argparse REMAINDER から先頭の '--' を除き remote コマンド文字列を返す。空なら None。"""
    if remote and remote[0] == "--":
        remote = remote[1:]
    return " ".join(remote) if remote else None


def git_local(args: list[str]) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True).stdout


def ssh_capture(host: str, remote: str) -> str:
    return subprocess.run(["ssh", host, remote], capture_output=True, text=True).stdout


def run_ssh(host: str, remote: str) -> bool:
    return subprocess.run(["ssh", host, remote]).returncode == 0


def scp(host: str, local: str, dest: str) -> bool:
    return subprocess.run(["scp", "-q", local, f"{host}:{dest}"]).returncode == 0


def cmd_run(args: argparse.Namespace) -> int:
    host = _env_or(args.host, "WINVM_HOST")
    repo = _env_or(args.repo, "WINVM_REPO")
    base = _env_or(args.base, "WINVM_BASE", "main")
    if not host or not repo:
        print("error: --host と --repo (または env) が必要です", file=sys.stderr)
        return 2
    repo_win = to_windows_path(repo)

    remote_cmd = remote_command_from_args(args.remote)
    if remote_cmd is None:
        print("error: run には -- の後に remote コマンドが必要です", file=sys.stderr)
        return 2

    vm_head = ssh_capture(host, f'cd /d "{repo_win}" && git rev-parse HEAD').strip()
    vm_head_known = bool(vm_head) and (
        subprocess.run(["git", "cat-file", "-e", f"{vm_head}^{{commit}}"]).returncode == 0
    )
    diff_base = resolve_diff_base(vm_head, vm_head_known, base)
    if not vm_head_known:
        print(f"警告: VM HEAD ({vm_head[:7]}) をローカル解決できず base={base} にフォールバック", file=sys.stderr)

    # --no-renames で rename を D+A に分解する (rename 検出されると --diff-filter=D が
    # 旧パスを拾えず、VM に stale ファイルが残って偽陰性になる)
    files = [
        f
        for f in files_to_sync(
            git_local(["diff", "--name-only", "--no-renames", diff_base, "HEAD"]),
            git_local(["diff", "--name-only", "--no-renames", "HEAD"]),
            git_local(["ls-files", "--others", "--exclude-standard"]),
        )
        if Path(f).is_file()
    ]
    deleted = files_to_delete(
        git_local(["diff", "--name-only", "--no-renames", "--diff-filter=D", diff_base, "HEAD"]),
        git_local(["diff", "--name-only", "--no-renames", "--diff-filter=D", "HEAD"]),
    )
    if not files and not deleted:
        if args.skip_when_no_changes:
            print(f"同期する変更ファイルがありません ({diff_base}..HEAD + working tree)")
            return 0
        print(f"同期対象なし。VM の現状で実行します ({diff_base}..HEAD)")

    if not run_ssh(host, remote_reset_command(repo_win)):
        print("VM の reset に失敗しました", file=sys.stderr)
        return 1
    if deleted:
        print(f"削除を同期: {len(deleted)} ファイル")
        for rm in remote_delete_commands(repo_win, deleted):
            if not run_ssh(host, rm):
                print("VM のファイル削除に失敗しました", file=sys.stderr)
                return 1
    if files:
        for mk in parent_mkdir_commands(repo_win, files):
            if not run_ssh(host, mk):
                print("VM のディレクトリ作成に失敗しました", file=sys.stderr)
                return 1
        for f in files:
            if not scp(host, f, f"{repo}/{f}"):
                print(f"scp 失敗: {f}", file=sys.stderr)
                return 1

    print(f"=== VM で {remote_cmd} を実行 ===")
    return 0 if run_ssh(host, remote_exec_command(repo_win, remote_cmd)) else 1


def build_health_powershell(check_tools: list[str], repo: str | None) -> str:
    """ASCII ラベル + `[Console]::OutputEncoding=UTF8` を含み、指定 tool の `--version` チェック行を含む PowerShell 本文。"""
    tools = ", ".join(f"'{t}'" for t in check_tools)
    lines = [
        "$ErrorActionPreference = 'Continue'",
        "try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}",
        "Write-Output '===== OS / Boot ====='",
        "$os = Get-CimInstance Win32_OperatingSystem",
        "Write-Output ('LastBootUp : ' + $os.LastBootUpTime)",
        "Write-Output '===== Volumes (Healthy is OK) ====='",
        "Get-Volume | Where-Object { $_.DriveLetter } | Format-Table DriveLetter, FileSystemType, HealthStatus -AutoSize | Out-String -Width 200",
        "Write-Output '===== NTFS dirty bit (NOT Dirty is OK) ====='",
        "(& cmd /c 'fsutil dirty query C:') 2>&1 | Out-String",
        "Write-Output '===== Unexpected shutdown (41/6008/1001) ====='",
        "Get-WinEvent -FilterHashtable @{LogName='System'; Id=41,6008,1001} -MaxEvents 6 -ErrorAction SilentlyContinue | Select-Object TimeCreated, Id | Format-Table -AutoSize | Out-String",
        "Write-Output '===== NTFS corruption events (0 is OK) ====='",
        "$ntfs = Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-Ntfs'} -MaxEvents 15 -ErrorAction SilentlyContinue | Where-Object { $_.LevelDisplayName -in 'Error','Warning' }",
        "if ($ntfs) { $ntfs | Format-Table TimeCreated, Id, LevelDisplayName -AutoSize | Out-String } else { Write-Output '  (none = OK)' }",
        "Write-Output '===== dev toolchain ====='",
        f"foreach ($t in @({tools})) {{ $c = Get-Command $t -ErrorAction SilentlyContinue; if ($c) {{ Write-Output ('  ' + $t + ': ' + ((& $t --version 2>&1 | Select-Object -First 1))) }} else {{ Write-Output ('  ' + $t + ': (not found)') }} }}",
    ]
    if repo:
        lines += [
            "Write-Output '===== repo state ====='",
            f"if (Test-Path '{repo}') {{ Push-Location '{repo}'; Write-Output ('  HEAD: ' + (git rev-parse --short HEAD 2>&1) + ' ' + (git rev-parse --abbrev-ref HEAD 2>&1)); Pop-Location }}",
        ]
    lines.append("Write-Output '===== HEALTHCHECK DONE ====='")
    return "\n".join(lines) + "\n"


def health_exec_command(remote: str) -> str:
    """転送した health .ps1 を pwsh(7) の -File で実行するコマンド。
    pwsh は RemoteSigned かつ scp 転送物には Mark-of-the-Web が付かないため
    -ExecutionPolicy Bypass 無しで実行できる (WinPS 5.1 の Restricted を Bypass で
    上書きする多層防御の穴を避ける。#119)。"""
    return f"pwsh -NoProfile -File {remote}"


def health_cleanup_command(remote: str) -> str:
    """転送した health .ps1 を削除する後始末コマンド (実行系と同じ pwsh に揃える)。"""
    return f"pwsh -NoProfile -Command Remove-Item -Force {remote}"


def pwsh_probe_command() -> str:
    """VM の PATH に pwsh(7) があるか判定する cmd.exe コマンド。
    見つかれば exit 0、無ければ非 0。パス出力は抑制する。"""
    return "where pwsh >nul 2>nul"


def cmd_health(args: argparse.Namespace, *, run=run_ssh) -> int:
    """.ps1 を scp して pwsh(7) の -File で実行、後始末。pwsh 必須 (不在ならエラー)。"""
    host = _env_or(args.host, "WINVM_HOST")
    repo = _env_or(args.repo, "WINVM_REPO")
    if not host:
        print("error: --host (または WINVM_HOST) が必要です", file=sys.stderr)
        return 2
    if not run(host, pwsh_probe_command()):
        # probe 失敗は pwsh 未導入とは限らず、SSH 未到達 (VM 未起動 / stale IP) でも起きる。
        # 両方の可能性を示し、pwsh 未導入と断定して誤誘導しない。
        print(
            "error: VM 上で pwsh(7) を確認できませんでした "
            "(VM 未起動 / SSH 未到達、または pwsh 未導入の可能性)。"
            "VM が起動していること・pwsh が PATH にあること "
            "(winget install --id Microsoft.PowerShell) を確認してください",
            file=sys.stderr,
        )
        return 1
    tools = args.check_tools.split(",") if args.check_tools else []
    ps = build_health_powershell([t for t in tools if t], repo)

    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8") as fh:
        fh.write(ps)
        local = fh.name
    remote = "C:/Users/Public/winvm_health.ps1"
    try:
        if not scp(host, local, remote):
            print("health スクリプトの scp に失敗しました", file=sys.stderr)
            return 1
        ok = run(host, health_exec_command(remote))
        run(host, health_cleanup_command(remote))
        return 0 if ok else 1
    finally:
        Path(local).unlink(missing_ok=True)


def find_stale_lock_dirs(bundle_dir: Path) -> list[Path]:
    return sorted(p for p in bundle_dir.glob("*.lck") if p.is_dir())


def _vmware_vmx_running() -> bool:
    out = subprocess.run(["pgrep", "-f", "vmware-vmx"], capture_output=True, text=True)
    return out.returncode == 0


def cmd_recover(args: argparse.Namespace, *, vmware_running=_vmware_vmx_running) -> int:
    vmx = _env_or(args.vmx, "WINVM_VMX")
    if not vmx:
        print("error: --vmx (または WINVM_VMX) が必要です", file=sys.stderr)
        return 2
    bundle = Path(vmx).parent
    if vmware_running():
        print("中止: vmware-vmx プロセスが稼働中です。VM を停止してから実行してください", file=sys.stderr)
        return 1
    locks = find_stale_lock_dirs(bundle)
    if not locks:
        print("stale ロックはありません")
        return 0
    # 既定は可逆 move。--delete 明示時のみ不可逆削除。
    if args.delete:
        for lk in locks:
            if args.dry_run:
                print(f"[dry-run] 削除対象: {lk}")
                continue
            shutil.rmtree(lk)
            print(f"削除: {lk}")
        return 0
    backup_root = (
        Path(args.backup)
        if args.backup
        else bundle / f".winvm-lck-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    for lk in locks:
        if args.dry_run:
            print(f"[dry-run] 退避対象: {lk} -> {backup_root / lk.name}")
            continue
        backup_root.mkdir(parents=True, exist_ok=True)
        dest = backup_root / lk.name
        shutil.move(str(lk), str(dest))
        print(f"退避: {lk} -> {dest}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="winvm", description="VMware Fusion Windows VM ops/verify")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("resolve-ip", help="VMware NAT lease から VM の現 IP を解決")
    sp.add_argument("--vmx")
    sp.add_argument("--leases")
    sp.set_defaults(func=cmd_resolve_ip)

    rp = sub.add_parser("run", help="git 差分を scp 同期して remote コマンド実行")
    rp.add_argument("--host")
    rp.add_argument("--repo")
    rp.add_argument("--base")
    rp.add_argument("--skip-when-no-changes", action="store_true")
    rp.add_argument("remote", nargs=argparse.REMAINDER, help="-- の後に remote コマンド")
    rp.set_defaults(func=cmd_run)

    hp = sub.add_parser("health", help="SSH 越しに VM の健全性を検査")
    hp.add_argument("--host")
    hp.add_argument("--repo")
    hp.add_argument("--check-tools", help="カンマ区切り (例: node,pnpm,cargo,rustc,git)")
    hp.set_defaults(func=cmd_health)

    cp = sub.add_parser("recover", help="stale *.lck を除去し起動不能を解消")
    cp.add_argument("--vmx")
    cp.add_argument("--backup", help="退避先 (省略時はバンドル内の timestamp 付き .winvm-lck-backup-*)")
    cp.add_argument("--delete", action="store_true", help="退避でなく不可逆削除")
    cp.add_argument("--dry-run", action="store_true")
    cp.set_defaults(func=cmd_recover)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
