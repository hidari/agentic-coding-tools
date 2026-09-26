"""jevlint_tree.py (ref の解決、パスの検査、コミットの展開) の仕様。

前半は `repo_root` / `resolve_commit` / `normalize_path` / `path_in_commit` と `TreeError`。
後半は送る範囲の境界そのもので、`validate_tree_paths` / `materialize` / `expanded_commit` /
`signals_as_exceptions` / `count_suffix` が対象。後半の fixture リポジトリには、checkout や
hook を経由すると中身が変わるもの (smudge filter、リポジトリの外を指す symlink、3 つの event
に hook ディレクトリと設定定義の 2 系統で置いた cwd に目印を置く hook) をわざと入れてあり、
展開後のディスクがコミットの blob と同一で、余分なものが無く、リポジトリの root にも何も
足されないことを見る。hook が本当に起動する状態かは陽性対照 (ラッパのフラグ無しで同じ git
操作をする) が系統ごとに確かめる。

git を呼ぶテストはすべて `GIT_*` を落とした `os.environ` の写しを `env` として自前で渡す
(env の組み立ては `jevlint_host.build_env` にあるが、jevlint_tree とこのテストは
jevlint_host に依存しない。依存は入口から下流への一方向)。一時リポジトリごとに
`user.name` / `user.email` / `commit.gpgsign=false` をリポジトリ設定へ入れて、開発機の
グローバルな git 設定 (実名や署名設定) の影響を受けないようにする。

`.GIT` や `A.py`/`a.py` のように、大文字小文字を区別しないファイルシステムでは作業ツリーに
置けない名前の fixture は、`git mktree` と `git commit-tree` で object を直接作る
(実測: git 2.55.0 の `mktree` は `.GIT`、`A.py` と `a.py` の組、`sub/.Git` をどれも拒否しない)。
"""

from __future__ import annotations

import contextlib
import io
import os
import shlex
import shutil
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import jevlint_tree

# `GIT_*` を落とした環境。jevlint_tree とこのテストは jevlint_host に依存しない (依存は
# 入口から下流への一方向) ので、`jevlint_host.build_env` を使わず env を自前で組み立てる。
# git は `commit -a` 等のとき hook へ
# `GIT_INDEX_FILE` を渡すことがあり、継承したままだと fixture ではなく呼び出し元
# リポジトリの index を書き換えうる (scripts/test_check_related_refs.py の実測と同じ理由)。
ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def run(args: list) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(args, env=ENV, capture_output=True, text=True, check=True)


def run_bytes(args: list, stdin: bytes = b"") -> bytes:
    return subprocess.run(args, env=ENV, input=stdin, capture_output=True, check=True).stdout


def worktree_count(repo: Path) -> int:
    """`git worktree list --porcelain` に載る worktree の数 (本体を含む)。"""
    lines = run(["git", "-C", str(repo), "worktree", "list", "--porcelain"]).stdout.splitlines()
    return sum(1 for line in lines if line.startswith("worktree "))


class GitRepo:
    """一時ディレクトリに、架空の identity を持つ git リポジトリを作る。"""

    def __init__(self, test: unittest.TestCase):
        self.path = Path(tempfile.mkdtemp(prefix="jevlint-tree-"))
        test.addCleanup(lambda: shutil.rmtree(self.path, ignore_errors=True))
        run(["git", "init", "-q", str(self.path)])
        # 実名やメールアドレスを書かないため架空の値を使う。開発機の global 設定に
        # 実名が入っていても、リポジトリ local の設定が優先されて上書きする
        self.config("user.name", "jevlint tree test")
        self.config("user.email", "jevlint-tree-test@example.invalid")
        # 開発機の global 設定が commit.gpgsign=true だと、鍵の無い CI で commit が
        # 失敗しうる。リポジトリ local で明示的に無効化して切り離す
        self.config("commit.gpgsign", "false")

    def git(self, *args: str) -> str:
        return run(["git", "-C", str(self.path), *args]).stdout

    def config(self, key: str, value: str) -> None:
        self.git("config", key, value)

    def write(self, rel: str, text: str = "content\n") -> str:
        target = self.path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return rel

    def commit(self, message: str = "commit") -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.head_sha()

    def head_sha(self) -> str:
        return self.git("rev-parse", "HEAD").strip()

    def blob_bytes(self, sha: str, path: str) -> bytes:
        """コミットされた中身の基準。`refs/replace` の差し替えを追わない形で読む。

        素の `git cat-file blob` は差し替え後の object を返すので (実測: git 2.55.0)、
        それを基準にすると差し替えられた中身を書いても比較が通ってしまう。
        """
        return run_bytes(
            ["git", "-C", str(self.path), "--no-replace-objects", "cat-file", "blob", f"{sha}:{path}"]
        )

    def hash_blob(self, data: bytes) -> str:
        return run_bytes(
            ["git", "-C", str(self.path), "hash-object", "-w", "--stdin"], stdin=data
        ).decode("ascii").strip()

    def mktree(self, entries: list, missing: bool = False) -> str:
        """`(mode, type, sha, name)` の列から tree object を作り、その SHA を返す。

        作業ツリーを経由しないので、大文字小文字を区別しないファイルシステムでは
        作れない名前の組 (`A.py` と `a.py`) や `.GIT` を持つ tree もここで作れる。
        `missing=True` は object store に無い SHA を指す項目を許す (partial clone の再現)。
        """
        lines = "".join(f"{mode} {otype} {sha}\t{name}\n" for mode, otype, sha, name in entries)
        args = ["git", "-C", str(self.path), "mktree"] + (["--missing"] if missing else [])
        return run_bytes(args, stdin=lines.encode("utf-8")).decode("ascii").strip()

    def commit_tree(self, tree_sha: str) -> str:
        return self.git("commit-tree", tree_sha, "-m", "synthetic").strip()

    def worktree_count(self) -> int:
        return worktree_count(self.path)

    def knows_config_hooks(self) -> "tuple[bool, str, str]":
        """この git が設定で定義する hook (`hook.<name>.command` / `.event`) を知っているか。

        `git hook list <event>` は、その event に結び付いた hook の名前を 1 行ずつ出す
        (実測: git 2.55.0。`-c hook.probe.command=true -c hook.probe.event=<event>` を
        付けると `probe` の行が出る)。知らない git では `hook` サブコマンド自体が無いか、
        設定の hook が列挙されない。判定、生の stdout、失敗時に読む根拠の文字列を返す
        """
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(self.path),
                "-c",
                "hook.probe.command=true",
                "-c",
                "hook.probe.event=post-index-change",
                "hook",
                "list",
                "post-index-change",
            ],
            env=ENV,
            capture_output=True,
            text=True,
        )
        evidence = f"rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}"
        knows = proc.returncode == 0 and "probe" in proc.stdout.split()
        return knows, proc.stdout, evidence


class NormalizePathTests(unittest.TestCase):
    def test_dot_and_empty_forms_normalize_to_root(self):
        for text in (".", "./", ""):
            with self.subTest(text=text):
                self.assertEqual("", jevlint_tree.normalize_path(text))

    def test_collapses_leading_dot_slash_doubled_slash_and_trailing_slash(self):
        self.assertEqual("a/b", jevlint_tree.normalize_path("./a//b/"))

    def test_plain_relative_path_is_unchanged(self):
        self.assertEqual("a/b", jevlint_tree.normalize_path("a/b"))

    def test_rejects_absolute_and_dotdot_and_dash(self):
        for text in ("/abs", "../x", "a/../b", "a/..", "-x"):
            with self.subTest(text=text):
                with self.assertRaises(jevlint_tree.TreeError):
                    jevlint_tree.normalize_path(text)

    def test_rejects_a_dash_prefix_that_only_appears_after_normalization(self):
        # `-` の判定は正規化前の生の文字列ではなく、正規化した結果に対して行う必要が
        # ある。生の文字列だけを見ると `./-x` は先頭が `.` なので素通りしてしまうが、
        # 正規化後は `-x` になり、呼び出し側 (`jevlint.py` の argv の組み立て) がそのまま
        # 渡すとオプションと誤認されうる
        for text in ("./-x", ".//-x"):
            with self.subTest(text=text):
                with self.assertRaises(jevlint_tree.TreeError):
                    jevlint_tree.normalize_path(text)

    def test_dash_in_a_non_leading_component_is_accepted(self):
        # 正規化後の文字列全体の先頭だけを見ることを確かめる対照。途中の成分が
        # `-` から始まっても、argv の 1 トークンとしての先頭文字は `a` のままなので
        # 拒否する理由が無い
        self.assertEqual("a/-x", jevlint_tree.normalize_path("a/-x"))

    def test_accepts_a_name_that_merely_contains_dotdot_as_a_substring(self):
        # 成分単位で判定することを確かめる。文字列全体を部分文字列として `..` を
        # 判定すると、`a..b.py` のような通常のファイル名まで拒否してしまう
        self.assertEqual("a..b.py", jevlint_tree.normalize_path("a..b.py"))

    def test_accepts_non_ascii_names(self):
        self.assertEqual(
            "日本語 ファイル.py", jevlint_tree.normalize_path("日本語 ファイル.py")
        )


