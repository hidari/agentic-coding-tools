"""macvm の仕様。

依存を増やさないため stdlib の unittest だけで書く (`python3 -m unittest`)。
macvm.py 自身も `dependencies = []` で、CI も runner の python3 をそのまま使う。

Parallels の出力に由来する固定値は、実機 (Parallels Desktop 27.0.0-58628 / macOS ゲスト)
で採取した生の値の形をそのまま使う。整形した理想形を書くと、実際の出力との差で壊れる。
"""
from __future__ import annotations

import argparse
import ast
import io
import os
import re
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import macvm

# 実機 `prlctl list -a -i -j` の出力を、レコードの形を保ったまま縮めたもの。
# 停止中の空 ipAddresses と、version キーごと欠ける GuestTools は実際の出力のままにしてある。
# Home と Conditioned は macvm が読まないが、実機の出力に在るので残してある
# (「ここにあるキーは全部読まれている」とは読まないこと)。
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

# GuestTools キーごと読めないレコード。「確認できなかった」と「未導入」を別扱いする
# 経路のために要る。未導入 (not_installed) は STOPPED_VM 側が持っている。
NO_TOOLS_LIST_JSON = PRLCTL_LIST_JSON.replace(
    '"GuestTools": {"state": "installed", "version": "27.0.0-58628"},\n', ""
)
assert NO_TOOLS_LIST_JSON != PRLCTL_LIST_JSON

# 起動中なのに Parallels Tools が未導入の VM。PRLCTL_LIST_JSON では「停止中かつ未導入」
# 「起動中かつ導入済み」しか無く 2 つの軸が完全に相関しているので、Tools の判定軸を
# VM 状態へ付け替える変異を識別できない。
RUNNING_NO_TOOLS_JSON = PRLCTL_LIST_JSON.replace(
    '"GuestTools": {"state": "installed", "version": "27.0.0-58628"},',
    '"GuestTools": {"state": "not_installed"},',
)
assert RUNNING_NO_TOOLS_JSON != PRLCTL_LIST_JSON

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

    def test_surrounding_whitespace_is_absorbed_for_names_too(self):
        # env ファイルやコピペ経由で前後に空白が混ざる。UUID 側だけ吸収すると
        # 「一覧に名前が出ているのに引けない」という読みにくい失敗になる。
        self.assertIsNotNone(macvm.find_vm(self.vms, f" {RUNNING_VM} "))
        self.assertIsNotNone(macvm.find_vm(self.vms, f"{RUNNING_VM}\n"))

    def test_a_name_that_really_has_surrounding_spaces_is_still_matchable(self):
        # strip だけにすると、前後へ空白を持つ名前の VM をその名前どおりに渡しても
        # 引けなくなる。一覧に出ている文字列をそのまま渡して引けること。
        vms = [{"Name": " Padded VM ", "ID": "{aaa}"}, *self.vms]
        self.assertEqual(macvm.find_vm(vms, " Padded VM ")["ID"], "{aaa}")

    def test_name_match_is_case_sensitive(self):
        # UUID は大小無視だが名前は完全一致。名前比較に _normalize_uuid を通すと、
        # この軸が静かに緩んで prlctl と指す VM がずれる。
        self.assertIsNone(macvm.find_vm(self.vms, RUNNING_VM.upper()))

    def test_empty_identifier_matches_nothing(self):
        # 空白だけの識別子は _require を truthy で通ってここまで来る。正規化後の
        # 空文字が ID キーを欠くレコードの "" と一致して誤ヒットしないこと。
        # ID キーを欠くレコードと Name キーを欠くレコードの両方を混ぜる。前者だけだと
        # UUID 側のガードが全部拾ってしまい、名前側のガード ("" == "" で無名レコードに
        # 誤ヒットする経路) を落とす変異が素通りする。
        vms = [{"Name": "No ID Record"}, {"ID": "{ABC-123}"}, *self.vms]
        for ident in (" ", "", "{}"):
            with self.subTest(ident=ident):
                self.assertIsNone(macvm.find_vm(vms, ident))

    def test_a_name_match_wins_over_a_uuid_match_earlier_in_the_list(self):
        # 名前を全件見てから UUID を見る。1 パスだと並び順で結果が変わる。
        vms = [
            {"Name": "other", "ID": "collide"},
            {"Name": "collide", "ID": "aaaa1111-bbbb-4ccc-8ddd-eeee22223333"},
        ]
        self.assertEqual(macvm.find_vm(vms, "collide")["Name"], "collide")


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

    def test_malformed_address_is_not_handed_out(self):
        # resolve-ip の stdout は ProxyCommand 経由で nc の接続先そのものになる。
        # type が ipv4 でも値が壊れていれば渡さない。
        network = {"ipAddresses": [{"type": "ipv4", "ip": "10.211"}]}
        self.assertIsNone(macvm.pick_ipv4(network))

    def test_malformed_address_falls_through_to_a_valid_one(self):
        network = {"ipAddresses": [
            {"type": "ipv4", "ip": "-"},
            {"type": "ipv4", "ip": "10.211.55.3"},
        ]}
        self.assertEqual(macvm.pick_ipv4(network), "10.211.55.3")

    def test_selection_is_by_type_not_by_shape(self):
        # 妥当性検証を足しても選別の軸は type のまま。IPv4 の形をした ipv6 エントリを
        # 採ってしまうと、この不変条件が新しい検証の陰に隠れて死ぬ
        # (fe80::1 を使う test_ipv6_only_is_none は IPv4Address でも落ちるので識別力が無い)。
        network = {"ipAddresses": [
            {"type": "ipv6", "ip": "10.0.0.9"},
            {"type": "ipv4", "ip": "10.211.55.4"},
        ]}
        self.assertEqual(macvm.pick_ipv4(network), "10.211.55.4")

    def test_non_dict_network_is_none(self):
        # 壊れた prlctl 出力で例外にせず「該当なし」へ倒す (parse_vm_list と同じ方針)。
        self.assertIsNone(macvm.pick_ipv4("not-a-dict"))


class ScanIpv4(unittest.TestCase):
    """「ipv4 エントリが無い」と「あるが全部壊れている」を区別する。

    どちらも pick_ipv4 は None を返すが、対処が違う。前者は起動直後なら待てば付き、
    後者は待っても変わらない。畳むと診断が「数秒待って再実行」と案内し続ける。
    """

    def test_no_entries_reports_nothing_malformed(self):
        self.assertEqual(macvm.scan_ipv4({"ipAddresses": []}), (None, []))

    def test_ipv6_only_reports_nothing_malformed(self):
        network = {"ipAddresses": [{"type": "ipv6", "ip": "fe80::1"}]}
        self.assertEqual(macvm.scan_ipv4(network), (None, []))

    def test_malformed_values_are_collected(self):
        network = {"ipAddresses": [
            {"type": "ipv4", "ip": "10.211"},
            {"type": "ipv4", "ip": "-"},
        ]}
        self.assertEqual(macvm.scan_ipv4(network), (None, ["10.211", "-"]))

    def test_a_valid_value_stops_the_scan(self):
        network = {"ipAddresses": [
            {"type": "ipv4", "ip": "-"},
            {"type": "ipv4", "ip": "10.211.55.3"},
            {"type": "ipv4", "ip": "also-bad"},
        ]}
        # 見つかった時点で返すので、後ろの不正値は集めない。
        self.assertEqual(macvm.scan_ipv4(network), ("10.211.55.3", ["-"]))


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
        self.assertIn("stat -L -f %z", macvm.remote_size_command("/tmp/x"))


