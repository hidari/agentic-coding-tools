import argparse

import winvm

LEASES = """
lease 172.16.237.130 {
\thardware ethernet 00:0c:29:bb:8b:20;
}
lease 172.16.237.128 {
\thardware ethernet 00:0c:29:b0:ea:5a;
}
lease 172.16.237.129 {
\thardware ethernet 00:0c:29:b0:ea:5a;
}
"""


def test_extract_mac_prefers_generated_address():
    vmx = (
        'ethernet0.address = "00:50:56:AA:BB:CC"\n'
        'ethernet0.generatedAddress = "00:0C:29:B0:EA:5A"\n'
    )
    assert winvm.extract_mac_from_vmx(vmx) == "00:0c:29:b0:ea:5a"


def test_extract_mac_falls_back_to_static_address():
    vmx = 'ethernet0.addressType = "static"\nethernet0.address = "00:50:56:AB:CD:EF"\n'
    assert winvm.extract_mac_from_vmx(vmx) == "00:50:56:ab:cd:ef"


def test_extract_mac_returns_none_when_absent():
    assert winvm.extract_mac_from_vmx('displayName = "x"\n') is None


def test_parse_latest_lease_ip_returns_latest_matching_block():
    assert winvm.parse_latest_lease_ip(LEASES, "00:0c:29:b0:ea:5a") == "172.16.237.129"


def test_parse_latest_lease_ip_is_case_insensitive_on_mac():
    assert winvm.parse_latest_lease_ip(LEASES, "00:0C:29:BB:8B:20") == "172.16.237.130"


def test_parse_latest_lease_ip_returns_none_when_no_match():
    assert winvm.parse_latest_lease_ip(LEASES, "de:ad:be:ef:00:00") is None


def test_cmd_resolve_ip_end_to_end(tmp_path, capsys):
    vmx = tmp_path / "vm.vmx"
    vmx.write_text('ethernet0.generatedAddress = "00:0c:29:b0:ea:5a"\n', encoding="utf-8")
    leases = tmp_path / "leases"
    leases.write_text(LEASES, encoding="utf-8")
    rc = winvm.main(["resolve-ip", "--vmx", str(vmx), "--leases", str(leases)])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "172.16.237.129"


def test_cmd_resolve_ip_no_lease_is_nonzero(tmp_path):
    vmx = tmp_path / "vm.vmx"
    vmx.write_text('ethernet0.generatedAddress = "de:ad:be:ef:00:00"\n', encoding="utf-8")
    leases = tmp_path / "leases"
    leases.write_text(LEASES, encoding="utf-8")
    assert winvm.main(["resolve-ip", "--vmx", str(vmx), "--leases", str(leases)]) == 1


def test_files_to_sync_dedups_blank_strips_sorts():
    got = winvm.files_to_sync("b/x.rs\na.rs\n", "a.rs\n\n  c.rs  \n", "d.rs\n")
    assert got == ["a.rs", "b/x.rs", "c.rs", "d.rs"]


def test_files_to_sync_empty_inputs_return_empty():
    assert winvm.files_to_sync("", "  \n", "\n") == []


def test_files_to_delete_unions_dedups_strips_sorts():
    # committed 削除 (diff_base..HEAD) と working tree 削除の和集合
    got = winvm.files_to_delete("b/old.ts\na/gone.rs\n", "a/gone.rs\n\n  c/x.ts  \n")
    assert got == ["a/gone.rs", "b/old.ts", "c/x.ts"]


def test_files_to_delete_empty_inputs_return_empty():
    assert winvm.files_to_delete("", "  \n") == []


def test_remote_delete_commands_one_per_file_windows_paths():
    # cmd の & 連結バグ回避のため 1 ファイル 1 独立コマンド、パスは \ 区切り
    cmds = winvm.remote_delete_commands(
        "C:\\repo", ["src/hooks/useOld.ts", "Cargo.lock"]
    )
    assert cmds == [
        'if exist "C:\\repo\\Cargo.lock" del /f /q "C:\\repo\\Cargo.lock"',
        'if exist "C:\\repo\\src\\hooks\\useOld.ts" del /f /q "C:\\repo\\src\\hooks\\useOld.ts"',
    ]


