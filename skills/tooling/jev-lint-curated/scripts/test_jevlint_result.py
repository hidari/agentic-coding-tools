"""jevlint_result.py (結果の判定と要約) の仕様。

見るのは `REQUIRED`、`classify`、`summarize` の 3 つの入口。合成する JSON の形は upstream
(jev-lint v0.7.0、`.cache/jev-lint/` の read-only チェックアウト) の実ソースから読んだもので、
推測では作らない。引用した行:

- `src/report.ts` `formatJson` (382-437 行): `findings[]` の 1 行 (`rule` は言語を含まない
  bare な id、`file`、`line`、`value`、`cutoff` 等) と、`stats`、`degraded`、`silentRules`、
  `idleLanguages`、`ignored`、`unpaired`、`errors` をトップレベルに持つ形
- `src/gate.ts` `collect` (175-198 行): `stats.subjects`・`stats.missing`・`stats.byFile`
  (`{file: {findings, subjects}}`) の形
- `src/report.ts` `silentRules` (313-338 行) と `ruleKey` (363-366 行): `silentRules` の
  各要素は `languageDir/id` の文字列
- `src/report.ts` `idleLanguages` (346-356 行): `{language, rules}` の配列
- `src/types.ts` `IgnoreStats` (745-752 行): `{subjects, files, unknownRules}`
- `src/types.ts` `UnpairedStats` (758-761 行): `{subjects, files}`
- `src/types.ts` `Spend` (791-806 行): `{calls, usd, ...}`
- `src/types.ts` `RunError` (808-812 行): `{file, subjects, error}`
- `src/cli/dry-run.ts` `dryRunDocument` (120-181 行): `dryRun: true`、`subjects` は数値、
  `usd`、`ignored`・`unpaired`・`idleLanguages`・`silentRules` は check/review と同じ形
- `src/run.ts` `buildRecord` (944-981 行): `model` は `result.servedModel ?? null`
  (何も応答が無ければ null)、`schema` は `"jev-lint-run-1"`

`classify` の判定は spec の「結果の要約と終了コード」節にある判定順 (2〜9 行目、`classify`
の docstring が引用する) を、次のデシジョンテーブルの行ごとにテストする (テストのコメントの
「表の行 N」はこの表を指す)。

 1. rc 2 -> 2
 2. rc 134 (0/1/3 以外) -> 2
 3. rc 0、stdout が JSON でない -> 2
 4. dry_run、dryRun/subjects がそろう -> 0
 5. dry_run、subjects が無い -> 2
 6. 非 dry_run、必須の 4 キー (findings / stats.subjects / stats.missing / errors) の
    どれか 1 つが欠けるか型違い -> 2 (キーごとに 1 件ずつ)
 7. 必須はそろうが record_text が None か JSON でない -> 2
 8. stats.missing 3、findings 0 -> 3
 9. errors 1 件、findings 2 件、上流の rc 1 -> 3 (errors が finding より先)
10. 不完全なし、findings 1 件、上流の rc 1 -> 1
11. 不完全なし、findings 0 件、上流の rc 0 -> 0
12. 不完全なし、findings 0 件、上流の rc 3 -> 3 にしない。errors が空なら 0 (rc は使わない)

`summarize` は spec が「要約として必ず出すもの」に列挙した各事実 (SHA・版・モデル・見た量・
落とした量・指摘・code 3 の「答えが無い」の位置) を個別に見る。
"""

from __future__ import annotations

import json
import unittest

import jevlint_result

# summarize のテスト専用の小さな厳選集合。jevlint.CURATED をそのまま import せず、
# フィルタが「curated に入っているものだけ通す」ことを確かめられる最小の対照
# (含むもの・含まないものを両方持つ) をここで作る。
CURATED_SAMPLE = (
    "typescript/pure-name-is-pure",
    "typescript/var-name-describes-value",
    "rust/var-name-describes-value",
)

