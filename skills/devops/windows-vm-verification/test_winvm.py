"""winvm の仕様。

依存を増やさないため stdlib の unittest だけで書く (`python3 -m unittest`)。
winvm.py 自身も `dependencies = []` で、CI も runner の python3 をそのまま使う。

Parallels の実出力に由来する文字列は、実機 (Parallels Desktop 26.4.0-57513) で
採取した生の値をそのまま使う。整形した理想形を書くと、実際の出力との差で壊れる。
"""
from __future__ import annotations

import argparse
import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

import winvm

# 実機 `prlctl list -a -f -j` の出力そのまま (停止中 VM の ip_configured は "-")。
PRLCTL_LIST_JSON = """[
\t{
\t\t"uuid": "e97a3a2f-9112-4017-a3a7-48c04bdd7a45",
\t\t"status": "stopped",
\t\t"ip_configured": "-",
\t\t"name": "Coda - macOS"
\t},
\t{
\t\t"uuid": "4eb49f98-7f09-4b43-a3f1-35f285ad4d26",
\t\t"status": "running",
\t\t"ip_configured": "10.211.55.3",
\t\t"name": "Staccato - Windows 11 ARM"
\t}
]
"""

# 実機 `prlctl list -i` の抜粋。"Home path:" が "Home:" より前に出る点が重要。
PRLCTL_INFO_TEXT = """ID: {4eb49f98-7f09-4b43-a3f1-35f285ad4d26}
Name: Staccato - Windows 11 ARM
Description:
Type: VM
State: running
OS: win-11
Template: no
Uptime: 00:42:11 (since 2026-08-07 06:06:12)
Home path: /Users/example/Parallels/Staccato - Windows 11 ARM.pvm/config.pvs
Home: /Users/example/Parallels/Staccato - Windows 11 ARM.pvm/
Owner: sho@localhost
GuestTools: state=installed version=26.4.0-57513
Autostart: off
"""


class ParseVmList(unittest.TestCase):
    def test_parses_prlctl_json_into_records(self):
        vms = winvm.parse_vm_list(PRLCTL_LIST_JSON)
        self.assertEqual([v["name"] for v in vms], ["Coda - macOS", "Staccato - Windows 11 ARM"])

    def test_empty_output_is_empty_list(self):
        self.assertEqual(winvm.parse_vm_list(""), [])
        self.assertEqual(winvm.parse_vm_list("   \n"), [])

    def test_malformed_json_is_empty_list(self):
        # prlctl が壊れた出力を返しても例外で落とさず「該当なし」に倒す。
        self.assertEqual(winvm.parse_vm_list("not json at all"), [])

    def test_non_list_json_is_empty_list(self):
        self.assertEqual(winvm.parse_vm_list('{"uuid": "x"}'), [])


class FindVm(unittest.TestCase):
    def setUp(self):
        self.vms = winvm.parse_vm_list(PRLCTL_LIST_JSON)

    def test_matches_exact_name_including_spaces(self):
        vm = winvm.find_vm(self.vms, "Staccato - Windows 11 ARM")
        self.assertIsNotNone(vm)
        self.assertEqual(vm["uuid"], "4eb49f98-7f09-4b43-a3f1-35f285ad4d26")

    def test_matches_bare_uuid(self):
        vm = winvm.find_vm(self.vms, "4eb49f98-7f09-4b43-a3f1-35f285ad4d26")
        self.assertEqual(vm["name"], "Staccato - Windows 11 ARM")

    def test_matches_braced_uuid(self):
        # prlctl list -i と prl_vm_app の argv は波括弧付きで UUID を出す。
        vm = winvm.find_vm(self.vms, "{4eb49f98-7f09-4b43-a3f1-35f285ad4d26}")
        self.assertEqual(vm["name"], "Staccato - Windows 11 ARM")

    def test_uuid_match_is_case_insensitive(self):
        vm = winvm.find_vm(self.vms, "4EB49F98-7F09-4B43-A3F1-35F285AD4D26")
        self.assertEqual(vm["name"], "Staccato - Windows 11 ARM")

    def test_name_match_is_exact_not_substring(self):
        # 部分一致を許すと別 VM を掴む。negative case。
        self.assertIsNone(winvm.find_vm(self.vms, "Staccato"))

    def test_unknown_identifier_is_none(self):
        self.assertIsNone(winvm.find_vm(self.vms, "no-such-vm"))

    def test_empty_identifier_matches_nothing(self):
        self.assertIsNone(winvm.find_vm(self.vms, ""))


