"""jev-lint 厳選ラッパの env、host ディレクトリ、node の事前検査、上流の起動 argv。

`jevlint.py` (入口) の下流モジュールの 1 つ。上流 (jev-lint) は消費側のリポジトリの外にある
ラッパ所有の host ディレクトリへ、キー無しの env で取得する (`prepare_host`)。上流の起動その
ものの env は `build_env` が組み立て、上流に渡してよい変数だけを列挙形式ではなく allowlist
形式で写す (spec の「設定と、起動の引数と環境変数」節)。`install_env` は取得のときだけに使う
別の組み立てで、pnpm と `@ast-grep/cli` の postinstall にキーと上流固有の変数を見せない
ための落とし方 (denylist 形式) を取る。この 2 つの組み立て方向が違う理由は、上流の起動は
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
import tempfile
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


def build_env(source: Mapping[str, str], with_key: bool) -> dict:
    """上流の起動 (と、ラッパ自身の git 呼び出し) に渡す env を組み立てる。

    `source` から `_ALLOWED_ENV_NAMES` と `LC_` で始まる変数だけを写す allowlist。
    `NODE_OPTIONS`、proxy 系、`NODE_TLS_*`、`GIT_*` (下の固定の 2 つを除く)、
    `JEV_LINT_*`、`TYPESAFEAI_*` はこの組み立てには存在しないので入らない。
    `with_key` が真のときだけ `TYPESAFE_API_KEY` を足す (`--dry-run` や事前検査など
    キーが要らない起動では呼び出し側が False を渡す)。

    `LC_ALL` を自分では足さない。上流の env は利用者の locale をそのまま保つ設計で
    (`jevlint_tree.py` の `_git_env` が行う `LC_ALL=C` の固定はラッパ自身の git 呼び出し
    専用であり、ここには適用しない)。`source` に `LC_ALL` があれば `LC_` prefix として
    素通しになるが、それは利用者自身の設定であって、ここが注入したものではない。
    """
    env: dict = {}
    for name in _ALLOWED_ENV_NAMES:
        if name in source:
            env[name] = source[name]
    for name, value in source.items():
        if name.startswith("LC_"):
            env[name] = value
    if with_key and "TYPESAFE_API_KEY" in source:
        env["TYPESAFE_API_KEY"] = source["TYPESAFE_API_KEY"]
    env.update(_FIXED_GIT_SAFETY_ENV)
    return env


def install_env(source: Mapping[str, str]) -> dict:
    """`pnpm add` (host の取得) に渡す env。`source` の写しから危険な prefix だけを落とす。

    `build_env` とは逆に denylist で組み立てる。取得は利用者の PATH やネットワーク周りの
    設定 (proxy 等) をほぼそのまま必要とする一方、キー (`TYPESAFE_API_KEY` /
    `TYPESAFEAI_API_KEY`) と上流固有の変数 (`TYPESAFE_BASE_URL` 等) だけを `@ast-grep/cli`
    の postinstall に見せないために落とす。
    """
    return {
        name: value
        for name, value in source.items()
        if not name.startswith(_INSTALL_DROPPED_PREFIXES)
    }


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
    """
    xdg = source.get("XDG_CACHE_HOME", "")
    if xdg:
        base = Path(xdg)
    else:
        home = source.get("HOME", "")
        if not home:
            raise HostError("XDG_CACHE_HOME も HOME も無いので host の置き場を決められない")
        base = Path(home) / ".cache"
    return base / "jev-lint-curated" / version


def host_reusable(host: Path, version: str) -> bool:
    """`host` を作り直さずに使えるか。`package.json` の `version` が一致し `cli.js` がある。"""
    package_json = host / "node_modules" / "jev-lint" / "package.json"
    cli_js = host / "node_modules" / "jev-lint" / "dist" / "cli.js"
    if not package_json.is_file() or not cli_js.is_file():
        return False
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("version") == version


def _reject_pnpm_workspace_ancestor(host: Path) -> None:
    """`host` の祖先ディレクトリに `pnpm-workspace.yaml` があれば拒否する。

    pnpm は cwd から親方向に `pnpm-workspace.yaml` を探してモノレポと判定し、workspace の
    root にある `.npmrc` を読む (spec 前提 14 の一般化)。`prepare_host` は host の親を
    cwd にして `pnpm add` を呼ぶため、host の祖先にこのファイルがあると同じ理由で意図しない
    registry に化ける経路が残る。macOS では `/tmp` が `/private/tmp` の symlink であるように
    (`jevlint_tree.py` の `_temp_base` と同じ実測)、与えられた表記だけでは祖先を見落とす
    ことがあるため、解決した表記の祖先も合わせて見る。
    """
    ancestors = set(host.parents) | set(host.resolve().parents)
    for ancestor in ancestors:
        candidate = ancestor / "pnpm-workspace.yaml"
        if candidate.exists():
            raise HostError(f"host の祖先に pnpm-workspace.yaml がある: {candidate}")


def prepare_host(
    version: str, source: Mapping[str, str], run: Callable = subprocess.run
) -> Path:
    """host を再利用できればそれを返し、できなければ取得してから host の名前に置く。

    pnpm を worktree や消費側のリポジトリを cwd にして起動しない理由 (spec 前提 14。境界
    レーンがこのリポジトリの外で実測: node 24.18.0 / pnpm 12.3.4 / git 2.55.0): pnpm は
    cwd の `.npmrc` の `registry=` を読み、`dlx` のキャッシュが温まって
    いても読む (キャッシュのキーが registry を含むため)。環境変数 `npm_config_registry`
    はこの project の `.npmrc` に負ける。消費側がコミットした `.npmrc` のある worktree で
    `pnpm dlx` を起動すると、偽の registry が返す別物の jev-lint がキー付きの env で走る
    ことをダミーのキーで再現した。この関数は host の親 (利用者の cache ディレクトリの
    下で、どの消費側のリポジトリにも属さない) を cwd にして `pnpm add` を 1 回だけ呼ぶ。

    一時ディレクトリを host と同じ親に作り、取得の成功を確認してから `rename` で host の
    名前に置くのは、途中で切れた取得 (ネットワーク断・プロセス kill 等) が host として
    再利用されるのを防ぐため。`rename` (`Path.replace`) は同一ファイルシステム上で
    atomic なので、host の名前が「未取得」か「完全に取得済み」のどちらかの状態しか
    取らない (取得の途中の状態を host の名前で観測することがない)。
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

    renamed = False
    try:
        try:
            (tmp / "package.json").write_text(
                json.dumps({"private": True}), encoding="utf-8"
            )
            argv = ["pnpm", "add", "--allow-build=@ast-grep/cli", f"jev-lint@{version}"]
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
        if host.exists():
            shutil.rmtree(host)
        tmp.replace(host)
        renamed = True
    finally:
        if not renamed:
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