FINDING_ROW = {
    # report.ts formatJson の row() (383-407 行) がそのまま持つキー一式。
    "rule": "var-name-describes-value",
    "severity": "warning",
    "messageId": "fail",
    "file": "src/foo.py",
    "line": 12,
    "endLine": 12,
    "value": 0.83,
    "confidence": 0.9,
    "explanation": None,
    "cutoff": 0.7,
    "margin": 0.13,
    "kind": "score",
    "level": None,
    "arm": "solo",
    "passes": None,
    "message": "変数名が値を表していない",
    "commit": None,
    "change": None,
    "cut": None,
    "violates": None,
}


def _doc(**overrides):
    """formatJson (report.ts 408-436 行) の形に沿った check/review の JSON。"""
    base = {
        "findings": [],
        "review": [],
        "stats": {
            "subjects": 10,
            "reported": 0,
            "missing": 0,
            "unsure": 0,
            "review": 0,
            "byRule": {},
            "byFile": {
                "src/foo.py": {"findings": 0, "subjects": 6},
                "src/bar.py": {"findings": 0, "subjects": 4},
            },
        },
        "degraded": [],
        "silentRules": [],
        "idleLanguages": [],
        "ignored": None,
        "unpaired": None,
        "retry": 1,
        "spent": {"calls": 3, "inputTokens": 1000, "usd": 0.02},
        "errors": [],
        "elapsedMs": 1234,
    }
    base.update(overrides)
    return base


def _record(**overrides):
    """buildRecord (run.ts 944-981 行) の形。"""
    base = {
        "schema": "jev-lint-run-1",
        "recorded": "2026-09-26T00:00:00.000Z",
        "model": "jev-latest-20260901",
        "arm": "solo",
        "cutoffs": {"typescript/var-name-describes-value": 0.7},
        "unsureBelow": None,
        "spent": {"calls": 3, "usd": 0.02},
        "rules": [],
        "answers": [],
    }
    base.update(overrides)
    return json.dumps(base)


class RequiredConstantTests(unittest.TestCase):
    def test_exact_names_and_order(self):
        self.assertEqual(
            jevlint_result.REQUIRED,
            ("findings", "stats.subjects", "stats.missing", "errors"),
        )


