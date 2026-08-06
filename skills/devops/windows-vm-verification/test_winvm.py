"""winvm の仕様。

依存を増やさないため stdlib の unittest だけで書く (`python3 -m unittest`)。
winvm.py 自身も `dependencies = []` で、CI も runner の python3 をそのまま使う。

Parallels の出力に由来する固定値は、実機 (Parallels Desktop 26.4.0-57513) で
採取した生の値をそのまま使う。整形した理想形を書くと、実際の出力との差で壊れる。
"""
from __future__ import annotations

import argparse
import io
import os
import re
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import winvm

# 実機 `prlctl list -a -i -j` の出力から、winvm が読むキーを抜き出したもの。
# 全 40 キーのうち読むのは 5 つだが、値の形 (停止中の空 ipAddresses、version キー
# ごと欠ける GuestTools、末尾スラッシュ付きの Home) は実際の出力のままにしてある。
PRLCTL_LIST_JSON = """[
\t{
\t\t"ID": "e97a3a2f-9112-4017-a3a7-48c04bdd7a45",
\t\t"Name": "Coda - macOS",
\t\t"State": "stopped",
\t\t"Home": "/Users/example/Parallels/Coda - macOS.macvm/",
\t\t"GuestTools": {"state": "not_installed"},
\t\t"Network": {"Conditioned": "off", "ipAddresses": []}
\t},
\t{
\t\t"ID": "4eb49f98-7f09-4b43-a3f1-35f285ad4d26",
\t\t"Name": "Staccato - Windows 11 ARM",
\t\t"State": "running",
\t\t"Home": "/Users/example/Parallels/Staccato - Windows 11 ARM.pvm/",
\t\t"GuestTools": {"state": "installed", "version": "26.4.0-57513"},
\t\t"Network": {"Conditioned": "off", "ipAddresses": [
\t\t\t{"type": "ipv4", "ip": "10.211.55.3"},
\t\t\t{"type": "ipv6", "ip": "fdb2:2c26:f4e4:0:34a5:e9e2:a530:d5ff"},
\t\t\t{"type": "ipv6", "ip": "fe80::a22a:acc8:4abd:345c"}
\t\t]}
\t}
]
"""

RUNNING_VM = "Staccato - Windows 11 ARM"
STOPPED_VM = "Coda - macOS"


def config_pvs(isolated: str) -> str:
    """実機 config.pvs の骨格。隔離フラグは `./Settings/Tools/IsolatedVm` にある。"""
    return (
        "<ParallelsVirtualMachine>"
        "<Settings><Tools>"
        f"<IsolatedVm>{isolated}</IsolatedVm>"
        "</Tools></Settings>"
        "</ParallelsVirtualMachine>"
    )


class ParseVmList(unittest.TestCase):
    def test_parses_prlctl_json_into_records(self):
        vms = winvm.parse_vm_list(PRLCTL_LIST_JSON)
        self.assertEqual([v["Name"] for v in vms], [STOPPED_VM, RUNNING_VM])

    def test_empty_output_is_empty_list(self):
        self.assertEqual(winvm.parse_vm_list(""), [])
        self.assertEqual(winvm.parse_vm_list("   \n"), [])

    def test_malformed_json_is_empty_list(self):
        # prlctl が壊れた出力を返しても例外で落とさず「該当なし」に倒す。
        self.assertEqual(winvm.parse_vm_list("not json at all"), [])

    def test_non_list_json_is_empty_list(self):
        self.assertEqual(winvm.parse_vm_list('{"ID": "x"}'), [])


class FindVm(unittest.TestCase):
    def setUp(self):
        self.vms = winvm.parse_vm_list(PRLCTL_LIST_JSON)

    def test_matches_exact_name_including_spaces(self):
        vm = winvm.find_vm(self.vms, RUNNING_VM)
        self.assertIsNotNone(vm)
        self.assertEqual(vm["ID"], "4eb49f98-7f09-4b43-a3f1-35f285ad4d26")

    def test_matches_bare_uuid(self):
        vm = winvm.find_vm(self.vms, "4eb49f98-7f09-4b43-a3f1-35f285ad4d26")
        self.assertEqual(vm["Name"], RUNNING_VM)

    def test_matches_braced_uuid(self):
        # prl_vm_app の argv は波括弧付きで UUID を出す。
        vm = winvm.find_vm(self.vms, "{4eb49f98-7f09-4b43-a3f1-35f285ad4d26}")
        self.assertEqual(vm["Name"], RUNNING_VM)

    def test_uuid_match_is_case_insensitive(self):
        vm = winvm.find_vm(self.vms, "4EB49F98-7F09-4B43-A3F1-35F285AD4D26")
        self.assertEqual(vm["Name"], RUNNING_VM)

    def test_name_match_is_exact_not_substring(self):
        # 部分一致を許すと別 VM を掴む。
        self.assertIsNone(winvm.find_vm(self.vms, "Staccato"))

    def test_unknown_identifier_is_none(self):
        self.assertIsNone(winvm.find_vm(self.vms, "no-such-vm"))

    def test_empty_identifier_matches_nothing(self):
        self.assertIsNone(winvm.find_vm(self.vms, ""))


