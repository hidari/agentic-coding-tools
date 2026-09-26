"""jevlint_compat.py の仕様。

compat の比較は、上流の `rules --json` を読んだ 2 つの表 (pin した版と引数の版) と、rule.yml の
中身を返す `read` だけから決まる。ここでは合成した表で、表の読み方 (`parse_rules`)、比較の
各分岐 (`compare`)、終了コードの判定 (`compat_exit`)、報告の行 (`describe`) を見る。上流と pnpm
をつないだときの性質 (子プロセスの cwd・env・argv、host の用意) は test_jevlint.py が持つ。

`curated` は jevlint.CURATED を使わず、2 項目の小さな一覧を渡す。このモジュールは厳選の一覧を
引数でしか受け取らないので、一覧の中身に依存しない形で比較の規則を書ける。
"""

from __future__ import annotations

import json
import unittest

import jevlint_compat
from jevlint_compat import CompatReport, RuleInfo

TS = "typescript/pure-name-is-pure"
PY = "python/var-name-describes-value"
CURATED = (TS, PY)
LABELS = ("0.7.0", "0.6.1")


def _entry(rule_id: str, language_dir: "str | None", **fields) -> dict:
    """上流の `rules --json` の `rules[]` の 1 件 (`src/cli/cmd-rules.ts` の形)。"""
    entry = {
        "id": rule_id,
        "languageDir": language_dir,
        "languages": ["TypeScript"],
        "kind": "noul",
        "subject": "function",
        "state": "located",
        "cutoff": 0.56,
        "loose": None,
        "severity": "warn",
        "ask": "Is the name honest?",
        "note": None,
        "explain": None,
        "extends": None,
        "context": None,
        "uncalibrated": False,
        "source": f"/pkg/rules/{language_dir}/{rule_id}/rule.yml",
    }
    entry.update(fields)
    return entry


def _doc(*entries: dict) -> str:
    return json.dumps({"rules": list(entries), "errors": [], "warnings": []})


class ParseRulesTests(unittest.TestCase):
    def test_upstream_shape_becomes_a_table_keyed_by_language_dir_and_id(self):
        text = _doc(
            _entry("pure-name-is-pure", "typescript"),
            _entry("var-name-describes-value", "python", kind="score", cutoff=1),
        )
        self.assertEqual(
            jevlint_compat.parse_rules(text),
            {
                TS: RuleInfo(
                    kind="noul", cutoff=0.56, source="/pkg/rules/typescript/pure-name-is-pure/rule.yml"
                ),
                PY: RuleInfo(
                    kind="score",
                    cutoff=1.0,
                    source="/pkg/rules/python/var-name-describes-value/rule.yml",
                ),
            },
        )

    def test_rule_without_language_dir_is_keyed_by_its_id(self):
        # 上流は languageDir の無い rule を修飾無しの id で名指す (`cmd-rules.ts` の表示と同じ)
        table = jevlint_compat.parse_rules(_doc(_entry("own-rule", None, source="/own/rule.yml")))
        self.assertEqual(list(table), ["own-rule"])

    def test_rejects_output_without_a_rules_array(self):
        for text in ("not json", "{}", "[]", json.dumps({"rules": {}}), ""):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    jevlint_compat.parse_rules(text)

    def test_rejects_entries_of_another_shape(self):
        # 寛容に読むと、上流が項目を改名した版で「kind が無い」を「kind が変わった」と
        # 取り違えたり、読めない source で比較が抜けたりする。表を作れないものは検査不能にする
        broken = {
            "entry is not an object": "pure-name-is-pure",
            "id missing": _entry("pure-name-is-pure", "typescript", id=None),
            "id empty": _entry("", "typescript"),
            "languageDir not a string": _entry("pure-name-is-pure", 7),
            "kind missing": _entry("pure-name-is-pure", "typescript", kind=None),
            "cutoff a string": _entry("pure-name-is-pure", "typescript", cutoff="0.56"),
            "cutoff a bool": _entry("pure-name-is-pure", "typescript", cutoff=True),
            "source null": _entry("pure-name-is-pure", "typescript", source=None),
        }
        for name, entry in broken.items():
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    jevlint_compat.parse_rules(json.dumps({"rules": [entry]}))
        # 対照: 同じ形で壊していない 1 件は読める
        self.assertIn(TS, jevlint_compat.parse_rules(_doc(_entry("pure-name-is-pure", "typescript"))))

    def test_rejects_the_same_key_twice(self):
        text = _doc(_entry("pure-name-is-pure", "typescript"), _entry("pure-name-is-pure", "typescript"))
        with self.assertRaises(ValueError):
            jevlint_compat.parse_rules(text)


