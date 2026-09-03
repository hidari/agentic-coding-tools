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
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

# 対話プロンプトで無期限にブロックしないための共通 ssh オプション。
# 診断だけに付けると「doctor はすぐ返るのに health は固まる」という逆転が起きるので、
# ssh を起動する経路すべてで同じものを使う。待ち時間の値はこの定数が canonical で、
# 散文に再掲しない (再掲した側だけが drift する)。
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]

# リモートファイル不在を表す ASCII の目印。locale で変わる stat のエラー文を判定に混ぜない。
REMOTE_MISSING_MARK = "MACVM_MISSING"

# GUI セッションが無いことを表す ASCII の目印。console の所有者は root か実ユーザーで、
# ログイン画面のままだと root のままになる。
NO_AQUA_MARK = "MACVM_NO_AQUA"

# GuestTools が読めなかったときの表示。prlctl が返しうる値 ("unknown" 等) と衝突しない
# 形にしてある。観測値の位置へ prlctl が返しうる文字列を置くと、実際にその値が返った場合と
# 区別できなくなるため。なお VM 状態 (State) の既定値は今もこの形になっていない
# (ISSUE-53 で扱う)。ここが全体の規約だと読まないこと。
UNKNOWN = "(未確認)"


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
    """UUID 比較用に中括弧と大小と前後空白を落とす。UUID かどうかは判定しない。

    名前の比較にこれを通してはいけない。大小無視の名前一致が意図せず入り、find_vm が
    宣言する「名前は完全一致」という契約が壊れる。
    """
    return value.strip().strip("{}").lower()


def find_vm(vms: list[dict], vm_id: str) -> dict | None:
    """名前 (完全一致) か UUID でレコードを引く。部分一致はしない。

    prlctl が受け付ける識別子と同じ集合に揃える。ここだけ部分一致を許すと、macvm と prlctl を
    混ぜて使ったときに指す VM がずれる。

    名前の照合は生の識別子を先に、strip した識別子を後に見る。env ファイルやコピペ経由で
    前後に空白が混ざったとき strip 無しでは「一覧に目的の名前が出ているのに引けない」と
    いう読みにくい失敗になるが、strip だけにすると逆に、前後へ空白を持つ名前の VM を
    その名前どおりに渡しても引けなくなる。両方を順に見ればどちらも引ける。

    空の識別子はここで弾く。`_require` は空白だけの値を truthy として通すので、
    正規化後の空文字が ID キーを欠くレコードの "" と一致して無関係なレコードを返しうる。

    走査は名前を全件見てから UUID を全件見る 2 パスにする。1 パスだとリストの並び順が
    優先順位になり、ある VM の名前が別の VM の UUID と一致するとき返るレコードが
    並びで変わる。名前一致をリスト全体で優先する、と決めておく。
    """
    ident = (vm_id or "").strip()
    if not ident:
        return None
    for candidate in (vm_id, ident):
        for v in vms:
            if str(v.get("Name", "")) == candidate:
                return v
    want = _normalize_uuid(ident)
    if not want:
        return None
    for v in vms:
        if _normalize_uuid(str(v.get("ID", ""))) == want:
            return v
    return None


def pick_ipv4(network: object) -> str | None:
    """Network レコードから最初の IPv4 を取る。無ければ None。

    エントリは `{"type": "ipv4"|"ipv6", "ip": "..."}` で、停止中の VM は空リストになる。
    選別は `type` で行う。そのうえで IPv4 として妥当かも確かめる。この戻り値は
    `resolve-ip` の stdout になり、ssh config の `ProxyCommand` を通って `nc` の接続先
    そのものになるので、値が壊れたエントリを素通しさせない。壊れた値を通すと、
    doctor 側の `is_apipa` も「APIPA ではない = 正常」へ倒れて緑を報告する。

    dict でない入力は例外にせず None へ倒す (parse_vm_list と同じ方針)。
    """
    return scan_ipv4(network)[0]