class ClassifyTests(unittest.TestCase):
    # 表の行 1: rc 2 -> 2
    def test_row1_rc_2_is_code_2(self):
        outcome = jevlint_result.classify(2, "{}", None, dry_run=False)
        self.assertEqual(outcome.code, 2)
        self.assertIsNone(outcome.doc)
        self.assertIsNone(outcome.record)

    # 表の行 2: rc 134 (0/1/3 以外) -> 2
    def test_row2_rc_134_is_code_2(self):
        outcome = jevlint_result.classify(134, "{}", None, dry_run=False)
        self.assertEqual(outcome.code, 2)

    # 表の行 3: rc 0、stdout が JSON でない -> 2
    def test_row3_stdout_not_json_is_code_2(self):
        outcome = jevlint_result.classify(0, "not json", None, dry_run=False)
        self.assertEqual(outcome.code, 2)

    # 表の行 4: dry_run、dryRun/subjects がそろえば 0
    def test_row4_dry_run_with_subjects_is_code_0(self):
        stdout = '{"dryRun": true, "subjects": 5, "usd": 0.01}'
        outcome = jevlint_result.classify(0, stdout, None, dry_run=True)
        self.assertEqual(outcome.code, 0)
        self.assertEqual(outcome.doc["subjects"], 5)
        self.assertIsNone(outcome.record)

    # 表の行 5: dry_run、subjects が無ければ 2
    def test_row5_dry_run_without_subjects_is_code_2(self):
        outcome = jevlint_result.classify(0, '{"dryRun": true}', None, dry_run=True)
        self.assertEqual(outcome.code, 2)
        self.assertIsNone(outcome.doc)

    def test_dry_run_false_dryrun_flag_is_code_2(self):
        # dryRun が偽値なら「持てば」の条件を満たさない。真偽混同 (dryRun: 1 のような
        # truthy) を通さないことも合わせて見る。
        outcome = jevlint_result.classify(
            0, '{"dryRun": false, "subjects": 5}', None, dry_run=True
        )
        self.assertEqual(outcome.code, 2)

    def test_dry_run_subjects_bool_is_code_2(self):
        # bool は int のサブクラスなので、subjects が真偽値のときに件数として
        # 誤って通らないことを見る。
        outcome = jevlint_result.classify(
            0, '{"dryRun": true, "subjects": true}', None, dry_run=True
        )
        self.assertEqual(outcome.code, 2)

    # 表の行 6: 非 dry_run、必須の 4 キーそれぞれが 1 件ずつ無ければ 2
    def test_row6_missing_findings_key_is_code_2(self):
        doc = _doc()
        del doc["findings"]
        outcome = jevlint_result.classify(0, json.dumps(doc), _record(), dry_run=False)
        self.assertEqual(outcome.code, 2)

    def test_row6_missing_stats_subjects_key_is_code_2(self):
        doc = _doc()
        del doc["stats"]["subjects"]
        outcome = jevlint_result.classify(0, json.dumps(doc), _record(), dry_run=False)
        self.assertEqual(outcome.code, 2)

    def test_row6_missing_stats_missing_key_is_code_2(self):
        doc = _doc()
        del doc["stats"]["missing"]
        outcome = jevlint_result.classify(0, json.dumps(doc), _record(), dry_run=False)
        self.assertEqual(outcome.code, 2)

    def test_row6_missing_errors_key_is_code_2(self):
        doc = _doc()
        del doc["errors"]
        outcome = jevlint_result.classify(0, json.dumps(doc), _record(), dry_run=False)
        self.assertEqual(outcome.code, 2)

    def test_row6_wrong_type_stats_missing_is_code_2(self):
        # 型違い (文字列) も「無い」と同じ扱いで 2 にする。
        doc = _doc()
        doc["stats"]["missing"] = "0"
        outcome = jevlint_result.classify(0, json.dumps(doc), _record(), dry_run=False)
        self.assertEqual(outcome.code, 2)

    def test_row6_findings_not_a_list_is_code_2(self):
        doc = _doc()
        doc["findings"] = {}
        outcome = jevlint_result.classify(0, json.dumps(doc), _record(), dry_run=False)
        self.assertEqual(outcome.code, 2)

    # 表の行 7: 必須はそろうが record_text が None か JSON でない -> 2
    def test_row7_record_text_none_is_code_2(self):
        outcome = jevlint_result.classify(0, json.dumps(_doc()), None, dry_run=False)
        self.assertEqual(outcome.code, 2)

    def test_row7_record_text_not_json_is_code_2(self):
        outcome = jevlint_result.classify(
            0, json.dumps(_doc()), "not json either", dry_run=False
        )
        self.assertEqual(outcome.code, 2)

    def test_record_json_not_an_object_is_code_2(self):
        # record が JSON として読めても配列などオブジェクトでなければ「読めない」に含める
        # (buildRecord は常に object を返すため、これが起きるのは記録が壊れている場合)。
        outcome = jevlint_result.classify(0, json.dumps(_doc()), "[1, 2, 3]", dry_run=False)
        self.assertEqual(outcome.code, 2)

    # 表の行 8: stats.missing 3、findings 0 -> 3
    def test_row8_missing_three_findings_zero_is_code_3(self):
        doc = _doc()
        doc["stats"]["missing"] = 3
        outcome = jevlint_result.classify(0, json.dumps(doc), _record(), dry_run=False)
        self.assertEqual(outcome.code, 3)
        self.assertIsNotNone(outcome.doc)
        self.assertIsNotNone(outcome.record)

    # 表の行 9: errors 1 件、findings 2 件、上流の rc 1 -> 3 (errors が finding より先)
    def test_row9_errors_with_findings_upstream_rc1_is_code_3(self):
        doc = _doc(
            findings=[FINDING_ROW, FINDING_ROW],
            errors=[{"file": "src/foo.py", "subjects": 3, "error": "HTTP 402"}],
        )
        outcome = jevlint_result.classify(1, json.dumps(doc), _record(), dry_run=False)
        self.assertEqual(outcome.code, 3)

    # 表の行 10: 不完全なし、findings 1 件、上流の rc 1 -> 1
    def test_row10_findings_one_upstream_rc1_is_code_1(self):
        doc = _doc(findings=[FINDING_ROW])
        outcome = jevlint_result.classify(1, json.dumps(doc), _record(), dry_run=False)
        self.assertEqual(outcome.code, 1)

    # 表の行 11: 不完全なし、findings 0 件、上流の rc 0 -> 0
    def test_row11_findings_zero_upstream_rc0_is_code_0(self):
        outcome = jevlint_result.classify(0, json.dumps(_doc()), _record(), dry_run=False)
        self.assertEqual(outcome.code, 0)

    # 表の行 12: 不完全なし、findings 0 件、上流の rc 3 -> 3 にしない。errors が空なら 0
    # (上流の rc は使わない)。
    def test_row12_upstream_rc3_without_incompleteness_is_code_0(self):
        outcome = jevlint_result.classify(3, json.dumps(_doc()), _record(), dry_run=False)
        self.assertEqual(outcome.code, 0)


