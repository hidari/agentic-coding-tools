"""jevlint_fs.py (葉のモジュール) の仕様。

見るのは `stat_or_none` と `find_in_ancestors` の 2 入口。「無い」(None を返す) と「確かめられない
(そのまま例外を投げる)」の境界を、実物のファイルシステムの状態 (権限 0 のディレクトリ、
自分を指す symlink、別の場所を指す symlink) で作って検証する。モックは使わない。

権限 0 のディレクトリは `deny_all_access` で作る。jev-lint-curated の他のテストも同じ関数を使う。
"""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

import jevlint_fs


def deny_all_access(test: unittest.TestCase, directory: Path) -> None:
    """`directory` の権限を 0 にし、テストの後始末で元に戻す。

    root は権限ビットを無視して読めてしまうので、これを使うテストは root で走らせると
    赤になる。CI の runner と検証コンテナは非 root で走る前提。skip にしないのは、CI の
    runner が skip を赤にするので環境で分ける形が取れないため。

    後始末を先に登録するのは、chmod の後で落ちても権限 0 のディレクトリを残さないため
    (残ると一時ディレクトリの削除が失敗する)。
    """
    test.addCleanup(os.chmod, directory, stat.S_IRWXU)
    os.chmod(directory, 0)


class StatOrNoneTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_regular_file_returns_stat(self):
        target = self.root / "f.txt"
        target.write_text("x", encoding="utf-8")
        result = jevlint_fs.stat_or_none(target)
        self.assertIsNotNone(result)
        self.assertTrue(stat.S_ISREG(result.st_mode))

    def test_missing_returns_none(self):
        self.assertIsNone(jevlint_fs.stat_or_none(self.root / "does-not-exist"))

    def test_not_a_directory_parent_returns_none(self):
        # 親のはずの成分が通常ファイルで、その下は辿れない (NotADirectoryError)
        parent = self.root / "not-a-dir"
        parent.write_text("x", encoding="utf-8")
        self.assertIsNone(jevlint_fs.stat_or_none(parent / "child"))

    def test_embedded_nul_returns_none(self):
        # OS に渡せないパス (embedded NUL) は stat() が ValueError を投げる
        self.assertIsNone(jevlint_fs.stat_or_none(Path("a\0b")))

    def test_permission_denied_raises(self):
        blocked = self.root / "blocked"
        blocked.mkdir()
        deny_all_access(self, blocked)
        with self.assertRaises(PermissionError):
            jevlint_fs.stat_or_none(blocked / "child")

    def test_symlink_loop_raises(self):
        loop = self.root / "loop"
        loop.symlink_to(loop)
        # 型だけを見る。ELOOP の errno は macOS では 62、Linux では 40 で値が違う
        with self.assertRaises(OSError):
            jevlint_fs.stat_or_none(loop)


class FindInAncestorsTests(unittest.TestCase):
    """与えられた表記の祖先と、`os.path.realpath` で解決した表記の祖先の和を探す。

    symlink の fixture は `outer/given/link -> outer/resolved/real` で、探す path は
    `outer/given/link/leaf`。`outer/given` は与えられた表記の祖先にしか現れず、
    `outer/resolved` は解決した表記の祖先にしか現れない。片方の祖先だけを見る実装は、
    どちらか一方のテストで赤になる。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.outer = Path(self._tmp.name)
        real = self.outer / "resolved" / "real"
        real.mkdir(parents=True)
        (self.outer / "given").mkdir()
        (self.outer / "given" / "link").symlink_to(real)
        self.path = self.outer / "given" / "link" / "leaf"

    def assert_found(self, found, expected: Path) -> None:
        # macOS の一時ディレクトリは `/var` と `/private/var` の 2 表記で祖先に現れるので、
        # Path の等値ではなく同じ実体かで比べる
        self.assertIsNotNone(found)
        self.assertEqual(found.name, expected.name)
        self.assertTrue(os.path.samefile(found, expected), f"{found} は {expected} ではない")

    def test_nothing_found_returns_none(self):
        self.assertIsNone(jevlint_fs.find_in_ancestors(self.path, ("marker.yml",)))

    def test_found_only_in_an_ancestor_of_the_given_notation(self):
        expected = self.outer / "given" / "marker.yml"
        expected.write_text("", encoding="utf-8")
        self.assert_found(jevlint_fs.find_in_ancestors(self.path, ("marker.yml",)), expected)

    def test_found_only_in_an_ancestor_of_the_resolved_notation(self):
        expected = self.outer / "resolved" / "marker.yml"
        expected.write_text("", encoding="utf-8")
        self.assert_found(jevlint_fs.find_in_ancestors(self.path, ("marker.yml",)), expected)

    def test_every_name_is_looked_for(self):
        # 2 つ目の名前だけがある配置。最初の名前だけを見る実装はここで赤になる。置き場は
        # 両方の表記の祖先に現れる outer にし、表記の扱いとは独立に見る
        expected = self.outer / "second.yaml"
        expected.write_text("", encoding="utf-8")
        found = jevlint_fs.find_in_ancestors(self.path, ("first.yml", "second.yaml"))
        self.assert_found(found, expected)

    def test_the_nearest_ancestor_of_the_given_notation_comes_first(self):
        # 祖先を回す順序を pin する。与えられた表記の祖先を近い順に、次に解決した表記の
        # 祖先を近い順に見る。同じ名前を祖先の各段に置き、最も近い段が返ることを見る。
        # 順序を set の反復に任せると PYTHONHASHSEED で返る段が変わる
        deep = self.outer / "a" / "b" / "c" / "d" / "e"
        deep.mkdir(parents=True)
        for directory in (deep, *list(deep.parents)[:4]):
            (directory / "marker.yml").write_text("", encoding="utf-8")
        found = jevlint_fs.find_in_ancestors(deep / "f" / "leaf", ("marker.yml",))
        self.assert_found(found, deep / "marker.yml")

    def test_permission_denied_ancestor_raises(self):
        # denied 自身の stat は親の権限で決まるので落ちない。denied の中 (denied/sub) を
        # 探す段で search 権限が要るので、path は 2 段下に置く
        denied = self.outer / "denied"
        denied.mkdir()
        deny_all_access(self, denied)
        with self.assertRaises(PermissionError):
            jevlint_fs.find_in_ancestors(denied / "sub" / "leaf", ("marker.yml",))

    def test_symlink_loop_in_an_ancestor_raises(self):
        loop = self.outer / "loop"
        loop.symlink_to(loop)
        with self.assertRaises(OSError):
            jevlint_fs.find_in_ancestors(loop / "sub" / "leaf", ("marker.yml",))


if __name__ == "__main__":
    unittest.main()
