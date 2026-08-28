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
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

# 対話プロンプトで無期限にブロックしないための共通 ssh オプション。
# 診断だけに付けると「doctor は 10 秒で返るのに run と health は固まる」という
# 逆転が起きるので、ssh を起動する経路すべてで同じものを使う。
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]

# ---------------------------------------------------------------------------
# prlctl の argv 組み立て
# ---------------------------------------------------------------------------


def prlctl_list_argv() -> list[str]:
    """全 VM の詳細を JSON で列挙する argv。

    `-i` (詳細) と `-j` (JSON) を併せると、状態・IP・Parallels Tools・バンドルの
    パスが 1 回の呼び出しで構造化されて返る。`-f` + `-j` の要約形にすると IP が
    `10.211.55.3` / `-` / 空白区切りの複数値という 3 通りの文字列になり、
    ダッシュや空白を IP と取り違えないための解析をこちら側に持つことになる。
    """
    return ["prlctl", "list", "-a", "-i", "-j"]


def prlctl_exec_argv(vm: str, command: list[str]) -> list[str]:
    """ゲスト内でコマンドを実行する argv。

    コマンドはトークンを分割したまま渡す。1 つの文字列に連結して渡すと
    exit 2 で無出力のまま黙って失敗する (Parallels Desktop 26.4.0 で実測)。
    """
    return ["prlctl", "exec", vm, *command]