# BSD stat の `-L` と `-f %z` だけを真似る shim。
#
# 生成コマンドをそのまま走らせて観測したいが、ゲストは常に macOS なのに CI は Linux で、
# `stat -f` の意味が違う (BSD=書式指定 / GNU=--file-system)。実行すると CI だけが必ず
# 赤くなる。プラットフォームで skip する手も使えない (run-python-tests.py は skip された
# テストを赤にする)。そこで BSD の意味論を再現した shim を PATH の先に置いて観測する。
# 再現するのは実 VM で採った非対称そのもの: `stat -f %z` はリンク自身 (= リンク先パスの
# 文字列長、実測 33)、`stat -L -f %z` は実体のサイズ (実測 4096)。lstat / stat が対応する。
STAT_SHIM = """import os
import sys

args = sys.argv[1:]
deref = False
if args[:1] == ["-L"]:
    deref, args = True, args[1:]
if args[:2] != ["-f", "%z"] or len(args) != 3:
    sys.exit(64)
st = os.stat(args[2]) if deref else os.lstat(args[2])
print(st.st_size)
"""


class RemoteSizeCommandSemantics(unittest.TestCase):
    """生成したコマンドを実際に sh へ通し、symlink の扱いを観測する。

    `[ -f ]` も scp も最終要素の symlink を辿る。stat だけ辿らないと、転送が正しく
    終わっているのに照合が外れて「途中で切れた」と誤報する。macOS ゲストでは Homebrew の
    bin がほぼ全て symlink なので現実に踏む。
    """

    def _run(self, command, tmp):
        bindir = Path(tmp) / "bin"
        bindir.mkdir(exist_ok=True)
        shim = bindir / "stat"
        shim.write_text(f"#!{sys.executable}\n{STAT_SHIM}", encoding="utf-8")
        shim.chmod(0o755)
        env = dict(os.environ, PATH=f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
        return subprocess.run(
            ["sh", "-c", command], capture_output=True, text=True, env=env
        )

    def test_size_follows_a_symlink_the_way_scp_does(self):
        with TemporaryDirectory() as tmp:
            real = Path(tmp) / "real.bin"
            real.write_bytes(b"x" * 16)
            link = Path(tmp) / "link.bin"
            link.symlink_to(real)
            command = macvm.remote_size_command(str(link))

            p = self._run(command, tmp)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(macvm.parse_remote_size(p.stdout), 16)

            # 対照。shim が非対称を再現できていること。常に実体のサイズを返す壊れた
            # shim だと、-L を落とす変異でもこのテストが緑になり歯が無くなる。
            without_l = command.replace("stat -L -f %z", "stat -f %z")
            self.assertNotEqual(without_l, command, "-L が生成コマンドに無い")
            q = self._run(without_l, tmp)
            self.assertEqual(macvm.parse_remote_size(q.stdout), link.lstat().st_size)
            self.assertNotEqual(link.lstat().st_size, 16)

    def test_a_missing_file_falls_to_the_ascii_mark(self):
        with TemporaryDirectory() as tmp:
            p = self._run(macvm.remote_size_command(f"{tmp}/nope.bin"), tmp)
            self.assertEqual(p.stdout.strip(), macvm.REMOTE_MISSING_MARK)
            self.assertIsNone(macvm.parse_remote_size(p.stdout))

    def test_a_path_with_spaces_and_quotes_survives_the_shell(self):
        with TemporaryDirectory() as tmp:
            weird = Path(tmp) / "a b's c.bin"
            weird.write_bytes(b"y" * 7)
            p = self._run(macvm.remote_size_command(str(weird)), tmp)
            self.assertEqual(macvm.parse_remote_size(p.stdout), 7)


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


class RejectTildePath(unittest.TestCase):
    """ゲスト側パスの先頭 ~ は受け付けない。

    shlex.quote がチルダ展開を殺すので、macvm が組み立てるコマンドはリテラルな `~` を
    見る。ゲスト側のシェルが展開する経路 (scp の `host:~/...`) と食い違う。展開をこちらで
    再現するのはシェルの語彙の再実装になるので境界で弾く。ssh の作業ディレクトリは
    $HOME なので、相対パスが等価な書き方として残り表現力は落ちない。
    """

    def test_leading_tilde_is_refused(self):
        for path in ("~/w/a.dmg", "~", "~user/w/a.dmg"):
            with self.subTest(path=path):
                self.assertIsNotNone(macvm.reject_tilde_path(path, "remote"))

    def test_tilde_elsewhere_in_the_path_is_allowed(self):
        # 展開されるのは先頭だけ。/tmp/a~b は正当な macOS パス。
        self.assertIsNone(macvm.reject_tilde_path("/tmp/a~b", "remote"))

    def test_absolute_and_relative_paths_are_allowed(self):
        for path in ("/tmp/x", "w/a.dmg", "./w/a.dmg"):
            with self.subTest(path=path):
                self.assertIsNone(macvm.reject_tilde_path(path, "remote"))

    def test_the_message_names_the_argument_and_the_workaround(self):
        msg = macvm.reject_tilde_path("~/w/a.dmg", "remote")
        self.assertIn("remote", msg)
        self.assertIn("~", msg)

    def test_the_message_does_not_mention_transfers(self):
        # --repo からも呼ばれる。health は転送をしないので、scp の話を持ち出すと
        # 存在しない転送の説明を読ませることになる。
        self.assertNotIn("scp", macvm.reject_tilde_path("~/repo", "--repo"))


class RejectDirectoryishRemote(unittest.TestCase):
    """転送先/元はファイルを名指しすること。

    scp はディレクトリ宛だとその中へ置くのに、サイズ照合は渡された文字列をそのまま
    `[ -f ]` に掛けるので偽になる。転送が完全に終わっていても「途中で切れた」と誤報する。
    `~` と同じ「同じ引数を scp と sh が別の場所として読む」欠陥クラス。
    """

    def test_a_trailing_slash_is_refused(self):
        # scp 流の一番自然な書き方なので、初回利用で踏む。
        self.assertIsNotNone(macvm.reject_directoryish_remote("w/", "remote"))
        self.assertIsNotNone(macvm.reject_directoryish_remote("/tmp/w/", "remote"))

    def test_an_empty_path_is_refused(self):
        # scp は host: 宛だと $HOME 直下へ置く。
        self.assertIsNotNone(macvm.reject_directoryish_remote("", "remote"))

    def test_paths_that_name_a_file_are_allowed(self):
        for path in ("/tmp/x.bin", "w/a.dmg", "a.dmg", "/tmp/a~b"):
            with self.subTest(path=path):
                self.assertIsNone(macvm.reject_directoryish_remote(path, "remote"))

    def test_the_message_says_what_to_write_instead(self):
        msg = macvm.reject_directoryish_remote("w/", "remote")
        self.assertIn("remote", msg)
        self.assertIn("ファイル名", msg)


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

    def test_the_tool_name_reaches_the_script_as_data_not_as_code(self):
        # 「欠けたら fail=1」という挙動そのものは BuildHealthShellQuoting が実際に sh へ
        # 通して確かめる。ここが見るのは、値がラベル (コードの位置) ではなく変数
        # (データの位置) に置かれていること。生で埋めるとゲスト上で $() が展開される。
        body = macvm.build_health_shell(["cargo"], None)
        self.assertIn("tool=cargo", body)
        self.assertIn("tool_$tool=", body)
        self.assertNotIn("tool_cargo=", body)
        self.assertIn("fail=1", body)

    def test_repo_check_is_omitted_when_not_requested(self):
        self.assertNotIn("repo=", macvm.build_health_shell([], None))

    def test_repo_check_is_included_when_requested(self):
        body = macvm.build_health_shell([], "/Users/example/repo")
        self.assertIn("repo=", body)

    def test_exit_code_comes_from_the_flag(self):
        self.assertTrue(macvm.build_health_shell([], None).rstrip().endswith("exit $fail"))


class BuildHealthShellQuoting(unittest.TestCase):
    """生成本文を実際に sh へ通して観測する。

    sh の二重引用符の内側では $() とバッククォートが展開されるので、shlex.quote は
    値の位置しか守らない。`echo "tool_..."` のラベル位置は素通りする。
    「"repo=" を含む」形の部分文字列テストではこの欠陥を原理的に検出できない。
    """

    def _run(self, body):
        return subprocess.run(["sh", "-c", body], capture_output=True, text=True)

    def test_command_substitution_in_a_tool_name_is_not_expanded(self):
        p = self._run(macvm.build_health_shell(["x$(id -un)"], None))
        self.assertIn("tool_x$(id -un)=MISSING", p.stdout)

    def test_command_substitution_in_the_repo_path_is_not_expanded(self):
        # 存在するディレクトリにして、観測値としてパスが返る経路を通す。
        # 展開されると観測値が実在しない別のパスに化ける。
        with TemporaryDirectory() as d:
            repo = Path(d) / "r$(id -un)"
            repo.mkdir()
            p = self._run(macvm.build_health_shell([], str(repo)))
            self.assertIn(f"repo={repo}", p.stdout)

    def test_an_apostrophe_in_the_repo_path_is_not_a_syntax_error(self):
        # 合法な macOS パス。構文エラーだと exit $fail に到達せず、健全な VM が
        # 「不健全」に見える。
        with TemporaryDirectory() as d:
            repo = Path(d) / "Ken's repo"
            repo.mkdir()
            p = self._run(macvm.build_health_shell([], str(repo)))
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn(f"repo={repo}", p.stdout)

    def test_a_missing_tool_makes_the_script_exit_non_zero(self):
        p = self._run(macvm.build_health_shell(["macvm-no-such-tool"], None))
        self.assertEqual(p.returncode, 1)
        self.assertIn("tool_macvm-no-such-tool=MISSING", p.stdout)

    def test_a_present_tool_reports_the_resolved_path(self):
        p = self._run(macvm.build_health_shell(["sh"], None))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertRegex(p.stdout, r"tool_sh=/\S*/sh")

    def test_all_items_are_reported_even_after_a_failure(self):
        # 最初の失敗で打ち切ると、残りが健全かどうか分からないまま報告になる。
        p = self._run(macvm.build_health_shell(["macvm-no-such-tool", "sh"], None))
        self.assertIn("tool_macvm-no-such-tool=MISSING", p.stdout)
        self.assertIn("tool_sh=", p.stdout)


class ParseTools(unittest.TestCase):
    """GuestTools から (state, version)。「読めなかった」を「未導入」に潰さない。"""

    def test_installed_reports_state_and_version(self):
        vm = {"GuestTools": {"state": "installed", "version": "27.0.0-58628"}}
        self.assertEqual(macvm.parse_tools(vm), ("installed", "27.0.0-58628"))

    def test_not_installed_has_no_version_key(self):
        # 未導入の VM は version キーごと欠ける (実測)。
        self.assertEqual(
            macvm.parse_tools({"GuestTools": {"state": "not_installed"}}),
            ("not_installed", None),
        )

    def test_a_missing_key_is_unconfirmed(self):
        self.assertEqual(macvm.parse_tools({}), (None, None))

    def test_a_non_dict_value_is_unconfirmed(self):
        self.assertEqual(macvm.parse_tools({"GuestTools": "installed"}), (None, None))


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


class UnknownMarker(unittest.TestCase):
    def test_it_cannot_be_mistaken_for_a_value_prlctl_returns(self):
        # "unknown" のような値にすると、「GuestTools を読めなかった」のか
        # 「prlctl が state=unknown を返した」のかが出力から区別できなくなる。
        self.assertIsNone(re.fullmatch(r"[a-z_]+", macvm.UNKNOWN))


class DoctorExitCode(unittest.TestCase):
    def test_any_failure_is_exit_1(self):
        checks = [macvm.Check("a", "x", ok=True), macvm.Check("b", "y", ok=False)]
        self.assertEqual(macvm.doctor_exit_code(checks), 1)

    def test_unknown_alone_is_exit_0(self):
        # 「確認できなかった」は失敗ではない。
        checks = [macvm.Check("a", "x", ok=True), macvm.Check("b", "y", ok=None)]
        self.assertEqual(macvm.doctor_exit_code(checks), 0)


class CollectDoctorChecks(unittest.TestCase):
    """Check.ok は tri-state (True / False / None)。

    判定は assertIs で行い、assertFalse / assertTrue を使わない。None も falsy なので、
    「確認できなかった」と「NG」を潰す変異 (ok を常に None へ滑らせる等) が素通りする。
    """

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
        self.assertIs(state.ok, False)
        self.assertEqual(state.observed, "stopped")

    def test_stopped_vm_reports_missing_ip_rather_than_silence(self):
        checks = macvm.collect_doctor_checks(STOPPED_VM, None, run=self.run)
        ip = next(c for c in checks if c.label == "IP")
        self.assertIs(ip.ok, False)

    def test_apipa_is_flagged_as_a_failure(self):
        run = FakeRunner({tuple(macvm.prlctl_list_argv()): (0, APIPA_LIST_JSON, "")})
        checks = macvm.collect_doctor_checks(RUNNING_VM, None, run=run)
        ip = next(c for c in checks if c.label == "IP")
        self.assertIs(ip.ok, False)
        self.assertEqual(ip.observed, "169.254.10.20")

    def test_unknown_vm_lists_the_known_names(self):
        checks = macvm.collect_doctor_checks("no-such-vm", None, run=self.run)
        self.assertIn(RUNNING_VM, checks[0].observed)

    def test_prlctl_failure_is_a_single_failed_check(self):
        run = FakeRunner({tuple(macvm.prlctl_list_argv()): (1, "", "boom")})
        checks = macvm.collect_doctor_checks(RUNNING_VM, None, run=run)
        self.assertEqual(len(checks), 1)
        self.assertIs(checks[0].ok, False)

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
            console_probe=lambda h, c: (0, macvm.NO_AQUA_MARK, ""),
        )
        gui = next(c for c in checks if c.label == "GUI セッション")
        self.assertIs(gui.ok, False)

    def test_logged_in_console_owner_is_reported_as_ok(self):
        checks = macvm.collect_doctor_checks(
            RUNNING_VM,
            "somehost",
            run=self.run,
            ssh_probe=lambda h: True,
            console_probe=lambda h, c: (0, "someuser\n", ""),
        )
        gui = next(c for c in checks if c.label == "GUI セッション")
        self.assertIs(gui.ok, True)
        self.assertEqual(gui.observed, "someuser")

    def test_a_failing_console_probe_is_unconfirmed_not_a_missing_session(self):
        # rc も stderr も見ずに空を NO_AQUA と同じ側へ畳むと、ssh が落ちただけで
        # 「ログイン画面」と断定する。console_owner_command は無セッションの信号を
        # NO_AQUA_MARK と定めているので、空はその信号ではない。
        checks = macvm.collect_doctor_checks(
            RUNNING_VM,
            "somehost",
            run=self.run,
            ssh_probe=lambda h: True,
            console_probe=lambda h, c: (255, "", "ssh: connect to host ... refused"),
        )
        gui = next(c for c in checks if c.label == "GUI セッション")
        self.assertIsNone(gui.ok)
        self.assertIn("refused", gui.observed)
        self.assertEqual(macvm.doctor_exit_code(checks), 0)

    def test_unreadable_guest_tools_is_unconfirmed_not_a_failure(self):
        # 「確認できなかった」を FAIL へ潰すと、他が健全でも exit 1 になる。
        run = FakeRunner({tuple(macvm.prlctl_list_argv()): (0, NO_TOOLS_LIST_JSON, "")})
        checks = macvm.collect_doctor_checks(RUNNING_VM, None, run=run)
        tools = next(c for c in checks if c.label == "Parallels Tools")
        self.assertIsNone(tools.ok)
        self.assertEqual(macvm.doctor_exit_code(checks), 0)

    def test_not_installed_guest_tools_is_still_a_failure(self):
        # 上の 1 本だけだと「常に None」へ滑る変異を検出できない。
        # 起動中かつ未導入のレコードを使う。PRLCTL_LIST_JSON は VM 状態と Tools 状態が
        # 完全に相関しているので、停止中のレコードだと判定軸を VM 状態へ付け替える
        # 変異 (state == "running") まで緑で通る。
        run = FakeRunner({tuple(macvm.prlctl_list_argv()): (0, RUNNING_NO_TOOLS_JSON, "")})
        checks = macvm.collect_doctor_checks(RUNNING_VM, None, run=run)
        state = next(c for c in checks if c.label == "VM 状態")
        tools = next(c for c in checks if c.label == "Parallels Tools")
        self.assertIs(state.ok, True)
        self.assertIs(tools.ok, False)

    def test_an_empty_guest_tools_state_is_unconfirmed(self):
        # 空文字を「読めた」扱いにすると [FAIL] と観測値 (未確認) が同時に出て、
        # exit 1 の根拠が出力から辿れなくなる。
        empty = PRLCTL_LIST_JSON.replace('"state": "installed"', '"state": ""')
        self.assertNotEqual(empty, PRLCTL_LIST_JSON)
        run = FakeRunner({tuple(macvm.prlctl_list_argv()): (0, empty, "")})
        checks = macvm.collect_doctor_checks(RUNNING_VM, None, run=run)
        tools = next(c for c in checks if c.label == "Parallels Tools")
        self.assertIsNone(tools.ok)

    def test_a_console_probe_that_returns_nothing_is_unconfirmed(self):
        # rc が 0 でも観測値が空なら「Aqua 無し」ではない。無セッションの信号は
        # NO_AQUA_MARK であって空ではない。rc だけを見る実装はここで落ちる。
        checks = macvm.collect_doctor_checks(
            RUNNING_VM,
            "somehost",
            run=self.run,
            ssh_probe=lambda h: True,
            console_probe=lambda h, c: (0, "\n", ""),
        )
        gui = next(c for c in checks if c.label == "GUI セッション")
        self.assertIsNone(gui.ok)

    def test_a_nonzero_console_probe_is_unconfirmed_even_with_output(self):
        # 逆側。stdout に何か出ていても rc が非 0 なら断定しない。stdout だけを見る
        # 実装はここで落ちる。
        checks = macvm.collect_doctor_checks(
            RUNNING_VM,
            "somehost",
            run=self.run,
            ssh_probe=lambda h: True,
            console_probe=lambda h, c: (255, "someuser\n", "boom"),
        )
        gui = next(c for c in checks if c.label == "GUI セッション")
        self.assertIsNone(gui.ok)

    def test_malformed_ipv4_values_are_not_reported_as_unassigned(self):
        # 「待てば付く」と「待っても変わらない」を同じ「未割当」へ畳まない。
        broken = PRLCTL_LIST_JSON.replace('"ip": "10.211.55.5"', '"ip": "10.211"')
        self.assertNotEqual(broken, PRLCTL_LIST_JSON)
        run = FakeRunner({tuple(macvm.prlctl_list_argv()): (0, broken, "")})
        checks = macvm.collect_doctor_checks(RUNNING_VM, None, run=run)
        ip = next(c for c in checks if c.label == "IP")
        self.assertIs(ip.ok, False)
        self.assertIn("10.211", ip.observed)
        self.assertNotIn("未割当", ip.observed)
        self.assertNotIn("数秒待って", ip.hint)

    def test_the_tools_row_reports_the_version_it_observed(self):
        checks = macvm.collect_doctor_checks(RUNNING_VM, None, run=self.run)
        tools = next(c for c in checks if c.label == "Parallels Tools")
        self.assertEqual(tools.observed, "installed 27.0.0-58628")

    def test_the_vm_row_shows_the_uuid_of_the_record_it_matched(self):
        # 名前と UUID の両方で引ける以上、どのレコードを掴んだかを出力から読めること。
        checks = macvm.collect_doctor_checks(RUNNING_VM, None, run=self.run)
        self.assertIn("ffff4444-aaaa-4bbb-8ccc-dddd55556666", checks[0].observed)

    def test_an_unassigned_ip_still_records_that_ssh_was_not_checked(self):
        # 行ごと消すと --host の有無で report が同一になり、「見なかった」が
        # 2 通りに表現される (--host 未指定は [ -- ] 行を残している)。
        checks = macvm.collect_doctor_checks(
            STOPPED_VM, "somehost", run=self.run, ssh_probe=lambda h: True
        )
        ssh = next(c for c in checks if c.label == "SSH")
        self.assertIsNone(ssh.ok)

    def test_an_unassigned_ip_does_not_probe_ssh(self):
        # IP が無ければ ProxyCommand が必ず落ちるので撃たない (省くこと自体は正しい)。
        def boom(host):
            raise AssertionError("IP 未割当で ssh を撃ってはいけない")

        macvm.collect_doctor_checks(STOPPED_VM, "somehost", run=self.run, ssh_probe=boom)


