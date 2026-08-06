#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""windows-vm-verification: Parallels Desktop 上の Windows VM を繋ぐ/調べる/検証する generic CLI。"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# prlctl の argv 組み立て
# ---------------------------------------------------------------------------


def prlctl_list_argv() -> list[str]:
    """全 VM を JSON で列挙する argv。

    `-f` が無いと `ip_configured` フィールド自体が出ず、`-j` が無いと人間向けの
    表形式になって text-parse に逆戻りする。両方必要。
    """
    return ["prlctl", "list", "-a", "-f", "-j"]


def prlctl_info_argv(vm: str) -> list[str]:
    """1 台の詳細情報を出す argv (JSON 出力は無いので text を parse する)。"""
    return ["prlctl", "list", "-i", vm]


def prlctl_exec_argv(vm: str, command: list[str]) -> list[str]:
    """ゲスト内でコマンドを実行する argv。

    コマンドはトークンを分割したまま渡す。1 つの文字列に連結して渡すと
    exit 2 で無出力のまま黙って失敗する (Parallels Desktop 26.4.0 で実測)。
    """
    return ["prlctl", "exec", vm, *command]


def run_capture(argv: list[str]) -> tuple[int, str, str]:
    """argv を実行して (returncode, stdout, stderr) を返す。実行不能も戻り値で表す。"""
    try:
        p = subprocess.run(argv, capture_output=True, text=True)
    except OSError as e:
        return 127, "", f"{argv[0]} を実行できません: {e}"
    return p.returncode, p.stdout, p.stderr


# ---------------------------------------------------------------------------
# prlctl 出力の parse
# ---------------------------------------------------------------------------


def parse_vm_list(json_text: str) -> list[dict]:
    """`prlctl list -a -f -j` の JSON をレコードのリストにする。

    壊れた出力で例外を投げず「該当なし」に倒す。呼び出し側は必ず
    「見つからなかった」経路を持つので、そこへ合流させる方が扱いが一様になる。
    """
    try:
        data = json.loads(json_text)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    return [v for v in data if isinstance(v, dict)]


def _normalize_uuid(value: str) -> str:
    return value.strip().strip("{}").lower()


def find_vm(vms: list[dict], identifier: str) -> dict | None:
    """名前の完全一致、または UUID の一致 (大小無視・波括弧の有無を吸収) で VM を返す。

    名前は部分一致させない。`prlctl` が受け付ける識別子と同じ集合に揃えることで、
    このツールと `prlctl` を混ぜて使っても指す VM がずれない。
    """
    ident = (identifier or "").strip()
    if not ident:
        return None
    for vm in vms:
        if vm.get("name") == ident:
            return vm
    wanted = _normalize_uuid(ident)
    for vm in vms:
        if _normalize_uuid(str(vm.get("uuid", ""))) == wanted:
            return vm
    return None


def pick_ipv4(ip_field: str) -> str | None:
    """`ip_configured` フィールドから IPv4 を 1 つ返す。無ければ None。

    このフィールドは形が 3 通りある (いずれも実測):
      - 単一の IPv4              : "10.211.55.3"
      - 停止中を表すダッシュ     : "-"
      - `-o` 併用時の複数アドレス: "10.211.55.3  fdb2:... fe80::...   " (末尾に空白)
    ダッシュや空白をそのまま IP として下流に渡さないことがこの関数の責務。
    """
    for token in (ip_field or "").split():
        try:
            ipaddress.IPv4Address(token)
        except ValueError:
            continue
        return token
    return None


def parse_home_path(info_text: str) -> str | None:
    """`prlctl list -i` の `Home:` 行からバンドルのパスを返す。

    同じ出力に `Home path:` (= config.pvs のパス) が先に現れる。前方一致で
    "Home" を拾うとそちらを掴むので、コロンまで含めて一致させる。
    """
    for line in info_text.splitlines():
        if line.startswith("Home:"):
            return line[len("Home:") :].strip()
    return None


_ISOLATED_RE = re.compile(r"<IsolatedVm>\s*([01])\s*</IsolatedVm>")


def parse_isolated_flag(config_text: str) -> bool | None:
    """config.pvs の `<IsolatedVm>` を bool で返す。要素が無ければ None。

    「隔離されていない」と「読めなかった」を同じ値にしない。この区別を潰すと
    診断が「未確認」を「健全」として報告してしまう。
    """
    m = _ISOLATED_RE.search(config_text)
    if not m:
        return None
    return m.group(1) == "1"


_TOOLS_RE = re.compile(r"^GuestTools:\s*state=(\S+)(?:\s+version=(\S+))?", re.MULTILINE)


def parse_tools_state(info_text: str) -> tuple[str | None, str | None]:
    """`prlctl list -i` の GuestTools 行から (state, version) を返す。"""
    m = _TOOLS_RE.search(info_text)
    if not m:
        return None, None
    return m.group(1), m.group(2)


