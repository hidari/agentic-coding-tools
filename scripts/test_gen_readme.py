#!/usr/bin/env python3
"""gen-readme.py が生成する plugin の表の不変条件を検証する。

build() の出力そのものを読む。plugin の列挙を build() と同じ形で組み直すと、build() 側の
列挙が変わったとき検出できない (実測: build() から 1 行落とす変異を入れても、自前で
組み直していた版は緑のままだった)。

`--check` は生成結果と README の一致しか見ないので、生成器そのものが行を落としたり
説明を取り違えたりしても、README を生成し直せば緑になる。ここはそれを止める。

期待値の description は生成器と同じ frontmatter() で読むので、パーサ自身の誤りは
ここでは見えない。
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERATOR = "scripts/gen-readme.py"


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = _load(GENERATOR, "gen_readme_under_test")


class GeneratedTable(unittest.TestCase):
    """生成された表そのものを読む。

    実数ではなく追跡下の plugin パッケージとの対応を pin する。実数を書くと plugin の増減で
    毎回赤くなり、テストが drift の検出ではなく更新作業の対象に変わる。
    """

    def test_rows_are_every_plugin_with_its_description(self) -> None:
        """root SKILL.md を持つ plugin ごとに、パスと description の 2 列の行が 1 つずつ並ぶ。

        行の欠落、説明の取り違え (別パッケージの値、空文字)、列の増減のどれも赤くする。
        """
        expected = []
        for d in sorted((ROOT / "plugins").iterdir()):
            if not (d / "SKILL.md").exists():
                continue
            desc = gen.frontmatter(str(d / "SKILL.md")).get("description", "")
            # 空のままだと生成側も空になり、等値が自明に通る
            self.assertNotEqual(desc, "", f"{d.name} の description が空")
            expected.append(f"| `plugins/{d.name}` | {desc} |")
        self.assertGreater(len(expected), 0, "plugin パッケージが 1 つも無い")

        rows = [line for line in gen.build().split("\n") if line.startswith("| `plugins/")]
        self.assertEqual(rows, expected)


if __name__ == "__main__":
    unittest.main()