def test_remote_delete_commands_empty_is_empty():
    assert winvm.remote_delete_commands("C:\\repo", []) == []


def test_to_windows_path():
    assert winvm.to_windows_path("C:/proj/app") == "C:\\proj\\app"
    assert winvm.to_windows_path("a") == "a"


def test_resolve_diff_base():
    assert winvm.resolve_diff_base("abc123", True, "main") == "abc123"
    assert winvm.resolve_diff_base("deadbeef", False, "main") == "main"


def test_parent_mkdir_commands_one_per_parent_deduped():
    files = ["crates/xtask/src/a.rs", "crates/xtask/src/b.rs", "crates/core/c.rs"]
    cmds = winvm.parent_mkdir_commands("C:\\repo", files)
    assert cmds == [
        'if not exist "C:\\repo\\crates\\core" mkdir "C:\\repo\\crates\\core"',
        'if not exist "C:\\repo\\crates\\xtask\\src" mkdir "C:\\repo\\crates\\xtask\\src"',
    ]


def test_parent_mkdir_commands_root_only_is_empty():
    assert winvm.parent_mkdir_commands("C:\\repo", ["Cargo.toml", "README.md"]) == []


def test_remote_reset_command():
    assert winvm.remote_reset_command("C:\\repo") == (
        'cd /d "C:\\repo" && git checkout -- . && git clean -fd'
    )
    # parameterization (negative case): different repo path -> different, correct output (not hardcoded)
    assert winvm.remote_reset_command("D:\\other") == (
        'cd /d "D:\\other" && git checkout -- . && git clean -fd'
    )


def test_remote_exec_command():
    assert winvm.remote_exec_command("C:\\repo", "cargo xtask check-desktop") == (
        'cd /d "C:\\repo" && cargo xtask check-desktop'
    )
    assert winvm.remote_exec_command("D:\\other", "echo hi") == 'cd /d "D:\\other" && echo hi'


def test_build_health_powershell_includes_encoding_and_tools():
    ps = winvm.build_health_powershell(["node", "cargo"], "C:/proj/app")
    assert "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8" in ps
    assert "fsutil dirty query C:" in ps
    assert "'node'" in ps and "'cargo'" in ps
    assert "C:/proj/app" in ps


def test_build_health_powershell_no_repo_omits_repo_section():
    ps = winvm.build_health_powershell(["node"], None)
    assert "repo state" not in ps


def test_health_exec_command_uses_pwsh_file_without_bypass():
    # #119: WinPS 5.1 の Restricted を Bypass で回避せず、pwsh(7) の -File で実行する
    # (RemoteSigned + Mark-of-the-Web 無しで Bypass 不要)。
    remote = "C:/Users/Public/winvm_health.ps1"
    cmd = winvm.health_exec_command(remote)
    assert cmd == f"pwsh -NoProfile -File {remote}"
    assert "-ExecutionPolicy Bypass" not in cmd
    assert "powershell" not in cmd  # pwsh(7) であって WinPS 5.1 ではない
    # parameterization (negative): 別 remote -> 別出力 (ハードコードでない)
    assert winvm.health_exec_command("D:/x.ps1") == "pwsh -NoProfile -File D:/x.ps1"


def test_health_cleanup_command_uses_pwsh():
    remote = "C:/Users/Public/winvm_health.ps1"
    cmd = winvm.health_cleanup_command(remote)
    assert cmd == f"pwsh -NoProfile -Command Remove-Item -Force {remote}"
    assert cmd.startswith("pwsh ")
    assert "-ExecutionPolicy Bypass" not in cmd


def test_pwsh_probe_command_probes_pwsh_quietly():
    # cmd.exe 経由: pwsh が PATH にあれば exit 0、無ければ非 0。出力は抑制する。
    assert winvm.pwsh_probe_command() == "where pwsh >nul 2>nul"


def test_cmd_health_errors_when_pwsh_absent(capsys):
    # pwsh 必須。不在なら scp/exec へ進まずエラーで short-circuit する (#119)。
    calls: list[str] = []

    def fake_run(host, remote):
        calls.append(remote)
        return False  # pwsh probe 失敗 = pwsh 不在

    args = argparse.Namespace(host="vm", repo=None, check_tools=None)
    rc = winvm.cmd_health(args, run=fake_run)
    assert rc == 1
    assert calls == [winvm.pwsh_probe_command()]  # probe だけ、scp/exec は走らない
    assert "pwsh" in capsys.readouterr().err


