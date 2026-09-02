#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""macos-vm-verification: Parallels Desktop 上の macOS VM を繋ぐ/調べる/検証する generic CLI。

winvm (Windows 版) と役割は同じだが、ゲスト内の操作系が sh になる点と、GUI セッション
(Aqua) の有無を観測する点が違う。ホスト側 (prlctl) の扱いは共通で、意図的に同じ形にしてある。
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

# 対話プロンプトで無期限にブロックしないための共通 ssh オプション。
# 診断だけに付けると「doctor は 10 秒で返るのに health は固まる」という逆転が起きるので、
# ssh を起動する経路すべてで同じものを使う。
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]

# リモートファイル不在を表す ASCII の目印。locale で変わる stat のエラー文を判定に混ぜない。
REMOTE_MISSING_MARK = "MACVM_MISSING"

# GUI セッションが無いことを表す ASCII の目印。console の所有者は root か実ユーザーで、
# ログイン画面のままだと root のままになる。
NO_AQUA_MARK = "MACVM_NO_AQUA"


# ---------------------------------------------------------------------------
# prlctl の argv 組み立て
# ---------------------------------------------------------------------------


def prlctl_list_argv() -> list[str]:
    """全 VM の詳細を JSON で列挙する argv。

    `-i` (詳細) と `-j` (JSON) を併せると、状態・IP・Parallels Tools が 1 回の呼び出しで
    構造化されて返る。`-f` + `-j` の要約形にすると IP がダッシュや空白区切りの文字列になり、
    その解析をこちら側に持つことになる。
    """
    return ["prlctl", "list", "-a", "-i", "-j"]


def prlctl_capture_argv(vm: str, file: str) -> list[str]:
    """VM の画面をホスト側から PNG に撮る argv。

    SSH 越しに screencapture を叩かない。SSH セッションは Aqua セッションと分離しており、
    対話ユーザーのデスクトップが見えない。prlctl capture はホスト側から VM の画面を直接
    撮るのでセッション分離の影響を受けない。
    """
    return ["prlctl", "capture", vm, "--file", file]


def run_capture(argv: list[str]) -> tuple[int, str, str]:
    """argv を実行して (returncode, stdout, stderr) を返す。実行不能も戻り値で表す。

    `errors="replace"` を付けておく。macOS ゲストの出力は UTF-8 なので Windows 版のような
    CP932 問題は出ないが、判定に使う目印を ASCII に保つ方針は共通で、化けても落ちない方が
    診断として扱いやすい。
    """
    try:
        p = subprocess.run(argv, capture_output=True, text=True, errors="replace")
    except OSError as e:
        return 127, "", f"{argv[0]} を実行できません: {e}"
    return p.returncode, p.stdout, p.stderr


# ---------------------------------------------------------------------------
# prlctl 出力の parse
# ---------------------------------------------------------------------------


def parse_vm_list(json_text: str) -> list[dict]:
    """`prlctl list -a -i -j` の JSON をレコードのリストにする。

    壊れた出力で例外を投げず「該当なし」に倒す。呼び出し側は必ず「見つからなかった」経路を
    持つので、そこへ合流させる方が扱いが一様になる。
    """
    try:
        data = json.loads(json_text)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    return [v for v in data if isinstance(v, dict)]


def _normalize_uuid(value: str) -> str:
    """UUID の中括弧と大小を落として比較用に揃える。UUID でなければそのまま返す。"""
    v = value.strip().strip("{}").lower()
    return v


def find_vm(vms: list[dict], vm_id: str) -> dict | None:
    """名前 (完全一致) か UUID でレコードを引く。部分一致はしない。

    prlctl が受け付ける識別子と同じ集合に揃える。ここだけ部分一致を許すと、macvm と prlctl を
    混ぜて使ったときに指す VM がずれる。
    """
    want = _normalize_uuid(vm_id)
    for v in vms:
        if str(v.get("Name", "")) == vm_id:
            return v
        if _normalize_uuid(str(v.get("ID", ""))) == want:
            return v
    return None


def pick_ipv4(network: object) -> str | None:
    """Network レコードから最初の IPv4 を取る。無ければ None。"""
    if not isinstance(network, dict):
        return None
    entries = network.get("ipAddresses")
    if not isinstance(entries, list):
        return None
    for e in entries:
        if not isinstance(e, dict):
            continue
        if str(e.get("type", "")) != "ipv4":
            continue
        ip = str(e.get("ip", "")).strip()
        if ip:
            return ip
    return None