# ---------------------------------------------------------------------------
# 共通
# ---------------------------------------------------------------------------


def _env_or(arg_value: str | None, env_key: str, default: str | None = None) -> str | None:
    return arg_value or os.environ.get(env_key) or default


def _load_vms(run) -> tuple[list[dict] | None, str | None]:
    rc, out, err = run(prlctl_list_argv())
    if rc != 0:
        detail = err.strip() or out.strip() or f"rc={rc}"
        return None, f"error: prlctl の VM 列挙に失敗しました: {detail}"
    return parse_vm_list(out), None


def _known_names(vms: list[dict]) -> str:
    names = sorted(str(v.get("name", "")) for v in vms if v.get("name"))
    return ", ".join(names) if names else "(登録済み VM なし)"


# ---------------------------------------------------------------------------
# resolve-ip
# ---------------------------------------------------------------------------


def cmd_resolve_ip(args: argparse.Namespace, *, run=run_capture) -> int:
    vm_id = _env_or(args.vm, "WINVM_VM")
    if not vm_id:
        print("error: --vm (または WINVM_VM) が必要です", file=sys.stderr)
        return 2
    vms, err = _load_vms(run)
    if err:
        print(err, file=sys.stderr)
        return 1
    vm = find_vm(vms, vm_id)
    if vm is None:
        print(
            f"error: VM が見つかりません: {vm_id} / 登録済み: {_known_names(vms)}",
            file=sys.stderr,
        )
        return 1
    status = str(vm.get("status", "unknown"))
    ip = pick_ipv4(str(vm.get("ip_configured", "")))
    if ip is None:
        print(
            f"error: IP を解決できません (status={status})。"
            f'VM が起動していない場合は prlctl start "{vm.get("name", vm_id)}" で起動する',
            file=sys.stderr,
        )
        return 1
    print(ip)
    return 0


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


@dataclass
class Check:
    """診断 1 項目。`ok=None` は「確認できなかった」で、OK でも NG でもない。"""

    label: str
    observed: str
    ok: bool | None = None
    hint: str | None = None


_MARK = {True: "[ OK ]", False: "[FAIL]", None: "[ -- ]"}


def format_doctor_report(checks: list[Check]) -> str:
    """各項目の観測値を必ず並べる。OK/NG だけの出力にしない。

    「緑だから健全」ではなく「何をどう観測した結果その判定か」を読めるようにする。
    """
    width = max((len(c.label) for c in checks), default=0)
    lines: list[str] = []
    for c in checks:
        lines.append(f"{_MARK[c.ok]} {c.label.ljust(width)} : {c.observed}")
        if c.ok is False and c.hint:
            lines.append(f"{' ' * 7}-> {c.hint}")
    return "\n".join(lines)


def doctor_exit_code(checks: list[Check]) -> int:
    return 1 if any(c.ok is False for c in checks) else 0


def _ssh_reachable(host: str) -> bool:
    rc, _, _ = run_capture(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, "exit"]
    )
    return rc == 0


def collect_doctor_checks(
    vm_id: str,
    host: str | None = None,
    *,
    run=run_capture,
    ssh_probe=_ssh_reachable,
) -> list[Check]:
    """VM が使える状態かをホスト側から観測する。各項目は観測値を持つ。"""
    vms, err = _load_vms(run)
    if err:
        return [
            Check(
                "prlctl",
                err.removeprefix("error: "),
                False,
                hint="Parallels Desktop が起動しているか、prlctl が PATH にあるか確認する",
            )
        ]
    vm = find_vm(vms, vm_id)
    if vm is None:
        return [
            Check(
                "VM",
                f"{vm_id} は未登録 / 登録済み: {_known_names(vms)}",
                False,
                hint="prlctl list -a で名前か UUID を確認する",
            )
        ]

    name = str(vm.get("name", ""))
    checks = [Check("VM", f"{name} ({vm.get('uuid', '')})", True)]

    status = str(vm.get("status", "unknown"))
    checks.append(Check("status", status, status == "running", hint=f'prlctl start "{name}"'))

    ip = pick_ipv4(str(vm.get("ip_configured", "")))
    checks.append(
        Check(
            "IP",
            ip or "(未解決)",
            ip is not None,
            hint="VM を起動し Parallels Tools が動いているか確認する",
        )
    )

    rc_info, info, _ = run(prlctl_info_argv(vm_id))
    info = info if rc_info == 0 else ""

    tools_state, tools_version = parse_tools_state(info)
    checks.append(
        Check(
            "Parallels Tools",
            " ".join(x for x in (tools_state, tools_version) if x) or "(未確認)",
            None if tools_state is None else tools_state == "installed",
            hint="Parallels のメニューから Parallels Tools を再インストールする",
        )
    )

    isolated = _read_isolation(parse_home_path(info))
    checks.append(
        Check(
            "host isolation",
            {True: "on", False: "off", None: "(未確認)"}[isolated],
            None if isolated is None else not isolated,
            hint=f'prlctl set "{name}" --isolate-vm off',
        )
    )

    rc_exec, out_exec, err_exec = run(prlctl_exec_argv(vm_id, ["cmd.exe", "/c", "ver"]))
    checks.append(
        Check(
            "prlctl exec",
            out_exec.strip() or err_exec.strip() or f"rc={rc_exec}",
            rc_exec == 0,
            hint="host isolation を off にし、Parallels Tools が動いているか確認する",
        )
    )

    if host:
        reachable = ssh_probe(host)
        checks.append(
            Check(
                f"ssh {host}",
                "到達" if reachable else "未到達",
                reachable,
                hint="sshd の稼働とファイアウォールの許可範囲を確認する",
            )
        )
    return checks