class PickIpv4(unittest.TestCase):
    def test_single_address(self):
        self.assertEqual(winvm.pick_ipv4("10.211.55.3"), "10.211.55.3")

    def test_picks_ipv4_from_mixed_list_with_trailing_spaces(self):
        # 実機 `prlctl list -o ip_configured -f` は IPv4 + IPv6 + link-local を
        # 空白区切りで返し末尾に空白が残る。
        raw = "10.211.55.3  fdb2:2c26:f4e4:0:34a5:e9e2:a530:d5ff fe80::a22a:acc8:4abd:345c   "
        self.assertEqual(winvm.pick_ipv4(raw), "10.211.55.3")

    def test_dash_sentinel_is_none(self):
        # 停止中 VM は "-" が入る。空文字ではないのでそのまま渡すと不正な IP になる。
        self.assertIsNone(winvm.pick_ipv4("-"))

    def test_whitespace_only_is_none(self):
        self.assertIsNone(winvm.pick_ipv4("   "))

    def test_empty_is_none(self):
        self.assertIsNone(winvm.pick_ipv4(""))

    def test_ipv6_only_is_none(self):
        self.assertIsNone(winvm.pick_ipv4("fe80::a22a:acc8:4abd:345c"))

    def test_rejects_partial_octets(self):
        # "10.211" のような不完全な値を IP として通さない。
        self.assertIsNone(winvm.pick_ipv4("10.211"))


class ParseHomePath(unittest.TestCase):
    def test_reads_home_not_home_path(self):
        # "Home path:" は config.pvs を指し "Home:" がバンドル。前方一致で
        # "Home" を拾うと先に出る "Home path:" を掴んで config.pvs/config.pvs になる。
        got = winvm.parse_home_path(PRLCTL_INFO_TEXT)
        self.assertEqual(got, "/Users/example/Parallels/Staccato - Windows 11 ARM.pvm/")

    def test_absent_is_none(self):
        self.assertIsNone(winvm.parse_home_path("State: running\n"))


class ParseIsolatedFlag(unittest.TestCase):
    def test_one_is_isolated(self):
        self.assertIs(winvm.parse_isolated_flag("<Foo>1</Foo><IsolatedVm>1</IsolatedVm>"), True)

    def test_zero_is_not_isolated(self):
        self.assertIs(winvm.parse_isolated_flag("<IsolatedVm>0</IsolatedVm>"), False)

    def test_absent_is_none(self):
        # 「隔離されていない」と「読めなかった」を混同しない。
        self.assertIsNone(winvm.parse_isolated_flag("<Other>1</Other>"))


class ParseToolsState(unittest.TestCase):
    def test_reads_state_and_version(self):
        self.assertEqual(
            winvm.parse_tools_state(PRLCTL_INFO_TEXT), ("installed", "26.4.0-57513")
        )

    def test_state_without_version(self):
        self.assertEqual(
            winvm.parse_tools_state("GuestTools: state=not_installed\n"),
            ("not_installed", None),
        )

    def test_absent_is_none_pair(self):
        self.assertEqual(winvm.parse_tools_state("State: running\n"), (None, None))


