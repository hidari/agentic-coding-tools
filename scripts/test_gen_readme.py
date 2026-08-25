#!/usr/bin/env python3
"""gen-readme.py の component 数え上げと、生成される表の不変条件を検証する。

この数え上げは `--check` では守れない。生成側と検査側が同じ関数を通るので、数え方が
壊れても両者は一致したまま緑になる。README の数字だけが静かにずれる。

表の側は build() の出力そのものを読む。plugin の列挙を自前で組み直すと、build() 側の
列挙が変わったとき検出できない (実測: build() から 1 行落とす変異を入れても、自前で
組み直していた版は緑のままだった)。
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERATOR = "scripts/gen-readme.py"

FRONTMATTER = "---\nname: x\ndescription: y\n---\n"


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = _load(GENERATOR, "gen_readme_under_test")


def make_pkg(root: Path, *, skills=(), agents=(), commands=()) -> Path:
    """component の 3 種を任意の組み合わせで持つパッケージを組み立てる。

    skills は (ディレクトリ名, SKILL.md を置くか) の組で受ける。置かない側が
    「数えてはいけないディレクトリ」の対照になる。agents / commands は
    (相対パス, frontmatter を持つか) の組で受ける。持たない側が README のような
    非 component の対照になる。相対パスにサブディレクトリを含めれば名前空間付き
    command を作れる。
    """
    pkg = root / "pkg"
    pkg.mkdir(exist_ok=True)
    for name, has_skill_md in skills:
        d = pkg / "skills" / name
        d.mkdir(parents=True, exist_ok=True)
        if has_skill_md:
            (d / "SKILL.md").write_text(FRONTMATTER, encoding="utf-8")
    for kind, entries in (("agents", agents), ("commands", commands)):
        for rel, has_frontmatter in entries:
            f = pkg / kind / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(FRONTMATTER if has_frontmatter else "body\n", encoding="utf-8")
    return pkg


class CountComponents(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_counts_skills_only_package(self) -> None:
        pkg = make_pkg(self.root, skills=[("a", True), ("b", True)])
        self.assertEqual(gen.count_components(str(pkg)), 2)

    def test_counts_agents_and_commands_without_skills(self) -> None:
        """撤去後の形。skills/ が無くても 0 にならないことが本題。"""
        pkg = make_pkg(
            self.root, agents=[("x.md", True)], commands=[("p.md", True), ("q.md", True)]
        )
        self.assertEqual(gen.count_components(str(pkg)), 3)

    def test_counts_all_three_kinds_together(self) -> None:
        """3 種を同時に持つパッケージの受け入れテスト。

        個々の分岐は他のケースが pin しているので、単独でしか殺せない変異は無い。
        3 種が足し合わされる形そのものを 1 つ固定しておく意図で残す。
        """
        pkg = make_pkg(
            self.root,
            skills=[("a", True)],
            agents=[("x.md", True), ("y.md", True)],
            commands=[("p.md", True)],
        )
        self.assertEqual(gen.count_components(str(pkg)), 4)

    def test_skills_dir_without_skill_md_is_not_a_component(self) -> None:
        """SKILL.md を持たないディレクトリは登録されないので数えない。"""
        pkg = make_pkg(self.root, skills=[("real", True), ("stray", False)])
        self.assertEqual(gen.count_components(str(pkg)), 1)

    def test_markdown_without_frontmatter_is_not_a_component(self) -> None:
        """実際に起こる非 component は README.md、つまり拡張子は .md のまま。

        対照を拡張子違い (.txt など) だけにすると、拡張子で弾く実装でも緑になり
        「非 component を数えない」の主張が何も見ていない状態になる。
        """
        pkg = make_pkg(
            self.root,
            agents=[("real.md", True), ("README.md", False)],
            commands=[("p.md", True)],
        )
        self.assertEqual(gen.count_components(str(pkg)), 2)

    def test_counts_namespaced_commands(self) -> None:
        """command は commands/<ns>/<name>.md を取りうるので再帰で数える。"""
        pkg = make_pkg(
            self.root, commands=[("top.md", True), ("ns/nested.md", True)]
        )
        self.assertEqual(gen.count_components(str(pkg)), 2)

    def test_package_without_any_component_dir_is_zero(self) -> None:
        pkg = self.root / "pkg"
        pkg.mkdir()
        self.assertEqual(gen.count_components(str(pkg)), 0)


class GeneratedTable(unittest.TestCase):
    """生成された表そのものを読み、見出しと数字が食い違わないことを確かめる。

    実数ではなく「1 以上であること」を pin する。実数を書くと plugin の増減で毎回
    赤くなり、テストが drift の検出ではなく更新作業の対象に変わる。
    """

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
        expected = [
            d
            for d in sorted((ROOT / "plugins").iterdir())
            if d.is_dir() and (d / "SKILL.md").exists()
        ]
        self.assertGreater(len(expected), 0, "plugin パッケージが 1 つも無い")
        self.assertEqual(len(self.plugin_rows()), len(expected))

    def test_no_row_shows_zero_components(self) -> None:
        rows = self.plugin_rows()
        self.assertGreater(len(rows), 0, "plugin の表に行が 1 つも無い")
        for row in rows:
            with self.subTest(row=row):
                cells = row.split("|")
                self.assertNotEqual(
                    cells[2].strip(), "0", f"component 数 0 で表に載っている: {row}"
                )


if __name__ == "__main__":
    unittest.main()