def scan_ipv4(network: object) -> tuple[str | None, list[str]]:
    """(最初の妥当な IPv4, 妥当でなかった ipv4 値のリスト) を返す。

    `pick_ipv4` が None を返す理由は「ipv4 エントリが無い」と「あるが全部壊れている」の
    2 つあり、対処が違う。前者は起動直後なら待てば付くが、後者は待っても変わらない。
    2 つを同じ「未割当」へ畳むと、診断が「数秒待って再実行」と案内し続けて利用者が
    無限に待つ。壊れた値そのものを観測値に出さないと手掛かりがどこにも残らない。
    """
    if not isinstance(network, dict):
        return None, []
    entries = network.get("ipAddresses")
    if not isinstance(entries, list):
        return None, []
    malformed: list[str] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        if str(e.get("type", "")) != "ipv4":
            continue
        ip = str(e.get("ip", "")).strip()
        try:
            ipaddress.IPv4Address(ip)
        except ValueError:
            malformed.append(ip)
            continue
        return ip, malformed
    return None, malformed


def is_apipa(ip: str) -> bool:
    """169.254.0.0/16 かどうか。DHCP が取れていない状態の目印になる。

    `pick_ipv4` を通った値は妥当性が済んでいるので通常ここで例外は出ない。`except` は
    その前提が崩れたときのための第二層で、doctor の 1 項目を落とすために全体を
    traceback で終わらせないためのもの。生産側の検証を消してここだけに頼らないこと。
    """
    try:
        return ipaddress.ip_address(ip) in ipaddress.ip_network("169.254.0.0/16")
    except ValueError:
        return False


def parse_tools(vm: dict) -> tuple[str | None, str | None]:
    """レコードの `GuestTools` から (state, version) を返す。読めなければ (None, None)。

    未導入の VM は `{"state": "not_installed"}` で version キーごと欠ける (実測)。
    「読めなかった」を "unknown" のような文字列へ畳まない。畳むと判定側が
    `== "installed"` で必ず False になり、確認できなかっただけの項目が FAIL になる。
    """
    tools = vm.get("GuestTools")
    if not isinstance(tools, dict):
        return None, None
    state = tools.get("state")
    version = tools.get("version")
    # 空文字も「読めなかった」へ倒す。畳まないと [FAIL] と観測値 (未確認) が同時に出て、
    # exit 1 の根拠が出力から辿れなくなる。
    return (
        state if isinstance(state, str) and state else None,
        version if isinstance(version, str) and version else None,
    )


# ---------------------------------------------------------------------------
# ゲスト内で実行する sh コマンドの組み立て
# ---------------------------------------------------------------------------


def remote_size_command(remote: str) -> str:
    """リモートファイルのサイズをバイト数で出す sh コマンド。

    不在は ASCII の目印に固定する。`stat` が返すエラー文は locale で変わるので、数字でも
    目印でもない出力を判定に混ぜないため、先に `-f` で分岐する。

    `stat` には `-L` が要る。`[ -f ]` も scp も最終要素の symlink を辿って実体を見るのに、
    `stat -f %z` だけは辿らずリンク自身 (= リンク先のパス文字列長) を返す。3 者の意味論が
    揃っていないと、転送が正しく終わっているのにサイズ照合が外れて「転送が途中で切れた」
    と誤報する。macOS ゲストでは Homebrew の bin がほぼ全て symlink なので現実に踏む。
    """
    q = shlex.quote(remote)
    return f"if [ -f {q} ]; then stat -L -f %z {q}; else echo {REMOTE_MISSING_MARK}; fi"


def parse_remote_size(output: str) -> int | None:
    """remote_size_command の出力をバイト数にする。数字以外は等しく None。

    不在の目印も想定外の出力も「サイズは得られなかった」に倒す。呼び出し側は None を
    照合失敗として扱う。
    """
    t = output.strip()
    return int(t) if t.isdigit() else None


def remote_parent_mkdir_command(remote: str) -> str | None:
    """remote パスの親ディレクトリを作る sh コマンド。不要なら None。

    親が `.` や `/` のときは発行しない。どちらも既に在るので作る必要が無い。`mkdir -p` は
    既存ディレクトリを成功として扱うので発行しても壊れないが、ssh の往復が 1 回無駄に増える。
    """
    parent = str(PurePosixPath(remote).parent)
    if parent in (".", "/"):
        return None
    return f"mkdir -p {shlex.quote(parent)}"


