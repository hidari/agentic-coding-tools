"""jevlint_tree.py (ref の解決とパスの検査) の仕様。

対象は `repo_root` / `resolve_commit` / `normalize_path` / `path_in_commit` の 4 関数と
`TreeError`。worktree の展開や blob の書き出し (Task 3 で同モジュールの後半に追加される)
はここでは扱わない。

git を呼ぶテストはすべて `GIT_*` を落とした `os.environ` の写しを `env` として自前で渡す
(production の `build_env` は Task 4 で追加されるため)。一時リポジトリごとに
`user.name` / `user.email` / `commit.gpgsign=false` をリポジトリ設定へ入れて、開発機の
グローバルな git 設定 (実名や署名設定) の影響を受けないようにする。
"""

from __future__ import annotations

import os
import shutil
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


class GitRepo:
    """一時ディレクトリに、架空の identity を持つ git リポジトリを作る。"""

    def __init__(self, test: unittest.TestCase):
        self.path = Path(tempfile.mkdtemp(prefix="jevlint-tree-"))
        test.addCleanup(lambda: shutil.rmtree(self.path, ignore_errors=True))
        run(["git", "init", "-q", str(self.path)])
        # 実名やメールアドレスを書かないため架空の値を使う。開発機の global 設定に
        # 実名が入っていても、リポジトリ local の設定が優先されて上書きする
        run(["git", "-C", str(self.path), "config", "user.name", "jevlint tree test"])
        run(
            [
                "git",
                "-C",
                str(self.path),
                "config",
                "user.email",
                "jevlint-tree-test@example.invalid",
            ]
        )
        # 開発機の global 設定が commit.gpgsign=true だと、鍵の無い CI で commit が
        # 失敗しうる。リポジトリ local で明示的に無効化して切り離す
        run(["git", "-C", str(self.path), "config", "commit.gpgsign", "false"])

    def write(self, rel: str, text: str = "content\n") -> str:
        target = self.path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return rel

    def commit(self, message: str = "commit") -> str:
        run(["git", "-C", str(self.path), "add", "-A"])
        run(["git", "-C", str(self.path), "commit", "-q", "-m", message])
        return self.head_sha()

    def head_sha(self) -> str:
        return run(["git", "-C", str(self.path), "rev-parse", "HEAD"]).stdout.strip()


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


class RepoRootTests(unittest.TestCase):
    def test_subdirectory_cwd_still_resolves_the_root(self):
        repo = GitRepo(self)
        repo.write("sub/deep/file.txt")
        repo.commit()
        subdir = repo.path / "sub" / "deep"
        result = jevlint_tree.repo_root(subdir, ENV)
        self.assertTrue(result.samefile(repo.path))


if __name__ == "__main__":
    unittest.main()
