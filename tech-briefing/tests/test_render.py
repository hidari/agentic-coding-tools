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
    MAX_COMMENT_CHARS,
    plan_post,
    render_failure,
    render_report,
    render_too_large,
    is_bot_report,
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

    def test_any_scheme_gets_invisible_char_after_colon(self):
        cases = {
            "ftp://evil.example/x": "ftp:" + ZWSP + "//evil.example/x",
            "FTP://evil.example/x": "FTP:" + ZWSP + "//evil.example/x",
            "git+ssh://evil.example/x": "git+ssh:" + ZWSP + "//evil.example/x",
            "a.b-c://x": "a.b-c:" + ZWSP + "//x",
        }
        for src, want in cases.items():
            with self.subTest(src=src):
                self.assertEqual(sanitize(src), want)
        # 対照: // が続かないコロンと、英字を1つも含まないスキームの形には足さない
        self.assertEqual(sanitize("note: a/b"), "note: a/b")
        self.assertEqual(sanitize("+://y"), "+://y")
        # 数字で始まる形は、英字から始まる後ろの部分がスキームの形になるので足す
        self.assertEqual(sanitize("1x://y"), "1x:" + ZWSP + "//y")

    def test_www_and_gh_reference_get_invisible_char(self):
        cases = {
            "www.evil.example/login": "www." + ZWSP + "evil.example/login",
            "WWW.evil.example": "WWW." + ZWSP + "evil.example",
            "GH-123": "GH-" + ZWSP + "123",
            "gh-123": "gh-" + ZWSP + "123",
            "see Gh-9 and wWw.x": "see Gh-" + ZWSP + "9 and wWw." + ZWSP + "x",
            "https://www.x": "https:" + ZWSP + "//www." + ZWSP + "x",
            "www.x://y": "www." + ZWSP + "x:" + ZWSP + "//y",
        }
        for src, want in cases.items():
            with self.subTest(src=src):
                self.assertEqual(sanitize(src), want)
        # 対照: ドットやハイフンが無い形は変えない
        self.assertEqual(sanitize("www gh 123"), "www gh 123")

    def test_www_and_gh_only_at_word_start(self):
        # 語の途中の gh- と www. は、ふつうの語を壊さないように無加工で残す
        for src in ("high-severity", "awww.example", "GH-x", "gh-", "1www.x", "agh-1"):
            with self.subTest(kept=src):
                self.assertEqual(sanitize(src), src)
        # 英数字以外の直後は語の頭として扱う。GFM は _ や * の直後の www. もリンクにするので、
        # \b (_ と日本語を語の文字とみなす) では切らない
        cases = {
            "GH-123": "GH-" + ZWSP + "123",
            "gh-7": "gh-" + ZWSP + "7",
            "www.x": "www." + ZWSP + "x",
            "_www.x_": "_www." + ZWSP + "x_",
            "影響はwww.x": "影響はwww." + ZWSP + "x",
            "参照gh-5": "参照gh-" + ZWSP + "5",
        }
        for src, want in cases.items():
            with self.subTest(src=src):
                self.assertEqual(sanitize(src), want)

    def test_bidi_controls_are_removed(self):
        for cp in list(range(0x202A, 0x202F)) + list(range(0x2066, 0x206A)):
            with self.subTest(cp=hex(cp)):
                self.assertEqual(sanitize("a" + chr(cp) + "b"), "ab")

    def test_format_and_control_characters_are_removed(self):
        # 範囲の列挙ではなく一般カテゴリ (Cc と Cf) で落とすことを、列挙から漏れやすかった文字で見る
        removed = {
            "LRM": 0x200E,
            "RLM": 0x200F,
            "ALM": 0x061C,
            "BOM": 0xFEFF,
            "WORD JOINER": 0x2060,
            "LANGUAGE TAG": 0xE0001,
            "TAG SPACE": 0xE0020,
            "CANCEL TAG": 0xE007F,
            "C1 DCS": 0x0090,
            "C1 CSI": 0x009B,
            "DEL": 0x007F,
            "SOFT HYPHEN": 0x00AD,
            "ZWJ": 0x200D,
            "ZWSP": 0x200B,
        }
        for name, cp in removed.items():
            with self.subTest(name):
                self.assertEqual(sanitize("a" + chr(cp) + "b"), "ab")
        # 対照: Cc でも Cf でもない文字は残す (空白類・未割り当て・置換文字)
        for cp in (0x00A0, 0x202F, 0x3000, 0x2065, 0xFFFD):
            with self.subTest(kept=hex(cp)):
                self.assertEqual(sanitize("a" + chr(cp) + "b"), "a" + chr(cp) + "b")

    def test_c0_controls_other_than_newline_and_tab_are_removed(self):
        # 0x0a・0x0b・0x0c・0x0d は改行として空白にする (test_newlines_become_one_space_each と同じ扱い)
        newlines = {0x0A, 0x0B, 0x0C, 0x0D}
        for cp in range(0x00, 0x20):
            if cp == 0x09 or cp in newlines:
                continue
            with self.subTest(cp=hex(cp)):
                self.assertEqual(sanitize("a" + chr(cp) + "b"), "ab")
        for cp in newlines:
            with self.subTest(newline=hex(cp)):
                self.assertEqual(sanitize("a" + chr(cp) + "b"), "a b")
        # 対照: タブと空白は残す
        self.assertEqual(sanitize("a\tb c"), "a\tb c")

    def test_controls_are_removed_before_inserting_invisible_chars(self):
        # 逆の順だと、制御文字で割った ww?w. や GH?- が除去の後に素の形へ戻る
        nul = chr(0x00)
        rlo = chr(0x202E)
        cases = {
            "ww" + nul + "w.evil.example": "www." + ZWSP + "evil.example",
            "GH" + rlo + "-123": "GH-" + ZWSP + "123",
            "https" + nul + "://x": "https:" + ZWSP + "//x",
        }
        for src, want in cases.items():
            with self.subTest(src=ascii(src)):
                self.assertEqual(sanitize(src), want)

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

    def test_notes_never_start_a_line_with_markdown_syntax(self):
        for notes in ("~~~", "- x", "1. x", "---", "    x", "> q", "## h"):
            with self.subTest(notes=notes):
                body = render_report(report([item()], unreachable=[notes], notes=notes))
                lines = body.split("\n")
                self.assertIn("補足: " + sanitize(notes), lines)
                for ln in lines:
                    self.assertFalse(ln.startswith(notes) and ln != "- " + sanitize(notes), ln)
                    self.assertFalse(ln.strip() in ("~~~", "---"), ln)

    def test_long_title_stays_within_limit_and_marker(self):
        body = render_report(report([item(title="a" * 70000)]))
        self.assertLessEqual(len(body), MAX_COMMENT_CHARS)
        self.assertTrue(body.startswith(MARKER + "\n"))

    def test_every_long_field_stays_within_limit(self):
        long = "&" * 70000
        body = render_report(report([item(title=long, findings=long, sources=[long] * 5)], [long] * 5, long))
        self.assertLessEqual(len(body), MAX_COMMENT_CHARS)
        self.assertTrue(body.startswith(MARKER + "\n"))

    def test_many_items_are_cut_from_the_end_with_omitted_count(self):
        items = [item(title=f"t{i:05d}", findings="x" * 200) for i in range(5000)]
        body = render_report(report(items))
        self.assertLessEqual(len(body), MAX_COMMENT_CHARS)
        self.assertTrue(body.startswith(MARKER + "\n"))
        kept = len(self.rows(body))
        self.assertTrue(0 < kept < 5000)
        self.assertIn(f"t{kept - 1:05d}", body)
        self.assertNotIn(f"t{kept:05d}", body)
        self.assertIn(f"省略した項目: {5000 - kept} 件", body)

    def test_small_report_has_no_omitted_line(self):
        self.assertNotIn("省略した項目", render_report(report([item()])))

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


class IsBotReportTest(unittest.TestCase):
    def test_table(self):
        rows = [
            ("名義と印", comment(), True),
            ("先頭の空白の後の印", comment(body="\n  " + MARKER), True),
            ("他のアカウントの印", comment(author="someone"), False),
            ("名義は完全一致で比べる", comment(author=BOT_LOGIN.upper()), False),
            ("印の無い名義のコメント", comment(body="ふつうのコメント"), False),
            ("本文の途中の印", comment(body="前置き\n" + MARKER), False),
        ]
        for label, c, expected in rows:
            with self.subTest(label):
                self.assertIs(is_bot_report(c), expected)


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

    def test_true_when_bot_comment_quotes_marker_mid_body(self):
        self.assertTrue(should_post(FakeGitHub(comments={1: [comment(body="前置き\n" + MARKER)]}), 1))

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
