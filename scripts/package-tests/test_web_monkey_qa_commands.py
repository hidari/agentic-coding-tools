#!/usr/bin/env python3
"""web-monkey-qa の手順書が explorer に打たせる agent-browser のコマンドの、資格情報まわりの性質。

explorer は手順書のコマンドを literal に流用するので、コマンドの形だけで決まる性質を pin する。
どれも agent-browser 0.39.1 で実測した漏れに対応する。

- `set credentials` は全 origin へ Authorization を付けるので、この形のコマンドを持たない
- network の生の出力は全ヘッダを持つので、出力を読むコマンドは agent の Phase 3 の射影と完全一致する
  1 つの形だけにする。部分一致で見ると、射影しない jq や tee を挟む形が通る
- `open --headers` は JSON が壊れているとエラー文に入力を出すので、出力を捨て、組み立てた値を
  `--headers` 以外へ渡さない

コマンドとして見るのは、コードブロックの全行と、散文の行のインラインコードのうち `agent-browser ` で
始まるものと network の出力を読む語を含むもの。散文は「set credentials を使わない」のように禁止を
書くので、行ごと見ると禁止そのものに当たる。フェンスの判定は check-issue-closure.py の
classify_lines を借りる (状態機械の写しを持つとずれる)。
"""

from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "web-monkey-qa"

PROJECTION = (
    "network requests --json | jq -c '{seen: (.data.requests | length), requests: [.data.requests[] | "
    "select(.status >= 400) | {status, method, url: (.url | split(\"?\")[0]), resourceType}]}'"
)
# network の出力を読む書き方。--clear はバッファを空にするだけで出力を持たないので含めない
NETWORK_READ = re.compile(r"network requests? (?!--clear)|network har\b")


def _closure():
    spec = importlib.util.spec_from_file_location(
        "check_issue_closure", ROOT / "scripts" / "check-issue-closure.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _commands() -> tuple[list[tuple[str, int, str]], list[str]]:
    """手順書の中のコマンドを (相対パス, 行番号, コマンド) で返す。フェンスが閉じない文書も返す。"""
    closure = _closure()
    found, unclosed = [], []
    for path in sorted(PLUGIN.rglob("*.md")):
        rel = str(path.relative_to(ROOT))
        lines, open_at_end = closure.classify_lines(path.read_text(encoding="utf-8"))
        if open_at_end:
            unclosed.append(rel)
        for n, (line, kind) in enumerate(lines, 1):
            if kind == closure.LINE_FENCE:
                found.append((rel, n, line))
            elif kind == closure.LINE_PROSE:
                found.extend(
                    (rel, n, span) for span in re.findall(r"`([^`]+)`", line)
                    if span.startswith("agent-browser ") or NETWORK_READ.search(span)
                )
    return found, unclosed


COMMANDS, UNCLOSED = _commands()


class CommandShapeTests(unittest.TestCase):
    def test_the_manuals_are_parsed_and_hold_the_commands_the_checks_look_at(self):
        # 下の検査が 0 件を見て緑にならないための対照。閉じないフェンスは以降の行を全部コマンドに数える
        self.assertEqual(UNCLOSED, [])
        self.assertTrue(any("--headers" in c for _, _, c in COMMANDS))

    def test_no_command_sets_credentials_for_every_origin(self):
        bad = [(p, n) for p, n, c in COMMANDS if re.search(r"\bset credentials\b", c)]
        self.assertEqual(bad, [])

    def test_network_output_is_read_only_through_the_single_projection(self):
        bad = [(p, n) for p, n, c in COMMANDS if NETWORK_READ.search(c) and PROJECTION not in c]
        self.assertEqual(bad, [])
        # 確定形を 2 か所に置くと片方だけが drift する (README と agent で形が分かれたことがある)
        self.assertEqual(len([c for _, _, c in COMMANDS if PROJECTION in c]), 1)

    def test_scoped_header_commands_discard_their_output(self):
        bad = [
            (p, n) for p, n, c in COMMANDS
            if "--headers" in c and '--headers "$H" >/dev/null 2>&1' not in c
        ]
        self.assertEqual(bad, [])

    def test_the_built_header_is_only_passed_to_headers(self):
        bad = [(p, n) for p, n, c in COMMANDS if c.count("$H") != c.count('--headers "$H"')]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