class CmdDoctor(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ))
        os.environ.pop("MACVM_VM", None)
        os.environ.pop("MACVM_HOST", None)
        self.run = FakeRunner({tuple(macvm.prlctl_list_argv()): (0, PRLCTL_LIST_JSON, "")})

    def test_every_seam_is_forwarded_so_no_test_reaches_a_real_ssh(self):
        # console_probe を転送し忘れると、見えている seam を全部塞いだつもりで
        # 実 ssh が飛ぶ (ConnectTimeout=10 なので 1 件あたり最大 10 秒の沈黙になる)。
        args = argparse.Namespace(vm=RUNNING_VM, host="somehost")
        rc, out, _ = capture_io(
            lambda: macvm.cmd_doctor(
                args,
                run=self.run,
                ssh_probe=lambda h: True,
                console_probe=lambda h, c: (0, "someuser\n", ""),
            )
        )
        self.assertEqual(rc, 0)
        self.assertIn("someuser", out)

    def test_env_var_supplies_the_vm(self):
        os.environ["MACVM_VM"] = RUNNING_VM
        args = argparse.Namespace(vm=None, host=None)
        rc, out, _ = capture_io(lambda: macvm.cmd_doctor(args, run=self.run))
        self.assertEqual(rc, 0)
        self.assertIn(RUNNING_VM, out)


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

    def test_malformed_ipv4_values_are_named_instead_of_suggesting_a_start(self):
        # 起動中の VM に prlctl start を勧めると誤誘導になる。壊れた値そのものを出す。
        broken = PRLCTL_LIST_JSON.replace('"ip": "10.211.55.5"', '"ip": "10.211"')
        self.assertNotEqual(broken, PRLCTL_LIST_JSON)
        run = FakeRunner({tuple(macvm.prlctl_list_argv()): (0, broken, "")})
        rc, out, err = self._resolve(RUNNING_VM, run=run)
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("10.211", err)
        self.assertNotIn("prlctl start", err)


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


