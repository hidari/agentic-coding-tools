"""macvm の仕様。

依存を増やさないため stdlib の unittest だけで書く (`python3 -m unittest`)。
macvm.py 自身も `dependencies = []` で、CI も runner の python3 をそのまま使う。

Parallels の出力に由来する固定値は、実機 (Parallels Desktop 27.0.0-58628 / macOS ゲスト)
で採取した生の値の形をそのまま使う。整形した理想形を書くと、実際の出力との差で壊れる。
"""
from __future__ import annotations

import argparse
import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import macvm

# 実機 `prlctl list -a -i -j` の出力から、macvm が読むキーを抜き出したもの。
# 停止中の空 ipAddresses と、version キーごと欠ける GuestTools は実際の出力のままにしてある。
PRLCTL_LIST_JSON = """[
\t{
\t\t"ID": "aaaa1111-bbbb-4ccc-8ddd-eeee22223333",
\t\t"Name": "Fixture Stopped - macOS",
\t\t"State": "stopped",
\t\t"Home": "/Users/example/Parallels/Fixture Stopped - macOS.macvm/",
\t\t"GuestTools": {"state": "not_installed"},
\t\t"Network": {"Conditioned": "off", "ipAddresses": []}
\t},
\t{
\t\t"ID": "ffff4444-aaaa-4bbb-8ccc-dddd55556666",
\t\t"Name": "Fixture Guest - macOS",
\t\t"State": "running",
\t\t"Home": "/Users/example/Parallels/Fixture Guest - macOS.macvm/",
\t\t"GuestTools": {"state": "installed", "version": "27.0.0-58628"},
\t\t"Network": {"Conditioned": "off", "ipAddresses": [
\t\t\t{"type": "ipv4", "ip": "10.211.55.5"},
\t\t\t{"type": "ipv6", "ip": "fdb2:2c26:f4e4:0:14b2:c0a1:9f3e:2201"}
\t\t]}
\t}
]
"""

RUNNING_VM = "Fixture Guest - macOS"
STOPPED_VM = "Fixture Stopped - macOS"

# DHCP が応答しなかった VM。ipAddresses は埋まるので「IP は取れている」経路を通る。
APIPA_LIST_JSON = PRLCTL_LIST_JSON.replace('"ip": "10.211.55.5"', '"ip": "169.254.10.20"')
# 置換元の値を再掲する形なので、フィクスチャ側を変えると replace が黙って no-op になる。
# 空振りを import 時に落とす。
assert APIPA_LIST_JSON != PRLCTL_LIST_JSON


class FakeRunner:
    """argv -> (rc, stdout, stderr) を返す差し替え可能なランナー。

    教えていない argv は例外にする。既定値を返すと、テストが「fake に教えていない」
    ではなく「prlctl がその rc で失敗した」という別の意味に化けて、失敗経路を
    検証しているテストが緑のまま通り得る。
    """

    def __init__(self, responses: dict[tuple[str, ...], tuple[int, str, str]]):
        self.responses = responses
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> tuple[int, str, str]:
        self.calls.append(list(argv))
        key = tuple(argv)
        if key not in self.responses:
            raise AssertionError(f"FakeRunner に登録されていない argv: {argv}")
        return self.responses[key]


def capture_io(call) -> tuple[int, str, str]:
    """(rc, stdout, stderr) を返す。"""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = call()
    return rc, out.getvalue(), err.getvalue()


class ParseVmList(unittest.TestCase):
    def test_parses_prlctl_json_into_records(self):
        vms = macvm.parse_vm_list(PRLCTL_LIST_JSON)
        self.assertEqual([v["Name"] for v in vms], [STOPPED_VM, RUNNING_VM])

    def test_empty_output_is_empty_list(self):
        self.assertEqual(macvm.parse_vm_list(""), [])

    def test_malformed_json_is_empty_list(self):
        self.assertEqual(macvm.parse_vm_list("{not json"), [])

    def test_non_list_json_is_empty_list(self):
        self.assertEqual(macvm.parse_vm_list('{"Name": "x"}'), [])


