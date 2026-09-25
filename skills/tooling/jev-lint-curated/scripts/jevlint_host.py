"""jev-lint 厳選ラッパの env、host ディレクトリ、node の事前検査、上流の起動 argv。

`jevlint.py` (入口) の下流モジュールの 1 つ。上流 (jev-lint) は消費側のリポジトリの外にある
ラッパ所有の host ディレクトリへ、キー無しの env で取得する (`prepare_host`)。上流の起動その
ものの env は `build_env` が組み立て、上流に渡してよい変数だけを allowlist 形式で写す
(spec の「設定と、起動の引数と環境変数」節)。`install_env` は取得のときだけに使う別の
組み立てで、pnpm と `@ast-grep/cli` の postinstall にキーと上流固有の変数を見せないための
落とし方 (denylist 形式) を取る。allowlist と denylist で方向が違う理由は、上流の起動は
「入れてよいものだけ許す」対象が小さく、取得は「消費側や利用者の環境をほぼそのまま渡しつつ
危険な一部だけ落とす」対象が大きいため。

このモジュールは他の jevlint* モジュールを import しない。依存は入口 (`jevlint.py`) から
下流へ一方向に流し、循環を作らないため。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Callable, Mapping


class HostError(Exception):
    """env・host の用意・node の事前検査・argv の組み立ての失敗。終了コード 2 に落とす。"""


# 上流の起動の env に写してよい変数名 (完全一致)。この集合の外にあるものは、
# 消費側や利用者の環境に何が入っていても上流には見せない (allowlist)。
_ALLOWED_ENV_NAMES = ("PATH", "HOME", "TMPDIR", "LANG")

# ラッパ自身の git 呼び出しと上流の起動の両方の env に、source の値によらず固定で足す値。
# Task 3 で、上流が worktree の中で自分で呼ぶ `git diff <base>...HEAD` が partial clone の
# 上で lazy fetch (利用者の remote.origin.uploadpack のコマンドを cwd=root で起動し、
# worktree にファイルを書く) と `refs/replace` の差し替え追従の両方を実際に起こすことを
# 測定した。この 2 つを "1" にすると rc 128 で何も書かずに fail closed する (実測)。
# `git help git` (このマシンの git 2.55.0) の該当箇所:
#   --no-replace-objects : "equivalent to exporting the GIT_NO_REPLACE_OBJECTS environment
#                            variable with any value"
#   --no-lazy-fetch       : "equivalent to setting the GIT_NO_LAZY_FETCH environment variable
#                            to 1"
# どちらも documented な環境変数なので、値は "1" に統一する (GIT_NO_REPLACE_OBJECTS は
# "any value" と書かれているが "1" もその集合に含まれる)。source から写すのではなくここで
# 固定するのは、利用者の env にこの 2 つが偶然 "0" や空で入っていて安全策が無効化される
# 経路を作らないため。
_FIXED_GIT_SAFETY_ENV = {
    "GIT_NO_LAZY_FETCH": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
}

# 取得 (`pnpm add`) の env から落とす prefix。上流本体とその postinstall にキーと
# jev-lint 固有の設定を渡さないための denylist。前提 13 の列挙 (`JEV_LINT_MODEL` 等) は
# すべて `JEV_LINT_` で始まるのでこの 1 本の prefix で足りる。
_INSTALL_DROPPED_PREFIXES = ("TYPESAFE_", "TYPESAFEAI_", "JEV_LINT_")


def _sanitize_path(value: str) -> str:
    """`PATH` の各成分から、絶対パスでない・空の成分を落とす。

    相対な成分 (`node_modules/.bin`、`.`、先頭や途中の空成分など) を含む `PATH` で
    bare なコマンド名 (`git`、`pnpm`) を起動すると、worktree や消費側のリポジトリの
    中にある実行ファイルが先に解決されうる。`jevlint_tree.py` の展開は mode 100755 の
    blob を実行ビット付きで書き出すので、コミットされた実行ファイルは worktree の中で
    「実行できる」状態になる。node 24.18.0 の `spawnSync("git", {cwd: worktree})` で
    実測 (fix round 1 のレビュー): `PATH=node_modules/.bin:/usr/bin:/bin` は
    `node_modules/.bin/git` を、`PATH=:/usr/bin:/bin` (先頭が空成分) は `./git` を、
    `PATH=/usr/bin:/bin` (絶対パスのみ) は `/usr/bin/git` を解決した。`build_env`
    (上流の起動) と `install_env` (pnpm の取得) の両方がこの関数を通す。
    """
    parts = [part for part in value.split(":") if part and part.startswith("/")]
    return ":".join(parts)


def build_env(source: Mapping[str, str], with_key: bool) -> dict:
    """上流の起動 (と、ラッパ自身の git 呼び出し) に渡す env を組み立てる。

    `source` から `_ALLOWED_ENV_NAMES` と `LC_` で始まる変数だけを写す allowlist。
    `NODE_OPTIONS`、proxy 系、`NODE_TLS_*`、`GIT_*` (下の固定の 2 つを除く)、
    `JEV_LINT_*`、`TYPESAFEAI_*` はこの組み立てには存在しないので入らない。
    `with_key` が真のときだけ `TYPESAFE_API_KEY` を足す (`--dry-run` や事前検査など
    キーが要らない起動では呼び出し側が False を渡す)。`PATH` は `_sanitize_path` で
    相対・空の成分を落とす。

    `LC_ALL` を自分では足さない。上流の env は利用者の locale をそのまま保つ設計で
    (`jevlint_tree.py` の `_git_env` が行う `LC_ALL=C` の固定はラッパ自身の git 呼び出し
    専用であり、ここには適用しない)。`source` に `LC_ALL` があれば `LC_` prefix として
    素通しになるが、それは利用者自身の設定であって、ここが注入したものではない。
    """
    env: dict = {}
    for name in _ALLOWED_ENV_NAMES:
        if name in source:
            env[name] = _sanitize_path(source[name]) if name == "PATH" else source[name]
    for name, value in source.items():
        if name.startswith("LC_"):
            env[name] = value
    if with_key and "TYPESAFE_API_KEY" in source:
        env["TYPESAFE_API_KEY"] = source["TYPESAFE_API_KEY"]
    env.update(_FIXED_GIT_SAFETY_ENV)
    return env


def _is_npm_or_pnpm_config(name: str) -> bool:
    """`npm_config_*` / `pnpm_config_*` (大文字小文字を問わない) か。

    `npm_config_*` は npm/pnpm が子プロセスへ渡す慣例の小文字形、`PNPM_CONFIG_*` は
    利用者や CI 設定が書く大文字形。fix round 1 で実測: pnpm 12.3.4 は `pnpm add`
    自身が `PNPM_CONFIG_REGISTRY` を読み、到達不能な registry を指すとそこへ fetch
    しようとして失敗する (`pnpm config get registry` だけの話ではない)。
    """
    lowered = name.lower()
    return lowered.startswith("npm_config_") or lowered.startswith("pnpm_config_")


def install_env(source: Mapping[str, str]) -> dict:
    """`pnpm add` (host の取得) に渡す env。`source` の写しから危険な変数だけを落とす。

    `build_env` とは逆に denylist で組み立てる。取得は利用者の PATH やネットワーク周りの
    設定 (proxy 等) をほぼそのまま必要とする一方、キー (`TYPESAFE_API_KEY` /
    `TYPESAFEAI_API_KEY`)、上流固有の変数 (`TYPESAFE_BASE_URL` 等)、そして
    `npm_config_*` / `PNPM_CONFIG_*` を落とす。最後のものを落とす理由: pnpm は
    `pnpm_config_registry` / `PNPM_CONFIG_REGISTRY` を `pnpm add` の registry 解決に
    読む (実測)。プロジェクトの Claude Code 設定でコミットされた `env` がこれを注入
    すると、キー無しで取得したはずの host が実は偽の registry から取得したものになり、
    次のキー付きの起動でその host (別物の jev-lint) が使われる。利用者自身の
    `~/.npmrc` はファイルであってこの denylist の対象ではないので、そのまま効く。
    `PATH` は `_sanitize_path` で相対・空の成分を落とす (`build_env` と共通の理由)。
    """
    env = {
        name: value
        for name, value in source.items()
        if not name.startswith(_INSTALL_DROPPED_PREFIXES) and not _is_npm_or_pnpm_config(name)
    }
    if "PATH" in env:
        env["PATH"] = _sanitize_path(env["PATH"])
    return env


def key_status(source: Mapping[str, str]) -> str:
    """`TYPESAFE_API_KEY` (前後の空白を落として非空なら "ok") の有無を判定する。

    無ければ `TYPESAFEAI_API_KEY` (同じく前後の空白を落として非空) だけを見て
    `"legacy-only"` を返す。どちらも無いか空白のみなら `"missing"`。キーの値そのものは
    返り値に含めない (呼び出し側が誤ってログへ出す経路を作らないため)。
    """
    if source.get("TYPESAFE_API_KEY", "").strip():
        return "ok"
    if source.get("TYPESAFEAI_API_KEY", "").strip():
        return "legacy-only"
    return "missing"


def host_dir(version: str, source: Mapping[str, str]) -> Path:
    """host の置き場を決める。`XDG_CACHE_HOME` を優先し、無ければ `HOME/.cache`。

    `<repo>/.cache/` のようなリポジトリの中には置かない (spec の「host ディレクトリ」節)。
    ここではパスを計算するだけで、ディレクトリを作りも検査もしない (`prepare_host` の責務)。

    `XDG_CACHE_HOME` が絶対パスでなければ無視して `HOME` へ読み替える (XDG Base
    Directory の仕様: 相対な値は invalid で無視しなければならない)。相対な値をそのまま
    使うと host が起動時の cwd の下、つまり消費側のリポジトリの中に置かれかねない
    (fix round 1 で実測: `XDG_CACHE_HOME=cache` かつ
    `cache/jev-lint-curated/<版>/node_modules/jev-lint/{package.json,dist/cli.js}` を
    あらかじめ用意しておくと、`prepare_host` は pnpm を走らせずそれを再利用し、次の
    キー付きの起動が消費側の `cli.js` を実行した)。`~/.cache` のような未展開の `~` も
    `Path.is_absolute()` では絶対パスと判定されないので、同じ理由で弾かれる。`HOME`
    自体が無いか絶対パスでなければ置き場を決められない。

    `version` は `jev-lint-curated/<version>` という 1 つのパス成分になる。空・`.`・
    `..`・区切り文字を含む値は拒否する (X.Y.Z の形式そのものは `jevlint.py` の
    `parse_version` の責務で、ここは経路の安全性だけを見る defense in depth)。
    """
    if not version or version in (".", "..") or "/" in version or "\\" in version:
        raise HostError(f"version は単一の安全なパス成分でなければならない: {version!r}")
    xdg = source.get("XDG_CACHE_HOME", "")
    if xdg and Path(xdg).is_absolute():
        base = Path(xdg)
    else:
        home = source.get("HOME", "")
        if not Path(home).is_absolute():
            raise HostError(
                "XDG_CACHE_HOME が絶対パスでなく、HOME も絶対パスでないので host の"
                f"置き場を決められない (XDG_CACHE_HOME={xdg!r}, HOME={home!r})"
            )
        base = Path(home) / ".cache"
    return base / "jev-lint-curated" / version


def host_reusable(host: Path, version: str) -> bool:
    """`host` を作り直さずに使えるか。`package.json` の `version` が一致し `cli.js` がある。

    `is_file()` は ENOENT 等を握りつぶして `False` を返すが、権限エラー
    (`PermissionError`) は Python 3.9 では再送出する (実測: 3.9.6。3.14.7 の pathlib は
    `PermissionError` も握りつぶして `False` を返すようになっている。実測、fix round 1)。
    再送出された場合は判定不能を「無い」に丸めず `HostError` にする。
    """
    package_json = host / "node_modules" / "jev-lint" / "package.json"
    cli_js = host / "node_modules" / "jev-lint" / "dist" / "cli.js"
    try:
        package_json_is_file = package_json.is_file()
        cli_js_is_file = cli_js.is_file()
    except PermissionError as error:
        raise HostError(f"host を確認できない (権限不足): {host}: {error}") from None
    if not package_json_is_file or not cli_js_is_file:
        return False
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("version") == version


def _reject_pnpm_workspace_ancestor(host: Path) -> None:
    """`host` の祖先ディレクトリに `pnpm-workspace.yaml` があれば拒否する。

    pnpm は cwd から親方向に `pnpm-workspace.yaml` を探してモノレポと判定し、workspace の
    root にある `.npmrc` を読む (spec 前提 14 の一般化)。`prepare_host` は host の親の
    中に作った一時ディレクトリを cwd にして `pnpm add` を呼ぶため、host の祖先にこの
    ファイルがあると同じ理由で意図しない registry に化ける経路が残る。macOS では
    `/tmp` が `/private/tmp` の symlink であるように (`jevlint_tree.py` の `_temp_base`
    と同じ実測)、与えられた表記だけでは祖先を見落とすことがあるため、解決した表記の
    祖先も合わせて見る。

    `resolve()` は symlink のループで Python 3.9 では `RuntimeError` になる (実測:
    3.9.6。`jevlint_tree.py` の `_temp_base` と同じ現象。3.14.7 は投げない)。`exists()`
    は権限エラーで 3.9 では `PermissionError` を再送出する (3.14.7 は握りつぶして
    `False` を返す。実測、fix round 1)。どちらも `HostError` に変える。
    """
    try:
        resolved_parents = set(host.resolve().parents)
    except (OSError, RuntimeError) as error:
        raise HostError(f"host の祖先を解決できない: {host}: {error}") from None
    ancestors = set(host.parents) | resolved_parents
    for ancestor in ancestors:
        candidate = ancestor / "pnpm-workspace.yaml"
        try:
            found = candidate.exists()
        except OSError as error:
            raise HostError(f"host の祖先を確認できない: {candidate}: {error}") from None
        if found:
            raise HostError(f"host の祖先に pnpm-workspace.yaml がある: {candidate}")


def _replace_host_atomically(host: Path, tmp: Path, version: str) -> None:
    """再利用可能な `tmp` を host の名前へ置く。複数プロセスが同時に取得しても壊れない。

    直接 `shutil.rmtree(host)` してから `rename` すると、`rmtree` は atomic でないため
    (a) 別プロセスが使用中の host を削除してしまう (b) 2 つの `rmtree` が競合して
    `FileNotFoundError` になる、の 2 通りの競合を生む。fix round 1 のレビューで実測:
    2 プロセスが同時に「再利用できない」と判定して `pnpm add` を走らせたとき、先に
    `tmp.replace(host)` が通った側が host を置いたあと、後から `tmp.replace(host)`
    を呼んだ側は host が既に非空のディレクトリになっていて `OSError` (`ENOTEMPTY`、
    macOS で errno 66) になる。

    手順:

    1. host を再確認する。既に他プロセスが同じ version を置いていれば (`host_reusable`
       が真) 何もせず勝者に譲る (呼び出し側が `tmp` を消す)
    2. 既存の host (別 version か壊れた取得物) があれば、同じ親の中で重複しない名前
       (`.old-<uuid>`) へ `rename` で「どかして」から `tmp.replace(host)` する。
       `rename` は同一ファイルシステム上で atomic なので、host の名前が「無い」中間
       状態を経由しない
    3. その置き換えが競合で失敗したら (2 の rename、3 の replace のどちらでも) host を
       再確認し、他プロセスが勝っていればそちらに譲り、そうでなければ `HostError`
    4. どかした古いディレクトリは最後に消す (使用中でも `ignore_errors` で無視する)

    呼び出されるのは `host_reusable(tmp, version)` が真であることを確認した後だけ。
    """
    if host_reusable(host, version):
        return
    old = None
    try:
        if host.exists():
            old = host.parent / f".old-{uuid.uuid4().hex}"
            host.rename(old)
        tmp.replace(host)
    except OSError as error:
        if host_reusable(host, version):
            return
        raise HostError(f"host の設置に失敗した: {error}") from None
    finally:
        if old is not None:
            shutil.rmtree(old, ignore_errors=True)


def _default_run(argv: list, cwd: str, env: Mapping[str, str]) -> "subprocess.CompletedProcess":
    """`prepare_host` の `run` の既定実装。pnpm の stdout をラッパ自身の stderr へ流す。

    ラッパの stdout は Task 6 で書く JSON の要約専用にするため、pnpm 自身の進捗表示
    (`Progress: resolved ...` 等) を混ぜない。stderr は継承したまま (pnpm 自身の
    エラーメッセージは利用者にそのまま見えたほうが診断しやすい)。
    """
    return subprocess.run(argv, cwd=cwd, env=env, stdout=sys.stderr)


def prepare_host(
    version: str, source: Mapping[str, str], run: Callable = _default_run
) -> Path:
    """host を再利用できればそれを返し、できなければ取得してから host の名前に置く。

    pnpm を worktree や消費側のリポジトリを cwd にして起動しない理由 (spec 前提 14。境界
    レーンがこのリポジトリの外で実測: node 24.18.0 / pnpm 12.3.4 / git 2.55.0): pnpm は
    cwd の `.npmrc` の `registry=` を読み、`dlx` のキャッシュが温まって
    いても読む (キャッシュのキーが registry を含むため)。環境変数 `npm_config_registry`
    はこの project の `.npmrc` に負ける。消費側がコミットした `.npmrc` のある worktree で
    `pnpm dlx` を起動すると、偽の registry が返す別物の jev-lint がキー付きの env で走る
    ことをダミーのキーで再現した。この関数は host の親の中に作った一時ディレクトリ
    (利用者の cache ディレクトリの下で、どの消費側のリポジトリにも属さない) を cwd に
    して `pnpm add` を 1 回だけ呼ぶ。

    一時ディレクトリを host と同じ親に作り、取得の成功を確認してから host の名前に
    置くのは、途中で切れた取得 (ネットワーク断・プロセス kill 等) が host として再利用
    されるのを防ぐため。host への設置自体の競合耐性は `_replace_host_atomically` が持つ。
    """
    host = host_dir(version, source)
    _reject_pnpm_workspace_ancestor(host)
    if host_reusable(host, version):
        return host

    parent = host.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise HostError(f"host の親ディレクトリを作れない: {parent}: {error}") from None
    try:
        tmp = Path(tempfile.mkdtemp(dir=str(parent), prefix=".tmp-"))
    except OSError as error:
        raise HostError(f"host の取得用の一時ディレクトリを作れない: {error}") from None

    try:
        try:
            (tmp / "package.json").write_text(
                json.dumps({"private": True}), encoding="utf-8"
            )
        except OSError as error:
            raise HostError(
                f"host の取得用の package.json を書けない: {tmp / 'package.json'}: {error}"
            ) from None
        argv = ["pnpm", "add", "--allow-build=@ast-grep/cli", f"jev-lint@{version}"]
        try:
            proc = run(argv, cwd=str(tmp), env=install_env(source))
        except OSError as error:
            # `pnpm` が PATH に無いときの `subprocess.run` は非 0 の returncode ではなく
            # OSError (実測: FileNotFoundError) を投げる。素の例外で抜けると Python は
            # 終了コード 1 で終わり、ラッパの「1 = finding あり」と衝突する
            # (`jevlint_tree.py` の `materialize` が書き出しの OSError を `TreeError` に
            # 変える理由と同じ)。ここで HostError に変えて fail closed にする
            raise HostError(f"pnpm を起動できない: {error}") from None
        if proc.returncode != 0:
            raise HostError(
                f"pnpm add jev-lint@{version} が失敗した (終了コード {proc.returncode})"
            )
        if not host_reusable(tmp, version):
            raise HostError(
                f"pnpm add jev-lint@{version} は成功したが取得物の形が想定と違う"
            )
        _replace_host_atomically(host, tmp, version)
    finally:
        # `_replace_host_atomically` が成功すると tmp はその名前ではもう存在しない
        # (host の名前へ rename 済み) ので、ここでの rmtree は無視されるだけの no-op に
        # なる。どの失敗経路でも tmp の残骸を必ず片付けるために分岐を作らず常に呼ぶ
        shutil.rmtree(tmp, ignore_errors=True)
    return host


_NODE_VERSION_RE = re.compile(r"v(\d+)\.(\d+)\.(\d+)")


def parse_node_version(text: str) -> tuple:
    """`node --version` の出力 (`v24.18.0\\n` の形) を `(major, minor, patch)` にする。"""
    match = _NODE_VERSION_RE.fullmatch(text.strip())
    if match is None:
        raise HostError(f"node --version の出力を解釈できない: {text!r}")
    major, minor, patch = match.groups()
    return (int(major), int(minor), int(patch))


# `engines.node` として受け付ける唯一の形。上流はこの値を宣言するだけで実行時には検査
# しない (spec の「host ディレクトリ」節) ので、ここで拒否したときは SKILL.md の手順へ
# 誘導する (呼び出し側の役目)。
_ENGINES_RE = re.compile(r"^>=\d+(\.\d+){0,2}$")


def engines_ok(engines: str, version: tuple) -> bool:
    """`engines` (`>=N`、`>=N.N`、`>=N.N.N` のいずれか) を `version` が満たすか。

    それ以外の形 (`^N`、範囲指定、`*` 等) は `HostError`。欠けた minor/patch は 0 として
    数値比較する (`>=24.1` は `(24, 1, 0)` 以上を要求する)。
    """
    if _ENGINES_RE.fullmatch(engines) is None:
        raise HostError(f"engines.node が '>=N' の形でない: {engines!r}")
    parts = [int(part) for part in engines[2:].split(".")]
    while len(parts) < 3:
        parts.append(0)
    required = (parts[0], parts[1], parts[2])
    return version >= required


def read_engines(host: Path) -> str:
    """`host` に取得済みの jev-lint の `package.json` から `engines.node` を読む。"""
    package_json = host / "node_modules" / "jev-lint" / "package.json"
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise HostError(f"jev-lint の package.json を読めない: {package_json}: {error}") from None
    engines = data.get("engines") if isinstance(data, dict) else None
    if not isinstance(engines, dict) or not isinstance(engines.get("node"), str):
        raise HostError(f"jev-lint の package.json に engines.node が無い: {package_json}")
    return engines["node"]


def fixed_tail(config: Path) -> list:
    """上流の起動 argv の末尾、固定部分 (11 要素)。

    フラグは後に書いたものが勝つ (spec 前提 5) ので、消費側の値 (`--base` / `--exclude` /
    path / `--dry-run` / `--record`) を上書きされないよう、固定する部分は必ず argv の
    最後に置く。
    """
    return [
        "--json",
        "--config",
        str(config),
        "--cache",
        "none",
        "--retry",
        "3",
        "--model",
        "jev-latest",
        "--base-url",
        "https://api.typesafe.ai",
    ]


def upstream_argv(
    node: str,
    host: Path,
    sub: str,
    *,
    base: "str | None",
    excludes: list,
    paths: list,
    dry_run: bool,
    record: "Path | None",
    config: Path,
) -> list:
    """上流を起動する argv を組み立てる。pnpm を含まず、node で `dist/cli.js` を直接呼ぶ。

    ast-grep はパッケージの位置から解決されるので (spec の「host ディレクトリ」節)、argv に
    ast-grep 自身の指定は要らない。並びは spec が定める仕様の形をそのまま実装する。
    """
    return [
        node,
        str(host / "node_modules/jev-lint/dist/cli.js"),
        sub,
        *(["--base", base] if base else []),
        *[x for e in excludes for x in ("--exclude", e)],
        *paths,
        *(["--dry-run"] if dry_run else []),
        *(["--record", str(record)] if record else []),
        *fixed_tail(config),
    ]
