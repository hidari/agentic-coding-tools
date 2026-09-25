"""jevlint_tree.py (ref の解決、パスの検査、コミットの展開) の仕様。

前半は `repo_root` / `resolve_commit` / `normalize_path` / `path_in_commit` と `TreeError`。
後半は送る範囲の境界そのもので、`validate_tree_paths` / `materialize` / `expanded_commit` /
`signals_as_exceptions` / `count_suffix` が対象。後半の fixture リポジトリには、checkout を
経由すると中身が変わるもの (smudge filter、リポジトリの外を指す symlink、post-checkout hook)
をわざと入れてあり、展開後のディスクがコミットの blob と同一であることを `git cat-file blob`
との比較で見る。

git を呼ぶテストはすべて `GIT_*` を落とした `os.environ` の写しを `env` として自前で渡す
(production の `build_env` は Task 4 で追加されるため)。一時リポジトリごとに
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

# `GIT_*` を落とした環境。production の env 組み立て (`build_env`, Task 4) はまだ無いので、
# その呼び出し側の責務をここで自前に再現する。git は `commit -a` 等のとき hook へ
# `GIT_INDEX_FILE` を渡すことがあり、継承したままだと fixture ではなく呼び出し元
# リポジトリの index を書き換えうる (scripts/test_check_related_refs.py の実測と同じ理由)。
ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def run(args: list) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(args, env=ENV, capture_output=True, text=True, check=True)


def run_bytes(args: list, stdin: bytes = b"") -> bytes:
    return subprocess.run(args, env=ENV, input=stdin, capture_output=True, check=True).stdout


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
        return run_bytes(["git", "-C", str(self.path), "cat-file", "blob", f"{sha}:{path}"])

    def hash_blob(self, data: bytes) -> str:
        return run_bytes(
            ["git", "-C", str(self.path), "hash-object", "-w", "--stdin"], stdin=data
        ).decode("ascii").strip()

    def mktree(self, entries: list) -> str:
        """`(mode, type, sha, name)` の列から tree object を作り、その SHA を返す。

        作業ツリーを経由しないので、大文字小文字を区別しないファイルシステムでは
        作れない名前の組 (`A.py` と `a.py`) や `.GIT` を持つ tree もここで作れる。
        """
        lines = "".join(f"{mode} {otype} {sha}\t{name}\n" for mode, otype, sha, name in entries)
        return run_bytes(
            ["git", "-C", str(self.path), "mktree"], stdin=lines.encode("utf-8")
        ).decode("ascii").strip()

    def commit_tree(self, tree_sha: str) -> str:
        return self.git("commit-tree", tree_sha, "-m", "synthetic").strip()

    def worktree_count(self) -> int:
        """`git worktree list --porcelain` に載る worktree の数 (本体を含む)。"""
        lines = self.git("worktree", "list", "--porcelain").splitlines()
        return sum(1 for line in lines if line.startswith("worktree "))


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
        # 正規化後は `-x` になり、Task 6 で argv へそのまま渡すとオプションと
        # 誤認されうる (このテストを足す前の実装はここを通していなかった: 実測)
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
        # 別の failure mode であり、区別せず False を返すと Task 6 で実際の git 障害が
        # 「対象パスがコミットに存在しない」という誤ったメッセージに化ける
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


class ExpansionFixture:
    """checkout を経由すると中身が変わるものを揃えたコミット。

    filter / symlink / hook のどれも、`materialize` が blob の生のバイトを書く限りは
    ディスクに影響しない。逆に `worktree add` に checkout させると smudge が走り
    (filtered.txt が大文字になる)、symlink が symlink として復元され、post-checkout の
    目印が書かれる (実測: git 2.55.0)。
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
        self.marker = root / "HOOK_RAN"
        hook = root / ".git" / "hooks" / "post-checkout"
        hook.parent.mkdir(exist_ok=True)
        hook.write_text(f"#!/bin/sh\ntouch {shlex.quote(str(self.marker))}\n", encoding="utf-8")
        hook.chmod(0o755)
        self.sha = self.repo.commit()
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


class MaterializeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ExpansionFixture(self)
        self.repo = self.fixture.repo
        self.dest = Path(tempfile.mkdtemp(prefix="jevlint-dest-"))
        self.addCleanup(lambda: shutil.rmtree(self.dest, ignore_errors=True))

    def test_every_written_file_equals_the_blob_and_the_count_is_returned(self):
        count = jevlint_tree.materialize(self.repo.path, self.fixture.sha, self.dest, ENV)
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
        jevlint_tree.materialize(self.repo.path, self.fixture.sha, self.dest, ENV)
        link = self.dest / "link"
        self.assertFalse(link.is_symlink())
        self.assertTrue(link.is_file())
        self.assertEqual(b"../outside.txt", link.read_bytes())

    def test_filtered_file_is_not_smudged(self):
        jevlint_tree.materialize(self.repo.path, self.fixture.sha, self.dest, ENV)
        self.assertEqual(b"hello lower\n", (self.dest / "filtered.txt").read_bytes())

    def test_executable_bit_follows_mode_100755(self):
        jevlint_tree.materialize(self.repo.path, self.fixture.sha, self.dest, ENV)
        self.assertTrue(os.access(self.dest / "run.sh", os.X_OK))
        self.assertFalse(os.access(self.dest / "plain.py", os.X_OK))

    def test_five_megabyte_blob_is_written_byte_for_byte(self):
        jevlint_tree.materialize(self.repo.path, self.fixture.sha, self.dest, ENV)
        data = (self.dest / "big.bin").read_bytes()
        self.assertEqual(FIVE_MB, len(data))
        self.assertEqual(bytes(range(256)) * (FIVE_MB // 256), data)

    def test_submodule_entry_is_not_written_and_not_counted(self):
        blob = self.repo.hash_blob(b"x\n")
        tree = self.repo.mktree(
            [("100644", "blob", blob, "file.txt"), ("160000", "commit", self.fixture.sha, "vendor")]
        )
        sha = self.repo.commit_tree(tree)
        count = jevlint_tree.materialize(self.repo.path, sha, self.dest, ENV)
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
            jevlint_tree.materialize(self.repo.path, sha, self.dest, ENV)
        self.assertEqual([], list(self.dest.iterdir()))

    def test_case_only_duplicate_names_reject_before_writing_anything(self):
        blob = self.repo.hash_blob(b"x\n")
        tree = self.repo.mktree(
            [("100644", "blob", blob, "0.py"), ("100644", "blob", blob, "A.py"), ("100644", "blob", blob, "a.py")]
        )
        sha = self.repo.commit_tree(tree)
        with self.assertRaises(jevlint_tree.TreeError):
            jevlint_tree.materialize(self.repo.path, sha, self.dest, ENV)
        self.assertEqual([], list(self.dest.iterdir()))

    def test_git_failure_is_a_tree_error(self):
        with self.assertRaises(jevlint_tree.TreeError):
            jevlint_tree.materialize(self.repo.path, "deadbeef" * 5, self.dest, ENV)
        self.assertEqual([], list(self.dest.iterdir()))


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

    def _assert_torn_down(self, tmp: Path) -> None:
        self.assertFalse(tmp.exists(), f"一時ディレクトリが残っている: {tmp}")
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

    def test_post_checkout_hook_does_not_run(self):
        with jevlint_tree.expanded_commit(self.repo.path, self.sha, ENV):
            pass
        self.assertFalse(self.fixture.marker.exists())

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
        base = Path(tempfile.mkdtemp(prefix="jevlint-base-"))
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        env = dict(ENV, TMPDIR=str(base))
        with jevlint_tree.expanded_commit(self.repo.path, self.sha, env) as expanded:
            self.assertEqual(base, expanded.tree.parent.parent)
        self.assertEqual([], list(base.iterdir()))

    def test_unusable_tmpdir_is_a_tree_error_and_registers_nothing(self):
        base = Path(tempfile.mkdtemp(prefix="jevlint-base-"))
        self.addCleanup(lambda: shutil.rmtree(base, ignore_errors=True))
        env = dict(ENV, TMPDIR=str(base / "missing"))
        with self.assertRaises(jevlint_tree.TreeError):
            with jevlint_tree.expanded_commit(self.repo.path, self.sha, env):
                self.fail("存在しない TMPDIR で展開が通った")
        self.assertEqual(1, self.repo.worktree_count())

    def test_tree_error_during_expansion_tears_down(self):
        blob = self.repo.hash_blob(b"x\n")
        tree = self.repo.mktree([("100644", "blob", blob, ".GIT")])
        sha = self.repo.commit_tree(tree)
        with self.assertRaises(jevlint_tree.TreeError):
            with jevlint_tree.expanded_commit(self.repo.path, sha, ENV):
                self.fail("`.GIT` を持つコミットの展開が通った")
        self.assertEqual(1, self.repo.worktree_count())


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