class PrlctlArgv(unittest.TestCase):
    def test_list_argv_asks_for_json_and_full(self):
        # -f が無いと ip_configured が出ず、-j が無いと text-parse に戻る。
        self.assertEqual(winvm.prlctl_list_argv(), ["prlctl", "list", "-a", "-f", "-j"])

    def test_exec_argv_keeps_command_tokens_separate(self):
        # 実測: コマンドを 1 文字列に連結して渡すと exit 2 で無出力のまま黙って失敗する。
        got = winvm.prlctl_exec_argv("Staccato - Windows 11 ARM", ["cmd.exe", "/c", "ver"])
        self.assertEqual(
            got, ["prlctl", "exec", "Staccato - Windows 11 ARM", "cmd.exe", "/c", "ver"]
        )
        self.assertNotIn("cmd.exe /c ver", got)

    def test_info_argv_targets_one_vm(self):
        self.assertEqual(
            winvm.prlctl_info_argv("Staccato - Windows 11 ARM"),
            ["prlctl", "list", "-i", "Staccato - Windows 11 ARM"],
        )


class FakeRunner:
    """argv -> (rc, stdout, stderr) を返す差し替え可能なランナー。"""

    def __init__(self, responses: dict[tuple[str, ...], tuple[int, str, str]]):
        self.responses = responses
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> tuple[int, str, str]:
        self.calls.append(list(argv))
        return self.responses.get(tuple(argv), (127, "", "unexpected argv"))


def _resolve_args(vm: str | None) -> argparse.Namespace:
    return argparse.Namespace(vm=vm)


class CmdResolveIp(unittest.TestCase):
    def setUp(self):
        self.run = FakeRunner({tuple(winvm.prlctl_list_argv()): (0, PRLCTL_LIST_JSON, "")})
        os.environ.pop("WINVM_VM", None)

    def _capture(self, args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = winvm.cmd_resolve_ip(args, run=self.run)
        return rc, out.getvalue(), err.getvalue()

    def test_prints_ip_for_running_vm(self):
        rc, out, _ = self._capture(_resolve_args("Staccato - Windows 11 ARM"))
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "10.211.55.3")

    def test_resolves_by_uuid_too(self):
        rc, out, _ = self._capture(_resolve_args("4eb49f98-7f09-4b43-a3f1-35f285ad4d26"))
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "10.211.55.3")

    def test_missing_identifier_is_exit_2(self):
        rc, _, err = self._capture(_resolve_args(None))
        self.assertEqual(rc, 2)
        self.assertIn("--vm", err)

    def test_unknown_vm_is_exit_1_and_lists_known_names(self):
        rc, _, err = self._capture(_resolve_args("no-such-vm"))
        self.assertEqual(rc, 1)
        self.assertIn("Staccato - Windows 11 ARM", err)  # 診断に実在の候補を出す

    def test_stopped_vm_is_exit_1_and_says_not_running(self):
        rc, _, err = self._capture(_resolve_args("Coda - macOS"))
        self.assertEqual(rc, 1)
        self.assertIn("stopped", err)

    def test_prlctl_failure_is_exit_1_and_does_not_print_an_ip(self):
        self.run = FakeRunner({tuple(winvm.prlctl_list_argv()): (1, "", "prlctl boom")})
        rc, out, err = self._capture(_resolve_args("Staccato - Windows 11 ARM"))
        self.assertEqual(rc, 1)
        self.assertEqual(out.strip(), "")
        self.assertIn("prlctl", err)

    def test_env_var_supplies_identifier(self):
        os.environ["WINVM_VM"] = "Staccato - Windows 11 ARM"
        try:
            rc, out, _ = self._capture(_resolve_args(None))
        finally:
            os.environ.pop("WINVM_VM", None)
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "10.211.55.3")

    def test_argument_beats_env_var(self):
        os.environ["WINVM_VM"] = "Coda - macOS"
        try:
            rc, out, _ = self._capture(_resolve_args("Staccato - Windows 11 ARM"))
        finally:
            os.environ.pop("WINVM_VM", None)
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "10.211.55.3")