class FindVm(unittest.TestCase):
    def setUp(self):
        self.vms = macvm.parse_vm_list(PRLCTL_LIST_JSON)

    def test_matches_exact_name_including_spaces(self):
        vm = macvm.find_vm(self.vms, RUNNING_VM)
        self.assertIsNotNone(vm)
        self.assertEqual(vm["State"], "running")

    def test_matches_bare_uuid(self):
        vm = macvm.find_vm(self.vms, "ffff4444-aaaa-4bbb-8ccc-dddd55556666")
        self.assertEqual(vm["Name"], RUNNING_VM)

    def test_matches_braced_uuid(self):
        vm = macvm.find_vm(self.vms, "{ffff4444-aaaa-4bbb-8ccc-dddd55556666}")
        self.assertEqual(vm["Name"], RUNNING_VM)

    def test_uuid_match_is_case_insensitive(self):
        vm = macvm.find_vm(self.vms, "FFFF4444-AAAA-4BBB-8CCC-DDDD55556666")
        self.assertEqual(vm["Name"], RUNNING_VM)

    def test_name_match_is_exact_not_substring(self):
        # 部分一致を許すと prlctl と指す VM がずれる。
        self.assertIsNone(macvm.find_vm(self.vms, "Fixture Guest"))

    def test_unknown_identifier_is_none(self):
        self.assertIsNone(macvm.find_vm(self.vms, "no-such-vm"))


class PickIpv4(unittest.TestCase):
    def setUp(self):
        self.vms = macvm.parse_vm_list(PRLCTL_LIST_JSON)

    def test_picks_the_ipv4_entry_from_a_mixed_list(self):
        vm = macvm.find_vm(self.vms, RUNNING_VM)
        self.assertEqual(macvm.pick_ipv4(vm["Network"]), "10.211.55.5")

    def test_stopped_vm_has_no_addresses(self):
        vm = macvm.find_vm(self.vms, STOPPED_VM)
        self.assertIsNone(macvm.pick_ipv4(vm["Network"]))

    def test_missing_network_is_none(self):
        self.assertIsNone(macvm.pick_ipv4(None))

    def test_ipv6_only_is_none(self):
        network = {"ipAddresses": [{"type": "ipv6", "ip": "fe80::1"}]}
        self.assertIsNone(macvm.pick_ipv4(network))

    def test_falls_through_bad_entry_to_a_good_one(self):
        network = {"ipAddresses": [{"type": "ipv4"}, {"type": "ipv4", "ip": "10.0.0.9"}]}
        self.assertEqual(macvm.pick_ipv4(network), "10.0.0.9")


class IsApipa(unittest.TestCase):
    def test_link_local_is_apipa(self):
        self.assertTrue(macvm.is_apipa("169.254.10.20"))

    def test_routable_address_is_not(self):
        self.assertFalse(macvm.is_apipa("10.211.55.5"))

    def test_non_address_is_not(self):
        self.assertFalse(macvm.is_apipa("not-an-ip"))


class PrlctlArgv(unittest.TestCase):
    def test_list_argv_asks_for_full_info_as_json(self):
        self.assertEqual(macvm.prlctl_list_argv(), ["prlctl", "list", "-a", "-i", "-j"])

    def test_capture_argv_uses_the_file_flag(self):
        argv = macvm.prlctl_capture_argv("VM", "/tmp/x.png")
        self.assertEqual(argv, ["prlctl", "capture", "VM", "--file", "/tmp/x.png"])


class SshOptions(unittest.TestCase):
    def test_options_prevent_blocking_on_a_prompt(self):
        self.assertIn("BatchMode=yes", macvm.SSH_OPTS)
        self.assertIn("ConnectTimeout=10", macvm.SSH_OPTS)


class RemoteCommandFromArgs(unittest.TestCase):
    """argparse REMAINDER が残す '--' の扱い。

    除かないとスクリプトの 1 行目が '--' で始まり、パイプの左辺だけが失敗する。
    2 行目以降と exit code は流れるので、出力が 1 つ欠けただけの成功に見える。
    """

    def test_leading_double_dash_is_dropped(self):
        self.assertEqual(macvm.remote_command_from_args(["--", "echo", "x"]), "echo x")

    def test_command_without_separator_is_kept(self):
        self.assertEqual(macvm.remote_command_from_args(["echo", "x"]), "echo x")

    def test_double_dash_inside_the_command_is_kept(self):
        # 2 つ目以降の '--' はコマンド自身の引数なので落とさない。
        self.assertEqual(macvm.remote_command_from_args(["--", "ls", "--", "x"]), "ls -- x")

    def test_only_the_separator_is_none(self):
        self.assertIsNone(macvm.remote_command_from_args(["--"]))

    def test_empty_is_none(self):
        self.assertIsNone(macvm.remote_command_from_args([]))

    def test_none_is_none(self):
        self.assertIsNone(macvm.remote_command_from_args(None))


