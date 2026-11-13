#!/usr/bin/env python3
"""web-monkey-qa の手順書が explorer に打たせる agent-browser のコマンドの、資格情報まわりの性質。

手順書は散文だが、explorer はコマンドを literal に流用する (README の Tooling 節と agent の手順書が
そう宣言している)。ここではコマンドの形だけで決まる性質を pin する。どれも agent-browser 0.39.1 で
実測した漏れに対応する。

- `set credentials` は Authorization を全 origin のリクエストへ付ける。第三者のホストの script と img
  にも届き、CORS の preflight の要求ヘッダにも載った。だからこの形のコマンドを 1 つも持たない
- network の生の出力は、リクエストヘッダとレスポンスヘッダを丸ごと持つ (`set credentials` のときは
  Authorization の値もそのまま載った)。だから network の出力を読むコマンドは、agent の Phase 3 の
  射影と完全一致する 1 つの形だけにする。部分一致で見ると、射影しない jq や tee を挟む形が通る
- `open --headers` は JSON が壊れているとエラー文に入力をそのまま出す (base64 の値が出た)。だから
  出力を捨てて rc だけで判定し、組み立てた値を表示する形を持たない

コマンドとして見るのは、コードブロックの全行と、インラインコードのうち `agent-browser ` で始まるものと
network の出力を読む語を含むもの。散文は「set credentials を使わない」のように禁止を書くので、行ごと
見ると禁止そのものに当たる。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "web-monkey-qa"

PROJECTION = (
    "network requests --json | jq -c '.data.requests[] | "
    "{status, method, url: (.url | split(\"?\")[0]), resourceType}'"
)
# network の出力を読む書き方。--clear はバッファを空にするだけで出力を持たないので含めない
NETWORK_READ = re.compile(r"network requests? (?!--clear)|network har\b")


def commands() -> list[tuple[str, int, str]]:
    """手順書の中のコマンドを (相対パス, 行番号, コマンド) で返す。"""
    found = []
    for path in sorted(PLUGIN.rglob("*.md")):
        rel = str(path.relative_to(ROOT))
        fenced = False
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("```"):
                fenced = not fenced
                continue
            if fenced:
                found.append((rel, n, line))
                continue
            for span in re.findall(r"`([^`]+)`", line):
                if span.startswith("agent-browser ") or NETWORK_READ.search(span):
                    found.append((rel, n, span))
    return found


class CommandShapeTests(unittest.TestCase):
    def test_the_manuals_contain_the_commands_the_checks_look_at(self):
        # 下の検査が 0 件を見て緑にならないための対照
        cmds = [c for _, _, c in commands()]
        self.assertTrue(any(PROJECTION in c for c in cmds))
        self.assertTrue(any("--headers" in c for c in cmds))
        self.assertTrue(any("$H" in c for c in cmds))

    def test_no_command_sets_credentials_for_every_origin(self):
        bad = [(p, n) for p, n, c in commands() if re.search(r"\bset credentials\b", c)]
        self.assertEqual(bad, [])

    def test_network_output_is_read_only_through_the_single_projection(self):
        bad = [(p, n) for p, n, c in commands() if NETWORK_READ.search(c) and PROJECTION not in c]
        self.assertEqual(bad, [])
        # 確定形を 2 か所に置くと片方だけが drift する (README と agent で形が分かれたことがある)
        self.assertEqual(len([c for _, _, c in commands() if PROJECTION in c]), 1)

    def test_scoped_header_commands_discard_their_output(self):
        bad = [
            (p, n) for p, n, c in commands()
            if "--headers" in c and '--headers "$H" >/dev/null 2>&1' not in c
        ]
        self.assertEqual(bad, [])

    def test_the_built_header_is_only_assigned_and_passed_to_headers(self):
        bad = []
        for p, n, c in commands():
            uses = c.count("$H")
            passed = c.count('--headers "$H"')
            if uses != passed:
                bad.append((p, n))
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
