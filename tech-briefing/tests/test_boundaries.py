"""本体・アダプタ・入口・テストの境界を ast とファイルの配置で固定する。

本体とテストは標準ライブラリだけで回り、実行時の依存 (anthropic の SDK と PyYAML) はアダプタと
入口のスクリプトだけが持つ。入口は PEP 723 で版を固定し、隣の `<script>.lock` で間接の依存まで hash で固定して、
`uv run --locked --script` で起動される。`--locked` が止めない食い違いと lock の検査は ScriptLock が持つ。

テストが import してよいアダプタは、実行時の依存を直にも兄弟のアダプタ経由でも import しないものだけ。
どれがそうかは adapters の ast から導くので、依存の無いアダプタが依存のある兄弟を import すれば、
そのアダプタもテストから読めなくなる。依存の無いアダプタは token と取得先の守りを固定するために test_adapters_stdlib が
読む。依存のあるアダプタ (どれかは test_dependent_adapters_are_the_ones_with_deps が固定する) は読むと CI のテストに
依存が増えるので読まず、守りは下の ast の検査で固定する。この検査は import 文を見るので、
本体・アダプタ・入口が動的な import (`__import__`・importlib) を持たないことも固定する。テストの側は
入口のスクリプトを importlib でファイルから読む (test_entrypoints) ので、その検査の対象にしない。
"""

import ast
import os
import re
import subprocess
import sys
import tomllib
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tech_briefing.workflow_def import workflow  # noqa: E402

TB = Path(__file__).resolve().parent.parent
REPO = TB.parent
# 実行時の依存 (PEP 723 で入口が宣言する配布物の import 名)
RUNTIME_DEPS = {"anthropic", "yaml"}
ADAPTERS = "adapters"
# 本体はどこでも、入口は module の読み込みの時点で読んではならないもの。アダプタは丸ごと入口の関数の中でだけ読む
NOT_AT_IMPORT_TIME = RUNTIME_DEPS | {ADAPTERS}
# PEP 723 の参照の実装の正規表現 (packaging.python.org の inline script metadata、2026-11-24 時点の仕様)
_PEP723 = re.compile(r"(?m)^# /// (?P<type>[a-zA-Z0-9-]+)$\s(?P<content>(^#(| .*)$\s)+)^# ///$")
_PIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*==[0-9][0-9A-Za-z.+!-]*")
# uv の exclude-newer に置く RFC 3339 の UTC の時刻 (秒まで、Z で終わる形に限る)
_UTC_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


def py_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _import_names(nodes, package: str | None) -> set[str]:
    names = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module
            elif node.level == 1 and package is not None:
                # `from . import x` と `from .x import y` は、パッケージの中のファイルでは兄弟を読む
                base = f"{package}.{node.module}" if node.module else package
            else:
                # パッケージを渡さない相対 import は、検査の対象の外 (同じパッケージの中) として数えない
                continue
            names.add(base)
            # `from pkg import name` の name は submodule でもありうる。兄弟のアダプタを名指しで読む形を
            # 見落とさないよう、属性か submodule かを問わず完全な名前として数える
            names.update(f"{base}.{a.name}" for a in node.names)
    return names


def imported_modules(tree: ast.AST, package: str | None = None) -> set[str]:
    """関数の中を含め、import された module の完全な名前。package を渡すと、その中の相対 import も解決する。"""
    return _import_names(ast.walk(tree), package)


def imported(tree: ast.AST) -> set[str]:
    """import された module の先頭の名前。"""
    return {name.split(".")[0] for name in imported_modules(tree)}


def _module_level_nodes(tree: ast.AST):
    # 関数の本体は呼ぶまで走らない。class の本体と最上位の try / if / with は module を読む時点で走るので辿る
    stack = [tree]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(
            child
            for child in ast.iter_child_nodes(node)
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
        )


def top_level_imported(tree: ast.Module) -> set[str]:
    """module を読む時点で走る import の先頭の名前。入口は実物のアダプタを関数の中でだけ読む。"""
    return {name.split(".")[0] for name in _import_names(_module_level_nodes(tree), None)}


def _hits_any(name: str, prefixes: set[str]) -> bool:
    """name が prefixes のどれかか、その中の名前か。"""
    return any(name == p or name.startswith(p + ".") for p in prefixes)