class RemoteSizeCommand(unittest.TestCase):
    def test_quotes_paths_with_spaces(self):
        cmd = macvm.remote_size_command("/tmp/a b.txt")
        self.assertIn("'/tmp/a b.txt'", cmd)

    def test_missing_file_falls_to_an_ascii_mark(self):
        # locale で変わる stat のエラー文を判定に混ぜない。
        self.assertIn(macvm.REMOTE_MISSING_MARK, macvm.remote_size_command("/tmp/x"))

    def test_uses_bsd_stat_format(self):
        # macOS の stat は -f %z。Linux の -c %s ではない。
        self.assertIn("stat -f %z", macvm.remote_size_command("/tmp/x"))


class ParseRemoteSize(unittest.TestCase):
    def test_digits_become_an_int(self):
        self.assertEqual(macvm.parse_remote_size("28\n"), 28)

    def test_missing_mark_is_none(self):
        self.assertIsNone(macvm.parse_remote_size(macvm.REMOTE_MISSING_MARK))

    def test_unexpected_output_is_none(self):
        self.assertIsNone(macvm.parse_remote_size("stat: No such file"))

    def test_empty_is_none(self):
        self.assertIsNone(macvm.parse_remote_size(""))


class RemoteParentMkdirCommand(unittest.TestCase):
    def test_creates_the_parent_directory(self):
        cmd = macvm.remote_parent_mkdir_command("/tmp/deep/x.txt")
        self.assertEqual(cmd, "mkdir -p /tmp/deep")

    def test_root_parent_is_skipped(self):
        self.assertIsNone(macvm.remote_parent_mkdir_command("/x.txt"))

    def test_relative_bare_name_is_skipped(self):
        self.assertIsNone(macvm.remote_parent_mkdir_command("x.txt"))

    def test_quotes_paths_with_spaces(self):
        cmd = macvm.remote_parent_mkdir_command("/tmp/a b/x.txt")
        self.assertEqual(cmd, "mkdir -p '/tmp/a b'")


class ConsoleOwnerCommand(unittest.TestCase):
    """GUI (Aqua) セッションの観測。

    ログイン画面のままだと /dev/console の所有者は root で、その状態では GUI アプリを
    起動しても画面に出ない。root を ASCII の目印へ倒して呼び出し側が読めるようにする。
    """

    def test_reads_the_console_owner(self):
        self.assertIn("stat -f %Su /dev/console", macvm.console_owner_command())

    def test_root_is_mapped_to_a_mark(self):
        self.assertIn(macvm.NO_AQUA_MARK, macvm.console_owner_command())


class BuildExecShell(unittest.TestCase):
    def test_command_is_passed_through_verbatim(self):
        # シェルを経由せずファイルへ書くので、クォートもパイプもそのまま残す。
        body = macvm.build_exec_shell("echo x | tr a-z A-Z")
        self.assertIn("echo x | tr a-z A-Z", body)

    def test_body_ends_with_a_newline(self):
        self.assertTrue(macvm.build_exec_shell("echo x").endswith("\n"))

    def test_existing_trailing_newline_is_not_doubled(self):
        self.assertEqual(macvm.build_exec_shell("echo x\n"), "echo x\n")


class BuildHealthShell(unittest.TestCase):
    def test_reports_observed_values_not_just_verdicts(self):
        body = macvm.build_health_shell([], None)
        self.assertIn("os_version=", body)
        self.assertIn("arch=", body)
        self.assertIn("disk_avail=", body)
        self.assertIn("console_owner=", body)

    def test_missing_tool_sets_the_failure_flag(self):
        body = macvm.build_health_shell(["cargo"], None)
        self.assertIn("tool_cargo=MISSING", body)
        self.assertIn("fail=1", body)

    def test_repo_check_is_omitted_when_not_requested(self):
        self.assertNotIn("repo=", macvm.build_health_shell([], None))

    def test_repo_check_is_included_when_requested(self):
        body = macvm.build_health_shell([], "/Users/example/repo")
        self.assertIn("repo=", body)

    def test_exit_code_comes_from_the_flag(self):
        self.assertTrue(macvm.build_health_shell([], None).rstrip().endswith("exit $fail"))


