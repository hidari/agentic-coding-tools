"""本体・アダプタ・入口・テストの境界を ast とファイルの配置で固定する。

本体とテストは標準ライブラリだけで回り、実行時の依存 (anthropic の SDK と PyYAML) はアダプタと
入口のスクリプトだけが持つ。入口は PEP 723 で版を固定し、`uv run --script` で起動される。

テストが import 文で adapters を読むことはここで禁じるが、importlib でファイルのパスから読むことは禁じない
(この検査は import 文だけを見る)。依存の無いアダプタ (http_common・github_api・fetcher) は token と取得先の
守りを固定するために test_adapters_stdlib がファイルから読む。依存のあるアダプタ (anthropic_client・
yaml_loader) は読むと CI のテストに依存が増えるので読まず、守りは下の ast の検査で固定する。
"""

import ast
import os
import re
import sys
import tomllib
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tech_briefing.workflow_def import workflow  # noqa: E402

TB = Path(__file__).resolve().parent.parent
REPO = TB.parent
RUNTIME_DEPS = {"adapters", "anthropic", "yaml"}
# PEP 723 の参照の実装の正規表現 (packaging.python.org の inline script metadata、2026-11-24 時点の仕様)
_PEP723 = re.compile(r"(?m)^# /// (?P<type>[a-zA-Z0-9-]+)$\s(?P<content>(^#(| .*)$\s)+)^# ///$")
_PIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*==[0-9][0-9A-Za-z.+!-]*")
# uv の exclude-newer に置く RFC 3339 の UTC の時刻 (秒まで、Z で終わる形に限る)
_UTC_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


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


def imported_modules(tree: ast.AST) -> set[str]:
    """import された module の完全な名前。imported() は先頭の名前に丸めるので、パッケージの中の向きを見るときに使う。"""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
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

    def test_selection_does_not_depend_on_render(self):
        # 印の判定は marker が持つ。select の判定が投稿の整形のモジュールに依存すると、整形の都合の変更が
        # 処理済みの判定と入力の絞り込みを動かす
        core = TB / "tech_briefing"
        self.assertNotIn("tech_briefing.render", imported_modules(parse(core / "selection.py")))
        # 対照: 集め方は完全な名前を拾えていて、両方が同じ述語を marker から取る
        for name in ("selection.py", "render.py"):
            with self.subTest(name):
                self.assertIn("tech_briefing.marker", imported_modules(parse(core / name)))

    def test_every_file_parses_as_python_3_11(self):
        files = py_files(TB)
        self.assertGreaterEqual(len(files), 25)
        for path in files:
            with self.subTest(str(path.relative_to(TB))):
                parse(path)


def yaml_uses(tree: ast.AST) -> tuple[set[str], set[str]]:
    """(yaml の属性として呼んだ名前, yaml を読んだ import の形)。from yaml import や別名は形として記録する。"""
    called, forms = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "yaml":
                called.add(node.func.attr)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] == "yaml":
                    forms.add(f"import {a.name} as {a.asname}" if a.asname else f"import {a.name}")
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "yaml":
            forms.add(f"from {node.module} import")
    return called, forms


class YamlSafeLoad(unittest.TestCase):
    def test_adapters_call_only_safe_load(self):
        # yaml.load や unsafe_load は任意の Python のオブジェクトを作れる。呼び出し側のリポジトリの YAML は信頼できない
        called, forms = set(), set()
        for path in py_files(TB / "adapters"):
            c, f = yaml_uses(parse(path))
            called |= c
            forms |= f
        # 部分集合ではなく一致で見る。呼び出しを1つも拾えない集め方でも素通りしないため
        self.assertEqual(called, {"safe_load"})
        # from yaml import load や別名で読むと、上の属性の呼び出しの検査を通らずに load を呼べる
        self.assertEqual(forms, {"import yaml"})

    def test_collector_sees_other_forms(self):
        # 対照: 集め方は safe_load 以外の呼び出しと、別の import の形を拾える
        tree = ast.parse("import yaml as y\nfrom yaml import load\nyaml.load(t)\nyaml.unsafe_load(t)\n")
        self.assertEqual(yaml_uses(tree), ({"load", "unsafe_load"}, {"import yaml as y", "from yaml import"}))


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
                    self.assertIsNotNone(_PIN.fullmatch(dep), dep)
                    names.add(dep.split("==")[0])
        # 対照: 固定の検査が空の dependencies で素通りしていない
        self.assertEqual(names, {"anthropic", "PyYAML"})

    def test_pinned_versions_are_the_checked_ones(self):
        # 版は triage の kwargs (betas・fallbacks・output_config・thinking・cache_control) と to_dict を wheel で確かめたもの。
        # 上げるときは同じ確かめをやり直してからここを変える (確かめていない版へ黙って動かさない)
        pins = {}
        for path in workflow_scripts():
            for dep in pep723(path)["dependencies"]:
                name, _, version = dep.partition("==")
                pins.setdefault(name, set()).add(version)
        self.assertEqual(pins, {"anthropic": {"2.7.0"}, "PyYAML": {"6.0.3"}})
        # SDK を使うのは triage の入口だけ
        triage = [d for d in pep723(TB / "bin" / "run_triage.py")["dependencies"] if d.startswith("anthropic")]
        self.assertEqual(triage, ["anthropic==2.7.0"])

    def test_each_script_has_the_same_exclude_newer(self):
        # dependencies は直接の依存しか固定しない。uv の exclude-newer で、解決に考える配布をその時刻より前に絞る
        # (docs.astral.sh/uv/guides/scripts、2026-11-25 確認)。間接の依存まで効くかは docs に明記が無いので、
        # hash までの固定は script の lock で扱う。値は anthropic 2.7.0 の wheel の公開 (2026-11-09) の後
        values = set()
        for path in workflow_scripts():
            with self.subTest(path.name):
                value = pep723(path).get("tool", {}).get("uv", {}).get("exclude-newer")
                # 引用符が無いと tomllib は datetime にする。uv が読む形は文字列
                self.assertIsInstance(value, str)
                self.assertIsNotNone(_UTC_TIMESTAMP.fullmatch(value), value)
                values.add(value)
        self.assertEqual(len(values), 1, values)
        (value,) = values
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        self.assertEqual(parsed.utcoffset(), timedelta(0))
        self.assertGreater(parsed, datetime(2026, 11, 9, 18, 52, tzinfo=timezone.utc))

    def test_timestamp_shape_rejects_date_only_and_offsets(self):
        self.assertIsNotNone(_UTC_TIMESTAMP.fullmatch("2026-11-10T00:00:00Z"))
        for bad in ("2026-11-10", "2026-11-10T09:00:00+09:00", "2026-11-10T00:00:00", "2026-11-10 00:00:00Z"):
            with self.subTest(bad):
                self.assertIsNone(_UTC_TIMESTAMP.fullmatch(bad))

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
