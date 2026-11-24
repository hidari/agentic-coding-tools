import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from fakes import FakeGitHub  # noqa: E402
from tech_briefing.constants import (  # noqa: E402
    BOT_LOGIN,
    FAILURE_KINDS,
    MARKER,
    SELECT_INPUT_TOO_LARGE,
    SELECT_TRIAGE,
    SELECT_UNEXPECTED,
)
from tech_briefing.protocols import CommentRef  # noqa: E402
from tech_briefing.render import (  # noqa: E402
    plan_post,
    render_failure,
    render_report,
    render_too_large,
    render_unexpected,
    sanitize,
    should_post,
)

ZWSP = chr(0x200B)
# リポジトリの記法検査が # と数字の連なりのリテラルに反応するため、# は変数から組む
HASH = "#"


def comment(author=BOT_LOGIN, body=MARKER + "\n報告", id=1):
    return CommentRef(id, author, body, "2026-11-24T01:00:00Z")


def report(items=None, unreachable=None, notes=""):
    return {"items": items or [], "unreachable_sources": unreachable or [], "notes": notes}


def item(title="t", classification="A", findings="f", sources=None):
    return {"title": title, "classification": classification, "findings": findings, "sources": sources or []}


class SanitizeTest(unittest.TestCase):
    def test_mention_gets_invisible_char_after_at(self):
        self.assertEqual(sanitize("@user"), "@" + ZWSP + "user")

    def test_issue_autolink_gets_invisible_char_after_hash(self):
        self.assertEqual(sanitize(f"{HASH}123"), HASH + ZWSP + "123")

    def test_html_is_entity_escaped(self):
        out = sanitize("<script>a&b</script>")
        self.assertNotIn("<", out)
        self.assertNotIn(">", out)
        self.assertEqual(out, "&lt;script&gt;a&amp;b&lt;/script&gt;")

    def test_numeric_entity_cannot_rebuild_a_mention(self):
        # & を先に潰すので &#64; は文字参照として解釈されない
        self.assertTrue(sanitize("&#64;user").startswith("&amp;#" + ZWSP))

    def test_link_and_image_syntax_is_backslash_escaped(self):
        self.assertEqual(sanitize("[a](b)"), "\\[a\\]\\(b\\)")
        self.assertEqual(sanitize("![x](y)"), "\\!\\[x\\]\\(y\\)")

    def test_pipe_is_escaped(self):
        self.assertEqual(sanitize("a|b"), "a\\|b")

    def test_backslash_before_pipe_cannot_cancel_the_escape(self):
        self.assertEqual(sanitize("a\\|b"), "a\\\\\\|b")

    def test_backtick_is_escaped(self):
        self.assertEqual(sanitize("`x`"), "\\`x\\`")

    def test_newlines_become_one_space_each(self):
        for nl in ("\n", "\r", "\r\n", chr(0x2028), chr(0x2029)):
            with self.subTest(nl=repr(nl)):
                self.assertEqual(sanitize("a" + nl + "b"), "a b")

    def test_url_scheme_gets_invisible_char_after_colon(self):
        self.assertEqual(sanitize("https://x"), "https:" + ZWSP + "//x")
        self.assertEqual(sanitize("HTTP://x"), "HTTP:" + ZWSP + "//x")

    def test_lone_surrogate_is_replaced(self):
        out = sanitize("a" + chr(0xD800) + "b")
        out.encode("utf-8")
        self.assertEqual(out, "a" + chr(0xFFFD) + "b")

    def test_plain_text_and_japanese_are_kept(self):
        self.assertEqual(sanitize("影響なし v1.2 ok"), "影響なし v1.2 ok")


class RenderReportTest(unittest.TestCase):
    def rows(self, body):
        return [ln for ln in body.split("\n") if ln.startswith("| ")][2:]

    def test_starts_with_marker_and_has_header_columns(self):
        body = render_report(report())
        self.assertTrue(body.startswith(MARKER + "\n"))
        self.assertIn("| 項目 | 分類 | 所見 | 取得元 |", body)

    def test_row_count_matches_item_count(self):
        body = render_report(report([item(title="a"), item(title="b"), item(title="c")]))
        self.assertEqual(len(self.rows(body)), 3)

    def test_pipe_in_item_does_not_add_columns(self):
        body = render_report(report([item(title="| x |", findings="a|b", sources=["s|t"])]))
        (row,) = self.rows(body)
        unescaped = re.findall(r"(?<!\\)\|", row)
        self.assertEqual(len(unescaped), 5)

    def test_multiline_finding_stays_in_one_row(self):
        body = render_report(report([item(findings="l1\nl2\r\nl3")]))
        self.assertEqual(len(self.rows(body)), 1)

    def test_sources_are_joined_with_comma_and_sanitized(self):
        body = render_report(report([item(sources=["a", "@b"])]))
        self.assertIn("a, @" + ZWSP + "b", body)

    def test_all_llm_strings_are_sanitized(self):
        body = render_report(
            report(
                [item(title=f"@t {HASH}1", findings="<b>", sources=["[x](y)"])],
                unreachable=[f"@u {HASH}2"],
                notes=f"@n <i> {HASH}3",
            )
        )
        for bad in ("@t", "@u", "@n", f"{HASH}1", f"{HASH}2", f"{HASH}3", "<b>", "<i>", "[x](y)"):
            self.assertNotIn(bad, body)
        # 無害化した後の本文に、本物のメンション (@ の直後が英数字) が残らない
        self.assertIsNone(re.search(r"@\w", body))
        self.assertIsNone(re.search(r"#\d", body))

    def test_classification_outside_a_to_d_is_question_mark(self):
        body = render_report(report([item(classification="E"), item(classification="@x"), item(classification="B")]))
        rows = self.rows(body)
        self.assertIn("| ? |", rows[0])
        self.assertIn("| ? |", rows[1])
        self.assertIn("| B |", rows[2])

    def test_unreachable_sources_and_notes_are_listed(self):
        body = render_report(report(unreachable=["u1", "u2"], notes="メモ"))
        self.assertIn("- u1", body)
        self.assertIn("- u2", body)
        self.assertIn("メモ", body)

    def test_malformed_report_does_not_raise(self):
        for bad in ({}, {"items": None}, {"items": [None, "x", {}]}, {"items": [{"sources": "abc"}]}, {"notes": None}):
            with self.subTest(bad=bad):
                body = render_report(bad)
                self.assertTrue(body.startswith(MARKER))

    def test_non_dict_report_does_not_raise(self):
        self.assertTrue(render_report(None).startswith(MARKER))


