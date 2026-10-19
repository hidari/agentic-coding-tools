#!/usr/bin/env python3
"""gen-readme.py が生成する plugin の表の不変条件を検証する。

表の側は build() の出力そのものを読む。plugin の列挙を自前で組み直すと、build() 側の
列挙が変わったとき検出できない (実測: build() から 1 行落とす変異を入れても、自前で
組み直していた版は緑のままだった)。

`--check` は生成結果と README の一致しか見ないので、生成器そのものが行を落としたり
説明を取り違えたりしても、README を生成し直せば緑になる。ここはそれを止める。
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

    def plugin_packages(self) -> list[Path]:
        return [
            d
            for d in sorted((ROOT / "plugins").iterdir())
            if d.is_dir() and (d / "SKILL.md").exists()
        ]

    def plugin_rows(self) -> list[str]:
        return [
            line
            for line in gen.build().split("\n")
            if line.startswith("| `plugins/")
        ]

    def test_table_lists_every_plugin_package(self) -> None:
        """build() の列挙が痩せたら赤くする。

        追跡下で root SKILL.md を持つ plugin パッケージの数と、表の行数を突き合わせる。
        """
        expected = self.plugin_packages()
        self.assertGreater(len(expected), 0, "plugin パッケージが 1 つも無い")
        self.assertEqual(len(self.plugin_rows()), len(expected))

    def test_row_is_path_and_frontmatter_description(self) -> None:
        """行はパスと説明の 2 列だけで、説明は frontmatter の値そのもの。

        説明の取り違え (別パッケージの値、空文字) と、列の増減の両方を赤くする。
        """
        rows = self.plugin_rows()
        self.assertGreater(len(rows), 0, "plugin の表に行が 1 つも無い")
        for pkg, row in zip(self.plugin_packages(), rows):
            with self.subTest(pkg=pkg.name):
                desc = gen.frontmatter(str(pkg / "SKILL.md")).get("description", "")
                self.assertNotEqual(desc, "", "description が空")
                self.assertEqual(row, f"| `plugins/{pkg.name}` | {desc} |")


if __name__ == "__main__":
    unittest.main()