def reject_tilde_path(path: str, label: str) -> str | None:
    """先頭が `~` のゲスト側パスを拒む理由を返す。問題なければ None。

    `shlex.quote` はチルダ展開を殺すので、macvm が組み立てる sh コマンドはリテラルな `~`
    という名前のディレクトリを見る。ゲスト側のシェルが展開する経路 (scp の `host:~/...`)
    と食い違い、同じ引数が 2 つの別の場所を指す。

    展開をこちら側で再現するのはシェルの語彙の再実装になるので、境界で弾く。ssh の作業
    ディレクトリは `$HOME` なので、相対パスが等価な書き方として残り表現力は落ちない。
    展開されるのは先頭の `~` だけなので、途中に現れる `~` は正当なパスとして通す。

    この関数は転送しない経路 (`--repo`) からも呼ばれるので、理由に scp を持ち出さない。
    """
    if not path.startswith("~"):
        return None
    return (
        f"{label} に ~ は使えません: {path}。"
        "クォートするとゲスト側で展開されないので、macvm が組み立てるコマンドは"
        "リテラルな ~ ディレクトリを指す。"
        "$HOME 基準の相対パス (例 w/a.dmg) か絶対パスで指定する"
    )


def reject_directoryish_remote(path: str, label: str) -> str | None:
    """転送先/元がファイルを名指ししていない書き方を拒む理由を返す。問題なければ None。

    push では scp がディレクトリ宛の転送をその中へ置く (`host:w/` へ a.dmg を送ると
    `w/a.dmg`)。空文字なら `$HOME` 直下。一方サイズ照合は渡された文字列をそのまま `[ -f ]` に
    掛けるので偽になり、転送が完全に終わっていても「転送が途中で切れた可能性」で exit 1 に
    なる。pull では scp がディレクトリを単一ファイルとして扱えないので、そもそも転送が
    成立しない。どちらも `~` と同じ「同じ引数を scp と sh が別のものとして読む」欠陥クラス。

    ここで塞げるのは書き方から判る形 (空・末尾 `/`) だけ。ゲスト側に同名のディレクトリが
    既に在る場合は、問い合わせないと区別できないので通ってしまう (ISSUE-53)。
    """
    if path and not path.endswith("/"):
        return None
    return (
        f"{label} はファイル名まで書いてください: {path or '(空)'}。"
        "ディレクトリを渡すと scp とサイズ照合が同じものを見ません "
        "(push は scp がディレクトリの中へ置くのに照合はディレクトリ自身を見る、"
        "pull は scp が単一ファイルとして扱えない)"
    )


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
    # 値をシェル変数へ束縛してから参照する。sh の二重引用符の内側では $() と
    # バッククォートが展開されるので、`echo "tool_{t}=..."` のようにラベルへ生で埋めると
    # shlex.quote を通した値の位置だけが守られ、ラベルの位置でゲスト上のコマンド置換が
    # 走る。アポストロフィを含む合法なパス (/Users/example/Ken's repo) では構文エラーになり、
    # exit $fail に到達しないまま「VM が不健全」に見える。変数参照なら、展開結果が
    # 再度展開されることはないので値は「データ」の位置に留まる。
    for t in tools:
        lines.append(f"tool={shlex.quote(t)}")
        lines.append(
            'if path=$(command -v "$tool" 2>/dev/null); then echo "tool_$tool=$path"; '
            'else echo "tool_$tool=MISSING"; fail=1; fi'
        )
    if repo:
        lines.append(f"repo_path={shlex.quote(repo)}")
        lines.append(
            'if [ -d "$repo_path" ]; then echo "repo=$repo_path"; '
            'else echo "repo=MISSING"; fail=1; fi'
        )
    lines.append("exit $fail")
    return "\n".join(lines) + "\n"


def remote_script_path(kind: str) -> str:
    """VM 上に置く一時スクリプトのパス。呼び出しごとに一意。

    固定名にすると、同じ VM へ並列に macvm を撃ったとき scp と実行の間に相手が同じパスを
    上書きし、こちらのコマンドのつもりで相手のコマンドを実行して相手の結果を自分の結果と
    して返す。エージェント駆動で同一 VM を並列に触る使い方が前提なので、起きうる競合では
    なく起きる競合として扱う。一方の後始末の `rm -f` が他方の `sh` を追い越して、
    無関係な rc 127 で落ちる形もある。

    予測可能な名前を全ユーザー書き込み可能な `/tmp` (実測で 1777) に置くと、scp と実行の
    間に差し替えられる窓も開く。一意名にすると両方まとめて閉じる。

    kind は後始末に失敗して残ったときに、どのサブコマンドの残骸か読むためのもの。
    """
    return f"/tmp/macvm-{kind}-{uuid.uuid4().hex}.sh"


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