class DoctorReport(unittest.TestCase):
    def test_report_shows_observed_value_for_every_check(self):
        # 「OK/NG だけ」ではなく実測値を並べる。空や緑を健全の根拠にしないため。
        checks = [
            winvm.Check("VM", "Staccato - Windows 11 ARM (4eb49f98)", True),
            winvm.Check("host isolation", "on", False, hint='prlctl set "<vm>" --isolate-vm off'),
            winvm.Check("Parallels Tools", "installed 26.4.0-57513", None),
        ]
        report = winvm.format_doctor_report(checks)
        self.assertIn("Staccato - Windows 11 ARM (4eb49f98)", report)
        self.assertIn("installed 26.4.0-57513", report)
        self.assertIn("on", report)

    def test_failing_check_renders_its_hint(self):
        checks = [winvm.Check("host isolation", "on", False, hint="RUN THIS TO FIX")]
        self.assertIn("RUN THIS TO FIX", winvm.format_doctor_report(checks))

    def test_informational_check_has_no_hint_noise(self):
        checks = [winvm.Check("Parallels Tools", "installed", None, hint="unused")]
        self.assertNotIn("unused", winvm.format_doctor_report(checks))

    def test_exit_code_is_1_when_any_check_failed(self):
        checks = [winvm.Check("a", "x", True), winvm.Check("b", "y", False)]
        self.assertEqual(winvm.doctor_exit_code(checks), 1)

    def test_exit_code_is_0_when_no_check_failed(self):
        checks = [winvm.Check("a", "x", True), winvm.Check("b", "y", None)]
        self.assertEqual(winvm.doctor_exit_code(checks), 0)

    def test_informational_check_alone_does_not_fail(self):
        self.assertEqual(winvm.doctor_exit_code([winvm.Check("a", "x", None)]), 0)