class TransferSpy:
    """push / pull が触る 3 つの seam を記録する。

    引数を捨てる lambda では「サイズ問い合わせが scp の書き込んだパスと同じ実体を
    指しているか」という軸も、「撃っていない」と「撃って成功した」の違いも見えない。
    """

    def __init__(self, *, mkdir_ok=True, copy_ok=True, size="28", writes=None, capture_rc=0):
        self.mkdir_ok = mkdir_ok
        self.copy_ok = copy_ok
        self.size = size
        self.writes = writes
        self.capture_rc = capture_rc
        self.hosts: list[str] = []
        self.runs: list[str] = []
        self.copies: list[tuple[str, str]] = []
        self.captures: list[str] = []

    def run(self, host, remote):
        self.hosts.append(host)
        self.runs.append(remote)
        return self.mkdir_ok

    def copy(self, host, local, dest):
        self.hosts.append(host)
        self.copies.append((local, dest))
        return self.copy_ok

    def copy_pull(self, host, remote, local):
        self.hosts.append(host)
        self.copies.append((remote, local))
        if self.copy_ok and self.writes is not None:
            Path(local).write_bytes(self.writes)
        return self.copy_ok

    def capture(self, host, remote):
        # ssh ラッパと同じ 3-tuple を返す。rc を落とすと「ssh が落ちた」と
        # 「ファイルが無い」が同じ値になり、呼び出し側が誤断定する。
        self.hosts.append(host)
        self.captures.append(remote)
        if self.capture_rc != 0:
            return self.capture_rc, "", "ssh: connect to host ... : Connection refused"
        return 0, self.size, ""


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
                capture=lambda h, c: (0, remote_size, ""),
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
                args,
                run=lambda h, c: True,
                copy=lambda h, l, d: True,
                capture=lambda h, c: (0, "0", ""),
            )
        )
        self.assertEqual(rc, 1)
        self.assertIn("ローカルファイルがありません", err)

    def test_missing_host_is_exit_2(self):
        args = argparse.Namespace(host=None, local=str(self.local), remote="/tmp/x")
        rc, _, err = capture_io(
            lambda: macvm.cmd_push(
                args,
                run=lambda h, c: True,
                copy=lambda h, l, d: True,
                capture=lambda h, c: (0, "28", ""),
            )
        )
        self.assertEqual(rc, 2)
        self.assertIn("--host", err)

    def _spied(self, spy, *, host="h", remote="/tmp/deep/p.bin", local=None):
        args = argparse.Namespace(
            host=host, local=local or str(self.local), remote=remote
        )
        return capture_io(
            lambda: macvm.cmd_push(args, run=spy.run, copy=spy.copy, capture=spy.capture)
        )

    def test_parent_is_made_then_copied_then_the_same_path_is_measured(self):
        # 期待値をビルダ自身から作ると同語反復になるので、mkdir とコピー先は literal で
        # 固定する。サイズ問い合わせは「コピー先と同じパスを見ているか」だけを見る
        # (コマンド本文の正しさは RemoteSizeCommand が持つ)。
        spy = TransferSpy()
        rc, _, _ = self._spied(spy)
        self.assertEqual(rc, 0)
        self.assertEqual(spy.runs, ["mkdir -p /tmp/deep"])
        self.assertEqual(spy.copies, [(str(self.local), "/tmp/deep/p.bin")])
        self.assertEqual(spy.captures, [macvm.remote_size_command("/tmp/deep/p.bin")])

    def test_mkdir_failure_stops_before_the_transfer(self):
        spy = TransferSpy(mkdir_ok=False)
        rc, _, err = self._spied(spy)
        self.assertEqual(rc, 1)
        self.assertEqual(spy.copies, [])
        self.assertIn("ディレクトリ作成", err)

    def test_scp_failure_stops_before_the_size_check(self):
        spy = TransferSpy(copy_ok=False)
        rc, _, _ = self._spied(spy)
        self.assertEqual(rc, 1)
        self.assertEqual(spy.captures, [])

    def test_missing_local_file_stops_before_touching_the_vm(self):
        spy = TransferSpy()
        rc, _, _ = self._spied(spy, local=str(Path(self.tmp) / "nope"))
        self.assertEqual(rc, 1)
        self.assertEqual(spy.runs, [])
        self.assertEqual(spy.copies, [])

    def test_a_tilde_remote_is_refused_without_touching_the_vm(self):
        spy = TransferSpy()
        rc, _, err = self._spied(spy, remote="~/w/a.dmg")
        self.assertEqual(rc, 2)
        self.assertEqual(spy.runs, [])
        self.assertEqual(spy.copies, [])
        self.assertIn("~", err)

    def test_a_directoryish_remote_is_refused_without_touching_the_vm(self):
        # scp 流の一番自然な書き方。転送は成功するのにサイズ照合が外れるので、
        # 通してしまうと「途中で切れた」という嘘の報告になる。
        for remote in ("w/", ""):
            with self.subTest(remote=remote):
                spy = TransferSpy()
                rc, _, err = self._spied(spy, remote=remote)
                self.assertEqual(rc, 2)
                self.assertEqual(spy.copies, [])
                self.assertIn("ファイル名", err)

    def test_an_ssh_failure_after_the_transfer_is_not_called_a_truncation(self):
        # scp が完全なファイルを届けた後で ssh だけが落ちることがある。これを
        # 「転送が途中で切れた」と報告すると、届いているものを再転送させる。
        spy = TransferSpy(capture_rc=255)
        rc, _, err = self._spied(spy)
        self.assertEqual(rc, 1)
        self.assertIn("ssh", err)
        self.assertNotIn("途中で切れた", err)
        self.assertIn("macvm doctor", err)

    def test_an_scp_failure_names_where_to_triage(self):
        # scp は -q 付きなので ssh の接続失敗の診断が出力に残らない。
        spy = TransferSpy(copy_ok=False)
        _, _, err = self._spied(spy)
        self.assertIn("macvm doctor", err)

    def test_env_var_supplies_the_host(self):
        os.environ["MACVM_HOST"] = "from-env"
        spy = TransferSpy()
        rc, _, _ = self._spied(spy, host=None)
        self.assertEqual(rc, 0)
        self.assertEqual(set(spy.hosts), {"from-env"})


