#!/usr/bin/env python3
"""web-monkey-qa の手順書が explorer に打たせる agent-browser のコマンドの、資格情報まわりの性質。

手順書は散文だが、explorer はコマンドの行を literal に流用する (README の Tooling 節がそう宣言している)。
ここでは行の形だけで決まる性質を pin する。どれも agent-browser 0.39.1 で実測した漏れに対応する。

- `set credentials` は Authorization を全 origin のリクエストへ付ける。第三者のホストの script と img
  にも届き、CORS の preflight の要求ヘッダにも載った。だからこの形のコマンドを 1 行も持たない
- `network requests --json` の生の出力は、リクエストヘッダとレスポンスヘッダを丸ごと持つ
  (`set credentials` のときは Authorization の値もそのまま載った)。だから必ず jq で射影し、
  射影の中で headers を読まない
- `open --headers` は JSON が壊れているとエラー文に入力をそのまま出す (base64 の値が出た)。
  だから出力を捨てて rc だけで判定する
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "web-monkey-qa"


def command_lines() -> list[tuple[str, int, str]]:
    """手順書の中で agent-browser を呼ぶコマンドを (相対パス, 行番号, コマンド) で返す。

    コマンドはコードブロックの行と、`agent-browser ` で始まるインラインコードの 2 つ。散文は
    「set credentials を使わない」のように禁止を書くので、行ごと見ると禁止そのものに当たる。
    """
    found = []
    for path in sorted(PLUGIN.rglob("*.md")):
        fenced = False
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("```"):
                fenced = not fenced
                continue
            rel = str(path.relative_to(ROOT))
            if fenced:
                if "agent-browser" in line:
                    found.append((rel, n, line))
                continue
            found.extend((rel, n, span) for span in re.findall(r"`(agent-browser [^`]*)`", line))
    return found


class CommandShapeTests(unittest.TestCase):
    def test_the_manuals_contain_agent_browser_commands_at_all(self):
        # 下の検査が 0 行を見て緑にならないための対照
        lines = command_lines()
        self.assertTrue(any("network requests --json" in l for _, _, l in lines))
        self.assertTrue(any("--headers" in l for _, _, l in lines))

    def test_no_command_sets_credentials_for_every_origin(self):
        bad = [(p, n) for p, n, l in command_lines() if re.search(r"\bset credentials\b", l)]
        self.assertEqual(bad, [])

    def test_network_dumps_are_projected_without_headers(self):
        bad = []
        for p, n, l in command_lines():
            if "network requests --json" not in l:
                continue
            tail = l.split("network requests --json", 1)[1]
            if "| jq" not in tail or "headers" in tail.lower():
                bad.append((p, n))
            # presigned URL はクエリに署名を持つ。検知に要るのは path までなのでクエリを落としてから出す
            elif ".url" in tail and '.url | split("?")[0]' not in tail:
                bad.append((p, n))
        self.assertEqual(bad, [])

    def test_scoped_header_commands_discard_their_output(self):
        bad = [(p, n) for p, n, l in command_lines() if "--headers" in l and ">/dev/null 2>&1" not in l]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