def dependent_modules(imports: dict[str, set[str]], deps: set[str]) -> set[str]:
    """imports (module の完全な名前 → それが import する完全な名前) のうち、deps を直にか、
    deps に当たる別の module を経由して import するもの。"""
    found: set[str] = set()
    while True:
        # 兄弟の兄弟まで辿るため、増えなくなるまで繰り返す
        more = {m for m, names in imports.items() if m not in found and any(_hits_any(n, deps | found) for n in names)}
        if not more:
            return found
        found |= more


def adapter_imports() -> dict[str, set[str]]:
    return {
        f"{ADAPTERS}.{p.stem}": imported_modules(parse(p), ADAPTERS)
        for p in py_files(TB / ADAPTERS)
        if p.stem != "__init__"
    }


def forbidden_in_tests() -> set[str]:
    """テストが import してはならない完全な名前の接頭辞。依存を読むアダプタと、依存と、入口。"""
    return dependent_modules(adapter_imports(), RUNTIME_DEPS) | RUNTIME_DEPS | {"bin"} | {p.stem for p in py_files(TB / "bin")}


def dynamic_imports(tree: ast.AST) -> set[str]:
    """import 文を通らずに module を読む形 (`__import__` と importlib)。"""
    found = {f"import {n}" for n in imported_modules(tree) if _hits_any(n, {"importlib"})}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "__import__":
            found.add("__import__")
        elif isinstance(node, ast.Attribute) and node.attr == "__import__":
            found.add(f"{ast.unparse(node.value)}.__import__")
    return found


def parse(path: Path) -> ast.Module:
    # runner の版が image で変わるので 3.11 の構文で読めることも見る
    return ast.parse(path.read_text(encoding="utf-8"), str(path), feature_version=(3, 11))


def workflow_scripts() -> list[Path]:
    runs = [s["run"] for job in workflow()["jobs"].values() for s in job["steps"] if "run" in s]
    found = [m.group(1) for r in runs for m in re.finditer(r"uv run --locked --script (\S+)", r)]
    if not found:
        # 起動の形を拾えないと、入口を回すテストがどれも0本で素通りする
        raise AssertionError("workflow から入口の起動を1つも拾えません")
    return sorted({REPO / f for f in found})


def lock_of(path: Path) -> Path:
    return path.with_name(f"{path.name}.lock")


def read_lock(path: Path) -> dict:
    return tomllib.loads(lock_of(path).read_text(encoding="utf-8"))


def pins(path: Path) -> list[tuple[str, str]]:
    """PEP 723 の dependencies を (名前, 版) の組にする。`==` で固定していることは Pep723 が見る。"""
    return [(name, version) for name, _, version in (d.partition("==") for d in pep723(path)["dependencies"])]


def normalized(name: str) -> str:
    # lock は配布物の名前を PEP 503 の形 (小文字、[-_.] の連続を - に) で持つ
    return re.sub(r"[-_.]+", "-", name).lower()


def pep723(path: Path) -> dict:
    blocks = [m for m in _PEP723.finditer(path.read_text(encoding="utf-8")) if m.group("type") == "script"]
    if len(blocks) != 1:
        raise AssertionError(f"{path.name}: script の block が {len(blocks)} 個あります")
    content = "".join(line[2:] if line.startswith("# ") else line[1:] for line in blocks[0].group("content").splitlines(keepends=True))
    return tomllib.loads(content)