class SummarizeCommonTests(unittest.TestCase):
    def test_sha_and_version_appear(self):
        outcome = jevlint_result.classify(0, json.dumps(_doc()), _record(), dry_run=False)
        text = jevlint_result.summarize(
            outcome,
            sha="abc1234",
            version="0.7.0",
            curated=CURATED_SAMPLE,
            mbt_count=0,
            dry_run=False,
        )
        self.assertIn("abc1234", text)
        self.assertIn("0.7.0", text)

    def test_model_shown_when_present(self):
        outcome = jevlint_result.classify(
            0, json.dumps(_doc()), _record(model="jev-latest-20260901"), dry_run=False
        )
        text = jevlint_result.summarize(
            outcome, sha="s", version="v", curated=CURATED_SAMPLE, mbt_count=0, dry_run=False
        )
        self.assertIn("jev-latest-20260901", text)

    def test_model_null_shows_no_response(self):
        outcome = jevlint_result.classify(
            0, json.dumps(_doc()), _record(model=None), dry_run=False
        )
        text = jevlint_result.summarize(
            outcome, sha="s", version="v", curated=CURATED_SAMPLE, mbt_count=0, dry_run=False
        )
        self.assertIn("応答なし", text)

    def test_dry_run_has_no_model_line(self):
        stdout = '{"dryRun": true, "subjects": 5, "usd": 0.01, "ignored": null, ' \
            '"unpaired": null, "idleLanguages": [], "silentRules": []}'
        outcome = jevlint_result.classify(0, stdout, None, dry_run=True)
        text = jevlint_result.summarize(
            outcome, sha="s", version="v", curated=CURATED_SAMPLE, mbt_count=0, dry_run=True
        )
        self.assertNotIn("応答したモデル", text)
        self.assertNotIn("応答なし", text)

    def test_seen_amount_fields(self):
        doc = _doc(
            stats={
                "subjects": 42,
                "reported": 1,
                "missing": 2,
                "unsure": 0,
                "review": 0,
                "byRule": {},
                "byFile": {
                    "src/foo.py": {"findings": 1, "subjects": 30},
                    "src/bar.py": {"findings": 0, "subjects": 12},
                },
            },
            errors=[{"file": "src/foo.py", "subjects": 1, "error": "HTTP 402: insufficient balance"}],
            degraded=[
                {
                    "file": "src/foo.py",
                    "rule": "var-name-describes-value",
                    "subjects": 2,
                    "from": "paired",
                    "to": "solo",
                    "reason": "no related test",
                }
            ],
            spent={"calls": 7, "inputTokens": 5000, "usd": 0.1234},
        )
        outcome = jevlint_result.classify(0, json.dumps(doc), _record(), dry_run=False)
        text = jevlint_result.summarize(
            outcome, sha="s", version="v", curated=CURATED_SAMPLE, mbt_count=0, dry_run=False
        )
        self.assertIn("subject 42 件", text)  # stats.subjects
        self.assertIn("2 ファイル", text)  # len(stats.byFile)
        self.assertIn("未回答: 2 件", text)  # stats.missing
        self.assertIn("エラー: 1 件", text)
        self.assertIn("HTTP 402: insufficient balance", text)  # errors[0].error
        self.assertIn("degraded: 1 件", text)
        self.assertIn("0.12340", text)  # spent.usd (.5f)
        self.assertIn("7 回", text)  # spent.calls

    def test_dropped_before_asking_shown_when_nonzero(self):
        doc = _doc(
            ignored={"subjects": 3, "files": ["a.py"], "unknownRules": []},
            unpaired={"subjects": 2, "files": ["b.py"]},
            silentRules=["typescript/pure-name-is-pure", "go/not-curated-rule"],
            idleLanguages=[{"language": "rust", "rules": 5}],
        )
        outcome = jevlint_result.classify(0, json.dumps(doc), _record(), dry_run=False)
        text = jevlint_result.summarize(
            outcome, sha="s", version="v", curated=CURATED_SAMPLE, mbt_count=0, dry_run=False
        )
        self.assertIn("3 件を見送った", text)
        self.assertIn("2 件 (paired)", text)
        # silentRules は curated に含まれるものだけ (curated に無い go/... は出ない)
        self.assertIn("typescript/pure-name-is-pure", text)
        self.assertNotIn("go/not-curated-rule", text)
        self.assertIn("rust (5)", text)

    def test_ignored_and_unpaired_hidden_when_zero(self):
        doc = _doc(
            ignored={"subjects": 0, "files": [], "unknownRules": []},
            unpaired={"subjects": 0, "files": []},
        )
        outcome = jevlint_result.classify(0, json.dumps(doc), _record(), dry_run=False)
        text = jevlint_result.summarize(
            outcome, sha="s", version="v", curated=CURATED_SAMPLE, mbt_count=0, dry_run=False
        )
        self.assertNotIn("見送った", text)

    def test_silent_rules_filtered_out_entirely_shows_no_line(self):
        doc = _doc(silentRules=["go/not-curated-rule"])
        outcome = jevlint_result.classify(0, json.dumps(doc), _record(), dry_run=False)
        text = jevlint_result.summarize(
            outcome, sha="s", version="v", curated=CURATED_SAMPLE, mbt_count=0, dry_run=False
        )
        self.assertNotIn("go/not-curated-rule", text)
        self.assertNotIn("一致しなかった", text)

    def test_mbt_count_nonzero_shown(self):
        outcome = jevlint_result.classify(0, json.dumps(_doc()), _record(), dry_run=False)
        text = jevlint_result.summarize(
            outcome, sha="s", version="v", curated=CURATED_SAMPLE, mbt_count=2, dry_run=False
        )
        self.assertIn(".mbt 2 本は parser が無いので見ていない", text)

    def test_mbt_count_zero_not_shown(self):
        outcome = jevlint_result.classify(0, json.dumps(_doc()), _record(), dry_run=False)
        text = jevlint_result.summarize(
            outcome, sha="s", version="v", curated=CURATED_SAMPLE, mbt_count=0, dry_run=False
        )
        self.assertNotIn(".mbt", text)

    def test_finding_line_format_and_japanese_filename_with_space(self):
        finding = dict(FINDING_ROW)
        finding["file"] = "src/日本語 ファイル.py"
        finding["line"] = 7
        finding["value"] = 0.91
        finding["cutoff"] = 0.7
        doc = _doc(findings=[finding])
        outcome = jevlint_result.classify(1, json.dumps(doc), _record(), dry_run=False)
        text = jevlint_result.summarize(
            outcome, sha="s", version="v", curated=CURATED_SAMPLE, mbt_count=0, dry_run=False
        )
        self.assertIn(
            "src/日本語 ファイル.py:7 var-name-describes-value 0.91/0.70", text
        )

    def test_code_3_via_realistic_error_batch_missing_matches_error_subjects(self):
        # run.ts の askBatch (537-556 行): 失敗した batch の全 subject は answer: null に
        # なり gate.ts の decide (77-90 行) が messageId "missing" にするので、
        # 実際の質問失敗は必ず stats.missing にも現れる。errors[0].subjects と
        # stats.missing が対応する、現実的な組み合わせを見る。
        doc = _doc(
            stats={
                "subjects": 5,
                "reported": 0,
                "missing": 3,
                "unsure": 0,
                "review": 0,
                "byRule": {},
                "byFile": {"src/foo.py": {"findings": 0, "subjects": 5}},
            },
            errors=[{"file": "src/foo.py", "subjects": 3, "error": "HTTP 402: insufficient balance"}],
        )
        outcome = jevlint_result.classify(1, json.dumps(doc), _record(), dry_run=False)
        self.assertEqual(outcome.code, 3)
        text = jevlint_result.summarize(
            outcome, sha="s", version="v", curated=CURATED_SAMPLE, mbt_count=0, dry_run=False
        )
        self.assertIn("3 件は答えが無い", text)
        self.assertIn("エラー: 1 件", text)

    def test_code_3_via_errors_only_missing_stays_zero(self):
        # run.ts の explainFindings (707-713 行): finding には既に answer が付いているので、
        # explain フォローアップだけが失敗しても stats.missing は動かない ("A failed
        # follow-up leaves the finding unlabelled and is reported; it never removes a
        # finding")。errors はあるが missing は 0 のままという組み合わせが実際に起きうる。
        # この行は「答えの無い subject の数」を言うので 0 のままで嘘にはならない -- errors
        # の件数と理由は別行が伝える。
        doc = _doc(
            findings=[FINDING_ROW],
            errors=[{"file": "src/foo.py", "subjects": 1, "error": "explain: HTTP 402"}],
        )
        outcome = jevlint_result.classify(1, json.dumps(doc), _record(), dry_run=False)
        self.assertEqual(outcome.code, 3)
        text = jevlint_result.summarize(
            outcome, sha="s", version="v", curated=CURATED_SAMPLE, mbt_count=0, dry_run=False
        )
        self.assertIn("0 件は答えが無い", text)
        self.assertIn("エラー: 1 件 (例: explain: HTTP 402)", text)

    def test_code_3_missing_line_precedes_first_finding_line(self):
        doc = _doc(
            stats={
                "subjects": 5,
                "reported": 1,
                "missing": 4,
                "unsure": 0,
                "review": 0,
                "byRule": {},
                "byFile": {"src/foo.py": {"findings": 1, "subjects": 5}},
            },
            findings=[FINDING_ROW],
        )
        outcome = jevlint_result.classify(1, json.dumps(doc), _record(), dry_run=False)
        self.assertEqual(outcome.code, 3)
        text = jevlint_result.summarize(
            outcome, sha="s", version="v", curated=CURATED_SAMPLE, mbt_count=0, dry_run=False
        )
        missing_pos = text.index("4 件は答えが無い")
        finding_pos = text.index("src/foo.py:12")
        self.assertLess(missing_pos, finding_pos)