def run_capture(argv: list[str]) -> tuple[int, str, str]:
    """argv を実行して (returncode, stdout, stderr) を返す。実行不能も戻り値で表す。

    `errors="replace"` が要る。ja-JP の VM は非対話出力を CP932 で書くため、
    strict に decode すると VM が失敗を報告した瞬間に winvm 自身が UnicodeDecodeError で
    落ちる。判定に使う目印 (サイズの数字・WINVM_MISSING・SHA 等) は ASCII に保つ方針
    なので、化けるのは人間向けのエラー文だけで判定には影響しない。
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
        if vm.get("Name") == ident:
            return vm
    wanted = _normalize_uuid(ident)
    for vm in vms:
        if _normalize_uuid(str(vm.get("ID", ""))) == wanted:
            return vm
    return None


def pick_ipv4(network: dict | None) -> str | None:
    """`Network.ipAddresses` から IPv4 を 1 つ返す。無ければ None。

    エントリは `{"type": "ipv4"|"ipv6", "ip": "..."}` で、停止中の VM は空リストに
    なる (実測)。`type` が正なので IPv6 と取り違える余地は無いが、値が壊れた
    エントリを SSH の接続先へ素通しさせないため IPv4 として妥当かも確かめる。
    """
    for entry in (network or {}).get("ipAddresses") or []:
        if not isinstance(entry, dict) or entry.get("type") != "ipv4":
            continue
        ip = str(entry.get("ip", "")).strip()
        try:
            ipaddress.IPv4Address(ip)
        except ValueError:
            continue
        return ip
    return None


def is_apipa(ip: str | None) -> bool:
    """`ip` が APIPA の帯 (169.254/16) にあるか。

    DHCP から応答が無いとき OS が自分で振る帯で、値が取れていてもネットワークは
    無い。doctor と resolve-ip の両方がこの判定を要るので、帯の定義はここだけに
    置く。`pick_ipv4` を通った値は妥当性が済んでいるので再 parse で例外は出ないが、
    IP 未取得の None は `IPv4Address` が送出するので先に弾く。
    """
    return ip is not None and ipaddress.IPv4Address(ip).is_link_local


def parse_tools(vm: dict) -> tuple[str | None, str | None]:
    """レコードの `GuestTools` から (state, version) を返す。

    未導入の VM は `{"state": "not_installed"}` で version キーごと欠ける (実測)。
    """
    tools = vm.get("GuestTools")
    if not isinstance(tools, dict):
        return None, None
    return tools.get("state"), tools.get("version")


ISOLATION_PATH = "./Settings/Tools/IsolatedVm"


def parse_isolated_flag(config_text: str) -> bool | None:
    """config.pvs の隔離フラグを bool で返す。読めなければ None。

    隔離の状態を出す prlctl の口が無いので (`prlctl list -L` にフィールドが無く、
    `-i` は text/JSON とも該当キーを持たない。バイナリにも `--isolate-vm` の
    setter しか無い)、設定ファイルを直接読むしかない。

    正規表現ではなく XML として `./Settings/Tools/IsolatedVm` を引く。要素名だけで
    探すと、Parallels が別セクション (スナップショット等) に同名要素を増やしたとき
    例外でも None でもなく「間違った bool」を静かに返す。それは tri-state を
    作ってまで防ごうとしている失敗そのものになる。

    「隔離されていない」と「読めなかった」を同じ値にしない。この区別を潰すと
    診断が「未確認」を「健全」として報告してしまう。
    """
    try:
        root = ElementTree.fromstring(config_text)
    except ElementTree.ParseError:
        return None
    node = root.find(ISOLATION_PATH)
    if node is None or node.text is None:
        return None
    return node.text.strip() == "1"


# ---------------------------------------------------------------------------
# 共通
# ---------------------------------------------------------------------------

UNKNOWN = "(未確認)"


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
        print(f"error: {err}", file=sys.stderr)
        return 1
    vm = find_vm(vms, vm_id)
    if vm is None:
        print(
            f"error: VM が見つかりません: {vm_id} / 登録済み: {_known_names(vms)}",
            file=sys.stderr,
        )
        return 1
    status = str(vm.get("State", "unknown"))
    ip = pick_ipv4(vm.get("Network"))
    if ip is None:
        print(
            f"error: IP を解決できません (status={status})。"
            f'VM が起動していない場合は prlctl start "{vm.get("Name", vm_id)}" で起動する',
            file=sys.stderr,
        )
        return 1
    if is_apipa(ip):
        # stdout と exit code は変えない。このコマンドは ProxyCommand の中で動くので
        # stdout は nc の接続先そのもので、exit code を 1 にすると nc が空文字を掴む。
        # ProxyCommand の stderr は ssh の呼び出し元へ素通しされる (実測) ので、
        # 不透明なタイムアウトを待っている最中の人の目に届く。原因の説明は doctor の
        # hint が持つ。ここはそこへの入口だけを出す。
        print(
            f'警告: {ip} は APIPA。winvm doctor --vm "{vm.get("Name", vm_id)}" で原因を確認する',
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


def _ssh_reachable(host: str) -> bool:
    rc, _, _ = run_capture(["ssh", *SSH_OPTS, host, "exit"])
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
                err,
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

    # 以降の prlctl 呼び出しには解決済みの名前を渡す。find_vm は大文字 UUID や
    # 波括弧付きも受けるが、prlctl が同じ集合を受けるかは確かめていない。
    name = str(vm.get("Name", ""))
    status = str(vm.get("State", "unknown"))
    ip = pick_ipv4(vm.get("Network"))
    tools_state, tools_version = parse_tools(vm)
    isolated = _read_isolation(vm.get("Home"))
    apipa = is_apipa(ip)

    checks = [
        Check("VM", f"{name} ({vm.get('ID', '')})", True),
        Check("status", status, status == "running", hint=f'prlctl start "{name}"'),
        Check(
            "IP",
            ip or UNKNOWN,
            ip is not None and not apipa,
            # 未取得と APIPA は原因が違うので確認先も分ける。APIPA では IP が取れている
            # 以上 Parallels Tools は動いており、そちらへ誘導すると誤診に時間を使う。
            hint=(
                "DHCP が応答していない。references/troubleshooting.md の APIPA の節を見る"
                if apipa
                else "VM を起動し Parallels Tools が動いているか確認する"
            ),
        ),
        Check(
            "Parallels Tools",
            " ".join(x for x in (tools_state, tools_version) if x) or UNKNOWN,
            None if tools_state is None else tools_state == "installed",
            hint="Parallels のメニューから Parallels Tools を再インストールする",
        ),
        Check(
            "host isolation",
            {True: "on", False: "off", None: UNKNOWN}[isolated],
            None if isolated is None else not isolated,
            hint=f'prlctl set "{name}" --isolate-vm off',
        ),
    ]

    rc_exec, out_exec, err_exec = run(prlctl_exec_argv(name, ["cmd.exe", "/c", "ver"]))
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


def _read_isolation(home: object) -> bool | None:
    """バンドルの `Home` から config.pvs を読んで隔離フラグを返す。読めなければ None。"""
    if not isinstance(home, str) or not home:
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
# screenshot (prlctl ベース。SSH のセッション分離を回避する)
# ---------------------------------------------------------------------------


def prlctl_capture_argv(vm: str, file: str) -> list[str]:
    """VM の画面をホスト側から PNG に撮る argv。

    SSH 越しに PowerShell でキャプチャしない。Windows の SSH セッションは session 0 で、
    対話ユーザーのデスクトップ (別セッション) が見えず黒画面になる。prlctl capture は
    ホスト側から VM の画面を直接撮るのでセッション分離の影響を受けない
    (`-f,--file` は prlctl 26.4.1 の capture --help で実測した唯一のオプション)。
    """
    return ["prlctl", "capture", vm, "--file", file]


def cmd_screenshot(args: argparse.Namespace, *, run=run_capture) -> int:
    vm_id = _env_or(args.vm, "WINVM_VM")
    if not vm_id:
        print("error: --vm (または WINVM_VM) が必要です", file=sys.stderr)
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
# run (SSH ベース。ハイパーバイザに依存しない)
# ---------------------------------------------------------------------------


def files_to_sync(branch_delta: str, working_delta: str, untracked: str) -> list[str]:
    """行をトリム/空行除去/重複排除/安定ソートする。"""
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
    return path.replace("/", "\\")


def resolve_diff_base(vm_head: str, vm_head_known: bool, fallback: str) -> str:
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
    return run_capture(["git", *args])[1]


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
    # 出力を捕捉する。解決できないのは想定内 (VM 側が別履歴のときに起きる) で、
    # git の "Not a valid object name" を端末へ出すと下の警告と二重に見える。
    vm_head_known = bool(vm_head) and (
        run_capture(["git", "cat-file", "-e", f"{vm_head}^{{commit}}"])[0] == 0
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
        print("error: VM の reset に失敗しました", file=sys.stderr)
        return 1
    if deleted:
        print(f"削除を同期: {len(deleted)} ファイル")
        for rm in remote_delete_commands(repo_win, deleted):
            if not run_ssh(host, rm):
                print("error: VM のファイル削除に失敗しました", file=sys.stderr)
                return 1
    if files:
        for mk in parent_mkdir_commands(repo_win, files):
            if not run_ssh(host, mk):
                print("error: VM のディレクトリ作成に失敗しました", file=sys.stderr)
                return 1
        for f in files:
            if not scp(host, f, f"{repo}/{f}"):
                print(f"error: scp 失敗: {f}", file=sys.stderr)
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


def pwsh_file_command(remote: str) -> str:
    """転送した .ps1 を pwsh(7) の -File で実行するコマンド (health / exec 共用)。

    pwsh は RemoteSigned かつ scp 転送物には Mark-of-the-Web が付かないため
    `-ExecutionPolicy Bypass` 無しで実行できる。WinPS 5.1 は Restricted なので
    同じスクリプトが弾かれる (実測)。Bypass で上書きすると多層防御に穴が空く。
    """
    return f"pwsh -NoProfile -File {remote}"


def pwsh_cleanup_command(remote: str) -> str:
    """転送した .ps1 を削除する後始末コマンド (実行系と同じ pwsh に揃える)。"""
    return f"pwsh -NoProfile -Command Remove-Item -Force {remote}"


def pwsh_probe_command() -> str:
    """VM の PATH に pwsh(7) があるか判定する cmd.exe コマンド。

    見つかれば exit 0、無ければ非 0。パス出力は抑制する。
    """
    return "where pwsh >nul 2>nul"


def remote_ps1_path(kind: str) -> str:
    """VM 上へ置く一時 .ps1 の絶対パス (health / exec 共用)。呼び出しごとに一意。

    固定名にすると、同じ VM へ並列に winvm を撃ったとき scp と実行の間に相手が同じ
    パスを上書きし、こちらのコマンドのつもりで相手のコマンドを実行して相手の結果を
    自分の結果として返す。エージェント駆動で同一 VM を並列に触る使い方が前提なので、
    起きうる競合ではなく起きる競合として扱う。

    予測可能な名前を全ユーザー書き込み可能な C:/Users/Public に置くと、scp と実行の
    間に差し替えられる窓も開く。一意名にすると両方まとめて閉じる。

    kind は後始末に失敗して残ったときにどのサブコマンドの残骸か読むためのもの。
    """
    return f"C:/Users/Public/winvm_{kind}_{uuid.uuid4().hex}.ps1"


# probe 失敗は pwsh 未導入とは限らず、SSH 未到達 (VM 未起動 / stale IP) でも起きる。
# 両方の可能性を示し、pwsh 未導入と断定して誤誘導しない (health / exec 共用)。
PWSH_PROBE_ERROR = (
    "error: VM 上で pwsh(7) を確認できませんでした "
    "(VM 未起動 / SSH 未到達、または pwsh 未導入の可能性)。"
    "winvm doctor --vm <id> --host <alias> で切り分け、"
    "pwsh が無ければ winget install --id Microsoft.PowerShell で導入する"
)


def cmd_health(args: argparse.Namespace, *, run=run_ssh, copy=scp) -> int:
    """.ps1 を scp して pwsh(7) の -File で実行、後始末。pwsh 必須 (不在ならエラー)。"""
    host = _env_or(args.host, "WINVM_HOST")
    repo = _env_or(args.repo, "WINVM_REPO")
    if not host:
        print("error: --host (または WINVM_HOST) が必要です", file=sys.stderr)
        return 2
    if not run(host, pwsh_probe_command()):
        print(PWSH_PROBE_ERROR, file=sys.stderr)
        return 1
    # `--check-tools "node, cargo"` のように空白を入れて書かれても拾えるようにする。
    # strip しないと PowerShell 側が " cargo" を探して導入済みのツールを未導入と誤報する。
    tools = args.check_tools.split(",") if args.check_tools else []
    ps = build_health_powershell([t.strip() for t in tools if t.strip()], repo)

    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8") as fh:
        fh.write(ps)
        local = fh.name
    remote = remote_ps1_path("health")
    try:
        if not copy(host, local, remote):
            print("error: health スクリプトの scp に失敗しました", file=sys.stderr)
            return 1
        try:
            return 0 if run(host, pwsh_file_command(remote)) else 1
        finally:
            run(host, pwsh_cleanup_command(remote))
    finally:
        Path(local).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# push / pull (SSH ベース。任意ファイル 1 個の転送 + サイズ照合)
# ---------------------------------------------------------------------------

# リモートファイル不在を表す ASCII の目印。localized な cmd 出力を判定に混ぜない。
REMOTE_MISSING_MARK = "WINVM_MISSING"


def remote_size_command(remote_win: str) -> str:
    """リモートファイルのサイズをバイト数で echo する cmd.exe コマンド。

    素の `for %I ... do @echo %~zI` は対象が無いと `%~zI` が空展開されて裸の `echo`
    になり、cmd が「ECHO は <ON> です。」を CP932 で返す (実測)。数字でもエラーでも
    ない localized な出力を判定に混ぜないため、if exist で分岐して不在は ASCII の
    目印に固定する。
    """
    return (
        f'if exist "{remote_win}" (for %I in ("{remote_win}") do @echo %~zI) '
        f"else (echo {REMOTE_MISSING_MARK})"
    )


def parse_remote_size(output: str) -> int | None:
    """remote_size_command の出力をバイト数にする。数字以外は等しく None。

    不在の目印も想定外の localized 出力も「サイズは得られなかった」に倒す。
    呼び出し側は None を照合失敗として扱う。
    """
    t = output.strip()
    return int(t) if t.isdigit() else None


def remote_parent_mkdir_command(remote_posix: str) -> str | None:
    """remote パス (/ 区切り) の親ディレクトリを作る cmd コマンド。不要なら None。

    ドライブ直下 (`C:/x`) の親 `C:` には発行しない。cmd の mkdir はドライブ名だけを
    渡すと失敗するし、そもそも作る必要が無い。
    """
    parent = str(PurePosixPath(remote_posix).parent)
    if parent in (".", "/") or re.fullmatch(r"[A-Za-z]:", parent):
        return None
    parent_win = to_windows_path(parent)
    return f'if not exist "{parent_win}" mkdir "{parent_win}"'


def _report_size_mismatch(expected: int, actual: int | None) -> None:
    actual_text = str(actual) if actual is not None else "取得できません"
    print(
        f"error: 転送後のサイズが一致しません (期待={expected} 実測={actual_text})。"
        "転送が途中で切れた可能性",
        file=sys.stderr,
    )


def cmd_push(args: argparse.Namespace, *, run=run_ssh, copy=scp, capture=ssh_capture) -> int:
    host = _env_or(args.host, "WINVM_HOST")
    if not host:
        print("error: --host (または WINVM_HOST) が必要です", file=sys.stderr)
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
    remote_size = parse_remote_size(capture(host, remote_size_command(to_windows_path(remote))))
    if remote_size != local_size:
        _report_size_mismatch(local_size, remote_size)
        return 1
    print(f"転送完了: {local} -> {host}:{remote} ({local_size} bytes)")
    return 0


def cmd_pull(args: argparse.Namespace, *, copy=scp_pull, capture=ssh_capture) -> int:
    host = _env_or(args.host, "WINVM_HOST")
    if not host:
        print("error: --host (または WINVM_HOST) が必要です", file=sys.stderr)
        return 2
    remote = args.remote
    local = Path(args.local)
    # 不在を後段のサイズ照合の失敗に化けさせず、転送前に明示して止める。
    remote_size = parse_remote_size(capture(host, remote_size_command(to_windows_path(remote))))
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
# exec (SSH ベース。任意 pwsh コマンドを .ps1 転送で実行)
# ---------------------------------------------------------------------------


def build_exec_powershell(command: str) -> str:
    """任意コマンドを包む PowerShell 本文。

    コマンドはシェルを経由せずファイルへ書かれるので、run の「argv を空白連結する
    のでクォートが落ちる」制約が無く、パイプも cmd.exe に解釈されない。

    末尾の exit 伝搬が要る: pwsh -File はスクリプトが exit しないと native コマンド
    の失敗を 0 に潰す。native の $LASTEXITCODE を優先しつつ、cmdlet の失敗
    ($? が偽で $LASTEXITCODE が無い/0 の場合) も非 0 へ倒す。
    """
    return (
        "\n".join(
            [
                "try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}",
                command,
                "$winvmOk = $?",
                "if (-not $winvmOk) { if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0)"
                " { exit $LASTEXITCODE } else { exit 1 } }",
                "if ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE }",
                "exit 0",
            ]
        )
        + "\n"
    )


def cmd_exec(args: argparse.Namespace, *, run=run_ssh_code, copy=scp) -> int:
    """コマンドを .ps1 に書いて scp し pwsh -File で実行、後始末 (health と同じ経路)。

    exit code はリモートの値をそのまま winvm の exit code にする (成否を握り潰さない)。
    """
    host = _env_or(args.host, "WINVM_HOST")
    if not host:
        print("error: --host (または WINVM_HOST) が必要です", file=sys.stderr)
        return 2
    command = remote_command_from_args(args.remote)
    if command is None:
        print("error: exec には -- の後に pwsh コマンドが必要です", file=sys.stderr)
        return 2
    if run(host, pwsh_probe_command()) != 0:
        print(PWSH_PROBE_ERROR, file=sys.stderr)
        return 1
    ps = build_exec_powershell(command)
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8") as fh:
        fh.write(ps)
        local = fh.name
    remote = remote_ps1_path("exec")
    try:
        if not copy(host, local, remote):
            print("error: exec スクリプトの scp に失敗しました", file=sys.stderr)
            return 1
        try:
            return run(host, pwsh_file_command(remote))
        finally:
            run(host, pwsh_cleanup_command(remote))
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

    cp = sub.add_parser("screenshot", help="prlctl でホスト側から VM 画面を PNG に撮る")
    cp.add_argument("--vm", help="VM 名または UUID (env: WINVM_VM)")
    cp.add_argument("--out", required=True, help="保存先 (macOS 側のパス)")
    cp.set_defaults(func=cmd_screenshot)

    pp = sub.add_parser("push", help="ローカルファイルを VM へ転送 (サイズ照合つき)")
    pp.add_argument("--host", help="ssh config エイリアス (env: WINVM_HOST)")
    pp.add_argument("local", help="ローカルのファイルパス")
    pp.add_argument("remote", help="VM 側の保存先ファイルパス (/ 区切り可。例 C:/Users/Public/app.msi)")
    pp.set_defaults(func=cmd_push)

    lp = sub.add_parser("pull", help="VM のファイルをローカルへ転送 (サイズ照合つき)")
    lp.add_argument("--host", help="ssh config エイリアス (env: WINVM_HOST)")
    lp.add_argument("remote", help="VM 側のファイルパス (/ 区切り可)")
    lp.add_argument("local", help="ローカルの保存先ファイルパス")
    lp.set_defaults(func=cmd_pull)

    ep = sub.add_parser("exec", help="任意の pwsh コマンドを .ps1 転送で実行 (クォート/パイプ安全)")
    ep.add_argument("--host", help="ssh config エイリアス (env: WINVM_HOST)")
    ep.add_argument("remote", nargs=argparse.REMAINDER, help="-- の後に pwsh コマンド")
    ep.set_defaults(func=cmd_exec)

    return p


def main(argv: list[str] | None = None) -> int:
    # 子プロセス (ssh/scp/prlctl) は端末へ直接書くのに対し、こちらの print は
    # 出力をファイルへリダイレクトするとブロックバッファされる。そのままだと
    # 進捗メッセージが子プロセスの出力より後ろにずれてログの因果が逆に読める。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(line_buffering=True)
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