class FormatDoctorReport(unittest.TestCase):
    def test_every_line_carries_its_observed_value(self):
        checks = [macvm.Check("IP", "10.211.55.5", ok=True)]
        self.assertIn("10.211.55.5", macvm.format_doctor_report(checks))

    def test_hint_is_shown_only_for_failures(self):
        ok = [macvm.Check("IP", "10.211.55.5", ok=True, hint="ヒント")]
        ng = [macvm.Check("IP", "未割当", ok=False, hint="ヒント")]
        self.assertNotIn("ヒント", macvm.format_doctor_report(ok))
        self.assertIn("ヒント", macvm.format_doctor_report(ng))

    def test_unknown_state_is_neither_ok_nor_fail(self):
        checks = [macvm.Check("SSH", "未確認", ok=None)]
        report = macvm.format_doctor_report(checks)
        self.assertIn("[ -- ]", report)


class DoctorExitCode(unittest.TestCase):
    def test_any_failure_is_exit_1(self):
        checks = [macvm.Check("a", "x", ok=True), macvm.Check("b", "y", ok=False)]
        self.assertEqual(macvm.doctor_exit_code(checks), 1)

    def test_unknown_alone_is_exit_0(self):
        # 「確認できなかった」は失敗ではない。
        checks = [macvm.Check("a", "x", ok=True), macvm.Check("b", "y", ok=None)]
        self.assertEqual(macvm.doctor_exit_code(checks), 0)


class CollectDoctorChecks(unittest.TestCase):
    def setUp(self):
        self.run = FakeRunner({tuple(macvm.prlctl_list_argv()): (0, PRLCTL_LIST_JSON, "")})

    def _labels(self, checks):
        return [c.label for c in checks]

    def test_running_vm_without_host_stops_before_ssh(self):
        checks = macvm.collect_doctor_checks(RUNNING_VM, None, run=self.run)
        ssh = [c for c in checks if c.label == "SSH"]
        self.assertEqual(len(ssh), 1)
        self.assertIsNone(ssh[0].ok)  # 未確認であって失敗ではない

    def test_stopped_vm_fails_on_state_and_reports_it(self):
        checks = macvm.collect_doctor_checks(STOPPED_VM, None, run=self.run)
        state = next(c for c in checks if c.label == "VM 状態")
        self.assertFalse(state.ok)
        self.assertEqual(state.observed, "stopped")

    def test_stopped_vm_reports_missing_ip_rather_than_silence(self):
        checks = macvm.collect_doctor_checks(STOPPED_VM, None, run=self.run)
        ip = next(c for c in checks if c.label == "IP")
        self.assertFalse(ip.ok)

    def test_apipa_is_flagged_as_a_failure(self):
        run = FakeRunner({tuple(macvm.prlctl_list_argv()): (0, APIPA_LIST_JSON, "")})
        checks = macvm.collect_doctor_checks(RUNNING_VM, None, run=run)
        ip = next(c for c in checks if c.label == "IP")
        self.assertFalse(ip.ok)
        self.assertEqual(ip.observed, "169.254.10.20")

    def test_unknown_vm_lists_the_known_names(self):
        checks = macvm.collect_doctor_checks("no-such-vm", None, run=self.run)
        self.assertIn(RUNNING_VM, checks[0].observed)

    def test_prlctl_failure_is_a_single_failed_check(self):
        run = FakeRunner({tuple(macvm.prlctl_list_argv()): (1, "", "boom")})
        checks = macvm.collect_doctor_checks(RUNNING_VM, None, run=run)
        self.assertEqual(len(checks), 1)
        self.assertFalse(checks[0].ok)

    def test_unreachable_ssh_stops_before_the_gui_check(self):
        checks = macvm.collect_doctor_checks(
            RUNNING_VM, "somehost", run=self.run, ssh_probe=lambda h: False
        )
        self.assertNotIn("GUI セッション", self._labels(checks))

    def test_login_window_is_reported_as_no_gui_session(self):
        checks = macvm.collect_doctor_checks(
            RUNNING_VM,
            "somehost",
            run=self.run,
            ssh_probe=lambda h: True,
            console_probe=lambda h, c: macvm.NO_AQUA_MARK,
        )
        gui = next(c for c in checks if c.label == "GUI セッション")
        self.assertFalse(gui.ok)

    def test_logged_in_console_owner_is_reported_as_ok(self):
        checks = macvm.collect_doctor_checks(
            RUNNING_VM,
            "somehost",
            run=self.run,
            ssh_probe=lambda h: True,
            console_probe=lambda h, c: "someuser\n",
        )
        gui = next(c for c in checks if c.label == "GUI セッション")
        self.assertTrue(gui.ok)
        self.assertEqual(gui.observed, "someuser")


