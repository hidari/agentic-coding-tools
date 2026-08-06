#!/usr/bin/env python3
"""README.md を各パッケージの frontmatter から生成する。

一覧を手で書くと必ず drift するため、name と description は SKILL.md の frontmatter を
唯一の真実として読む。README を直接編集してはならない。

--check を渡すと生成結果と既存 README の差分を検査し、ずれていれば exit 1 する
(CI と pre-commit 用)。
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
README = os.path.join(ROOT, "README.md")

HEADER = """# agentic-coding-tools

Claude Code のための skill と plugin を集めたリポジトリ。個人の開発ワークフローを
そのまま置いてあり、誰でも参考にできる状態にしておくことを目的にしている。

配布は [apm](https://github.com/microsoft/apm) 経由。宣言だけを自分のリポジトリへ置き、
実体は clone 後に取得する形を想定している。

## 使い方

`apm.yml` の依存に次の形で書く。`<sha>` は追随したいコミットで固定する。

```yaml
dependencies:
  apm:
  - hidari/agentic-coding-tools/<パッケージのパス>#<sha>
```

`apm install` を実行すると `.claude/skills/` 配下へ配置される。

パッケージの root には `SKILL.md` と `.claude-plugin/plugin.json` の両方を置いてある。
apm は前者を見てディレクトリ全体を verbatim でコピーし、Claude Code は後者を見て
plugin として読み込む。この 2 つは互いを知らず独立に判定されるため、1 つのパッケージが
両方を兼ねる。
"""

FOOTER = """
## 構造の規約

パッケージの形と命名には、破ってもエラーにならず静かに壊れる規約がいくつかある。散文の
約束にすると必ず drift するため、すべて `scripts/check-package-shape.py` の検査に落として
ある。検査する項目の一覧は同スクリプトの docstring が canonical なので、ここには再掲しない。

`plugin.json` のフィールド定義そのものは `claude plugin validate --strict` が canonical。

## ライセンス

MIT
"""


def frontmatter(path: str) -> dict[str, str]:
    text = open(path, encoding="utf-8").read()
    m = re.match(r"---\n(.*?)\n---", text, re.S)
    if not m:
        return {}
    out = {}
    key = None
    for line in m.group(1).split("\n"):
        if re.match(r"^\S+:", line):
            key, v = line.split(":", 1)
            key = key.strip()
            out[key] = v.strip()
        elif key and line.startswith(" "):
            out[key] += " " + line.strip()
    return out


def build() -> str:
    parts = [HEADER]

    plugins_dir = os.path.join(ROOT, "plugins")
    rows = []
    for name in sorted(os.listdir(plugins_dir)):
        p = os.path.join(plugins_dir, name, "SKILL.md")
        if not os.path.exists(p):
            continue
        fm = frontmatter(p)
        inner_dir = os.path.join(plugins_dir, name, "skills")
        inner = sorted(os.listdir(inner_dir)) if os.path.isdir(inner_dir) else []
        rows.append((f"plugins/{name}", fm.get("description", ""), len(inner)))

    parts.append("\n## plugin\n")
    parts.append(
        "component を持つパッケージ。呼び出しは `<plugin 名>:<component 名>` の修飾名で行う。\n"
    )
    parts.append("\n| パス | component 数 | 説明 |\n|---|---|---|\n")
    for path, desc, n in rows:
        parts.append(f"| `{path}` | {n} | {desc} |\n")

    skills_dir = os.path.join(ROOT, "skills")
    srows = []
    for category in sorted(os.listdir(skills_dir)):
        cat = os.path.join(skills_dir, category)
        if not os.path.isdir(cat):
            continue
        for name in sorted(os.listdir(cat)):
            p = os.path.join(cat, name, "SKILL.md")
            if not os.path.exists(p):
                continue
            fm = frontmatter(p)
            srows.append((f"skills/{category}/{name}", fm.get("description", "")))

    parts.append("\n## skill\n")
    parts.append("単体の skill。component を持たない。\n")
    parts.append("\n| パス | 説明 |\n|---|---|\n")
    for path, desc in srows:
        parts.append(f"| `{path}` | {desc} |\n")

    parts.append(FOOTER)
    return "".join(parts)


if __name__ == "__main__":
    content = build()
    if "--check" in sys.argv:
        current = open(README, encoding="utf-8").read() if os.path.exists(README) else ""
        if current != content:
            print("[x] README.md が frontmatter と一致しない。scripts/gen-readme.py を実行すること")
            sys.exit(1)
        print("README.md は frontmatter と一致している")
    else:
        open(README, "w", encoding="utf-8").write(content)
        print(f"README.md を生成した ({len(content)} 文字)")