class PickIpv4(unittest.TestCase):
    def _network(self, name):
        return winvm.find_vm(winvm.parse_vm_list(PRLCTL_LIST_JSON), name).get("Network")

    def test_picks_the_ipv4_entry_from_a_mixed_list(self):
        self.assertEqual(winvm.pick_ipv4(self._network(RUNNING_VM)), "10.211.55.3")

    def test_stopped_vm_has_no_addresses(self):
        self.assertIsNone(winvm.pick_ipv4(self._network(STOPPED_VM)))

    def test_missing_network_is_none(self):
        self.assertIsNone(winvm.pick_ipv4(None))
        self.assertIsNone(winvm.pick_ipv4({}))

    def test_ipv6_only_is_none(self):
        net = {"ipAddresses": [{"type": "ipv6", "ip": "fe80::a22a:acc8:4abd:345c"}]}
        self.assertIsNone(winvm.pick_ipv4(net))

    def test_entry_labelled_ipv4_but_not_a_valid_address_is_rejected(self):
        # type を信じて壊れた値を SSH の接続先へ素通しさせない。
        net = {"ipAddresses": [{"type": "ipv4", "ip": "10.211"}]}
        self.assertIsNone(winvm.pick_ipv4(net))

    def test_ipv4_shaped_address_labelled_ipv6_is_skipped(self):
        # 選別は type で行う。値の形で拾うと、ラベルと中身が食い違うデータを
        # 「たまたま IPv4 に見えたから」で採ってしまう。食い違いは壊れたデータ
        # なので、繋がりそうな方へ倒さず fail-closed にする。
        # (この対照が無いと「type で選別する」という pin が、IPv4 妥当性検証に
        #  隠れて死ぬ。IPv6 だけの入力では両者の判定が一致してしまうため)
        net = {
            "ipAddresses": [
                {"type": "ipv6", "ip": "10.211.55.99"},
                {"type": "ipv4", "ip": "10.211.55.3"},
            ]
        }
        self.assertEqual(winvm.pick_ipv4(net), "10.211.55.3")

    def test_falls_through_bad_entry_to_a_good_one(self):
        net = {
            "ipAddresses": [
                {"type": "ipv4", "ip": ""},
                {"type": "ipv4", "ip": "10.211.55.9"},
            ]
        }
        self.assertEqual(winvm.pick_ipv4(net), "10.211.55.9")


class ParseTools(unittest.TestCase):
    def _vm(self, name):
        return winvm.find_vm(winvm.parse_vm_list(PRLCTL_LIST_JSON), name)

    def test_reads_state_and_version(self):
        self.assertEqual(winvm.parse_tools(self._vm(RUNNING_VM)), ("installed", "26.4.0-57513"))

    def test_not_installed_has_no_version_key(self):
        self.assertEqual(winvm.parse_tools(self._vm(STOPPED_VM)), ("not_installed", None))

    def test_absent_guest_tools_is_none_pair(self):
        self.assertEqual(winvm.parse_tools({}), (None, None))


class ParseIsolatedFlag(unittest.TestCase):
    def test_one_is_isolated(self):
        self.assertIs(winvm.parse_isolated_flag(config_pvs("1")), True)

    def test_zero_is_not_isolated(self):
        self.assertIs(winvm.parse_isolated_flag(config_pvs("0")), False)

    def test_absent_is_none(self):
        # 「隔離されていない」と「読めなかった」を混同しない。
        self.assertIsNone(
            winvm.parse_isolated_flag("<ParallelsVirtualMachine></ParallelsVirtualMachine>")
        )

    def test_malformed_xml_is_none(self):
        self.assertIsNone(winvm.parse_isolated_flag("not xml at all"))

    def test_same_element_outside_the_canonical_path_is_ignored(self):
        # 要素名だけで探すと、別セクションの同名要素を掴んで
        # 例外でも None でもなく「間違った bool」を静かに返す。
        text = (
            "<ParallelsVirtualMachine>"
            "<Snapshots><SavedState><IsolatedVm>1</IsolatedVm></SavedState></Snapshots>"
            "<Settings><Tools><IsolatedVm>0</IsolatedVm></Tools></Settings>"
            "</ParallelsVirtualMachine>"
        )
        self.assertIs(winvm.parse_isolated_flag(text), False)

    def test_canonical_path_matches_the_real_config(self):
        # パスは実機の config.pvs から採った。ここを推測で書くと静かにずれる。
        self.assertEqual(winvm.ISOLATION_PATH, "./Settings/Tools/IsolatedVm")


