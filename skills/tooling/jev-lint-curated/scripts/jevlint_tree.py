"""jev-lint 厳選ラッパの ref 解決、パスの検査、コミットの展開。

`jevlint.py` (入口) の下流モジュールの 1 つ。前半は `--commit` / `--base` を本体の
リポジトリの SHA に固定し、対象のパスがそのコミットに実在するかを確かめる。後半は
そのコミットを checkout を伴わない worktree へ展開する。上流に送ってよいのはコミット
済みの中身だけというユーザー裁定を、手順ではなく構造で守るのがこの後半の役目である。

展開が checkout を使わない理由: `git worktree add` / `checkout` / `archive` は追跡された
`.gitattributes` の `filter=` が選ぶ smudge (git-crypt の平文化、git-lfs の取得) を利用者の
環境の driver で走らせ、symlink を symlink として復元し、hook を起動する (spec の前提 16)。
`worktree add --no-checkout` は worktree を登録するだけでファイルを書かず、index も作らない
(`read-tree` は呼ばない。上流が worktree の中で呼ぶ git はコミット同士の diff だけで、index
を読まない)。ディスクへ書く経路は `git cat-file --batch` が返す blob の生のバイトだけになり、
ディスクの中身がコミットの blob と同一であることが構造で保証される。ただし checkout を
伴わなくても hook は起動する (`worktree add --no-checkout` は `reference-transaction` を
起動する。実測: git 2.55.0) ので、展開の間の git はすべて `_QuietGit` を通し、hook
ディレクトリの hook・設定で定義する hook・fsmonitor の 3 経路を止める。

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


# githooks(5) が列挙する hook の名前。出典はこのマシンの `git help githooks` (git 2.55.0、
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
        return ["git", "-C", str(cwd), *self.flags, *args]

    def run(self, cwd: Path, args: list) -> "subprocess.CompletedProcess[bytes]":
        """終了コードの判定は呼び出し側が行う。"""
        return subprocess.run(self.argv(cwd, args), env=self.env, capture_output=True)

    def popen(self, cwd: Path, args: list) -> "subprocess.Popen[bytes]":
        # stderr は継承する。PIPE にすると読み切るまで子が詰まりうるし、git の診断は
        # 利用者にそのまま見えてよい
        return subprocess.Popen(
            self.argv(cwd, args), stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=self.env
        )


# 展開の外 (ref の解決とパスの検査) だけで使う読み取り専用の git。hook を止めるフラグを
# 持たないので、通せるサブコマンドを hook の event を持たないものに限る。展開の中で
# 誤って使うと ValueError で止まる
_READONLY_SUBCOMMANDS = ("rev-parse", "ls-tree")


def _git_readonly(
    cwd: Path, args: list, env: dict, text: bool = False
) -> "subprocess.CompletedProcess":
    if not args or args[0] not in _READONLY_SUBCOMMANDS:
        raise ValueError(f"展開の外で使える git は {_READONLY_SUBCOMMANDS} のみ: {args!r}")
    return subprocess.run(["git", "-C", str(cwd), *args], env=env, capture_output=True, text=text)


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
    は hook を起動しない (実測: git 2.55.0) が、展開の中の git はすべて `_QuietGit` で呼ぶ。

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


def _reject_sgconfig_in_ancestors(tree: Path) -> None:
    # ast-grep は cwd と親ディレクトリから sgconfig を探して動的ライブラリを読み込む
    # (spec の前提 15)。上流を起動する子プロセスの cwd は解決済みの形 (macOS では
    # `/var` が `/private/var`) になるので、与えられた形と解決した形の両方の祖先を見る
    ancestors = set(tree.parents) | set(tree.resolve().parents)
    for ancestor in ancestors:
        for name in _SGCONFIG_NAMES:
            if (ancestor / name).exists():
                raise TreeError(f"一時ディレクトリの祖先に {name} がある: {ancestor / name}")


def _discard_worktree(git: _QuietGit, root: Path, tree: Path) -> None:
    # 後始末は元の例外を隠さないよう、どの段も失敗を投げない。`worktree remove --force`
    # は worktree の `.git` ファイルが壊れていると終了コード 128 でディレクトリを残す
    # (実測: git 2.55.0) ので、そのときはディレクトリを消してから登録を prune する
    proc = git.run(root, ["worktree", "remove", "--force", str(tree)])
    if proc.returncode != 0:
        shutil.rmtree(tree, ignore_errors=True)
        git.run(root, ["worktree", "prune"])


def _temp_base(env: dict, root: Path) -> Path:
    """一時ディレクトリの置き場を決める。

    `env` の `TMPDIR` が空でない絶対パスならそれ、それ以外は `tempfile.gettempdir()`。
    空や相対の値を `mkdtemp(dir=...)` にそのまま渡すと cwd の下に作られ、3.9 では返る
    パスも相対になる (実測: 3.9.6 は `'jevlint-xxx'`、3.14.7 は cwd を前置した絶対パス)。
    相対のままだと `worktree add` は `-C root` の root から、書き出しは cwd から解決して
    別の場所を指す。

    解決した置き場がリポジトリの root の中なら拒否する。利用者の作業ツリーの中に
    worktree を作ると、走査対象に自分の展開が混ざるため。包含は inode で見る。大文字
    小文字を区別しないファイルシステムでは `resolve()` が与えられた表記の大文字小文字を
    保つので (実測: APFS)、文字列の比較は `.../Repo` と `.../repo/sub` の包含を見落とす。
    """
    candidate = env.get("TMPDIR", "")
    try:
        if candidate and os.path.isabs(candidate):
            base = Path(candidate).resolve()
        else:
            # gettempdir は候補が 1 つも使えないと FileNotFoundError を投げる
            base = Path(tempfile.gettempdir()).resolve()
    except (OSError, RuntimeError) as error:
        # 3.9 の resolve() は symlink のループを RuntimeError にする (実測: 3.9.6。3.14.7 は
        # 投げず、後の mkdtemp が OSError になる)
        raise TreeError(f"一時ディレクトリの置き場を解決できない: {error}") from None
    for ancestor in (base, *base.parents):
        try:
            same = os.path.samefile(ancestor, root)
        except OSError:
            # まだ無いディレクトリ。使えなければ mkdtemp が TreeError にする
            continue
        if same:
            raise TreeError(f"一時ディレクトリの置き場がリポジトリの中にある: {base}")
    return base


@contextlib.contextmanager
def expanded_commit(root: Path, sha: str, env: dict) -> Iterator[Expanded]:
    """`sha` を一時ディレクトリの worktree へ checkout 無しで展開し、抜けるときに消す。

    一時ディレクトリの置き場は `env` の `TMPDIR` から `_temp_base` が決める (`os.environ`
    ではなく。上流に渡す env と同じ値で決まるようにし、テストが置き場を差し替えられる
    ようにするため)。

    worktree は `worktree add --detach --no-checkout` で登録するだけで、index は作らない
    (`read-tree` は呼ばない)。上流が worktree の中で呼ぶ git はコミット同士の
    `git diff <base>...HEAD` だけで (spec の前提 17)、index も作業ツリーも読まない。index の
    無い worktree でのその diff は通常の checkout と同一の出力で、`worktree remove --force`
    も通る (実測: git 2.55.0)。

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
        _reject_sgconfig_in_ancestors(tree)
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