def ssh_capture_full(host: str, remote: str) -> tuple[int, str, str]:
    """doctor 用。rc と stderr も返す。

    stdout だけを見ると「ssh が落ちた」と「観測できたが空だった」が同じ値になる。観測対象が
    無セッションでも固有の目印を返す設計のとき、空を目印と同じ側へ畳むと失敗を断定へ化かす。
    """
    return run_capture(["ssh", *SSH_OPTS, host, remote])


def ssh_capture(host: str, remote: str) -> str:
    return ssh_capture_full(host, remote)[1]


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


def _refuse(reason: str | None) -> bool:
    """拒む理由があれば stderr へ出して True。呼び出し側は exit 2 で止める。"""
    if reason is None:
        return False
    print(f"error: {reason}", file=sys.stderr)
    return True


def _triage(host: str) -> str:
    """接続が疑わしいときの切り分け先。

    scp は `-q` 付きで、これは ssh(1) の warning と diagnostic も止める。閉じたポートへの
    接続で出るはずの "Connection refused" が消えるので、scp の失敗は原因がほぼ残らない
    形で返る。ssh 側も捕捉した経路では stderr を握っているだけで人には見えない。宛先を
    実値で名指しして、次に叩くコマンドを出力に残す。
    """
    return f"macvm doctor --vm <名前 or UUID> --host {host} で切り分ける"


def _remote_size_via_ssh(host: str, remote: str, capture) -> tuple[int | None, str | None]:
    """(サイズ, ssh 自体が失敗した理由) を返す。両方 None なら「問い合わせは成功したが
    ファイルが無い」。

    rc を捨てると「ssh が落ちた」と「ファイルが無い」が同じ None になる。push は前者を
    「転送が途中で切れた」、pull は「不在の可能性」と断定し、どちらも切り分け先を VM の
    中へ逸らす。実際には scp が完全なファイルを届けた後で ssh だけが落ちた場合がある
    (VM のスリープ、sshd の再起動、接続のタイムアウト)。
    """
    rc, out, err = capture(host, remote_size_command(remote))
    if rc != 0:
        return None, err.strip() or out.strip() or f"rc={rc}"
    return parse_remote_size(out), None