class CmdPull(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ))
        os.environ.pop("MACVM_HOST", None)
        self.tmp = self.enterContext(TemporaryDirectory())
        # 親ディレクトリを未作成にしておく。既存 tmp 直下だと、自動作成を落とす変異が
        # 素通りする。
        self.local = Path(self.tmp) / "in" / "out.bin"

    def _pull(self, *, remote_size="28", written=b"x" * 28, copy_ok=True):
        args = argparse.Namespace(host="h", remote="/tmp/p.bin", local=str(self.local))

        def copy(h, r, l):
            if copy_ok:
                Path(l).write_bytes(written)
            return copy_ok

        return capture_io(
            lambda: macvm.cmd_pull(args, copy=copy, capture=lambda h, c: (0, remote_size, ""))
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

    def test_the_missing_local_parent_directory_is_created(self):
        rc, _, _ = self._pull()
        self.assertEqual(rc, 0)
        self.assertTrue(self.local.is_file())

    def _spied(self, spy, *, host="h", remote="/tmp/p.bin"):
        args = argparse.Namespace(host=host, remote=remote, local=str(self.local))
        return capture_io(lambda: macvm.cmd_pull(args, copy=spy.copy_pull, capture=spy.capture))

    def test_the_same_path_is_measured_and_then_transferred(self):
        spy = TransferSpy(writes=b"x" * 28)
        rc, _, _ = self._spied(spy)
        self.assertEqual(rc, 0)
        self.assertEqual(spy.captures, [macvm.remote_size_command("/tmp/p.bin")])
        self.assertEqual(spy.copies, [("/tmp/p.bin", str(self.local))])

    def test_an_absent_remote_stops_before_the_transfer(self):
        # rc == 1 だけを見ると「転送前に止めた」ことは見えない。
        spy = TransferSpy(size=macvm.REMOTE_MISSING_MARK, writes=b"x" * 28)
        rc, _, err = self._spied(spy)
        self.assertEqual(rc, 1)
        self.assertEqual(spy.copies, [])
        self.assertIn("不在の可能性", err)

    def test_a_tilde_remote_is_refused_without_touching_the_vm(self):
        spy = TransferSpy(writes=b"x" * 28)
        rc, _, err = self._spied(spy, remote="~/w/a.dmg")
        self.assertEqual(rc, 2)
        self.assertEqual(spy.captures, [])
        self.assertEqual(spy.copies, [])
        self.assertIn("~", err)

    def test_a_directoryish_remote_is_refused_without_touching_the_vm(self):
        for remote in ("w/", ""):
            with self.subTest(remote=remote):
                spy = TransferSpy(writes=b"x" * 28)
                rc, _, err = self._spied(spy, remote=remote)
                self.assertEqual(rc, 2)
                self.assertEqual(spy.captures, [])
                self.assertIn("ファイル名", err)

    def test_an_ssh_failure_is_not_called_an_absent_file(self):
        # ssh の障害を「ファイルが無い」へすり替えると、切り分け先が VM の中へ逸れる。
        spy = TransferSpy(capture_rc=255, writes=b"x" * 28)
        rc, _, err = self._spied(spy)
        self.assertEqual(rc, 1)
        self.assertEqual(spy.copies, [])
        self.assertIn("ssh", err)
        self.assertNotIn("不在の可能性", err)
        self.assertIn("macvm doctor", err)

    def test_env_var_supplies_the_host(self):
        os.environ["MACVM_HOST"] = "from-env"
        spy = TransferSpy(writes=b"x" * 28)
        rc, _, _ = self._spied(spy, host=None)
        self.assertEqual(rc, 0)
        self.assertEqual(set(spy.hosts), {"from-env"})


class RemoteScriptPaths(unittest.TestCase):
    def test_every_call_returns_a_distinct_path(self):
        # 固定名だと、同じ VM へ並列に撃ったとき scp と実行の間に相手が同じパスを
        # 上書きし、相手のコマンドを実行して相手の結果を自分の結果として返す。
        # 「exec != health」だけを見るテストは固定名のままでも緑になる。
        paths = {macvm.remote_script_path("exec") for _ in range(32)}
        self.assertEqual(len(paths), 32)

    def test_kind_is_part_of_the_path_so_leftovers_are_identifiable(self):
        # 後始末に失敗して残ったとき、どのサブコマンドの残骸か読めるようにする。
        self.assertIn("exec", macvm.remote_script_path("exec"))
        self.assertIn("health", macvm.remote_script_path("health"))

    def test_commands_quote_the_path(self):
        p = "/tmp/a b.sh"
        self.assertIn("'/tmp/a b.sh'", macvm.remote_sh_command(p))
        self.assertIn("'/tmp/a b.sh'", macvm.remote_cleanup_command(p))


class RemoteScriptSpy:
    """exec / health が収束する _run_remote_script の seam を記録する。

    rc だけを見ると「撃っていない」と「撃って成功した」が区別できないので、
    転送先・本文・コマンド列を全部持つ。
    """

    def __init__(self, *, copy_ok=True, script_rc=0, raise_on_script=False):
        self.copy_ok = copy_ok
        self.script_rc = script_rc
        self.raise_on_script = raise_on_script
        self.copies: list[tuple[str, str, str]] = []
        self.bodies: list[str] = []
        self.run_calls: list[tuple[str, str]] = []

    def copy(self, host, local, dest):
        self.bodies.append(Path(local).read_text(encoding="utf-8"))
        self.copies.append((host, local, dest))
        return self.copy_ok

    def run(self, host, remote):
        self.run_calls.append((host, remote))
        if not remote.startswith("sh "):
            return 0
        if self.raise_on_script:
            raise RuntimeError("ssh が落ちた")
        return self.script_rc

    @property
    def dest(self) -> str:
        return self.copies[-1][2]

    @property
    def local(self) -> str:
        return self.copies[-1][1]


class RunRemoteScript(unittest.TestCase):
    """exec と health が共有する 転送 -> 実行 -> 後始末 の経路。"""

    def _call(self, spy, kind="exec", body="echo x\n"):
        return macvm._run_remote_script("h", kind, body, run=spy.run, copy=spy.copy)

    def test_the_body_is_transferred_then_run_then_cleaned_up_in_order(self):
        spy = RemoteScriptSpy()
        self.assertEqual(self._call(spy), 0)
        self.assertEqual(spy.bodies, ["echo x\n"])
        self.assertEqual(
            spy.run_calls,
            [
                ("h", macvm.remote_sh_command(spy.dest)),
                ("h", macvm.remote_cleanup_command(spy.dest)),
            ],
        )

    def test_the_remote_exit_code_is_passed_through(self):
        self.assertEqual(self._call(RemoteScriptSpy(script_rc=7)), 7)

    def test_cleanup_runs_when_the_script_reports_failure(self):
        spy = RemoteScriptSpy(script_rc=3)
        self._call(spy)
        self.assertEqual(spy.run_calls[-1], ("h", macvm.remote_cleanup_command(spy.dest)))

    def test_cleanup_runs_when_ssh_raises(self):
        spy = RemoteScriptSpy(raise_on_script=True)
        with self.assertRaises(RuntimeError):
            self._call(spy)
        self.assertEqual(spy.run_calls[-1], ("h", macvm.remote_cleanup_command(spy.dest)))

    def test_the_local_temporary_file_is_removed(self):
        spy = RemoteScriptSpy()
        self._call(spy)
        self.assertFalse(Path(spy.local).exists())

    def test_the_local_temporary_file_is_removed_when_scp_fails(self):
        spy = RemoteScriptSpy(copy_ok=False)
        capture_io(lambda: self._call(spy))
        self.assertFalse(Path(spy.local).exists())

    def test_scp_failure_stops_before_running_anything(self):
        spy = RemoteScriptSpy(copy_ok=False)
        rc, _, err = capture_io(lambda: self._call(spy))
        self.assertEqual(rc, 1)
        self.assertEqual(spy.run_calls, [])
        self.assertIn("scp", err)

    def test_scp_failure_names_where_to_triage(self):
        # scp は -q なので ssh の接続失敗の診断 (Connection refused 等) が出ない。
        # このツールで接続失敗が診断情報ほぼ無しで返る唯一の経路なので誘導を付ける。
        spy = RemoteScriptSpy(copy_ok=False)
        _, _, err = capture_io(lambda: self._call(spy))
        self.assertIn("macvm doctor", err)

    def test_a_non_zero_script_is_not_given_the_triage_hint(self):
        # 利用者のスクリプト自身の失敗に接続の切り分けを勧めると誤誘導になる。
        spy = RemoteScriptSpy(script_rc=3)
        _, _, err = capture_io(lambda: self._call(spy))
        self.assertNotIn("macvm doctor", err)

    def test_each_invocation_uses_a_distinct_remote_path(self):
        spy = RemoteScriptSpy()
        for _ in range(8):
            self._call(spy)
        self.assertEqual(len({d for _, _, d in spy.copies}), 8)


class CmdExec(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ))
        os.environ.pop("MACVM_HOST", None)

    def _exec(self, remote, *, host="h", spy=None):
        spy = spy or RemoteScriptSpy()
        args = argparse.Namespace(host=host, remote=remote)
        rc, out, err = capture_io(lambda: macvm.cmd_exec(args, run=spy.run, copy=spy.copy))
        return rc, err, spy

    def test_the_command_reaches_the_vm_verbatim(self):
        _, _, spy = self._exec(["--", "echo", "x", "|", "tr", "a-z", "A-Z"])
        self.assertEqual(spy.bodies, ["echo x | tr a-z A-Z\n"])

    def test_the_remote_exit_code_becomes_the_exit_code(self):
        rc, _, _ = self._exec(["--", "false"], spy=RemoteScriptSpy(script_rc=7))
        self.assertEqual(rc, 7)

    def test_missing_host_is_exit_2_without_touching_the_vm(self):
        rc, err, spy = self._exec(["--", "true"], host=None)
        self.assertEqual(rc, 2)
        self.assertEqual(spy.copies, [])
        self.assertEqual(spy.run_calls, [])
        self.assertIn("--host", err)

    def test_an_empty_command_is_exit_2_without_touching_the_vm(self):
        rc, _, spy = self._exec(["--"])
        self.assertEqual(rc, 2)
        self.assertEqual(spy.copies, [])

    def test_env_var_supplies_the_host(self):
        os.environ["MACVM_HOST"] = "from-env"
        _, _, spy = self._exec(["--", "true"], host=None)
        self.assertEqual([h for h, _, _ in spy.copies], ["from-env"])