class _Reader:
    """`compare` の `read` の偽物。source ごとの中身を返し、読んだ source を記録する。"""

    def __init__(self, contents: dict):
        self.contents = contents
        self.sources: list = []

    def __call__(self, source: str) -> bytes:
        self.sources.append(source)
        return self.contents[source]


def _tables(**new_changes) -> tuple:
    """同じ中身の 2 つの表と、その source の中身。`new_changes` は新しい表の項目を差し替える。"""
    old = {
        TS: RuleInfo(kind="noul", cutoff=0.56, source="/old/ts.yml"),
        PY: RuleInfo(kind="noul", cutoff=0.44, source="/old/py.yml"),
        "go/fn-name-promises": RuleInfo(kind="noul", cutoff=0.5, source="/old/go.yml"),
    }
    new = {
        TS: RuleInfo(kind="noul", cutoff=0.56, source="/new/ts.yml"),
        PY: RuleInfo(kind="noul", cutoff=0.44, source="/new/py.yml"),
        "go/fn-name-promises": RuleInfo(kind="noul", cutoff=0.5, source="/new/go.yml"),
    }
    new.update(new_changes)
    contents = {
        "/old/ts.yml": b"id: pure-name-is-pure\nthreshold: 0.56\n",
        "/new/ts.yml": b"id: pure-name-is-pure\nthreshold: 0.56\n",
        "/old/py.yml": b"id: var-name-describes-value\n",
        "/new/py.yml": b"id: var-name-describes-value\n",
    }
    return old, new, contents


def _compare(old: dict, new: dict, contents: dict) -> CompatReport:
    return jevlint_compat.compare(old, new, CURATED, _Reader(contents), labels=LABELS)


class CompareTests(unittest.TestCase):
    def test_identical_tables_report_nothing_but_every_curated_rule_yml_is_read(self):
        old, new, contents = _tables()
        read = _Reader(contents)
        report = jevlint_compat.compare(old, new, CURATED, read, labels=LABELS)
        self.assertEqual(report, CompatReport(failures=[], cutoffs=[], rule_changes=[], added=[]))
        # 対照: 何も報告しないのは比べていないからではない。厳選の項目の rule.yml は両方の版で
        # 読んでいる (厳選の外の go の rule.yml は読まない)
        self.assertEqual(sorted(read.sources), sorted(contents))

    def test_curated_item_missing_from_the_argument_version_is_a_failure(self):
        old, new, contents = _tables()
        del new[TS]
        report = _compare(old, new, contents)
        self.assertEqual(report.failures, [f"{TS}: 0.6.1 に無い"])

    def test_curated_item_missing_from_the_pinned_version_is_a_failure(self):
        old, new, contents = _tables()
        del old[PY]
        report = _compare(old, new, contents)
        self.assertEqual(report.failures, [f"{PY}: 0.7.0 に無い"])
        # 引数の版にしか無いものは増えた rule としても数える (表の差としてはそのとおりなので)
        self.assertEqual(report.added, [(PY, True)])

    def test_kind_change_is_a_failure(self):
        old, new, contents = _tables(**{PY: RuleInfo(kind="score", cutoff=0.44, source="/new/py.yml")})
        report = _compare(old, new, contents)
        self.assertEqual(report.failures, [f"{PY}: kind が noul から score に変わった"])
        self.assertEqual((report.cutoffs, report.rule_changes, report.added), ([], [], []))

    def test_cutoff_change_is_reported_not_failed(self):
        old, new, contents = _tables(**{TS: RuleInfo(kind="noul", cutoff=0.63, source="/new/ts.yml")})
        report = _compare(old, new, contents)
        self.assertEqual(report.cutoffs, [(TS, 0.56, 0.63)])
        self.assertEqual(report.failures, [])
        self.assertEqual(jevlint_compat.compat_exit(report, [0, 0], True), 0)

    def test_rule_yml_change_is_reported_with_a_unified_diff(self):
        old, new, contents = _tables()
        contents["/new/ts.yml"] = b"id: pure-name-is-pure\nat: 0.56\n"
        report = _compare(old, new, contents)
        self.assertEqual(
            report.rule_changes,
            [
                (
                    TS,
                    "\n".join(
                        [
                            f"--- 0.7.0/{TS}/rule.yml",
                            f"+++ 0.6.1/{TS}/rule.yml",
                            "@@ -1,2 +1,2 @@",
                            " id: pure-name-is-pure",
                            "-threshold: 0.56",
                            "+at: 0.56",
                        ]
                    ),
                )
            ],
        )
        self.assertEqual(report.failures, [])

    def test_rule_yml_change_without_a_line_difference_still_says_what_changed(self):
        # splitlines は \r\n と \n を同じ行に分けるので、改行だけの変化は diff が空になる。
        # 空の diff を「変化」として並べると、何が変わったのか読めない
        old, new, contents = _tables()
        contents["/new/py.yml"] = b"id: var-name-describes-value\r\n"
        report = _compare(old, new, contents)
        self.assertEqual(len(report.rule_changes), 1)
        key, text = report.rule_changes[0]
        self.assertEqual(key, PY)
        self.assertTrue(text)
        self.assertNotIn("@@", text)

    def test_rules_only_in_the_argument_version_are_added_with_the_curated_id_marked(self):
        extra = RuleInfo(kind="noul", cutoff=0.5, source="/new/extra.yml")
        old, new, contents = _tables(
            **{"typescript/new-rule": extra, "go/pure-name-is-pure": extra}
        )
        old["rust/old-only"] = extra
        read = _Reader(dict(contents, **{"/new/extra.yml": b"id: extra\n"}))
        report = jevlint_compat.compare(old, new, CURATED, read, labels=LABELS)
        self.assertEqual(
            report.added, [("go/pure-name-is-pure", True), ("typescript/new-rule", False)]
        )
        self.assertEqual(report.failures, [])
        # 厳選の外の rule.yml は、増えたものも読まない
        self.assertNotIn("/new/extra.yml", read.sources)