def is_apipa(ip: str) -> bool:
    """169.254.0.0/16 かどうか。DHCP が取れていない状態の目印になる。"""
    try:
        return ipaddress.ip_address(ip) in ipaddress.ip_network("169.254.0.0/16")
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# ゲスト内で実行する sh コマンドの組み立て
# ---------------------------------------------------------------------------


def remote_size_command(remote: str) -> str:
    """リモートファイルのサイズをバイト数で出す sh コマンド。

    不在は ASCII の目印に固定する。`stat` が返すエラー文は locale で変わるので、数字でも
    目印でもない出力を判定に混ぜないため、先に `-f` で分岐する。
    """
    q = shlex.quote(remote)
    return f"if [ -f {q} ]; then stat -f %z {q}; else echo {REMOTE_MISSING_MARK}; fi"


def parse_remote_size(output: str) -> int | None:
    """remote_size_command の出力をバイト数にする。数字以外は等しく None。

    不在の目印も想定外の出力も「サイズは得られなかった」に倒す。呼び出し側は None を
    照合失敗として扱う。
    """
    t = output.strip()
    return int(t) if t.isdigit() else None


def remote_parent_mkdir_command(remote: str) -> str | None:
    """remote パスの親ディレクトリを作る sh コマンド。不要なら None。

    親が `.` や `/` のときは発行しない。作る必要が無く、`/` への mkdir は無意味に失敗する。
    """
    parent = str(PurePosixPath(remote).parent)
    if parent in (".", "/"):
        return None
    return f"mkdir -p {shlex.quote(parent)}"


def console_owner_command() -> str:
    """GUI (Aqua) セッションの所有者を出す sh コマンド。

    ログイン画面のままだと `/dev/console` の所有者は root で、実ユーザーでログインすると
    そのユーザーになる。`who` は環境によって空を返すことがあるので、単独では使わない。
    root だった場合は ASCII の目印へ倒し、呼び出し側が「GUI セッション無し」と読めるようにする。
    """
    return (
        'owner=$(stat -f %Su /dev/console); '
        f'if [ "$owner" = "root" ]; then echo {NO_AQUA_MARK}; else echo "$owner"; fi'
    )


def build_exec_shell(command: str) -> str:
    """任意コマンドを包む sh スクリプト本文。

    コマンドはシェルを経由せずファイルへ書かれるので、`ssh host "..."` で argv が空白連結
    されてクォートが落ちる問題を受けない。パイプもリダイレクトもそのまま解釈される。

    exit code は最後のコマンドのものがそのまま sh の exit code になるので、伝搬のための
    細工は要らない (pwsh 版が必要としていた $LASTEXITCODE の扱いに相当するものが無い)。
    """
    return command if command.endswith("\n") else command + "\n"


def build_health_shell(tools: list[str], repo: str | None) -> str:
    """health が VM 上で走らせる sh スクリプト本文。

    判定に使う目印は ASCII に保つ。項目ごとに「観測値」を出し、OK/NG だけの出力にしない。
    どれか 1 つでも欠けたら非 0 で終わるが、途中で止めずに全項目を出してから終える
    (最初の失敗で打ち切ると、残りが健全かどうかが分からないまま報告になる)。
    """
    lines = [
        "fail=0",
        'echo "os_version=$(sw_vers -productVersion)"',
        'echo "arch=$(uname -m)"',
        'echo "disk_avail=$(df -h / | tail -1 | awk \'{print $4}\')"',
        f'echo "console_owner=$({console_owner_command()})"',
    ]
    for t in tools:
        q = shlex.quote(t)
        lines.append(
            f'if command -v {q} >/dev/null 2>&1; then echo "tool_{t}=$(command -v {q})"; '
            f'else echo "tool_{t}=MISSING"; fail=1; fi'
        )
    if repo:
        q = shlex.quote(repo)
        lines.append(
            f'if [ -d {q} ]; then echo "repo={q}"; '
            f'else echo "repo=MISSING"; fail=1; fi'
        )
    lines.append("exit $fail")
    return "\n".join(lines) + "\n"


def remote_script_path(kind: str) -> str:
    """VM 上に置く一時スクリプトのパス。衝突を避けるため用途を名前に含める。"""
    return f"/tmp/macvm-{kind}.sh"


def remote_sh_command(remote_path: str) -> str:
    return f"sh {shlex.quote(remote_path)}"