class CmdResolveIp(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ))
        os.environ.pop("MACVM_VM", None)
        self.run = FakeRunner({tuple(macvm.prlctl_list_argv()): (0, PRLCTL_LIST_JSON, "")})

    def _resolve(self, vm, run=None):
        args = argparse.Namespace(vm=vm)
        return capture_io(lambda: macvm.cmd_resolve_ip(args, run=run or self.run))

    def test_prints_ip_for_running_vm(self):
        rc, out, _ = self._resolve(RUNNING_VM)
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "10.211.55.5")

    def test_missing_identifier_is_exit_2(self):
        rc, _, err = self._resolve(None)
        self.assertEqual(rc, 2)
        self.assertIn("--vm", err)

    def test_unknown_vm_is_exit_1_and_lists_known_names(self):
        rc, _, err = self._resolve("no-such-vm")
        self.assertEqual(rc, 1)
        self.assertIn(RUNNING_VM, err)

    def test_stopped_vm_is_exit_1_with_the_start_hint(self):
        rc, _, err = self._resolve(STOPPED_VM)
        self.assertEqual(rc, 1)
        self.assertIn("prlctl start", err)

    def test_apipa_still_prints_the_ip_on_stdout(self):
        # ProxyCommand の中で動くので stdout と exit code は変えない。警告は stderr へ。
        run = FakeRunner({tuple(macvm.prlctl_list_argv()): (0, APIPA_LIST_JSON, "")})
        rc, out, err = self._resolve(RUNNING_VM, run=run)
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "169.254.10.20")
        self.assertIn("APIPA", err)

    def test_env_var_supplies_the_vm_when_the_flag_is_absent(self):
        os.environ["MACVM_VM"] = RUNNING_VM
        rc, out, _ = self._resolve(None)
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "10.211.55.5")


class CmdScreenshot(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ))
        os.environ.pop("MACVM_VM", None)
        self.tmp = self.enterContext(TemporaryDirectory())
        self.out = Path(self.tmp) / "shot.png"

    def _runner(self, capture_result, *, writes: bytes | None = None):
        list_key = tuple(macvm.prlctl_list_argv())
        cap_key = tuple(macvm.prlctl_capture_argv(RUNNING_VM, str(self.out)))

        def run(argv):
            key = tuple(argv)
            if key == list_key:
                return (0, PRLCTL_LIST_JSON, "")
            if key == cap_key:
                if writes is not None:
                    self.out.write_bytes(writes)
                return capture_result
            raise AssertionError(f"未登録の argv: {argv}")

        return run

    def _shot(self, vm, run):
        args = argparse.Namespace(vm=vm, out=str(self.out))
        return capture_io(lambda: macvm.cmd_screenshot(args, run=run))

    def test_writes_the_file_and_reports_its_size(self):
        rc, out, _ = self._shot(RUNNING_VM, self._runner((0, "", ""), writes=b"x" * 10))
        self.assertEqual(rc, 0)
        self.assertIn("10 bytes", out)

    def test_stopped_vm_is_refused_before_capturing(self):
        run = self._runner((0, "", ""))
        args = argparse.Namespace(vm=STOPPED_VM, out=str(self.out))
        rc, _, err = capture_io(lambda: macvm.cmd_screenshot(args, run=run))
        self.assertEqual(rc, 1)
        self.assertIn("status=stopped", err)

    def test_capture_success_without_a_file_is_a_failure(self):
        # rc 0 を成功と読み替えない。撮れたつもりで空を通さない。
        rc, _, err = self._shot(RUNNING_VM, self._runner((0, "", "")))
        self.assertEqual(rc, 1)
        self.assertIn("ファイルがありません", err)

    def test_zero_byte_capture_is_a_failure(self):
        rc, _, err = self._shot(RUNNING_VM, self._runner((0, "", ""), writes=b""))
        self.assertEqual(rc, 1)
        self.assertIn("0 バイト", err)

    def test_capture_failure_is_reported_with_the_detail(self):
        rc, _, err = self._shot(RUNNING_VM, self._runner((1, "", "capture failed")))
        self.assertEqual(rc, 1)
        self.assertIn("capture failed", err)