class CmdHealth(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ))
        os.environ.pop("MACVM_HOST", None)
        os.environ.pop("MACVM_REPO", None)

    def _health(self, *, host="h", repo=None, check_tools=None, spy=None):
        spy = spy or RemoteScriptSpy()
        args = argparse.Namespace(host=host, repo=repo, check_tools=check_tools)
        rc, out, err = capture_io(lambda: macvm.cmd_health(args, run=spy.run, copy=spy.copy))
        return rc, err, spy

    def test_tool_names_are_trimmed_before_they_reach_the_vm(self):
        # strip しないと " cargo" を探して導入済みのツールを未導入と誤報する。
        _, _, spy = self._health(check_tools="git, cargo")
        self.assertIn("tool=git\n", spy.bodies[0])
        self.assertIn("tool=cargo\n", spy.bodies[0])

    def test_missing_host_is_exit_2_without_touching_the_vm(self):
        rc, _, spy = self._health(host=None)
        self.assertEqual(rc, 2)
        self.assertEqual(spy.copies, [])

    def test_a_tilde_repo_is_refused_without_touching_the_vm(self):
        rc, err, spy = self._health(repo="~/repo")
        self.assertEqual(rc, 2)
        self.assertEqual(spy.copies, [])
        self.assertIn("~", err)

    def test_env_vars_supply_the_host_and_the_repo(self):
        os.environ["MACVM_HOST"] = "from-env"
        os.environ["MACVM_REPO"] = "/from/env/repo"
        _, _, spy = self._health(host=None)
        self.assertEqual([h for h, _, _ in spy.copies], ["from-env"])
        self.assertIn("/from/env/repo", spy.bodies[0])


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

    def _parse(self, argv):
        # argparse は SystemExit の前に usage を stderr へ書く。テスト出力を汚さない。
        with redirect_stderr(io.StringIO()):
            macvm.build_parser().parse_args(argv)

    def test_subcommand_is_required(self):
        with self.assertRaises(SystemExit):
            self._parse([])

    def test_screenshot_requires_an_output_path(self):
        # 省略を許すと保存先が None のまま Path(None) で TypeError になる。
        with self.assertRaises(SystemExit):
            self._parse(["screenshot", "--vm", "x"])


