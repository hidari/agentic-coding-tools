"""jev-lint 厳選ラッパの ref 解決、パスの検査、コミットの展開。

`jevlint.py` (入口) の下流モジュールの 1 つ。前半は `--commit` / `--base` を本体の
リポジトリの SHA に固定し、対象のパスがそのコミットに実在するかを確かめる。後半は
そのコミットを checkout を伴わない worktree へ展開する。上流に送ってよいのはコミット
済みの中身だけという制約を、手順ではなく構造で守るのがこの後半の役目である。

展開が checkout を使わない理由: `git worktree add` / `checkout` / `archive` は追跡された
`.gitattributes` の `filter=` が選ぶ smudge (git-crypt の平文化、git-lfs の取得) を利用者の
環境の driver で走らせ、symlink を symlink として復元し、hook を起動する。
`worktree add --no-checkout` は worktree を登録するだけでファイルを書かず、index も作らない
(`read-tree` は呼ばない。上流が worktree の中で呼ぶ git はコミット同士の diff だけで、index
を読まない)。ディスクへ書く経路は `git cat-file --batch` が返す blob の生のバイトだけになる。
ディスクの中身がコミットの blob と同一になるのは次の 3 つを git の呼び出しごとに満たす
ときで、どれも `_QuietGit` / `_git_readonly` が argv で付ける (実測: git 2.55.0):

- `--no-replace-objects`: `refs/replace` の差し替えを追わない。差し替え先はコミットから
  到達しない object でもよく、素の `cat-file` は差し替え後の中身を返す
- `--no-lazy-fetch`: partial clone で無い object を promisor remote から取りに行かず、
  `missing` か非 0 で失敗する。取りに行くと利用者の `remote.<name>.uploadpack` のコマンドが
  cwd = root で起動し、hook を止めていても pack が増える
- hook を起動しない: checkout を伴わなくても `worktree add --no-checkout` は
  `reference-transaction` を起動する。hook ディレクトリ・設定で定義する hook・fsmonitor の
  3 経路を止める

`--no-lazy-fetch` は後から入った global option で、これが入った版が要る最低の版になる
(`_MIN_GIT_VERSION` が持つ。SKILL.md はここを指す)。古い git は
`unknown option: --no-lazy-fetch` (終了コード 129) で全呼び出しが失敗し、`_run_git` が
版の不足として `TreeError` にする。この文言の照合は C locale の英語に依る。git は
メッセージを訳すので、ラッパは自分の git に `LC_ALL=C` を渡して読む (`_git_env`)。上流に
渡す env はこの上書きを受けない。

上流が worktree の中で自分で呼ぶ `git diff` は利用者の設定で走るので、コミットされた
`.gitattributes` が選ぶ textconv の driver はそこで起動しうる。この限界はこのモジュールでは
扱わず、`jevlint.py` の `main` の docstring (SKILL.md が指す先) が文書化する。

このモジュールは他の jevlint* モジュールを import しない。依存は入口 (`jevlint.py`)
から下流へ一方向に流し、循環を作らないため。git を呼ぶ関数はすべて `env` を引数で
受け取り、自分では組み立てない (組み立ては呼び出し側 `build_env` の責務)。
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


class TreeError(Exception):
    """ref の解決、パスの検査、展開の失敗。呼び出し側はこれを終了コード 2 に落とす。"""


class SignalInterrupt(KeyboardInterrupt):
    """SIGTERM / SIGHUP を例外にしたもの。

    `KeyboardInterrupt` の派生にするのは、Ctrl-C と同じ経路で `finally` を走らせつつ、
    呼び出し側の `except Exception` に吸収されないようにするため。
    """

    def __init__(self, signum: int):
        super().__init__(signum)
        self.signum = signum


# githooks(5) が列挙する hook の名前。出典は `git help githooks` (Homebrew の git 2.55.0、
# 28 件)。設定で定義する hook (`hook.<name>.command` + `hook.<name>.event`) は
# `core.hooksPath` では止まらず `hook.<event>.enabled=false` で止まる (実測) ので、観測した
# event に限らず全 event を止める。新しい版の git が event を足したらここにも足す
_HOOK_EVENTS = (
    "applypatch-msg",
    "pre-applypatch",
    "post-applypatch",
    "pre-commit",
    "pre-merge-commit",
    "prepare-commit-msg",
    "commit-msg",
    "post-commit",
    "pre-rebase",
    "post-checkout",
    "post-merge",
    "pre-push",
    "pre-receive",
    "update",
    "proc-receive",
    "post-receive",
    "post-update",
    "reference-transaction",
    "push-to-checkout",
    "pre-auto-gc",
    "post-rewrite",
    "sendemail-validate",
    "fsmonitor-watchman",
    "p4-changelist",
    "p4-prepare-changelist",
    "p4-post-changelist",
    "p4-pre-submit",
    "post-index-change",
)


# ラッパ自身が呼ぶ全 git に付ける global option (`-C` の直後、サブコマンドの前)。
# `--no-lazy-fetch` が入った版を `_MIN_GIT_VERSION` に置く (出典: 上流の
# Documentation/RelNotes/2.45.0.txt の「"git --no-lazy-fetch cmd" allows to run "cmd" while
# disabling lazy fetching」。2.44.0 の RelNotes には無い)。古い git は C locale では
# `unknown option: --no-lazy-fetch` と usage を stderr に出して終了コード 129 になる (知らない
# global option への応答の形は 2.55.0 と 2.50.1 で実測)。この文言は訳される (de_DE:
# `Unbekannte Option:`、fr_FR: `option inconnue :`。Homebrew の 2.55.0 で実測。Apple の
# 2.50.1 は訳を持たない) ので、照合は `_git_env` が固定する C locale の形にだけ合わせる
_GLOBAL_OPTIONS = ("--no-lazy-fetch", "--no-replace-objects")
_MIN_GIT_VERSION = "2.45.0"


def _git_env(env: dict) -> dict:
    """ラッパ自身の git に渡す env。メッセージを C locale の英語に固定する。

    git は `_()` で訳したメッセージを出し、`unknown option:` の照合が locale で外れる。
    `LC_ALL=C` は `LANG` / `LC_*` / `LANGUAGE` を de に向けたままでも英語に戻す (実測:
    Homebrew の 2.55.0)。`LANGUAGE` は gettext が C locale では無視するが、ラッパが読む
    出力に利用者の言語設定を残さないため落とす。上流に渡す env とは別で、こちらはラッパが
    読む出力のためだけの上書き。
    `ls-tree -z` の出力 (非 ASCII のパスを含む) は locale で変わらない (実測: C / de_DE /
    en_US で同一のバイト列)。
    """
    quiet = dict(env)
    quiet.pop("LANGUAGE", None)
    quiet["LC_ALL"] = "C"
    return quiet


def _run_git(argv: list, env: dict, text: bool = False) -> "subprocess.CompletedProcess":
    """`subprocess.run` で git を起動する唯一の場所。古い git は版の不足として `TreeError`。

    `_QuietGit.popen` だけは `cat-file --batch` のために `subprocess.Popen` を使うが、env は
    同じ `_git_env` で、版の不足は `materialize` で先に走る `_ls_tree` (`run` 経由) が
    名指す。非 0 なので呼び出し側はどのみち失敗にするが、その文言は「worktree add に
    失敗」のように的を外す。ここで名指して fail closed にする。照合は C locale の
    `unknown option: <名前>` に限る (usage の行にも option の名前は現れるので、名前だけの
    照合は新しい git の無関係な 129 を「古い」と誤読する)。
    """
    proc = subprocess.run(argv, env=_git_env(env), capture_output=True, text=text)
    if proc.returncode == 129:
        stderr = proc.stderr if text else proc.stderr.decode("utf-8", "replace")
        for option in _GLOBAL_OPTIONS:
            if f"unknown option: {option}" in stderr:
                raise TreeError(
                    f"この git は {option} を知らない。git {_MIN_GIT_VERSION} 以上が要る"
                )
    return proc


class _QuietGit:
    """展開の間の git。呼び出しごとに利用者の hook を止めるフラグを必ず付ける。

    止める経路は 3 つで、それぞれ別のフラグが要る (実測: git 2.55.0):

    - hook ディレクトリ (`.git/hooks` か `core.hooksPath`) のファイル: `core.hooksPath=<空の
      ディレクトリ>`。コマンドラインの `-c` は local 設定にも global 設定にも勝つ
    - 設定で定義する hook (`hook.<name>.command` + `hook.<name>.event`): `core.hooksPath`
      では止まらない。`hook.<event>.enabled=false` で止まる。逆にこのフラグは hook
      ディレクトリの hook を止めない (`git hook list` は `event-disabled` と表示しつつ
      `hook from hookdir` を残し、実際に起動する)
    - fsmonitor: `core.fsmonitor=false`。設定の `core.fsmonitor` は daemon か任意の hook
      コマンドを起動しうるが、展開が呼ぶコマンド (worktree add --no-checkout、ls-tree、
      cat-file、worktree remove / prune) と上流のコミット同士の diff は問い合わせない
      (実測: 問い合わせたのは対照の `git status` だけ)。予防のために止める

    加えて `_GLOBAL_OPTIONS` (`--no-lazy-fetch` と `--no-replace-objects`) を付ける。hook を
    止めても、partial clone で無い object を読めば lazy fetch が利用者の uploadpack の
    コマンドを起動して pack を増やし、`refs/replace` があればコミットに無い中身を読む
    (どちらも実測: git 2.55.0)。

    `expanded_commit` と `materialize` はこのクラス経由でしか git を呼ばない。hook を
    止めないコマンドは展開の外だけで使える形 (`_git_readonly`) にして、展開の中に新しい
    呼び出しを書くときフラグを忘れられないようにしている。
    """

    def __init__(self, env: dict, hooks: Path):
        self.env = env
        flags = ["-c", f"core.hooksPath={hooks}", "-c", "core.fsmonitor=false"]
        for event in _HOOK_EVENTS:
            flags += ["-c", f"hook.{event}.enabled=false"]
        self.flags = flags

    def argv(self, cwd: Path, args: list) -> list:
        return ["git", "-C", str(cwd), *_GLOBAL_OPTIONS, *self.flags, *args]

    def run(self, cwd: Path, args: list) -> "subprocess.CompletedProcess[bytes]":
        """終了コードの判定は呼び出し側が行う。"""
        return _run_git(self.argv(cwd, args), self.env)

    def popen(self, cwd: Path, args: list) -> "subprocess.Popen[bytes]":
        # stderr は継承する。PIPE にすると読み切るまで子が詰まりうるし、git の診断は
        # 利用者にそのまま見えてよい
        return subprocess.Popen(
            self.argv(cwd, args),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env=_git_env(self.env),
        )


# 展開の外 (ref の解決とパスの検査) だけで使う読み取り専用の git。hook を止める `-c` は
# 持たず、`_GLOBAL_OPTIONS` だけを付ける。allow-list の 2 つはそれと組み合わせてローカルの
# object を読むだけになる。lazy fetch を止めないと `ls-tree` は partial clone で取りに行き、
# 利用者の uploadpack のコマンドを起動し、`reference-transaction` まで起動する (実測:
# git 2.55.0、tree:0 の clone)。展開の中で誤って使うと ValueError で止まる
_READONLY_SUBCOMMANDS = ("rev-parse", "ls-tree")


def _git_readonly(
    cwd: Path, args: list, env: dict, text: bool = False
) -> "subprocess.CompletedProcess":
    if not args or args[0] not in _READONLY_SUBCOMMANDS:
        raise ValueError(f"展開の外で使える git は {_READONLY_SUBCOMMANDS} のみ: {args!r}")
    return _run_git(["git", "-C", str(cwd), *_GLOBAL_OPTIONS, *args], env, text)


def repo_root(cwd: Path, env: dict) -> Path:
    """`cwd` を含むリポジトリの root を返す。サブディレクトリを cwd にしても解決できる。"""
    proc = _git_readonly(cwd, ["rev-parse", "--show-toplevel"], env, text=True)
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
    proc = _git_readonly(
        root, ["rev-parse", "--verify", "--quiet", ref + "^{commit}"], env, text=True
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
    始まりでも、結果は `-x` になり、呼び出し側 (`jevlint.py` の argv の組み立て) が
    argv の 1 トークンとしてそのまま上流へ渡すため、判定を生の入力に対して行うと
    素通りする。
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
    proc = _git_readonly(root, ["ls-tree", "-z", sha, "--", path], env)
    if proc.returncode != 0:
        raise TreeError(f"git ls-tree の呼び出しに失敗した (sha={sha!r}, path={path!r})")
    # -z は区切りを NUL にするだけでなく、非 ASCII なファイル名を引用符で包む既定の
    # 挙動 (core.quotePath) も止める (実測)。存在しないパスは終了コード 0 のまま
    # 出力だけが空になるので、空かどうかだけを見ればよい
    return bool(proc.stdout)


def validate_tree_paths(paths: list) -> None:
    """コミットの tree に載るパスの列を、ディスクへ書く前に全件検査する。

    拒否するのは、絶対パス、成分の `..`、大文字小文字を問わず `.git` の成分、そして
    大文字小文字を区別しないファイルシステムで衝突する組。衝突はファイル同士
    (`A.py` と `a.py`) だけでなくファイルとディレクトリ (`A` と `a/b`) も見る。後者は
    `a` を mkdir する時点で `A` と衝突して書き出しが途中で止まるため。

    `.git` を拒否する理由: macOS の既定のように大文字小文字を区別しないファイルシステム
    では、`.GIT` という blob が worktree の `.git` (gitdir を指すファイル) を上書きして、
    `review` の git が別のリポジトリを指しうる。判定は `casefold()` で行う。
    """
    seen_files: dict = {}
    seen_dirs: dict = {}
    for path in paths:
        if path.startswith("/"):
            raise TreeError(f"tree に絶対パスがある: {path!r}")
        parts = path.split("/")
        for part in parts:
            if part in ("", ".", ".."):
                raise TreeError(f"tree に '..' または空の成分を持つパスがある: {path!r}")
            if part.casefold() == ".git":
                raise TreeError(f"tree に '.git' の成分を持つパスがある: {path!r}")
        folded_parts = [part.casefold() for part in parts]
        for depth in range(1, len(parts)):
            prefix = "/".join(folded_parts[:depth])
            if prefix in seen_files:
                raise TreeError(
                    f"大文字小文字だけが違うパスが衝突する: {seen_files[prefix]!r} と {path!r}"
                )
            seen_dirs.setdefault(prefix, "/".join(parts[:depth]))
        folded = "/".join(folded_parts)
        other = seen_files.get(folded) or seen_dirs.get(folded)
        if other is not None:
            raise TreeError(f"大文字小文字だけが違うパスが衝突する: {other!r} と {path!r}")
        seen_files[folded] = path


# ディスクへ書く mode。160000 (submodule の gitlink) はこの集合に無いので書かない
_WRITTEN_MODES = ("100644", "100755", "120000")


def _ls_tree(root: Path, sha: str, git: _QuietGit) -> list:
    """`sha` の tree を `(mode, type, object sha, path)` の列にする。"""
    proc = git.run(root, ["ls-tree", "-r", "-z", "--full-tree", sha])
    if proc.returncode != 0:
        raise TreeError(f"git ls-tree の呼び出しに失敗した (sha={sha!r})")
    entries = []
    for record in proc.stdout.split(b"\0"):
        if not record:
            continue
        meta, _, raw_path = record.partition(b"\t")
        mode, otype, object_sha = meta.decode("ascii").split(" ")
        # パスは surrogateescape で復号する。UTF-8 でないバイト列の名前がコミットに
        # あっても、そのまま同じバイト列でディスクに書けるようにするため
        entries.append((mode, otype, object_sha, os.fsdecode(raw_path)))
    return entries


def _read_batch_blob(proc: "subprocess.Popen", object_sha: str) -> bytes:
    """`git cat-file --batch` から blob を 1 つ読む。

    形は「ヘッダの行を読んでから、宣言された大きさだけを読み、区切りの改行を 1 つ読む」。
    `BufferedReader.read(size)` は size 分そろうか EOF まで読み続けるので、大きな blob
    でも 1 回で読み切れる。実在しない object のヘッダは `<sha> missing` の 2 フィールド
    (実測)。フィールド数と type と実際に読めた長さを見て、途中で切れた読み込みを
    黙って短いファイルにしない。
    """
    proc.stdin.write(object_sha.encode("ascii") + b"\n")
    proc.stdin.flush()
    header = proc.stdout.readline()
    fields = header.split()
    if len(fields) != 3 or fields[1] != b"blob":
        raise TreeError(f"blob を読めない (object={object_sha!r}): {header!r}")
    size = int(fields[2])
    data = proc.stdout.read(size)
    if len(data) != size:
        raise TreeError(f"blob の読み込みが途中で切れた (object={object_sha!r})")
    proc.stdout.read(1)
    return data


def materialize(root: Path, sha: str, dest: Path, env: dict, hooks: Path) -> int:
    """`sha` の tree の blob を、生のバイトのまま `dest/<path>` に書く。書いた本数を返す。

    mode 100644 と 100755 は通常ファイル (100755 は実行ビットを立てる)、120000 (symlink)
    はリンク先の文字列を中身にした通常ファイルにする。symlink を復元しないのは、利用者が
    そのパスを明示すると上流がリンク先 (リポジトリの外を含む) を読んで送るため。

    `hooks` は `core.hooksPath` に渡す空のディレクトリ。ここで呼ぶ `ls-tree` と `cat-file`
    も `_QuietGit` で呼ぶ。lazy fetch を止めなければどちらも partial clone で無い object を
    取りに行き、利用者の uploadpack のコマンドを起動し、hook を止めていなければ
    `reference-transaction` も起動する (実測: git 2.55.0)。止めれば `cat-file --batch` は
    `<sha> missing` を返し、`_read_batch_blob` が `TreeError` にする。

    パスの検査は書き始める前に全件に対して通す (途中まで書いてから止めない)。書き出しは
    `O_EXCL` で行い、同じパスが既にあれば失敗させる。production では空の worktree に
    書くので当たらないが、checkout が走った・symlink が復元された・大文字小文字の衝突が
    検査を抜けた、のどれかが起きたときに上書きや書き抜けを黙って通さない第 2 層になる。
    その失敗も含め、書き出しの OSError は `TreeError` にする。素の例外で抜けると Python は
    1 で終わり、ラッパの「1 = finding あり」と衝突するため。
    """
    git = _QuietGit(env, hooks)
    entries = _ls_tree(root, sha, git)
    validate_tree_paths([path for _, _, _, path in entries])
    written = 0
    with git.popen(root, ["cat-file", "--batch"]) as proc:
        for mode, _, object_sha, path in entries:
            if mode not in _WRITTEN_MODES:
                continue
            data = _read_batch_blob(proc, object_sha)
            target = dest / path
            permission = 0o755 if mode == "100755" else 0o644
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, permission)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
            except OSError as error:
                raise TreeError(f"書き出しに失敗した: {path!r}: {error}") from None
            written += 1
    if proc.returncode != 0:
        raise TreeError(f"git cat-file --batch が失敗した (sha={sha!r})")
    return written


@dataclass(frozen=True)
class Expanded:
    """展開したコミット。`tree` は worktree、`scratch` はその外の書き捨て置き場。

    設定と記録は `scratch` に置く。worktree の中に置くと上流の baseDir が worktree に
    なり、消費側が追跡している設定や `.jev-lint/rules/` を読んでしまう。
    """

    tree: Path
    scratch: Path


_SGCONFIG_NAMES = ("sgconfig.yml", "sgconfig.yaml")


def reject_sgconfig_in_ancestors(path: Path) -> None:
    """`path` を cwd にして上流を起動する前に、その祖先の sgconfig を拒否する。

    上流は ast-grep を起動し、ast-grep は cwd と親ディレクトリから sgconfig を探して、
    `customLanguages` の動的ライブラリを読み込む。共有の一時ディレクトリには別の利用者も
    ファイルを置けるので、キーを使わない起動でも利用者の権限で任意のコードが走りうる。
    上流を起動する子プロセスの cwd は解決済みの形 (macOS では `/var` が `/private/var`) に
    なるので、与えられた形と解決した形の両方の祖先を見る。
    """
    ancestors = set(path.parents) | set(path.resolve().parents)
    for ancestor in ancestors:
        for name in _SGCONFIG_NAMES:
            if (ancestor / name).exists():
                raise TreeError(f"一時ディレクトリの祖先に {name} がある: {ancestor / name}")


def _discard_worktree(git: _QuietGit, root: Path, tree: Path) -> None:
    # 後始末は元の例外を隠さないよう、どの段も失敗を投げない。git の非 0 は見るだけ、
    # `_run_git` が投げる TreeError (版の不足) もここで握る (展開の途中で git が変わる
    # ことは無いが、finally の中で投げると元の例外を隠し一時ディレクトリの削除も飛ぶ)。
    # `worktree remove --force` は worktree の `.git` ファイルが壊れていると終了コード 128
    # でディレクトリを残す (実測: git 2.55.0) ので、そのときはディレクトリを消してから
    # 登録を prune する。prune も通らなければ利用者のリポジトリに登録が残るので、黙って
    # 残さず、残った worktree と消し方を stderr に 1 行で告げる
    try:
        removed = git.run(root, ["worktree", "remove", "--force", str(tree)]).returncode == 0
    except TreeError:
        removed = False
    if removed:
        return
    shutil.rmtree(tree, ignore_errors=True)
    try:
        pruned = git.run(root, ["worktree", "prune"]).returncode == 0
    except TreeError:
        pruned = False
    if not pruned:
        print(
            f"worktree の登録を消せなかった: {tree} (本体のリポジトリで `git worktree prune` を"
            "実行すると消える)",
            file=sys.stderr,
        )


def tmpdir_from_env(env: dict) -> Path:
    """一時ディレクトリの置き場を、解決した形で返す。解決できなければ `TreeError`。

    `env` の `TMPDIR` が空でない絶対パスならそれ、それ以外は `tempfile.gettempdir()`。
    `os.environ` ではなく `env` から読むのは、上流に渡す env と同じ値で置き場が決まるように
    し、テストが置き場を差し替えられるようにするため。空や相対の値を `mkdtemp(dir=...)` に
    そのまま渡すと cwd の下に作られ、3.9 では返るパスも相対になる (実測: 3.9.6 は
    `'jevlint-xxx'`、3.14.7 は cwd を前置した絶対パス)。相対のままだと `worktree add` は
    `-C root` の root から、書き出しは cwd から解決して別の場所を指す。
    """
    candidate = env.get("TMPDIR", "")
    try:
        if candidate and os.path.isabs(candidate):
            return Path(candidate).resolve()
        # gettempdir は候補が 1 つも使えないと FileNotFoundError を投げる
        return Path(tempfile.gettempdir()).resolve()
    except (OSError, RuntimeError) as error:
        # 3.9 の resolve() は symlink のループを RuntimeError にする (実測: 3.9.6。3.14.7 は
        # 投げず、後の mkdtemp が OSError になる)
        raise TreeError(f"一時ディレクトリの置き場を解決できない: {error}") from None


def is_inside(path: Path, directory: Path) -> bool:
    """`path` (解決した形で渡す) が `directory` そのものか、その下にあるか。

    包含は inode で見る。大文字小文字を区別しないファイルシステムでは `resolve()` が
    与えられた表記の大文字小文字を保つので (実測: APFS)、文字列の比較は `.../Repo` と
    `.../repo/sub` の包含を見落とす。まだ無い祖先は同じ inode を指しようがないので飛ばす。
    """
    for ancestor in (path, *path.parents):
        try:
            if os.path.samefile(ancestor, directory):
                return True
        except OSError:
            continue
    return False


def _temp_base(env: dict, root: Path) -> Path:
    """展開の一時ディレクトリの置き場を決める (`tmpdir_from_env`)。

    置き場がリポジトリの root の中なら拒否する。利用者の作業ツリーの中に worktree を作ると、
    走査対象に自分の展開が混ざるため。まだ無い置き場は `mkdtemp` が `TreeError` にする。
    """
    base = tmpdir_from_env(env)
    if is_inside(base, root):
        raise TreeError(f"一時ディレクトリの置き場がリポジトリの中にある: {base}")
    return base


@contextlib.contextmanager
def expanded_commit(root: Path, sha: str, env: dict) -> Iterator[Expanded]:
    """`sha` を一時ディレクトリの worktree へ checkout 無しで展開し、抜けるときに消す。

    一時ディレクトリの置き場は `env` の `TMPDIR` から `_temp_base` が決める。

    worktree は `worktree add --detach --no-checkout` で登録するだけで、index は作らない
    (`read-tree` は呼ばない)。上流が worktree の中で呼ぶ git はコミット同士の
    `git diff <base>...HEAD` だけで、index は読まない (作業ツリーからは
    コミット済みの `.gitattributes` を diff の属性として読む)。index の無い worktree での
    その diff は通常の checkout と同一の出力で、`worktree remove --force` も通る (実測:
    git 2.55.0)。

    git の呼び出しはすべて `_QuietGit` を通す。`--no-checkout` が止めるのは post-checkout
    だけで、`worktree add` 自体は `reference-transaction` を起動する (実測: git 2.55.0)。
    hook は利用者のリポジトリの設定で任意のコマンドになりうるので、展開の間は起動させない。
    後始末の `worktree remove` と `prune` は hook を起動しない (実測) が、同じ runner で呼ぶ。
    """
    base = _temp_base(env, root)
    try:
        tmp = Path(tempfile.mkdtemp(prefix="jevlint-", dir=str(base)))
    except OSError as error:
        raise TreeError(f"一時ディレクトリを作れない: {error}") from None
    tree = tmp / "tree"
    hooks = tmp / "hooks"
    git = _QuietGit(env, hooks)
    # `worktree add` が成功する前に失敗したときは登録が無いので、後始末で git を呼ばない。
    # 登録の無いパスへの `worktree remove` は失敗し、その fallback の `worktree prune` は
    # 利用者のリポジトリでディレクトリの見当たらない登録 (アンマウント中のボリューム等)
    # まで消してしまう
    registered = False
    try:
        reject_sgconfig_in_ancestors(tree)
        scratch = tmp / "scratch"
        try:
            hooks.mkdir()
            scratch.mkdir()
        except OSError as error:
            raise TreeError(f"一時ディレクトリの中を作れない: {error}") from None
        proc = git.run(root, ["worktree", "add", "--detach", "--no-checkout", str(tree), sha])
        if proc.returncode != 0:
            raise TreeError(f"git worktree add に失敗した (sha={sha!r})")
        registered = True
        materialize(root, sha, tree, env, hooks)
        for name in _SGCONFIG_NAMES:
            candidate = tree / name
            # コミットの `sgconfig.yml/x` は展開後にディレクトリになる。ast-grep が読む
            # のはファイルなので消す対象ではなく、`unlink()` も macOS では
            # PermissionError になる (実測)。消さずに拒否する
            if candidate.is_dir():
                raise TreeError(f"コミットの {name} がディレクトリなので展開を拒否する")
            if candidate.is_file():
                try:
                    candidate.unlink()
                except OSError as error:
                    raise TreeError(f"{name} を消せない: {error}") from None
                print(
                    f"展開した worktree から {name} を消した (ast-grep が読まないようにするため)",
                    file=sys.stderr,
                )
        yield Expanded(tree=tree, scratch=scratch)
    finally:
        if registered:
            _discard_worktree(git, root, tree)
        shutil.rmtree(tmp, ignore_errors=True)


@contextlib.contextmanager
def signals_as_exceptions() -> Iterator[None]:
    """SIGTERM と SIGHUP を `SignalInterrupt` に変え、抜けるときに元のハンドラへ戻す。

    既定のハンドラだとプロセスが即座に終わり `finally` が走らないので、worktree の登録と
    一時ディレクトリが残る。例外にすれば `expanded_commit` の後始末まで届く。
    """

    def raise_interrupt(signum: int, frame: object) -> None:
        raise SignalInterrupt(signum)

    previous = {}
    for sig in (signal.SIGTERM, signal.SIGHUP):
        previous[sig] = signal.signal(sig, raise_interrupt)
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def count_suffix(tree: Path, paths: list, suffix: str) -> int:
    """対象のパス (空なら `tree` 全体) の下にある、名前が `suffix` で終わるファイルの本数。"""
    if not paths or "" in paths:
        targets = [tree]
    else:
        targets = [tree / path for path in paths]
    count = 0
    for target in targets:
        if target.is_file():
            count += int(target.name.endswith(suffix))
            continue
        for _, _, filenames in os.walk(target):
            count += sum(1 for name in filenames if name.endswith(suffix))
    return count