def remote_cleanup_command(remote_path: str) -> str:
    return f"rm -f {shlex.quote(remote_path)}"


def remote_command_from_args(remote: list[str] | None) -> str | None:
    """argparse REMAINDER から先頭の '--' を除き remote コマンド文字列を返す。空なら None。

    REMAINDER は区切りの '--' を要素として残す。除かないとスクリプトの 1 行目が '--' で
    始まり、`-- echo x | tr a-z A-Z` のような形になる。'--' というコマンドは無いので
    左辺だけが失敗し、パイプの右辺には空が渡る。2 行目以降と exit code は正常に流れるため、
    出力が 1 つ欠けているだけの成功に見える。
    """
    if remote and remote[0] == "--":
        remote = remote[1:]
    if not remote:
        return None
    joined = " ".join(remote).strip()
    return joined or None


# ---------------------------------------------------------------------------
# ssh / scp ラッパ
# ---------------------------------------------------------------------------


def ssh_capture(host: str, remote: str) -> str:
    return run_capture(["ssh", *SSH_OPTS, host, remote])[1]


# 以下の ssh / scp ラッパは run_capture を使わない。remote コマンドの進捗とビルド出力を
# 端末へそのまま流すのが目的で、捕捉すると完了までユーザに何も見えなくなる。
def run_ssh_code(host: str, remote: str) -> int:
    """remote コマンドの exit code をそのまま返す (ssh は remote の code を伝搬する)。"""
    return subprocess.run(["ssh", *SSH_OPTS, host, remote]).returncode


def run_ssh(host: str, remote: str) -> bool:
    return run_ssh_code(host, remote) == 0


def scp(host: str, local: str, dest: str) -> bool:
    return subprocess.run(["scp", *SSH_OPTS, "-q", local, f"{host}:{dest}"]).returncode == 0


def scp_pull(host: str, remote: str, local: str) -> bool:
    return subprocess.run(["scp", *SSH_OPTS, "-q", f"{host}:{remote}", local]).returncode == 0


def _ssh_reachable(host: str) -> bool:
    rc, _, _ = run_capture(["ssh", *SSH_OPTS, host, "exit"])
    return rc == 0


# ---------------------------------------------------------------------------
# 共通ヘルパ
# ---------------------------------------------------------------------------


def _env_or(arg_value: str | None, env_key: str, default: str | None = None) -> str | None:
    return arg_value or os.environ.get(env_key) or default


def _load_vms(run) -> tuple[list[dict] | None, str | None]:
    """(レコード一覧, 素のエラーメッセージ) を返す。表示用の接頭辞は呼び出し側で付ける。"""
    rc, out, err = run(prlctl_list_argv())
    if rc != 0:
        detail = err.strip() or out.strip() or f"rc={rc}"
        return None, f"prlctl の VM 列挙に失敗しました: {detail}"
    return parse_vm_list(out), None


def _known_names(vms: list[dict]) -> str:
    names = sorted(str(v.get("Name", "")) for v in vms if v.get("Name"))
    return ", ".join(names) if names else "(登録済み VM なし)"