class Imports(unittest.TestCase):
    def test_collector_sees_imports(self):
        # 対照: 集め方が import を拾えていなければ、下の「持たない」はすべて素通りする
        self.assertIn("anthropic", imported(parse(TB / ADAPTERS / "anthropic_client.py")))
        self.assertIn("yaml", imported(parse(TB / ADAPTERS / "yaml_loader.py")))
        self.assertIn("tech_briefing", imported(parse(TB / "tests" / "test_select.py")))

    def test_collector_resolves_full_names(self):
        # 対照: 兄弟のアダプタを名指しで読む形 (from pkg import name と相対 import) を完全な名前で拾う
        tree = ast.parse("from adapters import yaml_loader\nfrom . import fetcher\nfrom .http_common import opener\n")
        self.assertEqual(
            imported_modules(tree, ADAPTERS),
            {
                "adapters", "adapters.yaml_loader", "adapters.fetcher",
                "adapters.http_common", "adapters.http_common.opener",
            },
        )
        # パッケージを渡さなければ相対 import は数えない
        self.assertEqual(imported_modules(tree), {"adapters", "adapters.yaml_loader"})

    def test_core_does_not_import_runtime_deps(self):
        files = py_files(TB / "tech_briefing")
        self.assertGreaterEqual(len(files), 10)
        for path in files:
            with self.subTest(path.name):
                self.assertEqual(imported(parse(path)) & (NOT_AT_IMPORT_TIME | {"bin"}), set())

    def test_dependent_modules_follow_siblings(self):
        # 依存の無いアダプタが依存のある兄弟を読むと、そのアダプタも依存を読むものになる (兄弟の兄弟も)
        imports = {
            "adapters.a": {"yaml"},
            "adapters.b": {"adapters", "adapters.a"},
            "adapters.c": {"adapters.b.load"},
            "adapters.d": {"json", "adapters.ab"},
        }
        self.assertEqual(dependent_modules(imports, RUNTIME_DEPS), {"adapters.a", "adapters.b", "adapters.c"})

    def test_dependent_adapters_are_the_ones_with_deps(self):
        # 対照: ast から導いた集合が実物の配置と合う (導き方が何も拾えなければ、下のテストの禁止が空になる)
        self.assertEqual(
            dependent_modules(adapter_imports(), RUNTIME_DEPS),
            {"adapters.anthropic_client", "adapters.yaml_loader"},
        )

    def test_tests_do_not_import_deps_dependent_adapters_or_bin(self):
        files = py_files(TB / "tests")
        self.assertGreaterEqual(len(files), 10)
        forbidden = forbidden_in_tests()
        for path in files:
            with self.subTest(path.name):
                names = imported_modules(parse(path))
                self.assertEqual({n for n in names if _hits_any(n, forbidden)}, set())

    def test_forbidden_names_catch_sibling_imports(self):
        # 対照: 禁止の照合は、依存のあるアダプタを読む形と、依存の無いアダプタを読む形を分ける
        forbidden = forbidden_in_tests()
        for source in ("from adapters import yaml_loader", "from adapters.anthropic_client import SdkMessages", "import yaml", "import run_select"):
            with self.subTest(source):
                self.assertNotEqual({n for n in imported_modules(ast.parse(source)) if _hits_any(n, forbidden)}, set())
        names = imported_modules(ast.parse("from adapters import fetcher, github_api, http_common"))
        self.assertEqual({n for n in names if _hits_any(n, forbidden)}, set())

    def test_module_level_collector_sees_nested_blocks(self):
        # 対照: module を読む時点で走る try / if / with / class の本体の import は拾い、関数の本体は拾わない
        tree = ast.parse(
            "try:\n    import yaml\nexcept ImportError:\n    pass\n"
            "if True:\n    import anthropic\n"
            "with open('x'):\n    import adapters.fetcher\n"
            "class C:\n    import json\n"
            "def f():\n    import zlib\n"
            "async def g():\n    import gzip\n"
        )
        self.assertEqual(top_level_imported(tree), {"yaml", "anthropic", "adapters", "json"})

    def test_bin_scripts_import_runtime_deps_only_inside_functions(self):
        for path in workflow_scripts():
            with self.subTest(path.name):
                tree = parse(path)
                self.assertEqual(top_level_imported(tree) & NOT_AT_IMPORT_TIME, set())
                # 対照: 実物のアダプタはどこかで読んでいる (読まないなら __main__ の配線が抜けている)
                self.assertIn(ADAPTERS, imported(tree))

    def test_no_dynamic_imports_outside_tests(self):
        # 上の検査は import 文だけを見るので、__import__ や importlib で読む形はすり抜ける
        files = py_files(TB / "tech_briefing") + py_files(TB / ADAPTERS) + py_files(TB / "bin")
        self.assertGreaterEqual(len(files), 25)
        for path in files:
            with self.subTest(str(path.relative_to(TB))):
                self.assertEqual(dynamic_imports(parse(path)), set())

    def test_dynamic_import_collector_sees_each_form(self):
        tree = ast.parse(
            "import importlib\nfrom importlib import import_module\nimport importlib.util\n"
            "__import__('yaml')\nbuiltins.__import__('yaml')\n"
        )
        self.assertEqual(
            dynamic_imports(tree),
            {
                "import importlib", "import importlib.import_module", "import importlib.util",
                "__import__", "builtins.__import__",
            },
        )

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


