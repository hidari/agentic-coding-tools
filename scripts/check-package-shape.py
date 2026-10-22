#!/usr/bin/env python3
"""パッケージの形と命名規約を機械検証する。

apm と Claude Code はそれぞれ別のファイルを見て動きを決める。この検査は、その 2 つが
同時に成立する形 (root に SKILL.md と .claude-plugin/plugin.json が同居する形) を保つ。

検査する規約は次のとおり。いずれも破ってもエラーにならず静かに壊れる種類である。

1. plugin パッケージの root に SKILL.md がある (apm に verbatim 経路を選ばせる条件)
2. plugin.json の name がディレクトリ名と一致する (ずれると component の修飾名が変わる)
3. root SKILL.md の name が内部 skills/ の name と重複しない
   (衝突すると skills directory loader が plugin 側の skill を skip する)
4. plugin.json の skills 宣言が ["./"] でない
   (apm が無限再帰し File name too long で install が落ちる)
5. plugin.json の author にメールアドレスが入っていない
6. どの SKILL.md にも name と description の frontmatter がある
7. パッケージが自分のインストール先を絶対パスで名指ししていない
   (配布先では解決せず、開発機の配置にだけ当たって気づけない)
8. plugins/ と skills/ の中にテストのファイルを置かない
   (apm は SKILL.md を持つディレクトリを丸ごと展開先へ写し、0.33.0 でも作者がファイルを
   外す手段は無い。置いたテストはそのまま配布物に入る。置き場は scripts/package-tests/)
   Python のテストの定義は scripts/run-python-tests.py の discover を借りる。

終了コードは 0 (合格) か 1 (違反あり)。借用先の runner を読めないときは、規約 8 を
0 件で素通りさせず 2 (検査不能) で落ちる。
"""

import importlib.util
import json
import os
import re
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 走査から外すディレクトリ。いずれも配布物ではない副産物。
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__"}

# Python のテストの定義の canonical。写さずに借りる (定義を 2 箇所に書くと片方だけが古びる)。
# この checker の隣から読むのは、検査する root ではなく checker 自身の規約で判定するため
RUNNER = os.path.join(ROOT, "scripts", "run-python-tests.py")

# 借りる名前。rename が規約 8 の静かな 0 件にならないよう実在を検査する
BORROWED = ("discover", "SKIP_DIRS")

# runner は .mjs を扱わないので、この 1 箇所だけが定義を持つ
MJS_TEST_GLOB = "*.test.mjs"

PACKAGE_DIRS = ("plugins", "skills")


class CheckError(Exception):
    """検査を走らせられない。規約違反 (rc 1) と分けて rc 2 で返す。"""


def load_runner(path: str | Path = RUNNER):
    """run-python-tests.py を module として読む。読めない・名前が無いときは CheckError。"""
    spec = importlib.util.spec_from_file_location("run_python_tests_borrowed", path)
    if spec is None or spec.loader is None:
        raise CheckError(f"{path} を読み込めない")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (OSError, SyntaxError, ValueError) as e:
        raise CheckError(f"{path} を読み込めない: {e}") from e
    missing = [name for name in BORROWED if not hasattr(module, name)]
    if missing:
        raise CheckError(
            f"{path} に {', '.join(missing)} が無い。"
            "canonical が動いたので、この検査の借用先を追随させること"
        )
    return module


def tests_in_packages(root: Path, runner) -> list[str]:
    """規約 8: plugins/ と skills/ の下にあるテストのファイルを root 相対で返す。"""
    found = list(runner.discover(root))
    for base in PACKAGE_DIRS:
        # 除外は runner の SKIP_DIRS を借りる。この checker の SKIP_DIRS には .cache が無いので、
        # そちらを使うと同じ .cache の下で .py は runner が除外して報告されず、.mjs だけが
        # 報告される。規約 8 の 2 つの半分を 1 つの除外集合に揃える。.cache は .gitignore 済みの
        # 書き捨ての置き場で、追跡されるファイルを持たない
        found += [
            p
            for p in (root / base).rglob(MJS_TEST_GLOB)
            if not runner.SKIP_DIRS & set(p.relative_to(root).parts)
        ]
    rels = {p.relative_to(root).as_posix() for p in found}
    return sorted(r for r in rels if r.split("/", 1)[0] in PACKAGE_DIRS)


def frontmatter(path: str) -> dict[str, str]:
    text = open(path, encoding="utf-8").read()
    m = re.match(r"---\n(.*?)\n---", text, re.S)
    if not m:
        return {}
    out = {}
    for line in m.group(1).split("\n"):
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def check_skill_md(path: str, label: str, violations: list[str]) -> dict[str, str]:
    fm = frontmatter(path)
    for key in ("name", "description"):
        if not fm.get(key):
            violations.append(f"{label}: SKILL.md の frontmatter に {key} がない")
    return fm