class CollectDoctorChecks(unittest.TestCase):
    """doctor は「OK/NG」ではなく観測値を並べる。未確認は OK でも NG でもなく情報扱い。"""

    def _runner(self, *, info=PRLCTL_INFO_TEXT, exec_rc=0, list_rc=0, list_out=PRLCTL_LIST_JSON):
        vm = "Staccato - Windows 11 ARM"
        return FakeRunner(
            {
                tuple(winvm.prlctl_list_argv()): (list_rc, list_out, "prlctl boom"),
                tuple(winvm.prlctl_info_argv(vm)): (0, info, ""),
                tuple(winvm.prlctl_exec_argv(vm, ["cmd.exe", "/c", "ver"])): (
                    exec_rc,
                    "Microsoft Windows [Version 10.0.26200.8875]",
                    "",
                ),
            }
        )

    def _bundle(self, tmp: str, isolated: str | None) -> str:
        """Home として使える一時バンドルを作り、そのパスを返す。"""
        bundle = Path(tmp) / "vm.pvm"
        bundle.mkdir()
        if isolated is not None:
            (bundle / "config.pvs").write_text(
                f"<ParallelsVirtualMachine><IsolatedVm>{isolated}</IsolatedVm>"
                "</ParallelsVirtualMachine>",
                encoding="utf-8",
            )
        return str(bundle) + "/"

    def _checks(self, *, isolated="0", **kw):
        with TemporaryDirectory() as tmp:
            home = self._bundle(tmp, isolated)
            info = PRLCTL_INFO_TEXT.replace(
                "Home: /Users/example/Parallels/Staccato - Windows 11 ARM.pvm/", f"Home: {home}"
            )
            return winvm.collect_doctor_checks(
                "Staccato - Windows 11 ARM", run=self._runner(info=info, **kw)
            )

    def _by_label(self, checks, label):
        for c in checks:
            if c.label == label:
                return c
        raise AssertionError(f"no check labelled {label!r}: {[c.label for c in checks]}")

    def test_healthy_vm_has_no_failing_check(self):
        checks = self._checks()
        self.assertEqual(winvm.doctor_exit_code(checks), 0)

    def test_reports_vm_name_and_uuid(self):
        c = self._by_label(self._checks(), "VM")
        self.assertIn("Staccato - Windows 11 ARM", c.observed)
        self.assertIn("4eb49f98-7f09-4b43-a3f1-35f285ad4d26", c.observed)

    def test_isolated_vm_fails_with_the_fix_command(self):
        c = self._by_label(self._checks(isolated="1"), "host isolation")
        self.assertEqual(c.observed, "on")
        self.assertIs(c.ok, False)
        self.assertIn("--isolate-vm off", c.hint)

    def test_isolation_off_passes(self):
        c = self._by_label(self._checks(isolated="0"), "host isolation")
        self.assertEqual(c.observed, "off")
        self.assertIs(c.ok, True)

    def test_unreadable_isolation_is_informational_not_ok(self):
        # 読めなかったことを「隔離されていない」と読み替えない。
        c = self._by_label(self._checks(isolated=None), "host isolation")
        self.assertIsNone(c.ok)
        self.assertEqual(winvm.doctor_exit_code(self._checks(isolated=None)), 0)

    def test_failing_prlctl_exec_is_a_failure(self):
        c = self._by_label(self._checks(exec_rc=2), "prlctl exec")
        self.assertIs(c.ok, False)

    def test_prlctl_list_failure_short_circuits(self):
        checks = winvm.collect_doctor_checks(
            "Staccato - Windows 11 ARM", run=self._runner(list_rc=1, list_out="")
        )
        self.assertEqual(winvm.doctor_exit_code(checks), 1)
        self.assertIn("prlctl", winvm.format_doctor_report(checks))

    def test_unknown_vm_lists_registered_names(self):
        checks = winvm.collect_doctor_checks("no-such-vm", run=self._runner())
        self.assertEqual(winvm.doctor_exit_code(checks), 1)
        self.assertIn("Staccato - Windows 11 ARM", winvm.format_doctor_report(checks))

    def test_stopped_vm_status_check_fails(self):
        checks = winvm.collect_doctor_checks("Coda - macOS", run=self._runner())
        self.assertIs(self._by_label(checks, "status").ok, False)

    def test_ssh_probe_only_runs_when_host_given(self):
        without = winvm.collect_doctor_checks("Staccato - Windows 11 ARM", run=self._runner())
        self.assertNotIn("ssh", [c.label.split()[0] for c in without])
        with_host = winvm.collect_doctor_checks(
            "Staccato - Windows 11 ARM",
            host="relay-winvm",
            run=self._runner(),
            ssh_probe=lambda host: True,
        )
        self.assertIn("ssh relay-winvm", [c.label for c in with_host])


class FilesToSync(unittest.TestCase):
    def test_dedups_blank_strips_sorts(self):
        got = winvm.files_to_sync("b/x.rs\na.rs\n", "a.rs\n\n  c.rs  \n", "d.rs\n")
        self.assertEqual(got, ["a.rs", "b/x.rs", "c.rs", "d.rs"])

    def test_empty_inputs_return_empty(self):
        self.assertEqual(winvm.files_to_sync("", "  \n", "\n"), [])


class FilesToDelete(unittest.TestCase):
    def test_unions_dedups_strips_sorts(self):
        got = winvm.files_to_delete("b/old.ts\na/gone.rs\n", "a/gone.rs\n\n  c/x.ts  \n")
        self.assertEqual(got, ["a/gone.rs", "b/old.ts", "c/x.ts"])

    def test_empty_inputs_return_empty(self):
        self.assertEqual(winvm.files_to_delete("", "  \n"), [])