class RequiredArguments(unittest.TestCase):
    """必須引数が無いときは exit 2 (usage) で、VM には一切触れない。

    7 箇所の _require 呼び出し側をまとめて pin する。個別に書くと片方だけ直したときに
    残りが素通りする。
    """

    def setUp(self):
        self.enterContext(patch.dict(os.environ))
        for key in ("MACVM_VM", "MACVM_HOST", "MACVM_REPO"):
            os.environ.pop(key, None)

    def test_every_entry_point_refuses_before_touching_the_vm(self):
        def boom(*args, **kwargs):
            raise AssertionError("必須引数が無いのに VM へ触れた")

        ns = argparse.Namespace
        cases = [
            ("resolve-ip", "--vm", lambda: macvm.cmd_resolve_ip(ns(vm=None), run=boom)),
            ("doctor", "--vm", lambda: macvm.cmd_doctor(ns(vm=None, host=None), run=boom)),
            (
                "screenshot",
                "--vm",
                lambda: macvm.cmd_screenshot(ns(vm=None, out="/tmp/x.png"), run=boom),
            ),
            (
                "push",
                "--host",
                lambda: macvm.cmd_push(
                    ns(host=None, local="/tmp/x", remote="/tmp/y"),
                    run=boom,
                    copy=boom,
                    capture=boom,
                ),
            ),
            (
                "pull",
                "--host",
                lambda: macvm.cmd_pull(
                    ns(host=None, remote="/tmp/x", local="/tmp/y"), copy=boom, capture=boom
                ),
            ),
            (
                "exec",
                "--host",
                lambda: macvm.cmd_exec(ns(host=None, remote=["--", "true"]), run=boom, copy=boom),
            ),
            (
                "health",
                "--host",
                lambda: macvm.cmd_health(
                    ns(host=None, repo=None, check_tools=None), run=boom, copy=boom
                ),
            ),
        ]
        for name, flag, call in cases:
            with self.subTest(command=name):
                rc, _, err = capture_io(call)
                self.assertEqual(rc, 2)
                self.assertIn(flag, err)