def _read_isolation(home: str | None) -> bool | None:
    if not home:
        return None
    try:
        text = (Path(home) / "config.pvs").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return parse_isolated_flag(text)


def cmd_doctor(args: argparse.Namespace, *, run=run_capture, ssh_probe=_ssh_reachable) -> int:
    vm_id = _env_or(args.vm, "WINVM_VM")
    if not vm_id:
        print("error: --vm (または WINVM_VM) が必要です", file=sys.stderr)
        return 2
    host = _env_or(args.host, "WINVM_HOST")
    checks = collect_doctor_checks(vm_id, host, run=run, ssh_probe=ssh_probe)
    print(format_doctor_report(checks))
    return doctor_exit_code(checks)


# ---------------------------------------------------------------------------
# run (SSH ベース。ハイパーバイザに依存しない)
# ---------------------------------------------------------------------------


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
    """diff_base..HEAD と working tree で削除されたファイルの和集合。

    scp は追加/上書きしかできず、reset (`git checkout -- .`) は VM HEAD の tracked
    ファイルを復元するため、ローカルで削除 (rename 含む) されたファイルは明示的に
    VM 側で消す必要がある。消し漏れると VM 側 tsc/cargo が stale ファイルを拾い
    偽陰性になる。
    """
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

    cmd の `if ... & if ...` 連結は最初の if が偽だと連鎖全体が束縛され実行されない
    ため、親ごとに独立コマンドとして発行する (連結バグ回避)。
    """
    parents: set[str] = set()
    for f in files:
        parent = str(Path(f).parent)
        if parent and parent != ".":
            parents.add(f"{repo_win}\\{to_windows_path(parent)}")
    return [f'if not exist "{p}" mkdir "{p}"' for p in sorted(parents)]


def remote_delete_commands(repo_win: str, files: list[str]) -> list[str]:
    """削除ファイルを VM 側で消す cmd コマンドのリスト (1 ファイル 1 独立コマンド)。

    parent_mkdir_commands と同じ理由で `&` 連結せず独立発行する。
    """
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
        print(
            f"警告: VM HEAD ({vm_head[:7]}) をローカル解決できず base={base} にフォールバック",
            file=sys.stderr,
        )

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


# ---------------------------------------------------------------------------
# health (SSH ベース。ハイパーバイザに依存しない)
# ---------------------------------------------------------------------------


def build_health_powershell(check_tools: list[str], repo: str | None) -> str:
    """ASCII ラベル + `[Console]::OutputEncoding=UTF8` を含む PowerShell 本文。"""
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
    `-ExecutionPolicy Bypass` 無しで実行できる。WinPS 5.1 は Restricted なので
    同じスクリプトが弾かれる (実測)。Bypass で上書きすると多層防御に穴が空く。
    """
    return f"pwsh -NoProfile -File {remote}"


def health_cleanup_command(remote: str) -> str:
    """転送した health .ps1 を削除する後始末コマンド (実行系と同じ pwsh に揃える)。"""
    return f"pwsh -NoProfile -Command Remove-Item -Force {remote}"


def pwsh_probe_command() -> str:
    """VM の PATH に pwsh(7) があるか判定する cmd.exe コマンド。

    見つかれば exit 0、無ければ非 0。パス出力は抑制する。
    """
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
            "winvm doctor --vm <id> --host <alias> で切り分け、"
            "pwsh が無ければ winget install --id Microsoft.PowerShell で導入する",
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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="winvm", description="Parallels Windows VM ops/verify")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("resolve-ip", help="prlctl から VM の現 IP を解決")
    sp.add_argument("--vm", help="VM 名または UUID (env: WINVM_VM)")
    sp.set_defaults(func=cmd_resolve_ip)

    dp = sub.add_parser("doctor", help="VM が使える状態かをホスト側から診断")
    dp.add_argument("--vm", help="VM 名または UUID (env: WINVM_VM)")
    dp.add_argument("--host", help="SSH 到達性も見る場合の ssh config エイリアス")
    dp.set_defaults(func=cmd_doctor)

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

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