class RemoteCommandBuilders(unittest.TestCase):
    def test_delete_commands_are_one_per_file_with_windows_paths(self):
        # cmd の & 連結バグ回避のため 1 ファイル 1 独立コマンド、パスは \ 区切り。
        cmds = winvm.remote_delete_commands("C:\\repo", ["src/hooks/useOld.ts", "Cargo.lock"])
        self.assertEqual(
            cmds,
            [
                'if exist "C:\\repo\\Cargo.lock" del /f /q "C:\\repo\\Cargo.lock"',
                'if exist "C:\\repo\\src\\hooks\\useOld.ts" del /f /q '
                '"C:\\repo\\src\\hooks\\useOld.ts"',
            ],
        )

    def test_delete_commands_empty_is_empty(self):
        self.assertEqual(winvm.remote_delete_commands("C:\\repo", []), [])

    def test_parent_mkdir_commands_one_per_parent_deduped(self):
        files = ["crates/xtask/src/a.rs", "crates/xtask/src/b.rs", "crates/core/c.rs"]
        self.assertEqual(
            winvm.parent_mkdir_commands("C:\\repo", files),
            [
                'if not exist "C:\\repo\\crates\\core" mkdir "C:\\repo\\crates\\core"',
                'if not exist "C:\\repo\\crates\\xtask\\src" mkdir '
                '"C:\\repo\\crates\\xtask\\src"',
            ],
        )

    def test_parent_mkdir_commands_root_only_is_empty(self):
        self.assertEqual(
            winvm.parent_mkdir_commands("C:\\repo", ["Cargo.toml", "README.md"]), []
        )

    def test_reset_command(self):
        self.assertEqual(
            winvm.remote_reset_command("C:\\repo"),
            'cd /d "C:\\repo" && git checkout -- . && git clean -fd',
        )
        # negative case: 別 repo は別出力 (決め打ちでない)
        self.assertEqual(
            winvm.remote_reset_command("D:\\other"),
            'cd /d "D:\\other" && git checkout -- . && git clean -fd',
        )

    def test_exec_command(self):
        self.assertEqual(
            winvm.remote_exec_command("C:\\repo", "cargo xtask check-desktop"),
            'cd /d "C:\\repo" && cargo xtask check-desktop',
        )
        self.assertEqual(
            winvm.remote_exec_command("D:\\other", "echo hi"), 'cd /d "D:\\other" && echo hi'
        )

    def test_to_windows_path(self):
        self.assertEqual(winvm.to_windows_path("C:/proj/app"), "C:\\proj\\app")
        self.assertEqual(winvm.to_windows_path("a"), "a")

    def test_resolve_diff_base(self):
        self.assertEqual(winvm.resolve_diff_base("abc123", True, "main"), "abc123")
        self.assertEqual(winvm.resolve_diff_base("deadbeef", False, "main"), "main")


class RemoteCommandFromArgs(unittest.TestCase):
    def test_strips_leading_separator(self):
        self.assertEqual(
            winvm.remote_command_from_args(["--", "cargo xtask check-desktop"]),
            "cargo xtask check-desktop",
        )

    def test_without_separator(self):
        self.assertEqual(
            winvm.remote_command_from_args(["cargo", "xtask", "check-desktop"]),
            "cargo xtask check-desktop",
        )

    def test_empty_is_none(self):
        self.assertIsNone(winvm.remote_command_from_args([]))
        self.assertIsNone(winvm.remote_command_from_args(["--"]))