def _enable_line_buffering(*streams) -> None:
    """print をブロックバッファから行バッファへ寄せる。

    子プロセス (ssh/scp/prlctl) は端末へ直接書くのに対し、こちらの print は出力をファイルへ
    リダイレクトするとブロックバッファされ、進捗メッセージが子プロセスの出力より後ろへ
    ずれてログの因果が逆に読める。

    現状の macvm では逆転しない。stdout 向けの print はいずれも直後が return で、子プロセスを
    流す呼び出しの「あいだ」に挟まるものが 1 つも無い。これは予防で、stdout へ進捗出力を
    足した人が気づかないまま踏むのを防ぐためのもの。
    """
    for stream in streams:
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(line_buffering=True)


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
    ip, malformed = scan_ipv4(vm.get("Network"))
    if ip is None:
        detail = (
            f"prlctl が IPv4 として読めない値を返している: "
            f"{', '.join(repr(x) for x in malformed)}"
            if malformed
            else f'VM が起動していない場合は prlctl start "{name}" で起動する'
        )
        print(f"error: IP を解決できません (status={status})。{detail}", file=sys.stderr)
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
    console_probe=ssh_capture_full,
) -> list[Check]:
    """VM が使える状態かをホスト側から観測する。各項目は観測値を持つ。"""
    vms, err = _load_vms(run)
    if err:
        return [
            Check(
                "prlctl",
                err,
                ok=False,
                # run_capture は OSError も戻り値へ倒すので、PATH に無い経路が実在する。
                hint="Parallels Desktop が起動しているか、prlctl が PATH にあるか確認する",
            )
        ]

    vm = find_vm(vms, vm_id)
    if vm is None:
        return [
            Check(
                "VM 登録",
                f"{vm_id} は見つからない / 登録済み: {_known_names(vms)}",
                ok=False,
                # 名前は完全一致だが UUID でも引ける。片側だけ書くと引き方を狭く伝える。
                hint="prlctl list -a で名前か UUID を確認する (名前は完全一致)",
            )
        ]

    name = str(vm.get("Name", vm_id))
    state = str(vm.get("State", "unknown"))
    checks = [
        # UUID も出す。名前と UUID の両方で引ける以上、どのレコードを掴んだかを
        # 出力から確認できないと、取り違えたときに気づく手段が無い。
        Check("VM 登録", f"{name} ({vm.get('ID', '')})", ok=True),
        Check(
            "VM 状態",
            state,
            ok=(state == "running"),
            hint=f'prlctl start "{name}" で起動する',
        ),
    ]

    tools_state, tools_version = parse_tools(vm)
    checks.append(
        Check(
            "Parallels Tools",
            " ".join(x for x in (tools_state, tools_version) if x) or UNKNOWN,
            # 「確認できなかった」を FAIL へ潰さない。文字列へ畳んでから "installed" と
            # 比べると、GuestTools が読めないだけで正常な VM が exit 1 になる。
            ok=None if tools_state is None else tools_state == "installed",
            # 矢印の先には行動が来る前提の書式なので、影響の説明ではなく手順を書く。
            hint="VM のメニューから Parallels Tools をインストールする (references/troubleshooting.md)",
        )
    )

    ip, malformed = scan_ipv4(vm.get("Network"))
    if ip is None:
        # 「エントリが無い」と「あるが全部壊れている」を同じ「未割当」へ畳まない。
        # 後者に「数秒待って再実行」と案内すると、待っても変わらないので無限に待つ。
        checks.append(
            Check(
                "IP",
                f"ipv4 として読めない値のみ: {', '.join(repr(x) for x in malformed)}"
                if malformed
                else "未割当",
                ok=False,
                hint=(
                    "prlctl が IPv4 として妥当でない値を返している。VM を再起動しても"
                    "変わらないので、prlctl list -a -i -j の出力を直接確認する"
                    if malformed
                    else "VM が起動直後だと未割当のことがある。数秒待って再実行する"
                ),
            )
        )
        # SSH は撃たない。ProxyCommand が resolve-ip に依存しているので、IP が無ければ
        # 必ず落ちる。ただし省いたことは行として残す。行ごと消すと --host を渡した場合と
        # 渡さない場合で report が完全に同一になり、同じ「見なかった」が 2 通りの
        # 表現になる (--host 未指定の側は [ -- ] 行を残している)。
        checks.append(Check("SSH", "IP 未割当のため未確認", ok=None, hint=None))
        return checks
    checks.append(
        Check(
            "IP",
            ip,
            ok=(not is_apipa(ip)),
            # 疑い先はホスト側が先。VM 側のネットワーク設定だけを挙げると、共有ネットワークが
            # 止まっているときに VM の中を探して時間を使う。
            hint=(
                "APIPA は DHCP が応答していない。Parallels の共有ネットワークが動いているか、"
                "次に VM のネットワーク設定を見る (references/troubleshooting.md の APIPA の節)"
            ),
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

    rc, out, err = console_probe(host, console_owner_command())
    owner = out.strip()
    if rc != 0 or not owner:
        # 空を「Aqua セッション無し」へ畳まない。無セッションの信号は NO_AQUA_MARK で
        # あって空ではないので、空は「その信号ではない何かが起きた」を意味する。
        checks.append(
            Check("GUI セッション", err.strip() or owner or f"rc={rc}", ok=None, hint=None)
        )
        return checks
    has_aqua = owner != NO_AQUA_MARK
    checks.append(
        Check(
            "GUI セッション",
            owner if has_aqua else "ログイン画面 (Aqua セッション無し)",
            ok=has_aqua,
            hint="GUI アプリを起動しても画面に出ない。自動ログインを設定する (references/macos-bootstrap.md)",
        )
    )
    return checks


def cmd_doctor(
    args: argparse.Namespace,
    *,
    run=run_capture,
    ssh_probe=_ssh_reachable,
    console_probe=ssh_capture_full,
) -> int:
    # seam は 3 つとも転送する。1 つ落とすと、見えている seam を全部塞いだつもりの
    # テストから実 ssh が飛び、SSH_OPTS の ConnectTimeout ぶんテストが沈黙する。
    vm_id = _env_or(args.vm, "MACVM_VM")
    if not _require(vm_id, "--vm", "MACVM_VM"):
        return 2
    host = _env_or(args.host, "MACVM_HOST")
    checks = collect_doctor_checks(
        vm_id, host, run=run, ssh_probe=ssh_probe, console_probe=console_probe
    )
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


def cmd_push(
    args: argparse.Namespace, *, run=run_ssh, copy=scp, capture=ssh_capture_full
) -> int:
    host = _env_or(args.host, "MACVM_HOST")
    if not _require(host, "--host", "MACVM_HOST"):
        return 2
    remote = args.remote
    if _refuse(reject_tilde_path(remote, "remote")) or _refuse(
        reject_directoryish_remote(remote, "remote")
    ):
        return 2
    local = Path(args.local)
    if not local.is_file():
        print(f"error: ローカルファイルがありません: {local}", file=sys.stderr)
        return 1
    mk = remote_parent_mkdir_command(remote)
    if mk is not None and not run(host, mk):
        print("error: VM のディレクトリ作成に失敗しました", file=sys.stderr)
        return 1
    if not copy(host, str(local), remote):
        print(f"error: scp 失敗: {local} (VM 未起動 / SSH 未到達の可能性)。{_triage(host)}",
              file=sys.stderr)
        return 1
    # scp の rc 0 を「完了」と読み替えない。実体のサイズで途中切れを検出する。
    local_size = local.stat().st_size
    remote_size, ssh_error = _remote_size_via_ssh(host, remote, capture)
    if ssh_error is not None:
        print(
            f"error: 転送後のサイズ照合ができませんでした (ssh の失敗: {ssh_error})。"
            f"ファイルは届いている可能性がある。{_triage(host)}",
            file=sys.stderr,
        )
        return 1
    if remote_size != local_size:
        _report_size_mismatch(local_size, remote_size)
        return 1
    print(f"転送完了: {local} -> {host}:{remote} ({local_size} bytes)")
    return 0


def cmd_pull(args: argparse.Namespace, *, copy=scp_pull, capture=ssh_capture_full) -> int:
    host = _env_or(args.host, "MACVM_HOST")
    if not _require(host, "--host", "MACVM_HOST"):
        return 2
    remote = args.remote
    if _refuse(reject_tilde_path(remote, "remote")) or _refuse(
        reject_directoryish_remote(remote, "remote")
    ):
        return 2
    local = Path(args.local)
    # 不在を後段のサイズ照合の失敗に化けさせず、転送前に明示して止める。
    remote_size, ssh_error = _remote_size_via_ssh(host, remote, capture)
    if ssh_error is not None:
        print(
            f"error: リモートのサイズを問い合わせる ssh が失敗しました: {ssh_error}。"
            f"ファイルの不在とは限らない。{_triage(host)}",
            file=sys.stderr,
        )
        return 1
    if remote_size is None:
        print(
            f"error: リモートファイルのサイズを取得できません (不在の可能性): {host}:{remote}",
            file=sys.stderr,
        )
        return 1
    local.parent.mkdir(parents=True, exist_ok=True)
    if not copy(host, remote, str(local)):
        print(f"error: scp 失敗: {remote} (VM 未起動 / SSH 未到達の可能性)。{_triage(host)}",
              file=sys.stderr)
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
            # 非 0 で終わったスクリプト自身の失敗 (下の run) には付けない。利用者の
            # コマンドが失敗しただけのときに接続の切り分けを勧めると誤誘導になる。
            print(
                f"error: {kind} スクリプトの scp に失敗しました (VM 未起動 / SSH 未到達の可能性)。"
                f"{_triage(host)}",
                file=sys.stderr,
            )
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
    if repo and _refuse(reject_tilde_path(repo, "--repo")):
        return 2
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
    # ホストもゲストも macOS で /Users/<name>/... が同形なので、どちら側かを明示する。
    # ゲスト内のつもりで渡すとホスト側にディレクトリを黙って作る。
    cp.add_argument("--out", required=True, help="保存先パス (ホスト側)")
    cp.set_defaults(func=cmd_screenshot)

    pp = sub.add_parser("push", help="ローカルファイルを VM へ転送 (サイズ照合つき)")
    pp.add_argument("--host", help="SSH ホスト (env: MACVM_HOST)")
    pp.add_argument("local", help="ローカルのファイルパス")
    pp.add_argument("remote", help="VM 側の保存先パス (~ 不可。$HOME 基準の相対パスで書く)")
    pp.set_defaults(func=cmd_push)

    lp = sub.add_parser("pull", help="VM のファイルをローカルへ転送 (サイズ照合つき)")
    lp.add_argument("--host", help="SSH ホスト (env: MACVM_HOST)")
    lp.add_argument("remote", help="VM 側のファイルパス (~ 不可。$HOME 基準の相対パスで書く)")
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
    _enable_line_buffering(sys.stdout, sys.stderr)
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