def check(root: str | Path, runner_path: str | Path = RUNNER) -> tuple[list[str], dict[str, int]]:
    """root のリポジトリを検査し、(違反の一覧, 件数) を返す。

    件数のキーは plugin / skill / file (走査したファイル) / skipped (読めずに飛ばしたファイル)。
    借用先の runner を読めないときは CheckError。
    """
    runner = load_runner(runner_path)
    root = os.fspath(root)
    plugins = os.path.join(root, "plugins")
    skills = os.path.join(root, "skills")
    violations: list[str] = []
    checked = {"plugin": 0, "skill": 0, "file": 0, "skipped": 0}
    package_names: set[str] = set()

    # --- plugin パッケージ ---
    for name in sorted(os.listdir(plugins)) if os.path.isdir(plugins) else []:
        pkg = os.path.join(plugins, name)
        if not os.path.isdir(pkg):
            continue
        checked["plugin"] += 1
        package_names.add(name)
        label = f"plugins/{name}"

        root_skill = os.path.join(pkg, "SKILL.md")
        manifest = os.path.join(pkg, ".claude-plugin", "plugin.json")

        if not os.path.exists(root_skill):
            violations.append(f"{label}: root に SKILL.md がない (apm が verbatim 経路を選ばない)")
            continue
        if not os.path.exists(manifest):
            violations.append(f"{label}: .claude-plugin/plugin.json がない (plugin として認識されない)")
            continue

        fm = check_skill_md(root_skill, label, violations)
        d = json.load(open(manifest, encoding="utf-8"))

        if d.get("name") != name:
            violations.append(f"{label}: plugin.json の name ({d.get('name')}) がディレクトリ名と違う")

        inner_dir = os.path.join(pkg, "skills")
        inner = sorted(os.listdir(inner_dir)) if os.path.isdir(inner_dir) else []
        if fm.get("name") in inner:
            violations.append(
                f"{label}: root SKILL.md の name ({fm.get('name')}) が内部 skill と重複する"
            )

        declared = d.get("skills")
        if declared is not None and "./" in declared:
            violations.append(f'{label}: plugin.json の skills に "./" がある (apm が無限再帰する)')

        author = d.get("author")
        if isinstance(author, dict) and "email" in author:
            violations.append(f"{label}: plugin.json の author にメールアドレスが入っている")

        for s in inner:
            p = os.path.join(inner_dir, s, "SKILL.md")
            if os.path.exists(p):
                check_skill_md(p, f"{label}/skills/{s}", violations)

    # --- 単体 skill パッケージ ---
    for category in sorted(os.listdir(skills)) if os.path.isdir(skills) else []:
        cat_dir = os.path.join(skills, category)
        if not os.path.isdir(cat_dir):
            continue
        for name in sorted(os.listdir(cat_dir)):
            pkg = os.path.join(cat_dir, name)
            if not os.path.isdir(pkg):
                continue
            checked["skill"] += 1
            package_names.add(name)
            label = f"skills/{category}/{name}"
            p = os.path.join(pkg, "SKILL.md")
            if not os.path.exists(p):
                violations.append(f"{label}: SKILL.md がない")
                continue
            check_skill_md(p, label, violations)

    # --- インストール先の決め打ち ---
    # `~/.claude/<plugins|skills>/<パッケージ名>/` は開発機の配置にしか当たらない。
    # marketplace install は `plugins/cache/<marketplace>/<plugin>/<version>/` へ、
    # apm install は `<project>/.claude/skills/<name>/` へ置くため、どちらでも解決しない。
    # component (agents/ commands/) からは ${CLAUDE_PLUGIN_ROOT} を、
    # SKILL.md 本文からは ${CLAUDE_SKILL_DIR} を使う。
    #
    # ユーザー環境そのものへの参照 (`~/.claude/CLAUDE.md`、`~/.claude/skills/` 全体の一覧など) は
    # 正当なので対象外。パッケージ名を伴う自己参照の形だけを違反とする。
    if package_names:
        install_path = re.compile(
            r"(?:~|\$HOME|\$\{HOME\})/\.claude/(?:plugins|skills)/("
            + "|".join(re.escape(n) for n in sorted(package_names))
            + r")\b"
        )
        for base in (plugins, skills):
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
                for fn in sorted(filenames):
                    path = os.path.join(dirpath, fn)
                    try:
                        text = open(path, encoding="utf-8").read()
                    except (UnicodeDecodeError, OSError):
                        checked["skipped"] += 1
                        continue
                    checked["file"] += 1
                    rel = os.path.relpath(path, root)
                    for i, line in enumerate(text.split("\n"), 1):
                        m = install_path.search(line)
                        if m:
                            violations.append(
                                f"{rel}:{i}: インストール先を絶対パスで名指ししている"
                                f" ({m.group(1)})"
                            )

    # --- パッケージの中のテスト ---
    for rel in tests_in_packages(Path(root), runner):
        violations.append(f"{rel}: パッケージの中にテストがある (配布物に入る)")

    return violations, checked


def main() -> int:
    try:
        violations, checked = check(ROOT)
    except CheckError as e:
        print(f"[x] 検査できない: {e}")
        return 2
    print(
        f"検査した plugin: {checked['plugin']} 個 / 単体 skill: {checked['skill']} 個"
        f" / 走査したファイル: {checked['file']} 個"
    )
    if checked["skipped"]:
        print(f"読めずに飛ばしたファイル: {checked['skipped']} 個")

    if violations:
        print(f"違反 {len(violations)} 件:")
        for v in violations:
            print(f"  [x] {v}")
        return 1

    print("違反なし")
    return 0


if __name__ == "__main__":
    sys.exit(main())
