"""stat 1 つで「無い」と「確かめられない」を版によらず区別する、jev-lint-curated の葉のモジュール。

pathlib の述語 (`is_file()` / `exists()` 等) は ENOENT 等を握りつぶして `False` を返す
一方、権限エラー (`PermissionError`) の扱いが Python の版で変わる: 3.12 までは再送出し、
3.14 では他の述語と同じく握りつぶして `False` を返す (実測: 3.9.6・3.11.15・3.12.12・
3.14.7、macOS)。`Path.stat()` はどの版でも `PermissionError` を投げる (実測)。
`stat_or_none` はこの版差を吸収し、判定不能を「無い」に丸めるかどうかを版ではなく
呼び出し側の文脈で決められるようにする。

このモジュールは他の jevlint* モジュールを import しない (葉)。依存は入口
(`jevlint.py`) から下流へ一方向に流し、循環を作らないため。
"""

from __future__ import annotations

import os
from pathlib import Path


def stat_or_none(path: Path) -> "os.stat_result | None":
    """`path` の stat。無ければ `None`、確かめられなければ例外をそのまま投げる。

    `None` に丸めるのは `FileNotFoundError` (無い)・`NotADirectoryError` (途中の成分が
    通常ファイルで辿れない)・`ValueError` (embedded NUL 等、OS に渡せない表記) の 3 つ
    だけ。権限エラー (`PermissionError`) や symlink のループ (`OSError`、errno は
    macOS と Linux で値が違うので比べない) はここでは丸めず、そのまま呼び出し側へ
    伝える。判定不能を「無い」に丸めてよいかは呼び出し側の文脈次第で、ここでは決めない。
    """
    try:
        return path.stat()
    except (FileNotFoundError, NotADirectoryError, ValueError):
        return None