def test_cmd_health_probe_error_message_is_not_misleading(capsys):
    # probe 失敗は SSH 未到達 (VM 未起動 / stale IP) の可能性もある。
    # pwsh 未導入と断定せず両方の可能性を示す診断にする (誤誘導の回避)。
    args = argparse.Namespace(host="vm", repo=None, check_tools=None)
    winvm.cmd_health(args, run=lambda host, remote: False)
    err = capsys.readouterr().err
    assert "pwsh" in err  # pwsh 未導入の可能性
    assert "未起動" in err  # SSH 未到達 (VM 未起動) の可能性にも言及


def test_find_stale_lock_dirs(tmp_path):
    (tmp_path / "disk.vmdk.lck").mkdir()
    (tmp_path / "disk.vmdk.lck" / "M1.lck").write_text("x")
    (tmp_path / "disk.vmdk").write_text("descriptor")
    found = winvm.find_stale_lock_dirs(tmp_path)
    assert [p.name for p in found] == ["disk.vmdk.lck"]


def test_find_stale_lock_dirs_none(tmp_path):
    (tmp_path / "disk.vmdk").write_text("x")
    assert winvm.find_stale_lock_dirs(tmp_path) == []


def _recover_args(vmx, *, backup=None, delete=False, dry_run=False):
    return argparse.Namespace(vmx=str(vmx), backup=backup, delete=delete, dry_run=dry_run)


def _planted_lock(tmp_path):
    bundle = tmp_path / "vm.vmwarevm"
    bundle.mkdir()
    vmx = bundle / "vm.vmx"
    vmx.write_text('ethernet0.generatedAddress = "00:0c:29:00:00:01"\n')
    lock = bundle / "disk.vmdk.lck"
    lock.mkdir()
    (lock / "M1.lck").write_text("x")
    return bundle, vmx, lock


def test_cmd_recover_default_moves_reversibly(tmp_path):
    bundle, vmx, lock = _planted_lock(tmp_path)
    rc = winvm.cmd_recover(_recover_args(vmx), vmware_running=lambda: False)
    assert rc == 0
    assert not lock.exists()  # moved out of the bundle
    moved = list(bundle.glob(".winvm-lck-backup-*/disk.vmdk.lck"))
    assert len(moved) == 1 and moved[0].is_dir()  # reversible: still recoverable


def test_cmd_recover_delete_flag_is_irreversible(tmp_path):
    bundle, vmx, lock = _planted_lock(tmp_path)
    rc = winvm.cmd_recover(_recover_args(vmx, delete=True), vmware_running=lambda: False)
    assert rc == 0
    assert not lock.exists()
    assert list(bundle.glob(".winvm-lck-backup-*")) == []  # deleted, no backup


def test_cmd_recover_dry_run_changes_nothing(tmp_path):
    bundle, vmx, lock = _planted_lock(tmp_path)
    rc = winvm.cmd_recover(_recover_args(vmx, dry_run=True), vmware_running=lambda: False)
    assert rc == 0
    assert lock.exists()  # untouched


def test_cmd_recover_refuses_when_vm_running(tmp_path):
    bundle, vmx, lock = _planted_lock(tmp_path)
    rc = winvm.cmd_recover(_recover_args(vmx), vmware_running=lambda: True)
    assert rc == 1
    assert lock.exists()  # guard prevented any change


def test_remote_command_from_args_strips_leading_separator():
    assert winvm.remote_command_from_args(["--", "cargo xtask check-desktop"]) == "cargo xtask check-desktop"


def test_remote_command_from_args_without_separator():
    assert winvm.remote_command_from_args(["cargo", "xtask", "check-desktop"]) == "cargo xtask check-desktop"


def test_remote_command_from_args_empty_is_none():
    assert winvm.remote_command_from_args([]) is None
    assert winvm.remote_command_from_args(["--"]) is None


def test_cmd_resolve_ip_missing_vmx_is_exit_2(monkeypatch):
    monkeypatch.delenv("WINVM_VMX", raising=False)
    assert winvm.main(["resolve-ip"]) == 2