def _require(value: str | None, flag: str, env_key: str) -> str | None:
    if value:
        return value
    print(f"error: {flag} (または {env_key}) が必要です", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# resolve-ip
# ---------------------------------------------------------------------------


def cmd_resolve_ip(args: argparse.Namespace, *, run=run_capture) -> int:
    vm_id = _env_or(args.vm, "MACVM_VM")
    if not _require(vm_id, "--vm", "MACVM_VM"):
        return 2
    vms, err = _load_vms(run)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    vm = find_vm(vms, vm_id)
    if vm is None:
        print(
            f"error: VM が見つかりません: {vm_id} / 登録済み: {_known_names(vms)}",
            file=sys.stderr,
        )
        return 1
    name = str(vm.get("Name", vm_id))
    status = str(vm.get("State", "unknown"))
    ip = pick_ipv4(vm.get("Network"))
    if ip is None:
        print(
            f"error: IP を解決できません (status={status})。"
            f'VM が起動していない場合は prlctl start "{name}" で起動する',
            file=sys.stderr,
        )
        return 1
    if is_apipa(ip):
        # stdout と exit code は変えない。このコマンドは ProxyCommand の中で動くので stdout は
        # nc の接続先そのもので、exit code を 1 にすると nc が空文字を掴む。ProxyCommand の
        # stderr は ssh の呼び出し元へ素通しされるので、待たされている人の目には届く。
        print(
            f'警告: {ip} は APIPA。macvm doctor --vm "{name}" で原因を確認する',
            file=sys.stderr,
        )
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
    indent = " " * (max(len(m) for m in _MARK.values()) + 1)
    lines: list[str] = []
    for c in checks:
        lines.append(f"{_MARK[c.ok]} {c.label.ljust(width)} : {c.observed}")
        if c.ok is False and c.hint:
            lines.append(f"{indent}-> {c.hint}")
    return "\n".join(lines)


def doctor_exit_code(checks: list[Check]) -> int:
    return 1 if any(c.ok is False for c in checks) else 0


def collect_doctor_checks(
    vm_id: str,
    host: str | None = None,
    *,
    run=run_capture,
    ssh_probe=_ssh_reachable,
    console_probe=ssh_capture,
) -> list[Check]:
    """VM が使える状態かをホスト側から観測する。各項目は観測値を持つ。"""
    vms, err = _load_vms(run)
    if err:
        return [Check("prlctl", err, ok=False, hint="Parallels Desktop が起動しているか確認する")]

    vm = find_vm(vms, vm_id)
    if vm is None:
        return [
            Check(
                "VM 登録",
                f"{vm_id} は見つからない / 登録済み: {_known_names(vms)}",
                ok=False,
                hint="名前は完全一致で照合する。prlctl list -a で確認する",
            )
        ]

    name = str(vm.get("Name", vm_id))
    state = str(vm.get("State", "unknown"))
    checks = [
        Check("VM 登録", name, ok=True),
        Check(
            "VM 状態",
            state,
            ok=(state == "running"),
            hint=f'prlctl start "{name}" で起動する',
        ),
    ]

    tools = vm.get("GuestTools")
    tools_state = str(tools.get("state", "unknown")) if isinstance(tools, dict) else "unknown"
    checks.append(
        Check(
            "Parallels Tools",
            tools_state,
            ok=(tools_state == "installed"),
            hint="Tools が無いと IP 解決と capture が使えない",
        )
    )

    ip = pick_ipv4(vm.get("Network"))
    if ip is None:
        checks.append(
            Check(
                "IP",
                "未割当",
                ok=False,
                hint="VM が起動直後だと未割当のことがある。数秒待って再実行する",
            )
        )
        return checks
    checks.append(
        Check(
            "IP",
            ip,
            ok=(not is_apipa(ip)),
            hint="APIPA は DHCP が取れていない。VM のネットワーク設定を確認する",
        )
    )

    if not host:
        checks.append(
            Check("SSH", "--host 未指定のため未確認", ok=None, hint=None)
        )
        return checks

    reachable = ssh_probe(host)
    checks.append(
        Check(
            "SSH",
            f"{host} へ接続{'できる' if reachable else 'できない'}",
            ok=reachable,
            hint="鍵が配置されているか、Remote Login が有効かを確認する (references/macos-bootstrap.md)",
        )
    )
    if not reachable:
        return checks

    owner = console_probe(host, console_owner_command()).strip()
    has_aqua = bool(owner) and owner != NO_AQUA_MARK
    checks.append(
        Check(
            "GUI セッション",
            owner if has_aqua else "ログイン画面 (Aqua セッション無し)",
            ok=has_aqua,
            hint="GUI アプリを起動しても画面に出ない。自動ログインを設定する (references/macos-bootstrap.md)",
        )
    )
    return checks


def cmd_doctor(args: argparse.Namespace, *, run=run_capture, ssh_probe=_ssh_reachable) -> int:
    vm_id = _env_or(args.vm, "MACVM_VM")
    if not _require(vm_id, "--vm", "MACVM_VM"):
        return 2
    host = _env_or(args.host, "MACVM_HOST")
    checks = collect_doctor_checks(vm_id, host, run=run, ssh_probe=ssh_probe)
    print(format_doctor_report(checks))
    return doctor_exit_code(checks)


# ---------------------------------------------------------------------------
# screenshot
# ---------------------------------------------------------------------------


def cmd_screenshot(args: argparse.Namespace, *, run=run_capture) -> int:
    vm_id = _env_or(args.vm, "MACVM_VM")
    if not _require(vm_id, "--vm", "MACVM_VM"):
        return 2
    vms, err = _load_vms(run)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    vm = find_vm(vms, vm_id)
    if vm is None:
        print(
            f"error: VM が見つかりません: {vm_id} / 登録済み: {_known_names(vms)}",
            file=sys.stderr,
        )
        return 1
    name = str(vm.get("Name", vm_id))
    status = str(vm.get("State", "unknown"))
    if status != "running":
        print(
            f"error: スクリーンショットを撮れません (status={status})。"
            f'VM が起動していない場合は prlctl start "{name}" で起動する',
            file=sys.stderr,
        )
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rc, out_text, err_text = run(prlctl_capture_argv(name, str(out)))
    if rc != 0:
        detail = err_text.strip() or out_text.strip() or f"rc={rc}"
        print(f"error: prlctl capture が失敗しました: {detail}", file=sys.stderr)
        return 1
    # rc 0 を成功と読み替えない。「撮れたつもりで空」を防ぐためファイルの実体を見る。
    if not out.is_file():
        print(f"error: prlctl capture は成功したがファイルがありません: {out}", file=sys.stderr)
        return 1
    size = out.stat().st_size
    if size == 0:
        print(f"error: スクリーンショットが空です (0 バイト): {out}", file=sys.stderr)
        return 1
    print(f"スクリーンショットを保存: {out} ({size} bytes)")
    return 0


# ---------------------------------------------------------------------------
# push / pull
# ---------------------------------------------------------------------------


def _report_size_mismatch(expected: int, actual: int | None) -> None:
    actual_text = str(actual) if actual is not None else "取得できません"
    print(
        f"error: 転送後のサイズが一致しません (期待={expected} 実測={actual_text})。"
        "転送が途中で切れた可能性",
        file=sys.stderr,
    )


def cmd_push(args: argparse.Namespace, *, run=run_ssh, copy=scp, capture=ssh_capture) -> int:
    host = _env_or(args.host, "MACVM_HOST")
    if not _require(host, "--host", "MACVM_HOST"):
        return 2
    local = Path(args.local)
    if not local.is_file():
        print(f"error: ローカルファイルがありません: {local}", file=sys.stderr)
        return 1
    remote = args.remote
    mk = remote_parent_mkdir_command(remote)
    if mk is not None and not run(host, mk):
        print("error: VM のディレクトリ作成に失敗しました", file=sys.stderr)
        return 1
    if not copy(host, str(local), remote):
        print(f"error: scp 失敗: {local}", file=sys.stderr)
        return 1
    # scp の rc 0 を「完了」と読み替えない。実体のサイズで途中切れを検出する。
    local_size = local.stat().st_size
    remote_size = parse_remote_size(capture(host, remote_size_command(remote)))
    if remote_size != local_size:
        _report_size_mismatch(local_size, remote_size)
        return 1
    print(f"転送完了: {local} -> {host}:{remote} ({local_size} bytes)")
    return 0


def cmd_pull(args: argparse.Namespace, *, copy=scp_pull, capture=ssh_capture) -> int:
    host = _env_or(args.host, "MACVM_HOST")
    if not _require(host, "--host", "MACVM_HOST"):
        return 2
    remote = args.remote
    local = Path(args.local)
    # 不在を後段のサイズ照合の失敗に化けさせず、転送前に明示して止める。
    remote_size = parse_remote_size(capture(host, remote_size_command(remote)))
    if remote_size is None:
        print(
            f"error: リモートファイルのサイズを取得できません (不在の可能性): {host}:{remote}",
            file=sys.stderr,
        )
        return 1
    local.parent.mkdir(parents=True, exist_ok=True)
    if not copy(host, remote, str(local)):
        print(f"error: scp 失敗: {remote}", file=sys.stderr)
        return 1
    local_size = local.stat().st_size if local.is_file() else None
    if local_size != remote_size:
        _report_size_mismatch(remote_size, local_size)
        return 1
    print(f"転送完了: {host}:{remote} -> {local} ({local_size} bytes)")
    return 0


# ---------------------------------------------------------------------------
# exec / health (どちらもスクリプトを scp して sh で実行する)
# ---------------------------------------------------------------------------


def _run_remote_script(host: str, kind: str, body: str, *, run, copy) -> int:
    """スクリプト本文を VM へ送って sh で実行し、後始末する。exit code はそのまま返す。

    コマンドをシェル経由で渡さないのは、`ssh host "..."` が argv を空白連結するためで、
    クォートやパイプを含むコマンドが呼び出し側の意図と違う形で解釈される。
    """
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False, encoding="utf-8") as fh:
        fh.write(body)
        local = fh.name
    remote = remote_script_path(kind)
    try:
        if not copy(host, local, remote):
            print(f"error: {kind} スクリプトの scp に失敗しました", file=sys.stderr)
            return 1
        try:
            return run(host, remote_sh_command(remote))
        finally:
            run(host, remote_cleanup_command(remote))
    finally:
        Path(local).unlink(missing_ok=True)


