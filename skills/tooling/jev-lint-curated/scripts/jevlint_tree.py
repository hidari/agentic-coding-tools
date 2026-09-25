"""jev-lint 厳選ラッパの ref 解決とパスの検査。

`jevlint.py` (入口) の下流モジュールの 1 つ。`--commit` / `--base` を本体のリポジトリの
SHA に固定し、対象のパスがそのコミットに実在するかを確かめるところまでを持つ。
worktree の展開と blob の書き出し (checkout を伴わない側) はこのファイルの後半として
別タスクで追加される。

このモジュールは他の jevlint* モジュールを import しない。依存は入口 (`jevlint.py`)
から下流へ一方向に流し、循環を作らないため。git を呼ぶ関数はすべて `env` を引数で
受け取り、自分では組み立てない (組み立ては呼び出し側 `build_env` の責務)。
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class TreeError(Exception):
    """ref の解決またはパスの検査の失敗。呼び出し側はこれを終了コード 2 に落とす。"""


def repo_root(cwd: Path, env: dict) -> Path:
    """`cwd` を含むリポジトリの root を返す。サブディレクトリを cwd にしても解決できる。"""
    proc = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise TreeError(f"git リポジトリの root を解決できない: {cwd}")
    return Path(proc.stdout.strip())


def resolve_commit(root: Path, ref: str, env: dict) -> str:
    """`ref` をコミットの SHA (40 桁、SHA-256 のリポジトリなら 64 桁) に解決する。

    `-` で始まる ref は拒否してから git を呼ぶ。`jevlint.py` の位置引数検査は
    `--commit`/`--base` のようなオプションの値までは見ないため、ここが最後の関門になる
    (`--all` のような値がオプションとして誤認されるのを防ぐ)。
    """
    if ref.startswith("-"):
        raise TreeError(f"'-' で始まる ref は受け付けない: {ref!r}")
    proc = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", ref + "^{commit}"],
        env=env,
        capture_output=True,
        text=True,
    )
    sha = proc.stdout.strip()
    # --quiet は「解決できる ref が無い」ときの fatal メッセージだけを消す。blob の
    # ような「コミットでない object」を渡したときは --quiet でも stderr に出る
    # (実測)。stderr はどちらも読まずに終了コードと stdout の空非空だけを見るので、
    # ここでは影響しない
    if proc.returncode != 0 or not sha:
        raise TreeError(f"ref をコミットとして解決できない: {ref!r}")
    return sha


def normalize_path(text: str) -> str:
    """`git ls-tree` に渡す前提で、パスをリポジトリ root からの相対形へ正規化する。

    先頭の `./`、重なった `/`、末尾の `/` は成分の分割時に空文字列や `"."` として
    落ちるので、成分の列から取り除くだけで正規化になる。`..` の判定も成分単位で行う。
    文字列全体を部分文字列として `".." in text` で判定すると `a..b.py` のような
    通常のファイル名まで拒否してしまう。

    `-` の判定は正規化した後の結果に対して行う。`./-x` のように正規化前は `.`
    始まりでも、結果は `-x` になり Task 6 で argv の 1 トークンとしてそのまま
    上流へ渡るため、判定を生の入力に対して行うと素通りする。
    """
    if text.startswith("/"):
        raise TreeError(f"絶対パスは受け付けない: {text!r}")
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if ".." in parts:
        raise TreeError(f"'..' を含むパスは受け付けない: {text!r}")
    normalized = "/".join(parts)
    if normalized.startswith("-"):
        raise TreeError(f"'-' で始まるパスは受け付けない: {text!r}")
    return normalized


def path_in_commit(root: Path, sha: str, path: str, env: dict) -> bool:
    """`path` (root 相対、`normalize_path` 済みの形) が `sha` のコミットに実在するか。

    git 呼び出し自体の失敗 (実在しない sha、partial clone で tree object が未取得、
    object store の破損等) は「パスが無い」(`False`) に吸収せず `TreeError` にする。
    実測: 40 桁 hex として well-formed だが実在しない sha を渡すと `git ls-tree` は
    終了コード 128・`fatal: not a tree object` で失敗する。これは「パスが無い」
    (終了コード 0・出力空) とは別の failure mode であり、区別しないと git 障害が
    「対象パスがコミットに存在しない」という誤ったメッセージに化ける。
    `repo_root` / `resolve_commit` と同じ「非 0 終了は TreeError」の形に揃える。
    """
    if path == "":
        return True
    proc = subprocess.run(
        ["git", "-C", str(root), "ls-tree", "-z", sha, "--", path],
        env=env,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise TreeError(f"git ls-tree の呼び出しに失敗した (sha={sha!r}, path={path!r})")
    # -z は区切りを NUL にするだけでなく、非 ASCII なファイル名を引用符で包む既定の
    # 挙動 (core.quotePath) も止める (実測)。存在しないパスは終了コード 0 のまま
    # 出力だけが空になるので、空かどうかだけを見ればよい
    return bool(proc.stdout)
