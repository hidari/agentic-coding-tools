"""jevlint_fs.py (葉のモジュール) の仕様。

見るのは `stat_or_none` の 1 入口だけ。「無い」(None を返す) と「確かめられない
(そのまま例外を投げる)」の境界を、実物のファイルシステムの状態 (権限 0 のディレクトリ、
自分を指す symlink) で作って検証する。モックは使わない。

権限 0 のディレクトリを使う `test_permission_denied_raises` は root では意味を失う
(root は権限ビットを無視して読めてしまうため) ので、root で走らせると赤になる。CI の
runner と検証コンテナは非 root で走る前提。
"""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

import jevlint_fs


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
        os.chmod(blocked, 0)
        self.addCleanup(os.chmod, blocked, stat.S_IRWXU)
        with self.assertRaises(PermissionError):
            jevlint_fs.stat_or_none(blocked / "child")

    def test_symlink_loop_raises(self):
        loop = self.root / "loop"
        loop.symlink_to(loop)
        # 型だけを見る。errno は macOS と Linux で値が違う (プロジェクトの CLAUDE.md)
        with self.assertRaises(OSError):
            jevlint_fs.stat_or_none(loop)


if __name__ == "__main__":
    unittest.main()
