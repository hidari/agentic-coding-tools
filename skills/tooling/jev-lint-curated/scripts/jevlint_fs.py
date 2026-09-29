"""stat 1 つで「無い」と「確かめられない」を版によらず区別する、jev-lint-curated の葉のモジュール。

pathlib の述語 (`is_file()` / `exists()` 等) は ENOENT 等を握りつぶして `False` を返す
一方、権限エラー (`PermissionError`) の扱いが Python の版で変わる: 3.12 までは再送出し、
3.14 では他の述語と同じく握りつぶして `False` を返す (実測: 3.9.6・3.11.15・3.12.12・
3.14.7、macOS)。`Path.stat()` はどの版でも `PermissionError` を投げる (実測)。
`stat_or_none` はこの版差を吸収し、判定不能を「無い」に丸めるかどうかを版ではなく
呼び出し側の文脈で決められるようにする。`find_in_ancestors` は同じ区別を保ったまま
祖先ディレクトリを探す。

このモジュールは他の jevlint* モジュールを import しない (葉)。依存は入口
(`jevlint.py`) から下流へ一方向に流し、循環を作らないため。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence


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


def find_in_ancestors(path: Path, names: Sequence[str]) -> "Path | None":
    """`path` の祖先ディレクトリに `names` のどれかがあれば、最初に見つかったものを返す。

    祖先は与えられた表記の祖先と、`os.path.realpath` で解決した表記の祖先の和。macOS では
    `/tmp` は `/private/tmp` への、`/var` は `/private/var` への symlink で、子プロセスの
    cwd は解決した表記になる (getcwd は解決済みの表記を返す) ので、cwd から親を辿るツールは
    解決した側の祖先を見る。与えられた表記の祖先だけでは、symlink の先にしか無い祖先を
    見落とす。

    回す順序は、与えられた表記の祖先を近い順に、次に解決した表記の祖先を近い順に (重複は
    最初の 1 回だけ)。set で回すと PYTHONHASHSEED で順序が変わり、当たりと確かめられない
    祖先が両方あるときに、返るものか投げるかが起動ごとに変わる。

    各候補は `stat_or_none` で見る。確かめられない候補 (権限エラー・symlink のループ等) の
    `OSError` はそのまま投げ、「無い」に丸めない。

    解決に `Path.resolve()` を使わないのは、symlink のループを 3.12 までは `RuntimeError`
    にし、3.14 では投げないため (実測: 3.9.6・3.11.15・3.12.12・3.14.7)。`realpath` は
    同じ版のどれでもループで投げない。embedded NUL を含む表記では 3.14.7 の `realpath` が
    `ValueError` を投げ、3.9.6 は NUL を含んだままの絶対パスを返す (実測)。argv と
    environ は C 文字列なので、この経路には CLI からは到達しない。
    """
    resolved = Path(os.path.realpath(path))
    ancestors = dict.fromkeys([*path.parents, *resolved.parents])
    for ancestor in ancestors:
        for name in names:
            candidate = ancestor / name
            if stat_or_none(candidate) is not None:
                return candidate
    return None
