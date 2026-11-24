"""本体・アダプタ・入口・テストの境界を ast とファイルの配置で固定する。

本体とテストは標準ライブラリだけで回り、実行時の依存 (anthropic の SDK と PyYAML) はアダプタと
入口のスクリプトだけが持つ。入口は PEP 723 で版を固定し、`uv run --script` で起動される。
"""

import ast
import os
import re
import sys
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tech_briefing.workflow_def import workflow  # noqa: E402

TB = Path(__file__).resolve().parent.parent
REPO = TB.parent
RUNTIME_DEPS = {"adapters", "anthropic", "yaml"}
# PEP 723 の参照の実装の正規表現 (packaging.python.org の inline script metadata、2026-11-24 時点の仕様)
_PEP723 = re.compile(r"(?m)^# /// (?P<type>[a-zA-Z0-9-]+)$\s(?P<content>(^#(| .*)$\s)+)^# ///$")
_PIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*==[0-9][0-9A-Za-z.+!-]*")


def py_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def imported(tree: ast.AST) -> set[str]:
    """import された module の先頭の名前。相対 import は同じパッケージの中なので数えない。"""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def top_level_imported(tree: ast.Module) -> set[str]:
    # 関数の中の import は、その関数を呼ぶまで走らない。入口は実物のアダプタを関数の中でだけ読む
    return imported(ast.Module(body=[n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))], type_ignores=[]))


def parse(path: Path) -> ast.Module:
    # runner の版が image で変わるので 3.11 の構文で読めることも見る
    return ast.parse(path.read_text(encoding="utf-8"), str(path), feature_version=(3, 11))


def workflow_scripts() -> list[Path]:
    runs = [s["run"] for job in workflow()["jobs"].values() for s in job["steps"] if "run" in s]
    found = [m.group(1) for r in runs for m in re.finditer(r"uv run --script (\S+)", r)]
    return sorted({REPO / f for f in found})


def pep723(path: Path) -> dict:
    blocks = [m for m in _PEP723.finditer(path.read_text(encoding="utf-8")) if m.group("type") == "script"]
    if len(blocks) != 1:
        raise AssertionError(f"{path.name}: script の block が {len(blocks)} 個あります")
    content = "".join(line[2:] if line.startswith("# ") else line[1:] for line in blocks[0].group("content").splitlines(keepends=True))
    return tomllib.loads(content)


class Imports(unittest.TestCase):
    def test_collector_sees_imports(self):
        # 対照: 集め方が import を拾えていなければ、下の「持たない」はすべて素通りする
        self.assertIn("anthropic", imported(parse(TB / "adapters" / "anthropic_client.py")))
        self.assertIn("yaml", imported(parse(TB / "adapters" / "yaml_loader.py")))
        self.assertIn("tech_briefing", imported(parse(TB / "tests" / "test_select.py")))

    def test_core_does_not_import_runtime_deps(self):
        files = py_files(TB / "tech_briefing")
        self.assertGreaterEqual(len(files), 10)
        for path in files:
            with self.subTest(path.name):
                self.assertEqual(imported(parse(path)) & (RUNTIME_DEPS | {"bin"}), set())

    def test_tests_do_not_import_adapters_or_bin(self):
        files = py_files(TB / "tests")
        self.assertGreaterEqual(len(files), 10)
        bin_stems = {p.stem for p in py_files(TB / "bin")}
        for path in files:
            with self.subTest(path.name):
                names = imported(parse(path))
                self.assertEqual(names & (RUNTIME_DEPS | {"bin"} | bin_stems), set())

    def test_bin_scripts_import_runtime_deps_only_inside_functions(self):
        scripts = workflow_scripts()
        for path in scripts:
            with self.subTest(path.name):
                tree = parse(path)
                self.assertEqual(top_level_imported(tree) & RUNTIME_DEPS, set())
                # 対照: 実物のアダプタはどこかで読んでいる (読まないなら __main__ の配線が抜けている)
                self.assertIn("adapters", imported(tree))

    def test_every_file_parses_as_python_3_11(self):
        files = py_files(TB)
        self.assertGreaterEqual(len(files), 25)
        for path in files:
            with self.subTest(str(path.relative_to(TB))):
                parse(path)


class Names(unittest.TestCase):
    def test_no_stem_shadows_stdlib_or_runtime_deps(self):
        # 対照: 衝突する名前が集合に入っていることを先に見る
        self.assertIn("select", sys.stdlib_module_names)
        files = py_files(TB)
        self.assertGreaterEqual(len(files), 25)
        for path in files:
            with self.subTest(str(path.relative_to(TB))):
                self.assertNotIn(path.stem, sys.stdlib_module_names)
                self.assertNotIn(path.stem, {"anthropic", "yaml"})


class Pep723(unittest.TestCase):
    def test_workflow_runs_exactly_the_bin_run_scripts(self):
        scripts = workflow_scripts()
        self.assertEqual(scripts, sorted((TB / "bin").glob("run_*.py")))
        self.assertEqual(len(scripts), 3)

    def test_each_script_pins_exact_versions(self):
        names = set()
        for path in workflow_scripts():
            with self.subTest(path.name):
                meta = pep723(path)
                self.assertEqual(meta.get("requires-python"), ">=3.11")
                deps = meta.get("dependencies")
                self.assertIsInstance(deps, list)
                for dep in deps:
                    self.assertRegex(dep, _PIN)
                    self.assertIsNotNone(_PIN.fullmatch(dep), dep)
                    names.add(dep.split("==")[0])
        # 対照: 固定の検査が空の dependencies で素通りしていない
        self.assertEqual(names, {"anthropic", "PyYAML"})

    def test_parser_rejects_unpinned(self):
        self.assertIsNone(_PIN.fullmatch("anthropic>=0.79"))
        self.assertIsNone(_PIN.fullmatch("anthropic"))
        self.assertIsNotNone(_PIN.fullmatch("PyYAML==6.0.3"))


class Fetcher(unittest.TestCase):
    def test_fetcher_never_sends_credentials(self):
        # release notes は認証なしで引く。token を付けると LLM が選んだパッケージ経由で private の release が読める
        source = (TB / "adapters" / "fetcher.py").read_text(encoding="utf-8")
        for word in ("Authorization", "GITHUB_TOKEN", "add_unredirected_header", "add_header"):
            with self.subTest(word):
                self.assertNotIn(word, source)


if __name__ == "__main__":
    unittest.main()
