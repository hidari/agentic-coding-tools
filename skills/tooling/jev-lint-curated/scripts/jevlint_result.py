"""jev-lint 厳選ラッパの結果判定と要約。

`jevlint.py` (入口) の下流モジュールの 1 つ。上流の終了コードは判定に使わない
(spec 前提 7、12: 上流は `errors` があって finding が 0 件のときだけ 3 を返し、それ以外は
`blocks()` が finding と `--fail-on` から 1 か 0 を決める。HTTP 402 のような途中終了は
上流が 0/1/3 以外を返すこともある)。判定は上流の `--json` の stdout と `--record` の記録
だけから行う (spec の「結果の要約と終了コード」節)。終了コードの意味そのものは
`jevlint.py` のモジュール docstring が canonical で、ここでは数値を再掲するだけに留める。

このモジュールは他の jevlint* モジュールを import しない。`curated` (`jevlint.CURATED`)
と `version` (`jevlint.UPSTREAM_VERSION`) は呼び出し側から引数で受け取る (依存は入口から
下流へ一方向に流し、循環と pin の二重管理を防ぐため)。

`classify` と `summarize` はどちらも print しない。利用者への出力 (stdout に要約、stderr に
上流の stderr をそのまま流す) は呼び出し側 (Task 6 の `main()`) の責務であり、このモジュールは
判定と文字列の組み立てだけを持つ。
"""

from __future__ import annotations

import json
from dataclasses import dataclass


# 必須キーと期待する型。REQUIRED (公開する定数) はこの一覧から名前だけを取り出して作る
# ので、名前の一覧を 2 箇所に書かない。「.」は stats 配下 1 段のネストだけを表す (この形
# 以外のネストは今のところ無い)。
_REQUIRED_SPEC: "tuple[tuple[str, type], ...]" = (
    ("findings", list),
    ("stats.subjects", int),
    ("stats.missing", int),
    ("errors", list),
)

REQUIRED: "tuple[str, ...]" = tuple(name for name, _ in _REQUIRED_SPEC)


@dataclass(frozen=True)
class Outcome:
    """判定の結果。

    `doc` は上流の `--json` の stdout を読んだ dict、`record` は `--record` の記録を
    読んだ dict。`code` が 2 のときはどちらも None にする (判定に使える文書が無い、
    または不完全で summarize に渡してはいけないという意味を型で示すため)。`summarize`
    は `code` が 2 でない Outcome にだけ呼ぶ想定で、その前提を assert で守る。
    """

    code: int
    reason: str
    doc: "dict | None"
    record: "dict | None"


def _read_required(doc: dict) -> "dict[str, object] | None":
    """REQUIRED のキーを doc から辿る。1 つでも欠けるか型が違えば None。

    `.get(key, 0)` のような寛容な既定値は使わない。上流がキーを改名した版で、判定に
    使えない不完全な実行を黙って「0 件」と読んでしまうと、本来 2 にすべき判定が
    3 (missing/errors が 0 に化ける) や 0 (findings が 0 に化ける) に取り違わる。
    そうなると「答えが揃っていない」を「finding 無し」と誤読したまま終了コードだけが
    正常系の顔をする、という一番避けたい事故になる。
    """
    out: "dict[str, object]" = {}
    for name, expected in _REQUIRED_SPEC:
        node: object = doc
        for part in name.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        # bool は int のサブクラス (Python の仕様) なので、int を期待する箇所に
        # True/False が紛れ込むと isinstance(node, int) が誤って真になる。
        if expected is int and isinstance(node, bool):
            return None
        if not isinstance(node, expected):
            return None
        out[name] = node
    return out


def _dry_run_ok(doc: dict) -> bool:
    """`--dry-run --json` の応答が `dryRun: true` と件数としての `subjects` を持つか。"""
    if doc.get("dryRun") is not True:
        return False
    subjects = doc.get("subjects")
    return isinstance(subjects, int) and not isinstance(subjects, bool)


