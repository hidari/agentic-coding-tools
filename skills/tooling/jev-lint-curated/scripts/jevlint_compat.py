"""jev-lint 厳選ラッパの compat (版の追従) の比較。

`jevlint.py` (入口) の下流モジュールの 1 つ。pin した版と引数の版の `rules --json` を表にし
(`parse_rules`)、厳選した項目について比べ (`compare`)、終了コードを決める (`compat_exit`)。
終了コードの意味は `jevlint.py` のモジュール docstring が canonical。

このモジュールは子プロセスを起動せず、ファイルも読まない。rule.yml の中身は呼び出し側が
渡す `read` から受け取り、厳選の一覧 (`jevlint.CURATED`) も引数で受け取る。他の jevlint*
モジュールを import しないのは、依存を入口から下流へ一方向に流し、循環と pin の二重管理を
防ぐため。print もしない。報告の行を組み立てて返すところまでを持つ。
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from typing import Callable

# 設定の読み込みを確かめるときに置く最小のファイル。上流の parser がある言語 (typescript、
# rust、python、javascript) に 1 本ずつ。moonbit は parser が無いので置かない。
# 厳選した rule の subject (宣言、コメント、変数) が 1 つずつ当たる形にしてある
SAMPLES: dict = {
    "sample.ts": (
        "// Returns twice the value.\n"
        "export function double(value: number): number {\n"
        "  const result = value * 2;\n"
        "  return result;\n"
        "}\n"
    ),
    "sample.rs": (
        "/// Returns twice the value.\n"
        "pub fn double(value: i32) -> i32 {\n"
        "    let result = value * 2;\n"
        "    result\n"
        "}\n"
    ),
    "sample.py": (
        "def double(value):\n"
        '    """Return twice the value."""\n'
        "    result = value * 2\n"
        "    return result\n"
    ),
    "sample.js": (
        "// Returns twice the value.\n"
        "export function double(value) {\n"
        "  const result = value * 2;\n"
        "  return result;\n"
        "}\n"
    ),
}


@dataclass(frozen=True)
class RuleInfo:
    """`rules --json` の 1 件のうち、compat が比べるもの。`source` は rule.yml のパス。"""

    kind: str
    cutoff: float
    source: str


@dataclass(frozen=True)
class CompatReport:
    """比較の結果。

    `failures` は厳選の項目の消失と kind の変化 (終了コード 1)。`cutoffs` は
    (キー, pin した版の値, 引数の版の値)、`rule_changes` は (キー, unified diff) で、どちらも
    報告だけ。`added` は引数の版にだけある rule のキーと、その id が厳選の id か。
    """

    failures: list
    cutoffs: list
    rule_changes: list
    added: list


def _rule_key(entry: object) -> "tuple[str, RuleInfo]":
    """`rules[]` の 1 件を (キー, RuleInfo) にする。形が違えば ValueError。

    寛容に読まないのは、上流が項目を改名した版で「kind が無い」を「kind が変わった」と
    取り違えたり、`source` の無い項目の rule.yml を比べ損ねたりしないため。
    """
    if not isinstance(entry, dict):
        raise ValueError(f"rules --json の rule が object でない: {entry!r}")
    rule_id = entry.get("id")
    language_dir = entry.get("languageDir")
    kind = entry.get("kind")
    cutoff = entry.get("cutoff")
    source = entry.get("source")
    # bool は int のサブクラス (Python の仕様) なので、数の検査の前に弾く
    well_formed = (
        isinstance(rule_id, str)
        and bool(rule_id)
        and (language_dir is None or isinstance(language_dir, str))
        and isinstance(kind, str)
        and not isinstance(cutoff, bool)
        and isinstance(cutoff, (int, float))
        and isinstance(source, str)
    )
    if not well_formed:
        raise ValueError(f"rules --json の rule の形が想定と違う (id={rule_id!r})")
    # 上流は languageDir の無い rule を修飾無しの id で名指す (`cmd-rules.ts` の表示と同じ)
    key = f"{language_dir}/{rule_id}" if language_dir else rule_id
    return key, RuleInfo(kind=kind, cutoff=float(cutoff), source=source)


def parse_rules(text: str) -> "dict[str, RuleInfo]":
    """`rules --json` の stdout を `<languageDir>/<id>` で引ける表にする。

    JSON でない、`rules` の配列が無い、1 件でも形が違う、同じキーが 2 度出る、のいずれかは
    ValueError (呼び出し側が「検査不能」にする)。
    """
    try:
        doc = json.loads(text)
    except ValueError:
        raise ValueError("rules --json の出力が JSON でない") from None
    rules = doc.get("rules") if isinstance(doc, dict) else None
    if not isinstance(rules, list):
        raise ValueError("rules --json の出力に rules の配列が無い")
    table: "dict[str, RuleInfo]" = {}
    for entry in rules:
        key, info = _rule_key(entry)
        if key in table:
            raise ValueError(f"rules --json に同じ rule が 2 度出る: {key}")
        table[key] = info
    return table


def _rule_id(key: str) -> str:
    return key.rpartition("/")[2]


def _diff(key: str, before: bytes, after: bytes, labels: "tuple[str, str]") -> str:
    lines = difflib.unified_diff(
        before.decode("utf-8", errors="replace").splitlines(),
        after.decode("utf-8", errors="replace").splitlines(),
        fromfile=f"{labels[0]}/{key}/rule.yml",
        tofile=f"{labels[1]}/{key}/rule.yml",
        lineterm="",
    )
    text = "\n".join(lines)
    # splitlines は \r\n と \n を同じ行に分けるので、改行だけの変化は diff が空になる
    return text or "(行の中身は同じ。改行か末尾の違い)"


def compare(
    old: "dict[str, RuleInfo]",
    new: "dict[str, RuleInfo]",
    curated: "tuple[str, ...]",
    read: "Callable[[str], bytes]",
    *,
    labels: "tuple[str, str]" = ("old", "new"),
) -> CompatReport:
    """pin した版の表 `old` と引数の版の表 `new` を、厳選の項目 `curated` について比べる。

    rule.yml の中身は `read(source)` で読み、バイト列が違えば unified diff を添える (matcher と
    criteria は `rules --json` に出ないので、diff で見せる)。`labels` は
    (pin した版, 引数の版) の名前で、失敗の文面と diff の見出しに使う。増えた rule は厳選の
    外も含めて全部並べ、厳選の id と同じ id のものに印を付ける (別の言語に同じ rule が増えた
    ことを、厳選に足すかの候補として見せるため)。
    """
    failures: list = []
    cutoffs: list = []
    rule_changes: list = []
    for key in curated:
        before, after = old.get(key), new.get(key)
        if before is None or after is None:
            failures.append(f"{key}: {labels[0] if before is None else labels[1]} に無い")
            continue
        if before.kind != after.kind:
            failures.append(f"{key}: kind が {before.kind} から {after.kind} に変わった")
        if before.cutoff != after.cutoff:
            cutoffs.append((key, before.cutoff, after.cutoff))
        old_bytes, new_bytes = read(before.source), read(after.source)
        if old_bytes != new_bytes:
            rule_changes.append((key, _diff(key, old_bytes, new_bytes, labels)))
    curated_ids = {_rule_id(key) for key in curated}
    added = [(key, _rule_id(key) in curated_ids) for key in sorted(new) if key not in old]
    return CompatReport(failures=failures, cutoffs=cutoffs, rule_changes=rule_changes, added=added)


def compat_exit(report: CompatReport, config_rcs: "list[int]", engines_satisfied: bool) -> int:
    """1 (失敗) か 0 (報告だけ)。検査不能の 2 は呼び出し側が例外から決める。

    `config_rcs` は生成した設定を引数の版に読ませたときの上流の終了コード。上流の stderr は
    判定に使わない (上流の文面は版で変わる)。
    """
    if report.failures or any(rc != 0 for rc in config_rcs) or not engines_satisfied:
        return 1
    return 0


def describe(report: CompatReport) -> "list[str]":
    """報告の行。どの節も件数を必ず出す (0 件と「見ていない」を読み分けられるように)。"""
    lines = [f"失敗 {len(report.failures)} 件"]
    lines += [f"  {failure}" for failure in report.failures]
    lines.append(f"cutoff の変化 {len(report.cutoffs)} 件")
    lines += [f"  {key}: {before:g} -> {after:g}" for key, before, after in report.cutoffs]
    lines.append(f"rule.yml の変化 {len(report.rule_changes)} 件")
    for key, diff in report.rule_changes:
        lines.append(f"  {key}")
        lines += [f"    {line}" for line in diff.splitlines()]
    lines.append(f"増えた rule {len(report.added)} 件")
    lines += [f"  {key}{' (厳選の id)' if marked else ''}" for key, marked in report.added]
    return lines