class RenderFailureTest(unittest.TestCase):
    def test_failure_contains_fixed_phrase(self):
        body = render_failure("no-report")
        self.assertTrue(body.startswith(MARKER + "\n"))
        self.assertIn("トリアージできなかった: no-report", body)

    def test_unknown_kind_is_internal_error(self):
        body = render_failure("@evil <x>")
        self.assertIn("トリアージできなかった: internal-error", body)
        self.assertNotIn("evil", body)

    def test_every_known_kind_renders_itself(self):
        for kind in FAILURE_KINDS:
            self.assertIn("トリアージできなかった: " + kind, render_failure(kind))

    def test_fixed_templates_start_with_marker(self):
        self.assertTrue(render_unexpected().startswith(MARKER + "\n"))
        self.assertTrue(render_too_large().startswith(MARKER + "\n"))
        self.assertNotEqual(render_unexpected(), render_too_large())


class ShouldPostTest(unittest.TestCase):
    def test_false_when_marked_comment_by_bot(self):
        self.assertFalse(should_post(FakeGitHub(comments={1: [comment()]}), 1))

    def test_true_when_marked_comment_by_other_account(self):
        self.assertTrue(should_post(FakeGitHub(comments={1: [comment(author="someone")]}), 1))

    def test_author_comparison_is_exact(self):
        self.assertTrue(should_post(FakeGitHub(comments={1: [comment(author=BOT_LOGIN.upper())]}), 1))

    def test_true_when_bot_comment_has_no_marker(self):
        self.assertTrue(should_post(FakeGitHub(comments={1: [comment(body="ふつうのコメント")]}), 1))

    def test_marker_after_leading_whitespace_counts(self):
        self.assertFalse(should_post(FakeGitHub(comments={1: [comment(body="\n  " + MARKER)]}), 1))

    def test_true_when_no_comments(self):
        self.assertTrue(should_post(FakeGitHub(), 1))

    def test_reads_the_given_issue_number(self):
        api = FakeGitHub(comments={2: [comment()]})
        self.assertTrue(should_post(api, 1))
        self.assertFalse(should_post(api, 2))


class PlanPostTest(unittest.TestCase):
    def test_triage_with_report(self):
        body, code = plan_post(SELECT_TRIAGE, report([item(title="タイトル")]), None)
        self.assertEqual(code, 0)
        self.assertIn("タイトル", body)
        self.assertIn("| 項目 |", body)

    def test_triage_with_failure(self):
        body, code = plan_post(SELECT_TRIAGE, None, "refusal")
        self.assertEqual(code, 1)
        self.assertIn("トリアージできなかった: refusal", body)

    def test_triage_with_neither_is_internal_error(self):
        body, code = plan_post(SELECT_TRIAGE, None, None)
        self.assertEqual(code, 1)
        self.assertIn("トリアージできなかった: internal-error", body)

    def test_report_wins_over_failure(self):
        _, code = plan_post(SELECT_TRIAGE, report(), "refusal")
        self.assertEqual(code, 0)

    def test_unexpected(self):
        self.assertEqual(plan_post(SELECT_UNEXPECTED, None, None), (render_unexpected(), 1))

    def test_input_too_large(self):
        self.assertEqual(plan_post(SELECT_INPUT_TOO_LARGE, None, None), (render_too_large(), 1))

    def test_other_results_are_internal_error(self):
        body, code = plan_post("no-issue", None, None)
        self.assertEqual(code, 1)
        self.assertIn("internal-error", body)

    def test_every_body_starts_with_marker(self):
        for args in (
            (SELECT_TRIAGE, report(), None),
            (SELECT_TRIAGE, None, "api-error"),
            (SELECT_TRIAGE, None, None),
            (SELECT_UNEXPECTED, None, None),
            (SELECT_INPUT_TOO_LARGE, None, None),
            ("bogus", None, None),
        ):
            with self.subTest(args=args):
                self.assertTrue(plan_post(*args)[0].startswith(MARKER + "\n"))


if __name__ == "__main__":
    unittest.main()