def cmd_exec(args: argparse.Namespace, *, run=run_ssh_code, copy=scp) -> int:
    """任意コマンドを .sh に書いて scp し sh で実行、後始末 (health と同じ経路)。

    exit code はリモートの値をそのまま macvm の exit code にする (成否を握り潰さない)。
    """
    host = _env_or(args.host, "MACVM_HOST")
    if not _require(host, "--host", "MACVM_HOST"):
        return 2
    command = remote_command_from_args(args.remote)
    if command is None:
        print("error: exec には -- の後にコマンドが必要です", file=sys.stderr)
        return 2
    return _run_remote_script(host, "exec", build_exec_shell(command), run=run, copy=copy)


def cmd_health(args: argparse.Namespace, *, run=run_ssh_code, copy=scp) -> int:
    """VM の健全性を観測する。項目ごとに観測値を出し、欠けがあれば非 0 で終わる。"""
    host = _env_or(args.host, "MACVM_HOST")
    if not _require(host, "--host", "MACVM_HOST"):
        return 2
    repo = _env_or(args.repo, "MACVM_REPO")
    # `--check-tools "git, cargo"` のように空白を入れて書かれても拾えるようにする。
    # strip しないと " cargo" を探して導入済みのツールを未導入と誤報する。
    tools = args.check_tools.split(",") if args.check_tools else []
    body = build_health_shell([t.strip() for t in tools if t.strip()], repo)
    return _run_remote_script(host, "health", body, run=run, copy=copy)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="macvm", description="Parallels macOS VM ops/verify")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("resolve-ip", help="prlctl から VM の現 IP を解決")
    sp.add_argument("--vm", help="VM 名または UUID (env: MACVM_VM)")
    sp.set_defaults(func=cmd_resolve_ip)

    dp = sub.add_parser("doctor", help="VM が使える状態かをホスト側から診断")
    dp.add_argument("--vm", help="VM 名または UUID (env: MACVM_VM)")
    dp.add_argument("--host", help="SSH ホスト (env: MACVM_HOST)。指定すると SSH と GUI も見る")
    dp.set_defaults(func=cmd_doctor)

    cp = sub.add_parser("screenshot", help="prlctl でホスト側から VM 画面を PNG に撮る")
    cp.add_argument("--vm", help="VM 名または UUID (env: MACVM_VM)")
    cp.add_argument("--out", required=True, help="保存先パス")
    cp.set_defaults(func=cmd_screenshot)

    pp = sub.add_parser("push", help="ローカルファイルを VM へ転送 (サイズ照合つき)")
    pp.add_argument("--host", help="SSH ホスト (env: MACVM_HOST)")
    pp.add_argument("local", help="ローカルのファイルパス")
    pp.add_argument("remote", help="VM 側の保存先パス")
    pp.set_defaults(func=cmd_push)

    lp = sub.add_parser("pull", help="VM のファイルをローカルへ転送 (サイズ照合つき)")
    lp.add_argument("--host", help="SSH ホスト (env: MACVM_HOST)")
    lp.add_argument("remote", help="VM 側のファイルパス")
    lp.add_argument("local", help="ローカルの保存先パス")
    lp.set_defaults(func=cmd_pull)

    ep = sub.add_parser("exec", help="任意コマンドを .sh 転送で実行 (クォート/パイプ安全)")
    ep.add_argument("--host", help="SSH ホスト (env: MACVM_HOST)")
    ep.add_argument("remote", nargs=argparse.REMAINDER, help="-- の後に実行するコマンド")
    ep.set_defaults(func=cmd_exec)

    hp = sub.add_parser("health", help="SSH 越しに VM の健全性を検査")
    hp.add_argument("--host", help="SSH ホスト (env: MACVM_HOST)")
    hp.add_argument("--repo", help="存在を確認するリポジトリパス (env: MACVM_REPO)")
    hp.add_argument("--check-tools", help="存在を確認するコマンドのカンマ区切り")
    hp.set_defaults(func=cmd_health)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