class SummarizeDryRunTests(unittest.TestCase):
    def test_dry_run_summary_fields(self):
        stdout = (
            '{"dryRun": true, "subjects": 8, "usd": 0.05, '
            '"ignored": {"subjects": 3, "files": ["a.py"], "unknownRules": []}, '
            '"unpaired": {"subjects": 1, "files": ["b.py"]}, '
            '"idleLanguages": [{"language": "rust", "rules": 5}], '
            '"silentRules": ["typescript/pure-name-is-pure"]}'
        )
        outcome = jevlint_result.classify(0, stdout, None, dry_run=True)
        text = jevlint_result.summarize(
            outcome, sha="deadbee", version="0.7.0", curated=CURATED_SAMPLE, mbt_count=1, dry_run=True
        )
        self.assertIn("deadbee", text)
        self.assertIn("0.7.0", text)
        self.assertIn("subject 8 件", text)  # subjects
        self.assertIn("0.05000", text)  # usd (.5f)
        self.assertIn("3 件を見送った", text)  # ignored
        self.assertIn("1 件 (paired)", text)  # unpaired
        self.assertIn("typescript/pure-name-is-pure", text)  # silentRules ∩ curated
        self.assertIn("rust (5)", text)  # idleLanguages
        self.assertIn(".mbt 1 本は parser が無いので見ていない", text)


if __name__ == "__main__":
    unittest.main()