class ResolveCommitTests(unittest.TestCase):
    def setUp(self):
        self.repo = GitRepo(self)
        self.repo.write("a.txt")
        self.sha = self.repo.commit()
        run(["git", "-C", str(self.repo.path), "tag", "mytag"])

    def test_head_resolves_to_a_sha_of_40_or_64_hex_digits(self):
        sha = jevlint_tree.resolve_commit(self.repo.path, "HEAD", ENV)
        self.assertRegex(sha, r"\A(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
        self.assertEqual(self.sha, sha)

    def test_tag_name_resolves(self):
        self.assertEqual(self.sha, jevlint_tree.resolve_commit(self.repo.path, "mytag", ENV))

    def test_short_sha_resolves(self):
        short = self.sha[:7]
        self.assertEqual(self.sha, jevlint_tree.resolve_commit(self.repo.path, short, ENV))

    def test_rejects_unknown_ref(self):
        with self.assertRaises(jevlint_tree.TreeError):
            jevlint_tree.resolve_commit(self.repo.path, "no-such-ref", ENV)

    def test_rejects_option_like_ref_all(self):
        with self.assertRaises(jevlint_tree.TreeError):
            jevlint_tree.resolve_commit(self.repo.path, "--all", ENV)

    def test_rejects_option_like_ref_h(self):
        with self.assertRaises(jevlint_tree.TreeError):
            jevlint_tree.resolve_commit(self.repo.path, "-h", ENV)

    def test_rejects_a_blob_sha_because_it_is_not_a_commit(self):
        blob = run(
            [
                "git",
                "-C",
                str(self.repo.path),
                "hash-object",
                "-w",
                str(self.repo.path / "a.txt"),
            ]
        ).stdout.strip()
        with self.assertRaises(jevlint_tree.TreeError):
            jevlint_tree.resolve_commit(self.repo.path, blob, ENV)

    def test_dash_prefixed_ref_is_rejected_without_invoking_git(self):
        # git 自身も `--all^{commit}` のような形を非 0 で終わらせうるので、TreeError が
        # 送出されたことだけを見ると「`-` の拒否を外す」変異でも赤にならないことがある
        # (git の挙動次第で偶然 TreeError になり、送出元を区別できていないため)。
        # ここでは subprocess.run が一度も呼ばれていないことまで確かめて送出元を区別する
        with mock.patch("jevlint_tree.subprocess.run") as run_mock:
            with self.assertRaises(jevlint_tree.TreeError):
                jevlint_tree.resolve_commit(self.repo.path, "--all", ENV)
            run_mock.assert_not_called()


class ResolveCommitEmptyRepositoryTests(unittest.TestCase):
    def test_head_in_a_repository_with_no_commits_names_head(self):
        repo = GitRepo(self)
        with self.assertRaises(jevlint_tree.TreeError) as cm:
            jevlint_tree.resolve_commit(repo.path, "HEAD", ENV)
        self.assertIn("HEAD", str(cm.exception))


class ResolveCommitShallowCloneTests(unittest.TestCase):
    def test_a_commit_missing_from_a_shallow_clone_names_its_sha(self):
        origin = GitRepo(self)
        origin.write("a.txt", "one\n")
        first_sha = origin.commit("first")
        origin.write("a.txt", "one\ntwo\n")
        origin.commit("second")

        clone_dir = Path(tempfile.mkdtemp(prefix="jevlint-tree-clone-"))
        self.addCleanup(lambda: shutil.rmtree(clone_dir, ignore_errors=True))
        # ローカルパスへの clone は `--depth` を無視する (git の警告で実測)。実際に
        # shallow にするには file:// スキームを使う必要がある
        run(
            [
                "git",
                "clone",
                "--quiet",
                "--depth",
                "1",
                f"file://{origin.path}",
                str(clone_dir),
            ]
        )

        with self.assertRaises(jevlint_tree.TreeError) as cm:
            jevlint_tree.resolve_commit(clone_dir, first_sha, ENV)
        self.assertIn(first_sha, str(cm.exception))


class PathInCommitTests(unittest.TestCase):
    def setUp(self):
        self.repo = GitRepo(self)
        self.repo.write("root.txt")
        self.repo.write("sub/file.txt")
        self.repo.write("日本語 ファイル.py")
        self.sha = self.repo.commit()

    def test_empty_path_is_always_true(self):
        self.assertTrue(jevlint_tree.path_in_commit(self.repo.path, self.sha, "", ENV))

    def test_tracked_file_is_true(self):
        self.assertTrue(
            jevlint_tree.path_in_commit(self.repo.path, self.sha, "root.txt", ENV)
        )

    def test_tracked_directory_is_true(self):
        self.assertTrue(jevlint_tree.path_in_commit(self.repo.path, self.sha, "sub", ENV))

    def test_untracked_file_is_false(self):
        self.repo.write("untracked.txt")  # write のみでコミットしない
        self.assertFalse(
            jevlint_tree.path_in_commit(self.repo.path, self.sha, "untracked.txt", ENV)
        )

    def test_nonexistent_name_is_false(self):
        self.assertFalse(
            jevlint_tree.path_in_commit(self.repo.path, self.sha, "nope.txt", ENV)
        )

    def test_non_ascii_tracked_file_is_true(self):
        self.assertTrue(
            jevlint_tree.path_in_commit(
                self.repo.path, self.sha, "日本語 ファイル.py", ENV
            )
        )

    def test_git_call_failure_raises_instead_of_returning_false(self):
        # git ls-tree の呼び出し自体が失敗するケース (実在しない sha、partial clone で
        # tree object が未取得、object store の破損等) を、`False` (= 「パスが
        # コミットに無い」) へ吸収してはいけない。実測: 40 桁 hex として well-formed
        # だが実在しない sha を渡すと `git ls-tree` は終了コード 128・`fatal: not a
        # tree object` で失敗する。これは「パスが無い」(終了コード 0・出力空) とは
        # 別の failure mode であり、区別せず False を返すと、呼び出し側 (`jevlint.py` の
        # パスの検査) で実際の git 障害が「対象パスがコミットに存在しない」という誤った
        # メッセージに化ける
        missing_sha = "deadbeef" * 5
        with self.assertRaises(jevlint_tree.TreeError) as cm:
            jevlint_tree.path_in_commit(self.repo.path, missing_sha, "root.txt", ENV)
        message = str(cm.exception)
        # メッセージは git 呼び出しの失敗そのものを名指す。「パスが無い」場合は
        # そもそも例外を投げず False を返すだけなので、経路も文言も区別できる
        self.assertIn(missing_sha, message)
        self.assertIn("root.txt", message)
        self.assertFalse(
            jevlint_tree.path_in_commit(self.repo.path, self.sha, "nope.txt", ENV),
            "パス不在の経路が例外を投げるようになっている",
        )


class RepoRootTests(unittest.TestCase):
    def test_subdirectory_cwd_still_resolves_the_root(self):
        repo = GitRepo(self)
        repo.write("sub/deep/file.txt")
        repo.commit()
        subdir = repo.path / "sub" / "deep"
        result = jevlint_tree.repo_root(subdir, ENV)
        self.assertTrue(result.samefile(repo.path))


class ReadonlyGitTests(unittest.TestCase):
    def test_refuses_subcommands_outside_the_readonly_allowlist(self):
        # 展開の外で使う runner は hook を止める `-c` を持たない。持つのは `--no-lazy-fetch`
        # と `--no-replace-objects` で、allow-list の `rev-parse` / `ls-tree` はそれと
        # 組み合わせてローカルの object を読むだけになる (lazy fetch が無ければ外部
        # コマンドも ref の更新も無い。PartialCloneTests の tree:0 のテストが、uploadpack の
        # 目印が付かないことと pack の数で実測し、reference-transaction の目印が付かない
        # ことも見る。後者は 2.55.0 でだけ lazy fetch が起動する hook で、版に依る)。
        # 誤って展開の中の操作に使われないよう、それ以外のサブコマンドを拒む
        repo = GitRepo(self)
        repo.write("a.py")
        sha = repo.commit()
        for args in (["read-tree", sha], ["worktree", "prune"], ["checkout", sha], []):
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    jevlint_tree._git_readonly(repo.path, args, ENV)
        self.assertEqual(0, jevlint_tree._git_readonly(repo.path, ["rev-parse", "HEAD"], ENV).returncode)


class ValidateTreePathsTests(unittest.TestCase):
    def test_rejects_absolute_dotdot_and_any_case_of_dot_git_component(self):
        for path in ("/abs", "a/../b", ".GIT/config", "sub/.Git", ".git"):
            with self.subTest(path=path):
                with self.assertRaises(jevlint_tree.TreeError):
                    jevlint_tree.validate_tree_paths([path])

    def test_rejects_two_paths_that_differ_only_in_case(self):
        with self.assertRaises(jevlint_tree.TreeError):
            jevlint_tree.validate_tree_paths(["A.py", "a.py"])

    def test_rejects_a_file_whose_name_collides_with_a_directory_by_case(self):
        # `A` (ファイル) と `a/b` (ディレクトリ `a` の下) は「大文字小文字だけが違う
        # 2 つのパス」ではないが、区別しないファイルシステムでは `a` を mkdir する
        # 時点で `A` と衝突して書き出しが途中で止まる。成分単位で同じ検査に含める
        for paths in (["A", "a/b"], ["a/b", "A"]):
            with self.subTest(paths=paths):
                with self.assertRaises(jevlint_tree.TreeError):
                    jevlint_tree.validate_tree_paths(paths)

    def test_accepts_dotdot_substring_dot_github_dot_gitignore_and_case_variants_in_different_dirs(self):
        jevlint_tree.validate_tree_paths(
            ["a..b.py", ".github/x", ".gitignore", "x/A.py", "y/a.py", "日本語 ファイル.py"]
        )

    def test_accepts_an_empty_tree(self):
        jevlint_tree.validate_tree_paths([])


FIVE_MB = 5 * 1024 * 1024

# 40 桁の hex として well-formed だが object store に無い SHA。partial clone で blob だけが
# 未取得の状態を `git mktree --missing` で再現するのに使う
MISSING_SHA = "0123456789abcdef0123456789abcdef01234567"

# 展開の間に利用者の hook が走ったことを示す目印。hook は自分の cwd に置くので、cwd が
# worktree なら worktree の中に、リポジトリの root なら root に現れる。checkout 無しの
# 操作でも hook は起動する: `worktree add --no-checkout` は reference-transaction を
# (cwd = root)、`read-tree` は post-index-change を (cwd = worktree) 起動する (実測:
# git 2.55.0)。post-checkout は checkout が走ったときだけ、cwd = 新しい worktree で起動する。
# hook の置き方は 2 系統ある。hook ディレクトリのファイル (`core.hooksPath` で止まる) と、
# 設定で定義する hook (`hook.<name>.command` + `hook.<name>.event`。`core.hooksPath` では
# 止まらず、`hook.<event>.enabled=false` で止まる。逆にこのフラグは hook ディレクトリの
# hook を止めない。実測: git 2.55.0)。fixture は両系統を同じ event に置く
HOOK_NAMES = ("post-checkout", "post-index-change", "reference-transaction")


def injected_marker(hook: str) -> str:
    return f"INJECTED-{hook}.txt"


def config_marker(hook: str) -> str:
    return f"INJECTED-config-{hook}.txt"


def tree_entries(tree: Path) -> list:
    """`tree` の下にある項目 (ファイルとディレクトリ、ドット始まりを含む) の相対パス。"""
    return sorted(str(p.relative_to(tree)) for p in tree.rglob("*"))


class ExpansionFixture:
    """checkout や hook を経由すると中身が変わるものを揃えたコミット。

    filter / symlink / hook のどれも、`materialize` が blob の生のバイトを書き、git の
    呼び出しが hook を止めている限りはディスクに影響しない。逆に `worktree add` に
    checkout させると smudge が走り (filtered.txt が大文字になる)、symlink が symlink
    として復元され、post-checkout の目印が書かれる (実測: git 2.55.0)。
    """

    def __init__(self, test: unittest.TestCase):
        self.repo = GitRepo(test)
        root = self.repo.path
        self.repo.write("plain.py", "print('hi')\n")
        self.repo.write("run.sh", "#!/bin/sh\n")
        (root / "run.sh").chmod(0o755)
        self.repo.write("日本語 ファイル.py", "x = 1\n")
        # リポジトリの外を指す symlink。絶対パスの literal を fixture に書かないよう、
        # 相対で 1 つ上 (= 一時ディレクトリの中) を指す
        os.symlink("../outside.txt", root / "link")
        self.repo.write("filtered.txt", "hello lower\n")
        self.repo.write(".gitattributes", "filtered.txt filter=up\n")
        self.repo.write("sgconfig.yml", "ruleDirs: []\n")
        (root / "big.bin").write_bytes(bytes(range(256)) * (FIVE_MB // 256))
        self.repo.config("filter.up.smudge", "tr a-z A-Z")
        self.repo.config("filter.up.clean", "cat")
        self.sha = self.repo.commit()
        # hook はコミットの後に置く。`git add` は post-index-change を、`git commit` は
        # reference-transaction を起動するので、先に置くと fixture の組み立てで目印が付く。
        # `core.hooksPath` を絶対パスで local 設定に入れるのは、開発機の global 設定に
        # `core.hooksPath` があると `.git/hooks` が読まれず、hook のテストが何も見ずに
        # 通るため (local は global より優先され、ラッパの `-c` は local より優先される。
        # 実測)。hook が本当に起動する状態かは陽性対照のテストが確かめる
        self.hooks_dir = root / ".git" / "hooks"
        self.hooks_dir.mkdir(exist_ok=True)
        for hook in HOOK_NAMES:
            targets = (
                (self.hooks_dir / hook, injected_marker(hook)),
                (self.hooks_dir / f"config-{hook}", config_marker(hook)),
            )
            for script, marker in targets:
                script.write_text(f'#!/bin/sh\ntouch "$PWD/{marker}"\n', encoding="utf-8")
                script.chmod(0o755)
            # 設定の hook の command には hook の引数がそのまま付く (post-index-change なら
            # `1 0`、reference-transaction なら `prepared` 等)。`touch "$PWD/..."` を直接
            # 書くと引数の名前のファイルまで作る (実測) ので、引数を読まないスクリプトを指す
            self.repo.config(f"hook.config-{hook}.command", str(self.hooks_dir / f"config-{hook}"))
            self.repo.config(f"hook.config-{hook}.event", hook)
        self.repo.config("core.hooksPath", str(self.hooks_dir))
        self.paths = [
            ".gitattributes",
            "big.bin",
            "filtered.txt",
            "link",
            "plain.py",
            "run.sh",
            "sgconfig.yml",
            "日本語 ファイル.py",
        ]

    def expected_tree_entries(self) -> list:
        """展開後の worktree にあるべき項目。コミットのパスから sgconfig を除き `.git` を足す。"""
        entries = {".git"}
        for path in self.paths:
            if path == "sgconfig.yml":
                continue
            parts = path.split("/")
            for depth in range(1, len(parts) + 1):
                entries.add("/".join(parts[:depth]))
        return sorted(entries)


class MaterializeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ExpansionFixture(self)
        self.repo = self.fixture.repo
        self.dest = Path(tempfile.mkdtemp(prefix="jevlint-dest-"))
        self.addCleanup(lambda: shutil.rmtree(self.dest, ignore_errors=True))
        # `core.hooksPath` に渡す空のディレクトリ。materialize も git を呼ぶので必須
        self.hooks = Path(tempfile.mkdtemp(prefix="jevlint-hooks-"))
        self.addCleanup(lambda: shutil.rmtree(self.hooks, ignore_errors=True))

    def test_every_written_file_equals_the_blob_and_the_count_is_returned(self):
        count = jevlint_tree.materialize(self.repo.path, self.fixture.sha, self.dest, ENV, self.hooks)
        self.assertEqual(len(self.fixture.paths), count)
        for path in self.fixture.paths:
            with self.subTest(path=path):
                self.assertEqual(
                    self.repo.blob_bytes(self.fixture.sha, path),
                    (self.dest / path).read_bytes(),
                )
        written = sorted(str(p.relative_to(self.dest)) for p in self.dest.rglob("*"))
        self.assertEqual(sorted(self.fixture.paths), written)

    def test_symlink_becomes_a_regular_file_holding_the_target_string(self):
        jevlint_tree.materialize(self.repo.path, self.fixture.sha, self.dest, ENV, self.hooks)
        link = self.dest / "link"
        self.assertFalse(link.is_symlink())
        self.assertTrue(link.is_file())
        self.assertEqual(b"../outside.txt", link.read_bytes())

    def test_filtered_file_is_not_smudged(self):
        jevlint_tree.materialize(self.repo.path, self.fixture.sha, self.dest, ENV, self.hooks)
        self.assertEqual(b"hello lower\n", (self.dest / "filtered.txt").read_bytes())

    def test_executable_bit_follows_mode_100755(self):
        jevlint_tree.materialize(self.repo.path, self.fixture.sha, self.dest, ENV, self.hooks)
        self.assertTrue(os.access(self.dest / "run.sh", os.X_OK))
        self.assertFalse(os.access(self.dest / "plain.py", os.X_OK))

    def test_five_megabyte_blob_is_written_byte_for_byte(self):
        jevlint_tree.materialize(self.repo.path, self.fixture.sha, self.dest, ENV, self.hooks)
        data = (self.dest / "big.bin").read_bytes()
        self.assertEqual(FIVE_MB, len(data))
        self.assertEqual(bytes(range(256)) * (FIVE_MB // 256), data)

    def test_submodule_entry_is_not_written_and_not_counted(self):
        blob = self.repo.hash_blob(b"x\n")
        tree = self.repo.mktree(
            [("100644", "blob", blob, "file.txt"), ("160000", "commit", self.fixture.sha, "vendor")]
        )
        sha = self.repo.commit_tree(tree)
        count = jevlint_tree.materialize(self.repo.path, sha, self.dest, ENV, self.hooks)
        self.assertEqual(1, count)
        self.assertEqual(["file.txt"], [p.name for p in self.dest.iterdir()])

    def test_dot_git_component_in_any_case_rejects_before_writing_anything(self):
        # `a.txt` は `sub/.Git` より前に並ぶ (ls-tree はバイト順)。書きながら検査する
        # 実装だと `a.txt` が先に書かれてしまうので、dest が空であることが「全件を
        # 検査してから書き始める」ことの pin になる
        blob = self.repo.hash_blob(b"x\n")
        sub = self.repo.mktree([("100644", "blob", blob, ".Git")])
        tree = self.repo.mktree([("100644", "blob", blob, "a.txt"), ("040000", "tree", sub, "sub")])
        sha = self.repo.commit_tree(tree)
        with self.assertRaises(jevlint_tree.TreeError):
            jevlint_tree.materialize(self.repo.path, sha, self.dest, ENV, self.hooks)
        self.assertEqual([], list(self.dest.iterdir()))

    def test_case_only_duplicate_names_reject_before_writing_anything(self):
        blob = self.repo.hash_blob(b"x\n")
        tree = self.repo.mktree(
            [("100644", "blob", blob, "0.py"), ("100644", "blob", blob, "A.py"), ("100644", "blob", blob, "a.py")]
        )
        sha = self.repo.commit_tree(tree)
        with self.assertRaises(jevlint_tree.TreeError):
            jevlint_tree.materialize(self.repo.path, sha, self.dest, ENV, self.hooks)
        self.assertEqual([], list(self.dest.iterdir()))

    def test_git_failure_is_a_tree_error(self):
        with self.assertRaises(jevlint_tree.TreeError):
            jevlint_tree.materialize(self.repo.path, "deadbeef" * 5, self.dest, ENV, self.hooks)
        self.assertEqual([], list(self.dest.iterdir()))

    def test_missing_blob_is_a_tree_error(self):
        # object store に無い blob を `mktree --missing` でローカルに再現する。`ls-tree -r`
        # は終了コード 0 で項目を返し、`cat-file --batch` が `<sha> missing` を返す
        # (実測)。本物の partial clone で同じ経路を通ることは PartialCloneTests が見る
        # (そちらは lazy fetch を止めなければ `missing` にならず取りに行く)。
        # 無い blob を先頭に置き、何も書かれないことまで見る
        blob = self.repo.hash_blob(b"x\n")
        tree = self.repo.mktree(
            [("100644", "blob", MISSING_SHA, "0.txt"), ("100644", "blob", blob, "a.txt")],
            missing=True,
        )
        sha = self.repo.commit_tree(tree)
        with self.assertRaises(jevlint_tree.TreeError) as cm:
            jevlint_tree.materialize(self.repo.path, sha, self.dest, ENV, self.hooks)
        self.assertIn(MISSING_SHA, str(cm.exception))
        self.assertEqual([], list(self.dest.iterdir()))

    def test_a_path_that_already_exists_in_dest_is_a_tree_error(self):
        # `O_EXCL` の失敗を素の `FileExistsError` で抜けさせると Python は 1 で終わり、
        # ラッパの「1 = finding あり」と衝突する
        (self.dest / "plain.py").write_text("stale\n", encoding="utf-8")
        with self.assertRaises(jevlint_tree.TreeError) as cm:
            jevlint_tree.materialize(self.repo.path, self.fixture.sha, self.dest, ENV, self.hooks)
        self.assertIn("plain.py", str(cm.exception))


class ExpandedCommitTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ExpansionFixture(self)
        self.repo = self.fixture.repo
        self.sha = self.fixture.sha
        self.assertEqual(1, self.repo.worktree_count(), "fixture の worktree は本体だけ")
        # 展開は sgconfig を消したことを stderr へ告げる。テストの出力に混ぜず、
        # 告げた内容を見るテストがここから読めるようにする
        self.stderr = io.StringIO()
        redirect = contextlib.redirect_stderr(self.stderr)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)
        # 展開がリポジトリの root に何も足さないこと (hook の目印を含む) の対照
        self.root_listing = sorted(os.listdir(self.repo.path))

    def _assert_torn_down(self, tmp: Path) -> None:
        self.assertFalse(tmp.exists(), f"一時ディレクトリが残っている: {tmp}")
        self.assertEqual(1, self.repo.worktree_count(), "worktree の登録が残っている")

    def _private_base(self) -> "tuple[Path, dict]":
        """展開の置き場を専用のディレクトリにした env。抜けた後に空であることを見るため。"""
        base = Path(tempfile.mkdtemp(prefix="jevlint-base-"))
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        return base, dict(ENV, TMPDIR=str(base))

    def _assert_torn_down_into(self, base: Path) -> None:
        self.assertEqual([], os.listdir(base), "一時ディレクトリが残っている")
        self.assertEqual(1, self.repo.worktree_count(), "worktree の登録が残っている")

    def test_tree_holds_raw_blobs_and_scratch_is_a_sibling_outside_the_tree(self):
        with jevlint_tree.expanded_commit(self.repo.path, self.sha, ENV) as expanded:
            self.assertEqual(2, self.repo.worktree_count())
            self.assertTrue((expanded.tree / ".git").is_file())
            self.assertTrue(expanded.scratch.is_dir())
            self.assertEqual(expanded.tree.parent, expanded.scratch.parent)
            self.assertNotEqual(expanded.tree, expanded.scratch)
            for path in self.fixture.paths:
                if path == "sgconfig.yml":
                    continue
                with self.subTest(path=path):
                    self.assertEqual(
                        self.repo.blob_bytes(self.sha, path),
                        (expanded.tree / path).read_bytes(),
                    )
            link = expanded.tree / "link"
            self.assertFalse(link.is_symlink())
            self.assertEqual(b"../outside.txt", link.read_bytes())
            self.assertEqual(b"hello lower\n", (expanded.tree / "filtered.txt").read_bytes())
            self.assertEqual(FIVE_MB, (expanded.tree / "big.bin").stat().st_size)

    def test_sgconfig_yml_is_removed_and_announced_in_one_stderr_line(self):
        with jevlint_tree.expanded_commit(self.repo.path, self.sha, ENV) as expanded:
            self.assertFalse((expanded.tree / "sgconfig.yml").exists())
        lines = self.stderr.getvalue().splitlines()
        self.assertEqual(1, len(lines), lines)
        self.assertIn("sgconfig.yml", lines[0])

    def test_sgconfig_yaml_is_removed_too(self):
        repo = GitRepo(self)
        repo.write("sgconfig.yaml", "ruleDirs: []\n")
        repo.write("a.py")
        sha = repo.commit()
        with jevlint_tree.expanded_commit(repo.path, sha, ENV) as expanded:
            self.assertFalse((expanded.tree / "sgconfig.yaml").exists())
            self.assertTrue((expanded.tree / "a.py").is_file())
        lines = self.stderr.getvalue().splitlines()
        self.assertEqual(1, len(lines), lines)
        self.assertIn("sgconfig.yaml", lines[0])

    def test_nothing_is_announced_when_there_is_no_sgconfig(self):
        repo = GitRepo(self)
        repo.write("a.py")
        sha = repo.commit()
        with jevlint_tree.expanded_commit(repo.path, sha, ENV):
            pass
        self.assertEqual("", self.stderr.getvalue())

    def test_worktree_holds_exactly_the_commit_paths_minus_sgconfig_plus_dot_git(self):
        # 「余分なものが無い」まで見る。cwd を worktree にして起動する hook
        # (read-tree の post-index-change、checkout の post-checkout) の目印はここに現れる
        with jevlint_tree.expanded_commit(self.repo.path, self.sha, ENV) as expanded:
            self.assertEqual(self.fixture.expected_tree_entries(), tree_entries(expanded.tree))

    def test_repository_root_is_untouched_by_the_expansion(self):
        # cwd をリポジトリの root にして起動する hook (worktree add の
        # reference-transaction) の目印はここに現れる
        with jevlint_tree.expanded_commit(self.repo.path, self.sha, ENV):
            pass
        self.assertEqual(self.root_listing, sorted(os.listdir(self.repo.path)))

    def test_hooks_do_fire_when_git_runs_without_the_wrappers_hooks_path(self):
        # 陽性対照。fixture の hook が本当に起動する状態でなければ、上の 2 テストは
        # 何も見ずに緑になる。ラッパの `-c core.hooksPath` を付けずに同じ操作をすると
        # 目印が付くことを、skip ではなく失敗で確かめる
        self.repo.git("read-tree", self.sha)
        self.assertIn(injected_marker("post-index-change"), os.listdir(self.repo.path))
        control = Path(tempfile.mkdtemp(prefix="jevlint-control-")) / "wt"
        self.addCleanup(lambda: shutil.rmtree(control.parent, ignore_errors=True))
        self.repo.git("worktree", "add", "--detach", str(control), self.sha)
        self.assertIn(injected_marker("reference-transaction"), os.listdir(self.repo.path))
        self.assertIn(injected_marker("post-checkout"), os.listdir(control))
        self.repo.git("worktree", "remove", "--force", str(control))

    def test_config_hooks_fire_without_the_wrappers_flags_or_this_git_cannot_run_them(self):
        # 設定で定義する hook の陽性対照。この git がそれを知っているかを先に
        # `git hook list` で判定し、知っていれば目印が付くことを、知らなければ判定の
        # 根拠 (列挙に `probe` が無い) と目印が付かないことを、どちらも失敗で確かめる。
        # skip にはしない (runner は skip を赤にし、CI の git の版は分からない)
        knows, listed, evidence = self.repo.knows_config_hooks()
        self.repo.git("read-tree", self.sha)
        control = Path(tempfile.mkdtemp(prefix="jevlint-control-")) / "wt"
        self.addCleanup(lambda: shutil.rmtree(control.parent, ignore_errors=True))
        self.repo.git("worktree", "add", "--detach", str(control), self.sha)
        in_root = os.listdir(self.repo.path)
        in_control = os.listdir(control)
        self.repo.git("worktree", "remove", "--force", str(control))
        if knows:
            self.assertIn(config_marker("post-index-change"), in_root, evidence)
            self.assertIn(config_marker("reference-transaction"), in_root, evidence)
            self.assertIn(config_marker("post-checkout"), in_control, evidence)
        else:
            # 判定の根拠そのものを見る: 列挙に `probe` が無い (生の stdout を split する。
            # 根拠の文字列は repr で包んであり、そちらを split しても token にならない)
            self.assertNotIn("probe", listed.split(), evidence)
            for name in (config_marker("post-index-change"), config_marker("reference-transaction")):
                self.assertNotIn(name, in_root, f"設定の hook を知らないはずの git が起動した: {evidence}")
            self.assertNotIn(config_marker("post-checkout"), in_control, evidence)

    def test_commit_to_commit_diff_in_the_expanded_tree_matches_the_repository(self):
        # 上流が worktree の中で呼ぶ git は `git diff <base>...HEAD` だけ。
        # 展開は read-tree を使わず worktree に index を持たせないが、コミット同士の diff
        # は index も作業ツリーも読まないので、リポジトリ本体で取った diff と一致する
        base_sha = self.sha
        self.repo.write("plain.py", "print('changed')\n")
        self.repo.write("added.py", "y = 2\n")
        head_sha = self.repo.commit("second")
        args = [
            "diff",
            "--unified=0",
            "--no-color",
            "--no-ext-diff",
            "--diff-filter=d",
            "--src-prefix=a/",
            "--dst-prefix=b/",
            f"{base_sha}...HEAD",
        ]
        expected = self.repo.git(*args)
        self.assertIn("plain.py", expected)
        self.assertIn("added.py", expected)
        with jevlint_tree.expanded_commit(self.repo.path, head_sha, ENV) as expanded:
            self.assertFalse(
                (self.repo.path / ".git" / "worktrees" / expanded.tree.name / "index").exists(),
                "展開した worktree に index がある",
            )
            actual = run(["git", "-C", str(expanded.tree), *args]).stdout
        self.assertEqual(expected, actual)

    def test_normal_exit_removes_the_worktree_and_the_temp_dir(self):
        with jevlint_tree.expanded_commit(self.repo.path, self.sha, ENV) as expanded:
            tmp = expanded.tree.parent
        self._assert_torn_down(tmp)

    def test_exception_inside_still_tears_down(self):
        with self.assertRaises(RuntimeError):
            with jevlint_tree.expanded_commit(self.repo.path, self.sha, ENV) as expanded:
                tmp = expanded.tree.parent
                raise RuntimeError("inside")
        self._assert_torn_down(tmp)

    def test_keyboard_interrupt_inside_still_tears_down(self):
        with self.assertRaises(KeyboardInterrupt):
            with jevlint_tree.expanded_commit(self.repo.path, self.sha, ENV) as expanded:
                tmp = expanded.tree.parent
                raise KeyboardInterrupt
        self._assert_torn_down(tmp)

    def test_sigterm_inside_signals_as_exceptions_still_tears_down(self):
        previous = signal.getsignal(signal.SIGTERM)
        with self.assertRaises(jevlint_tree.SignalInterrupt):
            with jevlint_tree.signals_as_exceptions():
                with jevlint_tree.expanded_commit(self.repo.path, self.sha, ENV) as expanded:
                    tmp = expanded.tree.parent
                    # ハンドラが入っていないまま送るとテストの実行プロセスごと落ちる。
                    # 送る前に差し替わっていることを確かめ、確かめられなければ送らない
                    self.assertIsNot(previous, signal.getsignal(signal.SIGTERM))
                    self.assertTrue(callable(signal.getsignal(signal.SIGTERM)))
                    os.kill(os.getpid(), signal.SIGTERM)
                    self.fail("SIGTERM が例外として届いていない")
        self.assertIs(previous, signal.getsignal(signal.SIGTERM))
        self._assert_torn_down(tmp)

    def test_falls_back_to_rmtree_and_prune_when_worktree_remove_fails(self):
        # worktree の `.git` (gitdir を指すファイル) を壊すと `git worktree remove --force`
        # は「not a .git file」で終了コード 128 になり、ディレクトリを残す (実測:
        # git 2.55.0)。この状態から抜けても、一時ディレクトリも登録も残らないこと
        with jevlint_tree.expanded_commit(self.repo.path, self.sha, ENV) as expanded:
            tmp = expanded.tree.parent
            (expanded.tree / ".git").write_text("garbage\n", encoding="utf-8")
        self._assert_torn_down(tmp)
        # prune で登録が消えたので、残った登録を告げる行は出ない
        self.assertNotIn("git worktree prune", self.stderr.getvalue())

    def test_rejects_when_an_ancestor_of_the_tree_holds_sgconfig(self):
        # ast-grep は cwd の祖先も探すので、一時ディレクトリの置き場そのものが
        # 汚染されていたら展開しても安全にならない。置き場は env の TMPDIR で決まる
        outer = Path(tempfile.mkdtemp(prefix="jevlint-anc-"))
        self.addCleanup(lambda: shutil.rmtree(outer, ignore_errors=True))
        (outer / "sgconfig.yml").write_text("ruleDirs: []\n", encoding="utf-8")
        inner = outer / "t"
        inner.mkdir()
        env = dict(ENV, TMPDIR=str(inner))
        with self.assertRaises(jevlint_tree.TreeError) as cm:
            with jevlint_tree.expanded_commit(self.repo.path, self.sha, env):
                self.fail("祖先に sgconfig があるのに展開が通った")
        self.assertIn("sgconfig.yml", str(cm.exception))
        self.assertEqual([], list(inner.iterdir()), "一時ディレクトリが残っている")
        self.assertEqual(1, self.repo.worktree_count())

    def test_failure_before_registration_leaves_other_stale_worktrees_alone(self):
        # 利用者のリポジトリに、ディレクトリだけ消えた worktree の登録が残っている状況
        # (アンマウント中のボリュームなど)。`worktree add` の前に失敗した展開が
        # `worktree prune` を呼ぶと、この登録まで消えてしまう
        stale = Path(tempfile.mkdtemp(prefix="jevlint-stale-")) / "wt"
        self.addCleanup(lambda: shutil.rmtree(stale.parent, ignore_errors=True))
        self.repo.git("worktree", "add", "--detach", "--no-checkout", str(stale), self.sha)
        shutil.rmtree(stale)
        self.assertEqual(2, self.repo.worktree_count(), "消えたディレクトリの登録が前提")
        outer = Path(tempfile.mkdtemp(prefix="jevlint-anc-"))
        self.addCleanup(lambda: shutil.rmtree(outer, ignore_errors=True))
        (outer / "sgconfig.yml").write_text("ruleDirs: []\n", encoding="utf-8")
        inner = outer / "t"
        inner.mkdir()
        with self.assertRaises(jevlint_tree.TreeError):
            with jevlint_tree.expanded_commit(self.repo.path, self.sha, dict(ENV, TMPDIR=str(inner))):
                self.fail("祖先に sgconfig があるのに展開が通った")
        self.assertEqual(2, self.repo.worktree_count(), "無関係な登録が prune された")

    def test_temp_dir_follows_tmpdir_of_the_given_env(self):
        base, env = self._private_base()
        with jevlint_tree.expanded_commit(self.repo.path, self.sha, env) as expanded:
            self.assertEqual(base.resolve(), expanded.tree.parent.parent)
        self.assertEqual([], list(base.iterdir()))

    def test_empty_or_relative_tmpdir_falls_back_to_the_system_temp_dir(self):
        # 空や相対の TMPDIR を mkdtemp にそのまま渡すと cwd の下に作られ、3.9 では返る
        # パスも相対になる (実測: 3.9.6 は 'jevlint-xxx'、3.14.7 は cwd を前置した絶対
        # パス)。相対のままだと worktree add は root から、書き出しは cwd から解決して
        # 別の場所を指す
        system = Path(tempfile.gettempdir()).resolve()
        for value in ("", "rel"):
            with self.subTest(TMPDIR=value):
                env = dict(ENV, TMPDIR=value)
                with jevlint_tree.expanded_commit(self.repo.path, self.sha, env) as expanded:
                    self.assertTrue(expanded.tree.is_absolute())
                    self.assertEqual(system, expanded.tree.parent.parent)
                    self.assertNotIn(self.repo.path.resolve(), expanded.tree.parents)
                self.assertFalse(Path("rel").exists(), "cwd の下に相対の置き場が作られた")

    def test_tmpdir_inside_the_repository_is_refused_and_leaves_nothing_behind(self):
        # 利用者の作業ツリーの中に worktree を作ると、走査対象に自分の展開が混ざる。
        # fixture の root には sgconfig.yml があり、祖先の検査が先に拒否してしまうので、
        # 置き場の検査だけが拒否できるよう sgconfig の無いリポジトリで見る
        repo = GitRepo(self)
        repo.write("a.py")
        sha = repo.commit()
        inside = repo.path / "tmpbase"
        inside.mkdir()
        listing = sorted(os.listdir(repo.path))
        for value in (str(inside), str(repo.path)):
            with self.subTest(TMPDIR=value):
                env = dict(ENV, TMPDIR=value)
                with self.assertRaises(jevlint_tree.TreeError) as cm:
                    with jevlint_tree.expanded_commit(repo.path, sha, env):
                        self.fail("リポジトリの中を置き場にした展開が通った")
                self.assertIn("リポジトリの中", str(cm.exception))
                self.assertEqual([], os.listdir(inside))
                self.assertEqual(listing, sorted(os.listdir(repo.path)))
                self.assertEqual(1, repo.worktree_count())

    def test_tmpdir_inside_the_repository_is_refused_even_when_only_the_case_differs(self):
        # 大文字小文字を区別しないファイルシステムでは `.../Repo` と `.../repo/sub` が同じ
        # 場所を指すが、`resolve()` は与えられた表記の大文字小文字を保つので (実測: APFS)
        # 文字列の包含では見えない。区別するファイルシステムでは表記違いのパスは存在せず、
        # 置き場を作れない失敗になる。どちらも TreeError で、root の下に何も残らない
        repo = GitRepo(self)
        repo.write("a.py")
        sha = repo.commit()
        (repo.path / "sub").mkdir()
        swapped = repo.path.parent / repo.path.name.swapcase() / "sub"
        listing = sorted(os.listdir(repo.path))
        with self.assertRaises(jevlint_tree.TreeError) as cm:
            with jevlint_tree.expanded_commit(repo.path, sha, dict(ENV, TMPDIR=str(swapped))):
                self.fail("表記違いでリポジトリの中を置き場にした展開が通った")
        if swapped.exists():
            self.assertIn("リポジトリの中", str(cm.exception))
        else:
            self.assertIn("一時ディレクトリを作れない", str(cm.exception))
        self.assertEqual([], os.listdir(repo.path / "sub"))
        self.assertEqual(listing, sorted(os.listdir(repo.path)))
        self.assertEqual(1, repo.worktree_count())

    def test_tmpdir_through_a_symlink_loop_is_a_tree_error(self):
        # 3.9 の `Path.resolve()` は symlink のループを RuntimeError にする (実測: 3.9.6。
        # 3.14.7 は投げず、後の mkdtemp が OSError になる)。どちらも TreeError に落とす
        outer = Path(tempfile.mkdtemp(prefix="jevlint-loop-"))
        self.addCleanup(lambda: shutil.rmtree(outer, ignore_errors=True))
        loop = outer / "loop"
        os.symlink(str(loop), loop)
        with self.assertRaises(jevlint_tree.TreeError):
            with jevlint_tree.expanded_commit(self.repo.path, self.sha, dict(ENV, TMPDIR=str(loop / "x"))):
                self.fail("symlink のループを置き場にした展開が通った")
        self.assertEqual(1, self.repo.worktree_count())

    def test_missing_blob_during_expansion_is_a_tree_error_and_tears_down(self):
        blob = self.repo.hash_blob(b"x\n")
        tree = self.repo.mktree(
            [("100644", "blob", MISSING_SHA, "0.txt"), ("100644", "blob", blob, "a.txt")],
            missing=True,
        )
        sha = self.repo.commit_tree(tree)
        base, env = self._private_base()
        with self.assertRaises(jevlint_tree.TreeError) as cm:
            with jevlint_tree.expanded_commit(self.repo.path, sha, env):
                self.fail("blob の無いコミットの展開が通った")
        self.assertIn(MISSING_SHA, str(cm.exception))
        self._assert_torn_down_into(base)

    def test_committed_sgconfig_directory_is_refused_and_tears_down(self):
        # コミットに `sgconfig.yml/x` があると展開後の `sgconfig.yml` はディレクトリで、
        # `unlink()` は macOS では PermissionError になる (実測)。ast-grep が読むのは
        # ファイルなので、消す代わりに拒否する
        blob = self.repo.hash_blob(b"x\n")
        sub = self.repo.mktree([("100644", "blob", blob, "x")])
        tree = self.repo.mktree(
            [("100644", "blob", blob, "a.py"), ("040000", "tree", sub, "sgconfig.yml")]
        )
        sha = self.repo.commit_tree(tree)
        base, env = self._private_base()
        with self.assertRaises(jevlint_tree.TreeError) as cm:
            with jevlint_tree.expanded_commit(self.repo.path, sha, env):
                self.fail("sgconfig.yml がディレクトリのコミットの展開が通った")
        self.assertIn("sgconfig.yml", str(cm.exception))
        self._assert_torn_down_into(base)

    def test_failing_worktree_add_is_a_tree_error_and_leaves_nothing_behind(self):
        base, env = self._private_base()
        with self.assertRaises(jevlint_tree.TreeError) as cm:
            with jevlint_tree.expanded_commit(self.repo.path, "deadbeef" * 5, env):
                self.fail("実在しない SHA の展開が通った")
        self.assertIn("worktree add", str(cm.exception))
        self._assert_torn_down_into(base)
        self.assertEqual(self.root_listing, sorted(os.listdir(self.repo.path)))

    def test_unusable_tmpdir_is_a_tree_error_and_registers_nothing(self):
        base = Path(tempfile.mkdtemp(prefix="jevlint-base-"))
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        env = dict(ENV, TMPDIR=str(base / "missing"))
        with self.assertRaises(jevlint_tree.TreeError):
            with jevlint_tree.expanded_commit(self.repo.path, self.sha, env):
                self.fail("存在しない TMPDIR で展開が通った")
        self.assertEqual(1, self.repo.worktree_count())

    def test_tree_error_from_materialize_during_expansion_tears_down(self):
        # `A.py`/`a.py` は worktree add を通り (展開は read-tree を呼ばず index を作らない)、
        # 書き出しの前の validate_tree_paths で落ちる
        blob = self.repo.hash_blob(b"x\n")
        tree = self.repo.mktree(
            [("100644", "blob", blob, "0.py"), ("100644", "blob", blob, "A.py"), ("100644", "blob", blob, "a.py")]
        )
        sha = self.repo.commit_tree(tree)
        base, env = self._private_base()
        with self.assertRaises(jevlint_tree.TreeError) as cm:
            with jevlint_tree.expanded_commit(self.repo.path, sha, env):
                self.fail("`A.py` と `a.py` を持つコミットの展開が通った")
        self.assertIn("a.py", str(cm.exception))
        self._assert_torn_down_into(base)

    def test_dot_git_blob_at_the_root_is_refused_after_worktree_add_and_tears_down(self):
        # 大文字小文字を区別しないファイルシステムでは、ルートの `.GIT` の blob が worktree の
        # `.git` (gitdir を指すファイル) を上書きしうる。worktree add はこのコミットを通すので、
        # 拒むのは書き出しの前の validate_tree_paths。文言でそれを見る (worktree add で落ちれば
        # 「worktree add に失敗」、書き出しで落ちれば「書き出しに失敗」になる)
        blob = self.repo.hash_blob(b"x\n")
        tree = self.repo.mktree([("100644", "blob", blob, ".GIT"), ("100644", "blob", blob, "a.py")])
        sha = self.repo.commit_tree(tree)
        base, env = self._private_base()
        with self.assertRaises(jevlint_tree.TreeError) as cm:
            with jevlint_tree.expanded_commit(self.repo.path, sha, env):
                self.fail("ルートに `.GIT` を持つコミットの展開が通った")
        self.assertIn("'.git' の成分", str(cm.exception))
        self.assertIn(".GIT", str(cm.exception))
        self._assert_torn_down_into(base)


UPLOADPACK_MARKER = "INJECTED-uploadpack.txt"


class PartialCloneFixture:
    """file:// の server から `--filter=<spec>` の partial clone を作る。

    無い object を読むと git は promisor remote から取りに行く (lazy fetch)。file:// では
    `remote.origin.uploadpack` のコマンドがローカルで、cwd = リポジトリの root で起動される
    (実測: git 2.55.0)。目印を置いてから本物の upload-pack に exec するスクリプトをそこに
    置き、fetch が走ったかを目印と `.git/objects/pack` の項目数で見る。
    """

    def __init__(self, test: unittest.TestCase, filter_spec: str):
        self.server = GitRepo(test)
        self.server.write("d/a.txt", "committed\n")
        self.server.write("b.txt", "x\n")
        self.sha = self.server.commit()
        self.server.config("uploadpack.allowFilter", "true")
        self.server.config("uploadpack.allowAnySHA1InWant", "true")
        self.path = Path(tempfile.mkdtemp(prefix="jevlint-partial-"))
        test.addCleanup(lambda: shutil.rmtree(self.path, ignore_errors=True))
        self.client = self.path / "client"
        run(
            [
                "git",
                "clone",
                "--quiet",
                f"--filter={filter_spec}",
                "--no-checkout",
                f"file://{self.server.path}",
                str(self.client),
            ]
        )
        script = self.path / "uploadpack.sh"
        script.write_text(
            f'#!/bin/sh\ntouch "$PWD/{UPLOADPACK_MARKER}"\nexec git upload-pack "$@"\n',
            encoding="utf-8",
        )
        script.chmod(0o755)
        run(["git", "-C", str(self.client), "config", "remote.origin.uploadpack", str(script)])
        # lazy fetch は git 2.55.0 では reference-transaction も起動する (実測: フラグ無しの
        # cat-file --batch で 3 回) が、Apple の 2.50.1 では起動しない (実測)。版に依るので
        # 陽性対照には使わず、展開の外の runner が ref を触っていないことの追加の観測に
        # だけ使う (fetch が走ったかの pin は uploadpack の目印と pack の数)
        client_hooks = self.path / "client-hooks"
        client_hooks.mkdir()
        hook = client_hooks / "reference-transaction"
        hook.write_text(
            f'#!/bin/sh\ntouch "$PWD/{injected_marker("reference-transaction")}"\n', encoding="utf-8"
        )
        hook.chmod(0o755)
        run(["git", "-C", str(self.client), "config", "core.hooksPath", str(client_hooks)])
        self.hooks = self.path / "hooks"
        self.hooks.mkdir()
        self.blob = self.server.git("rev-parse", f"{self.sha}:d/a.txt").strip()

    def packs(self) -> int:
        return len(list((self.client / ".git" / "objects" / "pack").iterdir()))

    def fetched(self) -> bool:
        return UPLOADPACK_MARKER in os.listdir(self.client)

    def ref_hook_fired(self) -> bool:
        return injected_marker("reference-transaction") in os.listdir(self.client)


class PartialCloneTests(unittest.TestCase):
    def test_fixture_lazily_fetches_when_git_runs_without_the_wrappers_options(self):
        # 陽性対照。この fixture が本当に lazy fetch する状態でなければ、下のテストは
        # 何も見ずに緑になる。素の `cat-file --batch` は取りに行って目印を置き pack を増やす
        fixture = PartialCloneFixture(self, "blob:none")
        before = fixture.packs()
        out = run_bytes(
            ["git", "-C", str(fixture.client), "cat-file", "--batch"],
            stdin=fixture.blob.encode("ascii") + b"\n",
        )
        self.assertIn(b"committed", out)
        self.assertTrue(fixture.fetched())
        self.assertGreater(fixture.packs(), before)

    def test_fixture_tree_0_lazily_fetches_on_a_plain_ls_tree(self):
        # tree:0 側の陽性対照。fetch が uploadpack より手前で壊れていても ls-tree は非 0 に
        # なり、下の `assertRaises(TreeError)` は option 無しでも通ってしまう。素の
        # ls-tree が取りに行けることを目印と pack の数で見て、その退化を防ぐ
        fixture = PartialCloneFixture(self, "tree:0")
        before = fixture.packs()
        out = run(["git", "-C", str(fixture.client), "ls-tree", "-r", fixture.sha]).stdout
        self.assertIn("d/a.txt", out)
        self.assertTrue(fixture.fetched())
        self.assertGreater(fixture.packs(), before)

    def test_materialize_on_a_blob_none_clone_raises_missing_and_does_not_fetch(self):
        fixture = PartialCloneFixture(self, "blob:none")
        dest = Path(tempfile.mkdtemp(prefix="jevlint-dest-"))
        self.addCleanup(lambda: shutil.rmtree(dest, ignore_errors=True))
        before = fixture.packs()
        with self.assertRaises(jevlint_tree.TreeError) as cm:
            jevlint_tree.materialize(fixture.client, fixture.sha, dest, ENV, fixture.hooks)
        self.assertIn("missing", str(cm.exception))
        self.assertFalse(fixture.fetched(), "lazy fetch が走った")
        self.assertFalse(fixture.ref_hook_fired(), "ref が更新された")
        self.assertEqual(before, fixture.packs())
        self.assertEqual([], list(dest.iterdir()))

    def test_path_in_commit_on_a_tree_0_clone_raises_and_does_not_fetch(self):
        # tree:0 の clone にはコミットしか無い。ref の解決は通り、tree を読む ls-tree は
        # 取りに行かずに失敗する。展開の外の runner は hook を止めないので、ref が
        # 更新されていないことは reference-transaction の目印そのもので見える
        fixture = PartialCloneFixture(self, "tree:0")
        before = fixture.packs()
        self.assertEqual(fixture.sha, jevlint_tree.resolve_commit(fixture.client, "HEAD", ENV))
        with self.assertRaises(jevlint_tree.TreeError):
            jevlint_tree.path_in_commit(fixture.client, fixture.sha, "d/a.txt", ENV)
        self.assertFalse(fixture.fetched(), "lazy fetch が走った")
        self.assertFalse(fixture.ref_hook_fired(), "ref が更新された")
        self.assertEqual(before, fixture.packs())

    def test_expanded_commit_on_a_blob_none_clone_raises_and_tears_down(self):
        fixture = PartialCloneFixture(self, "blob:none")
        base = Path(tempfile.mkdtemp(prefix="jevlint-base-"))
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        before = fixture.packs()
        with self.assertRaises(jevlint_tree.TreeError) as cm:
            with jevlint_tree.expanded_commit(fixture.client, fixture.sha, dict(ENV, TMPDIR=str(base))):
                self.fail("blob の無い partial clone の展開が通った")
        self.assertIn("missing", str(cm.exception))
        self.assertFalse(fixture.fetched(), "lazy fetch が走った")
        self.assertFalse(fixture.ref_hook_fired(), "ref が更新された")
        self.assertEqual(before, fixture.packs())
        self.assertEqual([], os.listdir(base))
        self.assertEqual(1, worktree_count(fixture.client))


class ReplaceRefTests(unittest.TestCase):
    """`git replace` (refs/replace) は object の読み出しを差し替える。

    差し替え先はコミットから到達しない object でもよいので、追ったまま書くと「コミット済みの
    中身だけを送る」が破れる。`--no-replace-objects` で差し替えを無視して読む。
    """

    def setUp(self):
        self.repo = GitRepo(self)
        self.repo.write("a.txt", "committed\n")
        self.one = self.repo.commit("one")
        self.blob_a = self.repo.git("rev-parse", f"{self.one}:a.txt").strip()
        self.blob_b = self.repo.hash_blob(b"never committed\n")
        self.repo.git("replace", self.blob_a, self.blob_b)
        self.hooks = Path(tempfile.mkdtemp(prefix="jevlint-hooks-"))
        self.addCleanup(lambda: shutil.rmtree(self.hooks, ignore_errors=True))

    def test_plain_git_follows_the_replacement_so_the_reference_must_not(self):
        # 陽性対照: 素の cat-file は差し替え後の中身を返す。比較の基準 (`blob_bytes`) は
        # `--no-replace-objects` で取る
        plain = run_bytes(["git", "-C", str(self.repo.path), "cat-file", "blob", f"{self.one}:a.txt"])
        self.assertEqual(b"never committed\n", plain)
        self.assertEqual(b"committed\n", self.repo.blob_bytes(self.one, "a.txt"))

    def test_materialize_writes_the_committed_bytes_not_the_replacement(self):
        dest = Path(tempfile.mkdtemp(prefix="jevlint-dest-"))
        self.addCleanup(lambda: shutil.rmtree(dest, ignore_errors=True))
        jevlint_tree.materialize(self.repo.path, self.one, dest, ENV, self.hooks)
        self.assertEqual(b"committed\n", (dest / "a.txt").read_bytes())
        self.assertEqual(self.repo.blob_bytes(self.one, "a.txt"), (dest / "a.txt").read_bytes())

    def test_path_in_commit_ignores_a_replaced_commit(self):
        # コミット自体の差し替えは ls-tree の見る tree を変える (実測: 素の ls-tree は
        # 差し替え先のコミットにしか無いパスを見つける)。rev-parse は名前の解決なので変わらない
        self.repo.write("only-in-two.txt", "y\n")
        two = self.repo.commit("two")
        self.repo.git("replace", self.one, two)
        plain = run(["git", "-C", str(self.repo.path), "ls-tree", self.one, "--", "only-in-two.txt"]).stdout
        self.assertIn("only-in-two.txt", plain)
        self.assertFalse(jevlint_tree.path_in_commit(self.repo.path, self.one, "only-in-two.txt", ENV))
        self.assertTrue(jevlint_tree.path_in_commit(self.repo.path, self.one, "a.txt", ENV))
        self.assertEqual(self.one, jevlint_tree.resolve_commit(self.repo.path, self.one, ENV))


# 本物の git が知らない global option に返す応答を、locale ごとに写した shell 関数。1 行目と
# 終了コード 129 は Homebrew の 2.55.0 で実測 (C: `unknown option: <名前>`、de_DE:
# `Unbekannte Option: <名前>`)。`LC_ALL=C` は LANG / LANGUAGE を de に向けたままでも英語に
# 戻す (実測)。usage の行は短縮してある (検出が見るのは 1 行目と終了コードだけ)
OLD_GIT_RESPONSE = """\
respond() {
  case "${LC_ALL:-}" in
    C|POSIX) printf '%s\\n' 'unknown option: --no-lazy-fetch' 'usage: git [-v | --version] [-h | --help]' >&2 ;;
    *) case "${LC_ALL:-}:${LANG:-}:${LANGUAGE:-}" in
         *de*) printf '%s\\n' 'Unbekannte Option: --no-lazy-fetch' 'Verwendung: git [-v | --version] [-h | --help]' >&2 ;;
         *) printf '%s\\n' 'unknown option: --no-lazy-fetch' 'usage: git [-v | --version] [-h | --help]' >&2 ;;
       esac ;;
  esac
  exit 129
}
"""

GERMAN_LOCALE = {"LANG": "de_DE.UTF-8", "LC_ALL": "de_DE.UTF-8", "LANGUAGE": "de"}


class GitShimMixin:
    """PATH の先頭に置く `git` の shim を作る。

    Homebrew の git 2.55.0 と Apple の git 2.50.1 はどちらも `--no-lazy-fetch` を知る
    (実測) ので、知らない git は shim で再現する。shim は `body` の条件に当たれば
    `OLD_GIT_RESPONSE` の応答を返し、それ以外は本物の git に exec する。subprocess は子の
    env の PATH で実行ファイルを探す (実測: 3.14.7 と 3.9.6)。
    """

    def shim_env(self, body: str, extra: "dict | None" = None) -> dict:
        real_git = shutil.which("git", path=ENV["PATH"])
        self.assertIsNotNone(real_git)
        shim_dir = Path(tempfile.mkdtemp(prefix="jevlint-gitshim-"))
        self.addCleanup(lambda: shutil.rmtree(shim_dir, ignore_errors=True))
        shim = shim_dir / "git"
        shim.write_text(
            "#!/bin/sh\n" + OLD_GIT_RESPONSE + body + f'exec {shlex.quote(real_git)} "$@"\n',
            encoding="utf-8",
        )
        shim.chmod(0o755)
        env = dict(ENV, PATH=str(shim_dir) + os.pathsep + ENV["PATH"])
        env.update(extra or {})
        return env


# `--no-lazy-fetch` を一切知らない git
REJECT_NO_LAZY_FETCH = 'for arg in "$@"; do case "$arg" in --no-lazy-fetch) respond ;; esac; done\n'
# 後始末のときだけ知らなくなる git (展開の途中で版が変わることは無いが、finally の中で
# `TreeError` が出る経路を作る)
REJECT_ONLY_TEARDOWN = 'case " $* " in *" worktree remove "*|*" worktree prune "*) respond ;; esac\n'
# 後始末の remove と prune だけが非 0 で終わる git (`TreeError` ではなく終了コードで失敗する経路)
FAIL_ONLY_TEARDOWN = 'case " $* " in *" worktree remove "*|*" worktree prune "*) exit 1 ;; esac\n'


class OldGitTests(GitShimMixin, unittest.TestCase):
    def setUp(self):
        self.repo = GitRepo(self)
        self.repo.write("a.py")
        self.sha = self.repo.commit()

    def _assert_names_the_minimum_version(self, env: dict) -> None:
        with self.assertRaises(jevlint_tree.TreeError) as cm:
            jevlint_tree.repo_root(self.repo.path, env)
        self.assertIn("--no-lazy-fetch", str(cm.exception))
        # 最低の版は上流の RelNotes/2.45.0.txt が根拠 (2.44.0 の RelNotes には無い)。
        # 文言が要る版を名指すことを、定数ではなく literal で pin する
        self.assertIn("2.45.0", str(cm.exception))
        with self.assertRaises(jevlint_tree.TreeError) as cm:
            with jevlint_tree.expanded_commit(self.repo.path, self.sha, env):
                self.fail("古い git で展開が通った")
        self.assertIn("2.45.0", str(cm.exception))
        self.assertEqual(1, self.repo.worktree_count())

    def test_git_without_no_lazy_fetch_fails_closed_naming_the_minimum_version(self):
        self._assert_names_the_minimum_version(self.shim_env(REJECT_NO_LAZY_FETCH))

    def test_old_git_is_named_even_when_the_users_locale_is_german(self):
        # 利用者の locale が de_DE だと本物の git は `Unbekannte Option:` を返し、英語の
        # 照合が外れて版を名指せない。ラッパは自分の git に LC_ALL=C を渡して C locale の
        # 文言を読む。shim は本物と同じく LC_ALL=C のときだけ英語を返す
        self._assert_names_the_minimum_version(self.shim_env(REJECT_NO_LAZY_FETCH, GERMAN_LOCALE))

    def _assert_leftover_registration_is_announced(self, env: dict) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with jevlint_tree.expanded_commit(self.repo.path, self.sha, env) as expanded:
                tmp = expanded.tree.parent
                self.assertTrue((expanded.tree / "a.py").is_file())
        self.assertFalse(tmp.exists(), "一時ディレクトリが残っている")
        self.assertEqual(2, self.repo.worktree_count(), "shim が remove と prune を拒んだはず")
        # 残った登録を黙って残さない: どの worktree か、どう消すかを 1 行で告げる
        lines = stderr.getvalue().splitlines()
        self.assertEqual(1, len(lines), lines)
        self.assertIn(str(expanded.tree), lines[0])
        self.assertIn("git worktree prune", lines[0])
        self.repo.git("worktree", "prune")
        self.assertEqual(1, self.repo.worktree_count())

    def test_teardown_does_not_raise_when_git_refuses_the_options_midway(self):
        # 後始末の `worktree remove` / `prune` が `_run_git` の TreeError になっても、
        # 展開の結果は返り、一時ディレクトリは消える。消せなかった登録だけが残る
        self._assert_leftover_registration_is_announced(self.shim_env(REJECT_ONLY_TEARDOWN))

    def test_teardown_announces_the_leftover_registration_when_remove_and_prune_exit_nonzero(self):
        # TreeError ではなく終了コードで remove と prune が失敗しても、同じく告げる
        self._assert_leftover_registration_is_announced(self.shim_env(FAIL_ONLY_TEARDOWN))


class GitLocaleTests(unittest.TestCase):
    def test_wrapper_reads_git_messages_in_the_c_locale(self):
        # ラッパが git の出力を照合するとき、文言は利用者の locale に依らず C locale の
        # 英語でなければならない (CLAUDE.md: 判定に使う目印は ASCII に保つ)。本物の git で、
        # de_DE の env を渡しても両方の runner に見える stderr が英語であることを見る。
        # 対照は同じ env で直接叩いた git で、訳を持つ git (Homebrew の 2.55.0) なら
        # `Schwerwiegend:` になり、持たない git (Apple の 2.50.1) なら英語のまま。後者では
        # 訳が無いことを明示的に assert し、skip にはしない
        repo = GitRepo(self)
        repo.write("a.py")
        repo.commit()
        env = dict(ENV, **GERMAN_LOCALE)
        args = ["ls-tree", "-z", "deadbeef" * 5, "--", "x"]
        raw = subprocess.run(["git", "-C", str(repo.path), *args], env=env, capture_output=True)
        self.assertEqual(128, raw.returncode)
        translated = not raw.stderr.startswith(b"fatal:")
        hooks = Path(tempfile.mkdtemp(prefix="jevlint-hooks-"))
        self.addCleanup(lambda: shutil.rmtree(hooks, ignore_errors=True))
        seen = {
            "readonly": jevlint_tree._git_readonly(repo.path, args, env).stderr,
            "quiet": jevlint_tree._QuietGit(env, hooks).run(repo.path, args).stderr,
        }
        for runner, stderr in seen.items():
            with self.subTest(runner=runner):
                self.assertTrue(stderr.startswith(b"fatal: not a tree object"), stderr)
                if translated:
                    self.assertNotEqual(raw.stderr, stderr)
                else:
                    self.assertEqual(raw.stderr, stderr, "この git は訳を持たないはず")


class SignalsAsExceptionsTests(unittest.TestCase):
    def test_sigterm_and_sighup_become_a_keyboard_interrupt_subclass_and_handlers_are_restored(self):
        self.assertTrue(issubclass(jevlint_tree.SignalInterrupt, KeyboardInterrupt))
        for sig in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=sig.name):
                previous = signal.getsignal(sig)
                with self.assertRaises(jevlint_tree.SignalInterrupt) as cm:
                    with jevlint_tree.signals_as_exceptions():
                        self.assertIsNot(previous, signal.getsignal(sig))
                        self.assertTrue(callable(signal.getsignal(sig)))
                        os.kill(os.getpid(), sig)
                        self.fail(f"{sig.name} が例外として届いていない")
                self.assertEqual(int(sig), cm.exception.signum)
                self.assertIs(previous, signal.getsignal(sig))

    def test_handlers_are_restored_after_a_normal_exit(self):
        previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGHUP)}
        with jevlint_tree.signals_as_exceptions():
            pass
        for sig, handler in previous.items():
            self.assertIs(handler, signal.getsignal(sig))


class CountSuffixTests(unittest.TestCase):
    def setUp(self):
        self.tree = Path(tempfile.mkdtemp(prefix="jevlint-count-"))
        self.addCleanup(lambda: shutil.rmtree(self.tree, ignore_errors=True))
        for rel in ("a.mbt", "sub/b.mbt", "sub/c.py", "other/d.py"):
            target = self.tree / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("", encoding="utf-8")

    def test_empty_target_counts_the_whole_tree(self):
        self.assertEqual(2, jevlint_tree.count_suffix(self.tree, [], ".mbt"))
        self.assertEqual(2, jevlint_tree.count_suffix(self.tree, [""], ".mbt"))

    def test_a_directory_without_matches_counts_zero(self):
        self.assertEqual(0, jevlint_tree.count_suffix(self.tree, ["other"], ".mbt"))

    def test_a_directory_and_a_file_are_counted_within_their_own_scope(self):
        self.assertEqual(1, jevlint_tree.count_suffix(self.tree, ["sub"], ".mbt"))
        self.assertEqual(1, jevlint_tree.count_suffix(self.tree, ["a.mbt"], ".mbt"))
        self.assertEqual(0, jevlint_tree.count_suffix(self.tree, ["sub/c.py"], ".mbt"))
        self.assertEqual(2, jevlint_tree.count_suffix(self.tree, ["sub", "a.mbt"], ".mbt"))


if __name__ == "__main__":
    unittest.main()