class HealthPowershell(unittest.TestCase):
    def test_includes_encoding_and_requested_tools(self):
        ps = winvm.build_health_powershell(["node", "cargo"], "C:/proj/app")
        self.assertIn("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8", ps)
        self.assertIn("fsutil dirty query C:", ps)
        self.assertIn("'node'", ps)
        self.assertIn("'cargo'", ps)
        self.assertIn("C:/proj/app", ps)

    def test_no_repo_omits_repo_section(self):
        self.assertNotIn("repo state", winvm.build_health_powershell(["node"], None))

    def test_exec_command_uses_pwsh_file_without_bypass(self):
        # WinPS 5.1 の Restricted を -ExecutionPolicy Bypass で上書きせず、
        # RemoteSigned の pwsh(7) に scp 転送物 (Mark-of-the-Web 無し) を渡す。
        remote = "C:/Users/Public/winvm_health.ps1"
        cmd = winvm.health_exec_command(remote)
        self.assertEqual(cmd, f"pwsh -NoProfile -File {remote}")
        self.assertNotIn("-ExecutionPolicy Bypass", cmd)
        self.assertNotIn("powershell", cmd)  # pwsh(7) であって WinPS 5.1 ではない
        self.assertEqual(winvm.health_exec_command("D:/x.ps1"), "pwsh -NoProfile -File D:/x.ps1")

    def test_cleanup_command_uses_pwsh(self):
        remote = "C:/Users/Public/winvm_health.ps1"
        cmd = winvm.health_cleanup_command(remote)
        self.assertEqual(cmd, f"pwsh -NoProfile -Command Remove-Item -Force {remote}")
        self.assertNotIn("-ExecutionPolicy Bypass", cmd)

    def test_pwsh_probe_is_quiet(self):
        self.assertEqual(winvm.pwsh_probe_command(), "where pwsh >nul 2>nul")


class CmdHealth(unittest.TestCase):
    def _run(self, run):
        args = argparse.Namespace(host="vm", repo=None, check_tools=None)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = winvm.cmd_health(args, run=run)
        return rc, err.getvalue()

    def test_errors_when_pwsh_absent_and_does_not_transfer(self):
        calls: list[str] = []

        def fake_run(host, remote):
            calls.append(remote)
            return False

        rc, err = self._run(fake_run)
        self.assertEqual(rc, 1)
        self.assertEqual(calls, [winvm.pwsh_probe_command()])  # probe だけ、scp/exec は走らない
        self.assertIn("pwsh", err)

    def test_probe_error_message_names_both_causes(self):
        # probe 失敗は SSH 未到達 (VM 未起動 / stale IP) でも起きる。pwsh 未導入と断定しない。
        rc, err = self._run(lambda host, remote: False)
        self.assertEqual(rc, 1)
        self.assertIn("pwsh", err)
        self.assertIn("未起動", err)

    def test_missing_host_is_exit_2(self):
        args = argparse.Namespace(host=None, repo=None, check_tools=None)
        os.environ.pop("WINVM_HOST", None)
        err = io.StringIO()
        with redirect_stderr(err):
            rc = winvm.cmd_health(args, run=lambda h, r: True)
        self.assertEqual(rc, 2)
        self.assertIn("--host", err.getvalue())


class ParserSurface(unittest.TestCase):
    def test_vmware_subcommands_are_gone(self):
        # Fusion 一本化の解除。recover は Parallels に対応する失敗モードが無いため削除した。
        parser = winvm.build_parser()
        actions = [a for a in parser._actions if hasattr(a, "choices") and a.choices]
        names = set()
        for a in actions:
            names |= set(a.choices)
        self.assertEqual(names, {"resolve-ip", "run", "health", "doctor"})

    def test_resolve_ip_rejects_the_legacy_identifier_flag(self):
        # argparse は拒否時に usage を stderr へ書くので、テスト出力を汚さないよう捨てる。
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            winvm.build_parser().parse_args(["resolve-ip", "--vmx", "x"])

    def test_resolve_ip_accepts_vm(self):
        args = winvm.build_parser().parse_args(["resolve-ip", "--vm", "Staccato"])
        self.assertEqual(args.vm, "Staccato")


class NoLegacyHypervisorResidue(unittest.TestCase):
    def test_source_mentions_no_legacy_identifiers(self):
        # 移行の取りこぼし検出。旧ハイパーバイザ固有の識別子が残っていたら移植漏れ。
        # assertNotIn にソース全体を渡すと失敗時に本文を丸ごと吐くので、
        # 見つかったトークン名だけを比較する。
        src = Path(winvm.__file__).read_text(encoding="utf-8").lower()
        residue = [t for t in ("vmx", "vmware", "winvm_leases", "dhcpd", ".lck") if t in src]
        self.assertEqual(residue, [])


if __name__ == "__main__":
    unittest.main()