class CompatExitTests(unittest.TestCase):
    CLEAN = CompatReport(failures=[], cutoffs=[], rule_changes=[], added=[])

    def test_reports_alone_are_0(self):
        report = CompatReport(
            failures=[],
            cutoffs=[(TS, 0.56, 0.63)],
            rule_changes=[(TS, "diff")],
            added=[("go/pure-name-is-pure", True)],
        )
        self.assertEqual(jevlint_compat.compat_exit(report, [0, 0], True), 0)

    def test_each_failure_is_1(self):
        failed = CompatReport(failures=[f"{TS}: 0.6.1 に無い"], cutoffs=[], rule_changes=[], added=[])
        cases = (
            ("failures", failed, [0, 0], True),
            ("threshold の設定が読めない", self.CLEAN, [0, 2], True),
            ("既定の設定が読めない", self.CLEAN, [1, 0], True),
            ("engines", self.CLEAN, [0, 0], False),
        )
        for name, report, config_rcs, engines_satisfied in cases:
            with self.subTest(name=name):
                self.assertEqual(
                    jevlint_compat.compat_exit(report, config_rcs, engines_satisfied), 1
                )
        # 対照: どれにも当たらなければ 0
        self.assertEqual(jevlint_compat.compat_exit(self.CLEAN, [0, 0], True), 0)


class DescribeTests(unittest.TestCase):
    def test_every_section_is_counted_even_when_empty(self):
        report = CompatReport(failures=[], cutoffs=[], rule_changes=[], added=[])
        self.assertEqual(
            jevlint_compat.describe(report),
            ["失敗 0 件", "cutoff の変化 0 件", "rule.yml の変化 0 件", "増えた rule 0 件"],
        )

    def test_items_follow_their_section(self):
        report = CompatReport(
            failures=[f"{PY}: kind が noul から score に変わった"],
            cutoffs=[(TS, 0.56, 0.63)],
            rule_changes=[(TS, "--- a\n+++ b")],
            added=[("go/pure-name-is-pure", True), ("typescript/new-rule", False)],
        )
        self.assertEqual(
            jevlint_compat.describe(report),
            [
                "失敗 1 件",
                f"  {PY}: kind が noul から score に変わった",
                "cutoff の変化 1 件",
                f"  {TS}: 0.56 -> 0.63",
                "rule.yml の変化 1 件",
                f"  {TS}",
                "    --- a",
                "    +++ b",
                "増えた rule 2 件",
                "  go/pure-name-is-pure (厳選の id)",
                "  typescript/new-rule",
            ],
        )


class SamplesTests(unittest.TestCase):
    def test_one_sample_per_language_that_has_a_parser(self):
        # moonbit は parser が無いので置かない
        suffixes = sorted(name.rpartition(".")[2] for name in jevlint_compat.SAMPLES)
        self.assertEqual(suffixes, ["js", "py", "rs", "ts"])
        for name, text in jevlint_compat.SAMPLES.items():
            with self.subTest(name=name):
                self.assertTrue(text.strip())
                self.assertNotIn("/", name)


if __name__ == "__main__":
    unittest.main()