def classify(rc: int, stdout: str, record_text: "str | None", dry_run: bool) -> Outcome:
    """upstream の rc / stdout / `--record` の中身から判定する。

    spec (`ISSUE-65-spec.md` の「結果の要約と終了コード」節) の 2〜9 行目を上から順に
    判定する。1 行目 (引数・事前検査・host の用意・コミットの展開のエラー) は呼び出し側の
    責務で、この関数を呼ぶ前に別の例外 (`UsageError` / `HostError` / `TreeError`) として
    上がっている前提であり、ここでは扱わない。`--dry-run` のときは判定がここで止まり
    0 か 2 にしかならない (dry-run の応答は `findings` や `stats.missing` を持たない
    別の形の文書なので、3・1 の判定は行われない)。
    """
    if rc not in (0, 1, 3):
        return Outcome(2, f"上流の終了コードが 0/1/3 のいずれでもない: {rc}", None, None)

    try:
        doc = json.loads(stdout)
    except (ValueError, TypeError):
        return Outcome(2, "stdout が JSON として読めない", None, None)
    if not isinstance(doc, dict):
        return Outcome(2, "stdout の JSON がオブジェクトでない", None, None)

    if dry_run:
        if _dry_run_ok(doc):
            return Outcome(0, "dry-run: 見積もりを得た", doc, None)
        return Outcome(2, "dry-run の応答が dryRun/subjects を持たない", None, None)

    required = _read_required(doc)
    if required is None:
        return Outcome(
            2,
            f"必須キー ({' / '.join(REQUIRED)}) のどれかが無いか型が違う",
            None,
            None,
        )

    if record_text is None:
        return Outcome(2, "記録 (--record) が無い", None, None)
    try:
        record = json.loads(record_text)
    except (ValueError, TypeError):
        return Outcome(2, "記録 (--record) が JSON として読めない", None, None)
    if not isinstance(record, dict):
        return Outcome(2, "記録 (--record) の JSON がオブジェクトでない", None, None)

    missing = required["stats.missing"]
    errors = required["errors"]
    findings = required["findings"]

    # 上流の rc はここでは使わない (行 12 の前提: 不完全でなければ findings の有無だけで
    # 1 か 0 を決める。上流が rc 3 を返していても、こちらの判定で不完全でなければ 3 に
    # しない)。missing または errors があれば「答えが揃っていない」を finding の有無より
    # 先に判定する。
    if missing >= 1 or errors:
        return Outcome(
            3,
            f"答えの欠けか request のエラーがある (missing={missing}, errors={len(errors)})",
            doc,
            record,
        )
    if findings:
        return Outcome(1, f"finding が {len(findings)} 件ある", doc, record)
    return Outcome(0, "finding 無し、答えの欠けも無い", doc, record)


def _dropped_before_asking(doc: dict, curated: "tuple[str, ...]") -> "list[str]":
    """「聞く前に落とした量」の行。upstream の `ignored` (`--dry-run` の) 判定
    (`src/cli/dry-run.ts`) と同じく、件数が 0 のものは出さない -- 0 件は「何も
    落としていない」という事実であって、利用者が確かめたい「落とした」量ではない。
    """
    lines: "list[str]" = []

    ignored = doc.get("ignored")
    if isinstance(ignored, dict):
        subjects = ignored.get("subjects", 0)
        files = ignored.get("files", [])
        if subjects or files:
            whole = f"、{len(files)} ファイルを丸ごと" if files else ""
            lines.append(f"jev-lint-ignore コメントで {subjects} 件を見送った{whole}")

    unpaired = doc.get("unpaired")
    if isinstance(unpaired, dict) and unpaired.get("subjects", 0):
        lines.append(f"関連テストが無く {unpaired['subjects']} 件 (paired) を見送った")

    # silentRules は上流の全 rule を含みうるので、厳選 (curated) に絞る
    # (厳選の外の rule はこのラッパの利用者には関係が無い)。
    silent = [r for r in doc.get("silentRules", []) if r in curated]
    if silent:
        lines.append(f"何にも一致しなかった rule: {', '.join(silent)}")

    idle = doc.get("idleLanguages", [])
    if idle:
        joined = ", ".join(f"{item['language']} ({item['rules']})" for item in idle)
        lines.append(f"対象ファイルの無い言語: {joined}")

    return lines