class LineBuffering(unittest.TestCase):
    """main() は stdout/stderr を行バッファへ寄せる。

    現状の macvm には、子プロセスを流す呼び出しの「あいだ」に挟まる stdout 出力が
    無いので逆転は観測できない。これは予防で、stdout へ進捗を足したときに
    「子プロセスの出力より後ろにずれる」事故を先に塞いでおくためのもの。
    """

    class Spy:
        def __init__(self):
            self.line_buffering = None

        def reconfigure(self, **kwargs):
            self.line_buffering = kwargs.get("line_buffering")

    def test_streams_that_support_it_are_reconfigured(self):
        a, b = self.Spy(), self.Spy()
        macvm._enable_line_buffering(a, b)
        self.assertEqual([a.line_buffering, b.line_buffering], [True, True])

    def test_streams_without_reconfigure_are_left_alone(self):
        # 差し替えられた stdout (テストの StringIO 等) で落ちない。
        macvm._enable_line_buffering(object())

    def test_main_enables_it_before_dispatching(self):
        # ヘルパ単体だけを見ると、main() からの呼び出しが消えても全テストが緑のまま
        # 通る。予防機構は「取り付いていること」まで pin しないと意味が無い。
        calls = []
        with patch.object(macvm, "_enable_line_buffering", lambda *s: calls.append(s)):
            with redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    macvm.main([])
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0]), 2)  # stdout と stderr の両方


class SourceInvariants(unittest.TestCase):
    """ソース走査で守る不変条件。個々のテストでは「全経路」を見られない。"""

    SOURCE = Path(macvm.__file__).read_text(encoding="utf-8")

    # ssh/scp を起動するリストリテラルの数。増減したらこのテストを直す前に、
    # 増えた起動が SSH_OPTS を通っているかを確かめること。件数の対照が無いと、
    # 走査から落ちた起動が「違反なし」として緑になる。
    EXPECTED_LAUNCH_SITES = 5

    def _launch_lists(self):
        """先頭要素が "ssh" / "scp" のリストリテラルを AST で全部拾う。

        正規表現だと改行や空白の揺れで母集団から落ち、落ちたものは「違反なし」として
        緑になる。AST なら書式に依らない (ただし argv を変数で組み立てる書き方は
        どちらの方法でも映らない。射程はリストリテラルまで)。
        """
        return [
            node
            for node in ast.walk(ast.parse(self.SOURCE))
            if isinstance(node, ast.List)
            and node.elts
            and isinstance(node.elts[0], ast.Constant)
            and node.elts[0].value in ("ssh", "scp")
        ]

    def test_every_ssh_and_scp_launch_applies_the_shared_options(self):
        # 診断だけを硬くすると「doctor はすぐ返るのに health は固まる」という逆転が
        # 起きる。ソース全体を assert に渡すと失敗時に本文を丸ごと吐くので、違反した
        # 起動だけを取り出して比較する。
        launches = self._launch_lists()
        self.assertEqual(
            len(launches),
            self.EXPECTED_LAUNCH_SITES,
            "ssh/scp の起動地点が増減した。SSH_OPTS を通しているか確かめてから件数を直す",
        )
        offenders = [
            ast.unparse(node)[:60]
            for node in launches
            if not any(
                isinstance(e, ast.Starred)
                and isinstance(e.value, ast.Name)
                and e.value.id == "SSH_OPTS"
                for e in node.elts
            )
        ]
        self.assertEqual(offenders, [])

    def test_the_launch_scan_can_actually_see_a_violation(self):
        # 対照。走査が違反を検出できることを、対象と同じ形の合成物で確かめる。
        # これが無いと「0 件」が「違反なし」なのか「見ていない」なのか区別できない。
        class Synthetic(SourceInvariants):
            SOURCE = 'subprocess.run(["ssh", host, remote])\n'
            EXPECTED_LAUNCH_SITES = 1

        probe = Synthetic("test_every_ssh_and_scp_launch_applies_the_shared_options")
        with self.assertRaises(AssertionError):
            probe.test_every_ssh_and_scp_launch_applies_the_shared_options()

    # print( から file=sys.stderr までを取る。途中に別の print( を挟まない
    # (stdout 向けの print から跨いで拾うと、検査対象でない文字列を判定してしまう)。
    STDERR_PRINT = re.compile(r"print\(((?:(?!print\().)*?)file=sys\.stderr", re.S)

    def test_every_stderr_message_carries_a_prefix(self):
        # error: は終端する失敗、警告: は処理を続ける通知。この 2 つ以外は認めない。
        # 接頭辞を手で付ける運用は、次に print を足した人が落とす。
        blocks = self.STDERR_PRINT.findall(self.SOURCE)
        # 対照。0 件は「全部合格」ではなく「見ていない」。実測値 (この時点で 20 件) の
        # 下に置く。winvm の閾値をそのまま写すと macvm では即落ちする。
        self.assertGreaterEqual(len(blocks), 15, "stderr print の走査が空振りしている")
        unprefixed = []
        for block in blocks:
            head = block.strip().rstrip(",").strip()
            # f-string 接頭辞と開きクォートを剥がして本文の先頭を見る。
            if head.lstrip("f").lstrip("\"'").startswith(("error:", "警告:")):
                continue
            unprefixed.append(head[:60])
        self.assertEqual(unprefixed, [])


if __name__ == "__main__":
    unittest.main()