class PrlctlArgv(unittest.TestCase):
    def test_list_argv_asks_for_full_info_as_json(self):
        # -i が詳細、-j が JSON。どちらが欠けても text-parse に逆戻りする。
        self.assertEqual(winvm.prlctl_list_argv(), ["prlctl", "list", "-a", "-i", "-j"])

    def test_exec_argv_keeps_command_tokens_separate(self):
        # 実測: コマンドを 1 文字列に連結して渡すと exit 2 で無出力のまま黙って失敗する。
        got = winvm.prlctl_exec_argv(RUNNING_VM, ["cmd.exe", "/c", "ver"])
        self.assertEqual(got, ["prlctl", "exec", RUNNING_VM, "cmd.exe", "/c", "ver"])
        self.assertNotIn("cmd.exe /c ver", got)


class SshOptions(unittest.TestCase):
    def test_options_prevent_blocking_on_a_prompt(self):
        self.assertIn("BatchMode=yes", winvm.SSH_OPTS)
        self.assertIn("ConnectTimeout=10", winvm.SSH_OPTS)

    def test_every_ssh_and_scp_launch_applies_them(self):
        # 診断だけを硬くすると「doctor は 10 秒で返るのに run と health は固まる」
        # という逆転が起きる。ssh を起動する経路すべてが同じ堰を通ること。
        # assertNotIn にソース全体を渡すと失敗時に本文を丸ごと吐くので、
        # 違反した起動だけを取り出して比較する。
        src = Path(winvm.__file__).read_text(encoding="utf-8")
        launches = re.findall(r'\["(?:ssh|scp)",[^\]]*', src)
        self.assertTrue(launches, "ssh/scp の起動が 1 つも見つからない (検査が空振り)")
        without_opts = [x[:40] for x in launches if "*SSH_OPTS" not in x]
        self.assertEqual(without_opts, [])


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


class CmdResolveIp(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ))
        os.environ.pop("WINVM_VM", None)
        self.run = FakeRunner({tuple(winvm.prlctl_list_argv()): (0, PRLCTL_LIST_JSON, "")})

    def _resolve(self, vm):
        args = argparse.Namespace(vm=vm)
        return capture_io(lambda: winvm.cmd_resolve_ip(args, run=self.run))

    def test_prints_ip_for_running_vm(self):
        rc, out, _ = self._resolve(RUNNING_VM)
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "10.211.55.3")

    def test_resolves_by_uuid_too(self):
        rc, out, _ = self._resolve("4eb49f98-7f09-4b43-a3f1-35f285ad4d26")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "10.211.55.3")

    def test_missing_identifier_is_exit_2(self):
        rc, _, err = self._resolve(None)
        self.assertEqual(rc, 2)
        self.assertIn("--vm", err)

    def test_unknown_vm_is_exit_1_and_lists_known_names(self):
        rc, _, err = self._resolve("no-such-vm")
        self.assertEqual(rc, 1)
        self.assertIn(RUNNING_VM, err)  # 診断に実在の候補を出す

    def test_stopped_vm_is_exit_1_and_says_not_running(self):
        rc, _, err = self._resolve(STOPPED_VM)
        self.assertEqual(rc, 1)
        self.assertIn("stopped", err)

    def test_prlctl_failure_is_exit_1_and_does_not_print_an_ip(self):
        self.run = FakeRunner({tuple(winvm.prlctl_list_argv()): (1, "", "prlctl boom")})
        rc, out, err = self._resolve(RUNNING_VM)
        self.assertEqual(rc, 1)
        self.assertEqual(out.strip(), "")
        self.assertIn("prlctl boom", err)

    def test_env_var_supplies_identifier(self):
        os.environ["WINVM_VM"] = RUNNING_VM
        rc, out, _ = self._resolve(None)
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "10.211.55.3")

    def test_argument_beats_env_var(self):
        os.environ["WINVM_VM"] = STOPPED_VM
        rc, out, _ = self._resolve(RUNNING_VM)
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "10.211.55.3")