class CmdPush(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ))
        os.environ.pop("MACVM_HOST", None)
        self.tmp = self.enterContext(TemporaryDirectory())
        self.local = Path(self.tmp) / "payload.bin"
        self.local.write_bytes(b"x" * 28)

    def _push(self, *, copy_ok=True, remote_size="28"):
        args = argparse.Namespace(host="h", local=str(self.local), remote="/tmp/p.bin")
        return capture_io(
            lambda: macvm.cmd_push(
                args,
                run=lambda h, c: True,
                copy=lambda h, l, d: copy_ok,
                capture=lambda h, c: remote_size,
            )
        )

    def test_matching_size_is_success(self):
        rc, out, _ = self._push()
        self.assertEqual(rc, 0)
        self.assertIn("28 bytes", out)

    def test_size_mismatch_is_a_failure(self):
        # scp の rc 0 を「完了」と読み替えない。途中切れを実体で検出する。
        rc, _, err = self._push(remote_size="12")
        self.assertEqual(rc, 1)
        self.assertIn("一致しません", err)

    def test_unreadable_remote_size_is_a_failure(self):
        rc, _, err = self._push(remote_size=macvm.REMOTE_MISSING_MARK)
        self.assertEqual(rc, 1)
        self.assertIn("取得できません", err)

    def test_scp_failure_is_reported(self):
        rc, _, err = self._push(copy_ok=False)
        self.assertEqual(rc, 1)
        self.assertIn("scp 失敗", err)

    def test_missing_local_file_is_refused_before_transfer(self):
        args = argparse.Namespace(host="h", local=str(Path(self.tmp) / "nope"), remote="/tmp/x")
        rc, _, err = capture_io(
            lambda: macvm.cmd_push(
                args, run=lambda h, c: True, copy=lambda h, l, d: True, capture=lambda h, c: "0"
            )
        )
        self.assertEqual(rc, 1)
        self.assertIn("ローカルファイルがありません", err)

    def test_missing_host_is_exit_2(self):
        args = argparse.Namespace(host=None, local=str(self.local), remote="/tmp/x")
        rc, _, err = capture_io(
            lambda: macvm.cmd_push(
                args, run=lambda h, c: True, copy=lambda h, l, d: True, capture=lambda h, c: "28"
            )
        )
        self.assertEqual(rc, 2)
        self.assertIn("--host", err)


class CmdPull(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ))
        os.environ.pop("MACVM_HOST", None)
        self.tmp = self.enterContext(TemporaryDirectory())
        self.local = Path(self.tmp) / "out.bin"

    def _pull(self, *, remote_size="28", written=b"x" * 28, copy_ok=True):
        args = argparse.Namespace(host="h", remote="/tmp/p.bin", local=str(self.local))

        def copy(h, r, l):
            if copy_ok:
                Path(l).write_bytes(written)
            return copy_ok

        return capture_io(
            lambda: macvm.cmd_pull(args, copy=copy, capture=lambda h, c: remote_size)
        )

    def test_matching_size_is_success(self):
        rc, out, _ = self._pull()
        self.assertEqual(rc, 0)
        self.assertIn("28 bytes", out)

    def test_absent_remote_is_refused_before_transfer(self):
        # 不在を後段のサイズ照合の失敗に化けさせない。
        rc, _, err = self._pull(remote_size=macvm.REMOTE_MISSING_MARK)
        self.assertEqual(rc, 1)
        self.assertIn("不在の可能性", err)

    def test_truncated_transfer_is_a_failure(self):
        rc, _, err = self._pull(written=b"x" * 12)
        self.assertEqual(rc, 1)
        self.assertIn("一致しません", err)

    def test_scp_failure_is_reported(self):
        rc, _, err = self._pull(copy_ok=False)
        self.assertEqual(rc, 1)
        self.assertIn("scp 失敗", err)


class RemoteScriptPaths(unittest.TestCase):
    def test_kind_is_part_of_the_path_so_uses_do_not_collide(self):
        self.assertNotEqual(macvm.remote_script_path("exec"), macvm.remote_script_path("health"))

    def test_commands_quote_the_path(self):
        p = "/tmp/a b.sh"
        self.assertIn("'/tmp/a b.sh'", macvm.remote_sh_command(p))
        self.assertIn("'/tmp/a b.sh'", macvm.remote_cleanup_command(p))


class BuildParser(unittest.TestCase):
    def test_every_subcommand_is_registered(self):
        parser = macvm.build_parser()
        actions = [a for a in parser._actions if hasattr(a, "choices") and a.choices]
        names = set()
        for a in actions:
            names.update(a.choices.keys())
        self.assertEqual(
            names,
            {"resolve-ip", "doctor", "screenshot", "push", "pull", "exec", "health"},
        )

    def test_subcommand_is_required(self):
        with self.assertRaises(SystemExit):
            macvm.build_parser().parse_args([])


if __name__ == "__main__":
    unittest.main()