def _mbt_lines(mbt_count: int) -> "list[str]":
    if mbt_count:
        return [f".mbt {mbt_count} 本は parser が無いので見ていない"]
    return []


def summarize(
    outcome: Outcome,
    *,
    sha: str,
    version: str,
    curated: "tuple[str, ...]",
    mbt_count: int,
    dry_run: bool,
) -> str:
    """要約の本文 (利用者へ出す日本語)。呼び出し側は `outcome.code` が 2 でないときにだけ
    呼ぶ (`doc` が判定に使える文書として存在する Outcome にだけ呼ぶ、という classify の
    契約をここで assert して守る)。
    """
    doc = outcome.doc
    assert doc is not None, "summarize は code が 2 でない Outcome にだけ呼ぶ"
    lines = [f"commit {sha}  jev-lint {version}"]

    if dry_run:
        # `subjects` は classify の `_dry_run_ok` が既に検査済みなので直接引ける。`usd` は
        # 判定に使わない (REQUIRED の対象外の) 記述用の値なので、他の記述用フィールドと
        # 同じく寛容な既定値で読む。
        lines.append(f"見積もり: subject {doc['subjects']} 件、費用 ${doc.get('usd', 0):.5f}")
        lines.extend(_dropped_before_asking(doc, curated))
        lines.extend(_mbt_lines(mbt_count))
        return "\n".join(lines)

    record = outcome.record
    assert record is not None, "dry-run でない Outcome は record も持つ (classify の契約)"
    model = record.get("model")
    lines.append(f"応答したモデル: {model if model is not None else '応答なし'}")

    stats = doc["stats"]
    subjects = stats["subjects"]
    by_file = stats.get("byFile", {})
    missing = stats["missing"]
    errors = doc["errors"]
    degraded = doc.get("degraded", [])
    spent = doc.get("spent", {})

    lines.append(f"見た対象: subject {subjects} 件 ({len(by_file)} ファイル)")
    lines.append(f"未回答: {missing} 件")
    if errors:
        lines.append(f"エラー: {len(errors)} 件 (例: {errors[0].get('error', '')})")
    else:
        lines.append("エラー: 0 件")
    lines.append(f"degraded: {len(degraded)} 件")
    lines.append(f"費用: ${spent.get('usd', 0):.5f} ({spent.get('calls', 0)} 回)")

    lines.extend(_dropped_before_asking(doc, curated))
    lines.extend(_mbt_lines(mbt_count))

    # 終了コード 3 のときは、指摘より先に「答えが無い」ことを言う (spec: 指摘だけを見て
    # 「全部揃った上での finding」と誤読させない)。N は stats.missing であって errors の
    # 件数と合算しない。upstream の run.ts には errors.push が 3 箇所あり、性質が違う:
    # 質問そのものの失敗 (askBatch の catch、537-556 行) はその batch の全 subject を
    # answer: null にして必ず stats.missing へ回る。一方、finding が既に付いた後の
    # 追加の問い合わせの失敗である explainFindings の catch (707-713 行) と
    # attributeFindings の catch (844-851 行) は、どちらも「finding は既に付いた
    # ままレポートされる (fail open)」ため stats.missing を動かさない。後者の 2 つの
    # どちらかだけが起きた実行は missing 0 のまま errors が非 0 になり、この行は
    # 「0 件は答えが無い」を出す。それは嘘ではない (答えが無い subject は実際に 0 件)
    # ので、errors の件数と理由は別行 (上の「エラー」) が担う。
    if outcome.code == 3:
        lines.append(f"{missing} 件は答えが無い")

    for finding in doc["findings"]:
        lines.append(
            f"{finding['file']}:{finding['line']} {finding['rule']} "
            f"{finding['value']:.2f}/{finding['cutoff']:.2f}"
        )

    return "\n".join(lines)