class DoctorReport(unittest.TestCase):
    def test_report_shows_the_observed_value_of_every_check(self):
        # 「OK/NG だけ」ではなく実測値を並べる。空や緑を健全の根拠にしないため。
        # ラベルの部分文字列に当たる assert (例: "on" は "host isolation" に一致する)
        # だと観測値を空にしても通るので、描画される区切りごと一致させる。
        checks = [
            winvm.Check("VM", "Staccato (4eb49f98)", True),
            winvm.Check("host isolation", "on", False, hint="FIX ME"),
            winvm.Check("Parallels Tools", "installed 26.4.0-57513", None),
        ]
        report = winvm.format_doctor_report(checks)
        self.assertIn(": Staccato (4eb49f98)", report)
        self.assertIn(": on", report)
        self.assertIn(": installed 26.4.0-57513", report)

    def test_dropping_the_observed_value_would_be_caught(self):
        # 上のテストが本当に observed を pin していることの対照。
        checks = [winvm.Check("host isolation", "", False)]
        self.assertNotIn(": on", winvm.format_doctor_report(checks))

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


class CollectDoctorChecks(unittest.TestCase):
    """doctor は「OK/NG」ではなく観測値を並べる。未確認は OK でも NG でもない。

    各チェックは失敗側も pin する。doctor は「繋がらないときの切り分け」が
    存在意義なので、失敗の写像こそが仕様の核になる。
    """

    def _runner(self, *, list_json=PRLCTL_LIST_JSON, list_rc=0, exec_rc=0, vm=RUNNING_VM):
        return FakeRunner(
            {
                tuple(winvm.prlctl_list_argv()): (list_rc, list_json, "prlctl boom"),
                tuple(winvm.prlctl_exec_argv(vm, ["cmd.exe", "/c", "ver"])): (
                    exec_rc,
                    "Microsoft Windows [Version 10.0.26200.8875]",
                    "not started",
                ),
            }
        )

    def _with_bundle(self, isolated: str | None, json_text=PRLCTL_LIST_JSON):
        """Home を実在の一時バンドルへ差し替えた JSON を返す。

        置換は Home の値だけを狙う。フィクスチャのパスを再掲して str.replace すると、
        フィクスチャ側を変えたとき replace が黙って no-op になり、テストは
        実在しないパスを見に行って隔離が全部 None に落ちる。
        """
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        bundle = Path(tmp.name) / "vm.pvm"
        bundle.mkdir()
        if isolated is not None:
            (bundle / "config.pvs").write_text(config_pvs(isolated), encoding="utf-8")
        import json as _json

        records = _json.loads(json_text)
        for rec in records:
            if rec.get("Name") == RUNNING_VM:
                rec["Home"] = str(bundle) + "/"
        return _json.dumps(records)

    def _checks(self, *, isolated="0", vm=RUNNING_VM, list_json=None, **kw):
        text = self._with_bundle(isolated, list_json or PRLCTL_LIST_JSON)
        return winvm.collect_doctor_checks(vm, run=self._runner(list_json=text, vm=vm, **kw))

    def _by_label(self, checks, label):
        for c in checks:
            if c.label == label:
                return c
        raise AssertionError(f"no check labelled {label!r}: {[c.label for c in checks]}")

    def test_healthy_vm_has_no_failing_check(self):
        self.assertEqual(winvm.doctor_exit_code(self._checks()), 0)

    def test_reports_vm_name_and_uuid(self):
        c = self._by_label(self._checks(), "VM")
        self.assertIn(RUNNING_VM, c.observed)
        self.assertIn("4eb49f98-7f09-4b43-a3f1-35f285ad4d26", c.observed)

    def test_running_vm_reports_its_ipv4(self):
        c = self._by_label(self._checks(), "IP")
        self.assertEqual(c.observed, "10.211.55.3")
        self.assertIs(c.ok, True)

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
        checks = self._checks(isolated=None)
        self.assertIsNone(self._by_label(checks, "host isolation").ok)
        self.assertEqual(winvm.doctor_exit_code(checks), 0)

    def test_installed_tools_pass(self):
        c = self._by_label(self._checks(), "Parallels Tools")
        self.assertEqual(c.observed, "installed 26.4.0-57513")
        self.assertIs(c.ok, True)

    def test_failing_prlctl_exec_is_a_failure(self):
        c = self._by_label(self._checks(exec_rc=2), "prlctl exec")
        self.assertIs(c.ok, False)

    def test_prlctl_list_failure_short_circuits(self):
        checks = winvm.collect_doctor_checks(
            RUNNING_VM, run=self._runner(list_rc=1, list_json="")
        )
        self.assertEqual(winvm.doctor_exit_code(checks), 1)
        self.assertIn("prlctl boom", winvm.format_doctor_report(checks))

    def test_unknown_vm_lists_registered_names(self):
        checks = winvm.collect_doctor_checks("no-such-vm", run=self._runner())
        self.assertEqual(winvm.doctor_exit_code(checks), 1)
        self.assertIn(RUNNING_VM, winvm.format_doctor_report(checks))

    def test_stopped_vm_fails_status_ip_and_tools(self):
        checks = self._checks(vm=STOPPED_VM, exec_rc=1)
        self.assertIs(self._by_label(checks, "status").ok, False)
        ip = self._by_label(checks, "IP")
        self.assertIs(ip.ok, False)
        self.assertEqual(ip.observed, winvm.UNKNOWN)
        tools = self._by_label(checks, "Parallels Tools")
        self.assertIs(tools.ok, False)
        self.assertEqual(tools.observed, "not_installed")
        self.assertEqual(winvm.doctor_exit_code(checks), 1)

    def test_absent_guest_tools_is_informational_not_a_failure(self):
        # state すら読めないのは「未導入」ではなく「未確認」。
        json_text = PRLCTL_LIST_JSON.replace(
            '"GuestTools": {"state": "installed", "version": "26.4.0-57513"},', ""
        )
        c = self._by_label(self._checks(list_json=json_text), "Parallels Tools")
        self.assertIsNone(c.ok)
        self.assertEqual(c.observed, winvm.UNKNOWN)

    def test_ssh_probe_only_runs_when_host_given(self):
        without = winvm.collect_doctor_checks(RUNNING_VM, run=self._runner())
        self.assertNotIn("ssh relay-winvm", [c.label for c in without])

    def test_unreachable_ssh_is_a_failure(self):
        checks = winvm.collect_doctor_checks(
            RUNNING_VM,
            host="relay-winvm",
            run=self._runner(),
            ssh_probe=lambda host: False,
        )
        c = self._by_label(checks, "ssh relay-winvm")
        self.assertIs(c.ok, False)
        self.assertEqual(c.observed, "未到達")
        self.assertEqual(winvm.doctor_exit_code(checks), 1)

    def test_reachable_ssh_passes(self):
        checks = winvm.collect_doctor_checks(
            RUNNING_VM, host="relay-winvm", run=self._runner(), ssh_probe=lambda host: True
        )
        c = self._by_label(checks, "ssh relay-winvm")
        self.assertIs(c.ok, True)
        self.assertEqual(c.observed, "到達")


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
    def setUp(self):
        self.enterContext(patch.dict(os.environ))
        os.environ.pop("WINVM_HOST", None)

    def _health(self, run_remote, *, host="vm"):
        args = argparse.Namespace(host=host, repo=None, check_tools=None)
        return capture_io(lambda: winvm.cmd_health(args, run=run_remote))

    def test_errors_when_pwsh_absent_and_does_not_transfer(self):
        calls: list[str] = []

        def fake_run(host, remote):
            calls.append(remote)
            return False

        rc, _, err = self._health(fake_run)
        self.assertEqual(rc, 1)
        self.assertEqual(calls, [winvm.pwsh_probe_command()])  # probe だけ、scp/exec は走らない
        self.assertIn("pwsh", err)

    def test_probe_error_message_names_both_causes(self):
        # probe 失敗は SSH 未到達 (VM 未起動 / stale IP) でも起きる。pwsh 未導入と断定しない。
        rc, _, err = self._health(lambda host, remote: False)
        self.assertEqual(rc, 1)
        self.assertIn("pwsh", err)
        self.assertIn("未起動", err)

    def test_missing_host_is_exit_2(self):
        rc, _, err = self._health(lambda host, remote: True, host=None)
        self.assertEqual(rc, 2)
        self.assertIn("--host", err)


class ParserSurface(unittest.TestCase):
    def test_subcommands_are_exactly_the_four_supported(self):
        parser = winvm.build_parser()
        names = {c for a in parser._actions if getattr(a, "choices", None) for c in a.choices}
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
        # 移行の取りこぼし検出。assertNotIn にソース全体を渡すと失敗時に本文を
        # 丸ごと吐くので、見つかったトークン名だけを比較する。
        src = Path(winvm.__file__).read_text(encoding="utf-8").lower()
        residue = [t for t in ("vmx", "vmware", "winvm_leases", "dhcpd", ".lck") if t in src]
        self.assertEqual(residue, [])


if __name__ == "__main__":
    unittest.main()