YAML_LOADER = TB / ADAPTERS / "yaml_loader.py"
# yaml_loader が yaml から使ってよい属性。load や unsafe_load は任意の Python のオブジェクトを作れる
YAML_ALLOWED_ATTRS = {"safe_load", "YAMLError"}


def yaml_import_forms(tree: ast.AST) -> set[str]:
    """yaml を読んだ import の形。from yaml import や別名は、下の名前の規則を通らずに load へ届く。"""
    forms = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] == "yaml":
                    forms.add(f"import {a.name} as {a.asname}" if a.asname else f"import {a.name}")
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "yaml":
            forms.add(f"from {node.module} import")
    return forms


def yaml_uses(tree: ast.AST) -> tuple[set[str], list[str]]:
    """(yaml の属性として読んだ名前, 許した属性の .value 以外で yaml の名前を読んだ箇所)。

    呼び出しの形だけを見ると、`f = yaml.load` や `getattr(yaml, "load")` のように呼ばずに取り出す形をすり抜ける。
    規則は「yaml の名前は、許した属性を取る式の左辺にしか現れない」の1つにする。
    """
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    attrs, violations = set(), []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Name) and node.id == "yaml" and isinstance(node.ctx, ast.Load)):
            continue
        parent = parents.get(node)
        if isinstance(parent, ast.Attribute) and parent.value is node:
            attrs.add(parent.attr)
            if parent.attr in YAML_ALLOWED_ATTRS:
                continue
        violations.append(ast.unparse(parent) if parent is not None else node.id)
    return attrs, violations


class YamlSafeLoad(unittest.TestCase):
    def test_only_yaml_loader_imports_yaml(self):
        # 呼び出し側のリポジトリの YAML は信頼できない。読む口を yaml_loader の1つに絞り、そこだけを下で見る
        files = [p for p in py_files(TB / ADAPTERS) + py_files(TB / "bin") if p != YAML_LOADER]
        self.assertIn(TB / "bin" / "gen_workflow.py", files)
        self.assertGreaterEqual(len(files), 9)
        for path in files:
            with self.subTest(str(path.relative_to(TB))):
                self.assertNotIn("yaml", imported(parse(path)))

    def test_yaml_loader_reaches_only_allowed_attrs(self):
        tree = parse(YAML_LOADER)
        self.assertEqual(yaml_import_forms(tree), {"import yaml"})
        attrs, violations = yaml_uses(tree)
        self.assertEqual(violations, [])
        # 一致で見る。yaml の名前を1つも拾えない集め方でも素通りしないため
        self.assertEqual(attrs, YAML_ALLOWED_ATTRS)

    def test_collector_sees_other_forms(self):
        # 対照: 呼ばずに取り出す形・getattr・属性の先の属性と、別の import の形を拾う
        tree = ast.parse(
            "import yaml as y\nfrom yaml import load\n"
            "f = yaml.load\ngetattr(yaml, 'load')\nyaml.load.__call__(t)\nyaml.unsafe_load(t)\n"
            "yaml.safe_load(t)\n"
        )
        self.assertEqual(yaml_import_forms(tree), {"import yaml as y", "from yaml import"})
        attrs, violations = yaml_uses(tree)
        # ast.walk は幅優先なので、並びではなく集めた箇所の多重集合で見る
        self.assertEqual(sorted(violations), sorted(["yaml.load", "getattr(yaml, 'load')", "yaml.load", "yaml.unsafe_load"]))
        self.assertEqual(attrs, {"load", "unsafe_load", "safe_load"})


