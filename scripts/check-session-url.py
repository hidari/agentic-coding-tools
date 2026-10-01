#!/usr/bin/env python3
"""コミットメッセージに Claude Code のセッションの URL が入っていないかを検査する commit-msg hook。

    check-session-url.py <message ファイル>

付けないことは設定 (attribution.sessionUrl) で止めており、この検査は設定が届かない環境
(ユーザー設定を読まない環境や、managed settings が上書きする環境) への backstop である。
裁定の経緯は ISSUE-70。このリポジトリに閉じた hook にしてあり、配布している
commit-and-pr-message の gitleaks の config にも送る前の入口にも足さない。足すと
このリポジトリの裁定が配布先の既定の挙動に及ぶ。

検出するのは次の 2 つで、どちらも行単位で見る。git の trailer の解釈は使わない。squash の
本文ではブランチの各コミットの trailer が本文の途中に並び、git はそれを trailer と見ない
(main の履歴の 123 行のうち trailer と解釈されたのは 38 行だった。実測)。

- 行頭の Claude-Session: (大小を区別しない。git の trailer のキーと同じ)。値が <URL> の
  ような山括弧のプレースホルダだけのときは、形を説明する行として通す
- セッションの URL の形 (claude.ai/code/session_ に英数字が続く。スキームの有無は問わない)。
  main の履歴に出る 124 件の URL はすべてこの形だった (実測)。説明として書く <id> の形は
  当たらない

出力は検出の行番号と種別と、走査した行数だけで、URL の値は出さない。終了コードは
0 (検出なし) / 1 (検出あり) / 2 (検査不能: 引数の数が違う、読めない、UTF-8 でない)。

## 既知の限界

- scissors 行 (`# ------------------------ >8 ------------------------`) より後は見ない。
  git commit -v はそこより後に diff を足すので、検出すべき例を fixture に持つテストの
  コミットが自分の diff に当たる (ISSUE-16 と同じ偽陽性)。隣の commit-msg hook
  (check-leak-guard-denylist.py --check-text と issue-id.py --check-text) は逆に切らずに
  全文を見ており、ここだけが非対称である。代わりに -F で渡す本文に scissors 行を書くと、
  それより後は見られないまま git に残る (-F の既定の cleanup は scissors で切らない。
  実測: git 2.55.0)。
  コメント文字は # のときだけ scissors と見る。core.commentChar を変えた環境の -v では
  diff まで見て偽陽性になる側へ倒す
- GitHub 上で作る squash merge のメッセージと PR 本文には commit-msg hook が届かない
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SESSION_KEY = re.compile(r"^Claude-Session:", re.IGNORECASE)
PLACEHOLDER_ONLY = re.compile(r"^Claude-Session:\s*<[^<>]+>\s*$", re.IGNORECASE)
SESSION_URL = re.compile(r"claude\.ai/code/session_[A-Za-z0-9]")
SCISSORS = "# ------------------------ >8 ------------------------"


def lines_to_scan(text: str) -> list[str]:
    """scissors 行より前の行。検出と走査した行数の報告が同じ範囲を数えるために 1 つにする。"""
    lines = text.splitlines()
    return lines[: lines.index(SCISSORS)] if SCISSORS in lines else lines


def find_session_urls(text: str) -> list[tuple[int, str]]:
    """検出した (行番号, 種別) の一覧。種別は trailer か url で、1 行に 1 つだけ数える。"""
    findings = []
    for number, line in enumerate(lines_to_scan(text), start=1):
        if SESSION_KEY.match(line) and not PLACEHOLDER_ONLY.match(line):
            findings.append((number, "trailer"))
        elif SESSION_URL.search(line):
            findings.append((number, "url"))
    return findings


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: check-session-url.py <message ファイル>", file=sys.stderr)
        return 2
    try:
        text = Path(argv[0]).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        print(f"[x] message ファイルを読めない ({type(error).__name__})", file=sys.stderr)
        return 2
    findings = find_session_urls(text)
    for number, kind in findings:
        print(f"  [x] line {number}: {kind}", file=sys.stderr)
    print(f"走査した行: {len(lines_to_scan(text))} 行 / 検出 {len(findings)} 件")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