class Names(unittest.TestCase):
    def test_no_stem_shadows_stdlib_or_runtime_deps(self):
        # 対照: 衝突する名前が集合に入っていることを先に見る
        self.assertIn("select", sys.stdlib_module_names)
        files = py_files(TB)
        self.assertGreaterEqual(len(files), 25)
        for path in files:
            with self.subTest(str(path.relative_to(TB))):
                self.assertNotIn(path.stem, sys.stdlib_module_names)
                self.assertNotIn(path.stem, RUNTIME_DEPS)


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
                names.update(name for name, _ in pins(path))
        # 対照: 固定の検査が空の dependencies で素通りしていない
        self.assertEqual(names, {"anthropic", "PyYAML"})

    def test_pinned_versions_are_the_checked_ones(self):
        # 版は triage の kwargs (betas・fallbacks・output_config・thinking・cache_control) と to_dict を wheel で確かめたもの。
        # 上げるときは同じ確かめをやり直してからここを変える (確かめていない版へ黙って動かさない)
        versions = {}
        for path in workflow_scripts():
            for name, version in pins(path):
                versions.setdefault(name, set()).add(version)
        self.assertEqual(versions, {"anthropic": {"2.7.0"}, "PyYAML": {"6.0.3"}})
        # SDK を使うのは triage の入口だけ
        triage = [d for d in pep723(TB / "bin" / "run_triage.py")["dependencies"] if d.startswith("anthropic")]
        self.assertEqual(triage, ["anthropic==2.7.0"])

    def test_each_script_has_the_same_exclude_newer(self):
        # dependencies は直接の依存しか固定しない。uv の exclude-newer で、解決に考える配布をその時刻より前に絞る
        # (docs.astral.sh/uv/guides/scripts、2026-11-25 確認)。間接の依存まで効くかは docs に明記が無いので、
        # hash までの固定は隣の lock が持つ (ScriptLock)。値は anthropic 2.7.0 の wheel の公開 (2026-11-09) の後
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


class ScriptLock(unittest.TestCase):
    """入口の隣の lock (`uv lock --script` が作る) が追跡下にあり、PEP 723 と食い違わないこと。

    `--locked` は起動の時点で、lock が無いとき (rc 2) と、依存・requires-python・前へずらした exclude-newer が
    PEP 723 と食い違うとき (rc 1) に止める。後ろへずらした exclude-newer は lock のまま黙って通るので、その一致は
    ここだけが見る。lock の形も含め uv 0.13.4 で確かめた。依存の無い入口の lock は `[manifest]` を持たない。
    どのテストが赤でも、直し方は `uv lock --script <入口>` で作り直して lock を追跡下に置くこと。
    """

    def test_each_script_has_a_tracked_lock(self):
        locks = [lock_of(p) for p in workflow_scripts()]
        out = subprocess.run(
            ["git", "ls-files", "-z", "--", *(str(p.relative_to(REPO)) for p in locks)],
            cwd=REPO, capture_output=True, check=True,
        ).stdout
        tracked = {REPO / p.decode() for p in out.split(b"\0") if p}
        self.assertEqual(tracked, set(locks))

    def test_each_lock_matches_pep723(self):
        seen = set()
        for path in workflow_scripts():
            with self.subTest(path.name):
                meta = pep723(path)
                lock = read_lock(path)
                regen = f"uv lock --script {path.relative_to(REPO)} で作り直す"
                self.assertEqual(lock.get("requires-python"), meta["requires-python"], regen)
                self.assertEqual(lock.get("options", {}).get("exclude-newer"), meta["tool"]["uv"]["exclude-newer"], regen)
                wanted = sorted((normalized(n), f"=={v}") for n, v in pins(path))
                got = sorted((r["name"], r["specifier"]) for r in lock.get("manifest", {}).get("requirements", []))
                self.assertEqual(got, wanted, regen)
                seen.update(n for n, _ in got)
        # 対照: 突き合わせが空の requirements どうしで素通りしていない
        self.assertEqual(seen, {"anthropic", "pyyaml"})

    def test_each_lock_resolves_direct_deps_to_the_pins_with_hashes(self):
        checked = 0
        for path in workflow_scripts():
            with self.subTest(path.name):
                packages = read_lock(path).get("package", [])
                versions = {p["name"]: p["version"] for p in packages}
                for name, version in pins(path):
                    self.assertEqual(versions.get(normalized(name)), version, name)
                    checked += 1
                # 間接の依存も含め、どの配布物も PyPI から取り、sha256 の hash を持つ (hash まで固定するのが lock を置く理由)
                for p in packages:
                    self.assertEqual(p.get("source"), {"registry": "https://pypi.org/simple"}, p["name"])
                    files = [p["sdist"]] if "sdist" in p else []
                    files += p.get("wheels", [])
                    self.assertTrue(files, p["name"])
                    for f in files:
                        self.assertTrue(f.get("hash", "").startswith("sha256:"), p["name"])
        # 対照: select の PyYAML と、triage の anthropic と PyYAML
        self.assertEqual(checked, 3)

    def test_normalized_follows_pep503(self):
        self.assertEqual(normalized("PyYAML"), "pyyaml")
        self.assertEqual(normalized("typing_extensions"), "typing-extensions")
        self.assertEqual(normalized("zope.Interface__x"), "zope-interface-x")


if __name__ == "__main__":
    unittest.main()
