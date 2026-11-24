import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from fakes import FakeGitHub  # noqa: E402
from test_caller_check import REUSABLE, valid as valid_caller  # noqa: E402
from tech_briefing.constants import BOT_LOGIN, MARKER, SELECT_RESULTS  # noqa: E402
from tech_briefing.protocols import CommentRef, IssueRef  # noqa: E402
from tech_briefing.selection import decide, is_red, run_select, today_jst  # noqa: E402

AUTHOR = "briefing-bot"
NOW = datetime(2026, 11, 24, 3, 0, tzinfo=timezone.utc)
TITLE = "[tech-briefing] 2026-11-24"
LIMIT = 1000


def issue(number=1, title=TITLE, author=AUTHOR, created_at="2026-11-24T00:00:00Z", body="本文"):
    return IssueRef(number, title, author, created_at, body)


def comment(id=1, author=AUTHOR, body="c", created_at="2026-11-24T01:00:00Z"):
    return CommentRef(id, author, body, created_at)


def marked(author=BOT_LOGIN, id=99):
    return comment(id=id, author=author, body=MARKER + "\n報告")


def run(issues, comments=None, author=AUTHOR, limit=LIMIT):
    return decide(FakeGitHub(issues, comments), author, NOW, limit)


class DecisionTable(unittest.TestCase):
    def test_table(self):
        near = issue(5, "[tech-briefing] 2026-11-24 extra")
        rows = [
            ("no-issue 0件", [], {}, "no-issue", None),
            ("別日だけ", [issue(title="[tech-briefing] 2026-11-23")], {}, "no-issue", None),
            ("wrong-author", [issue(author="someone")], {}, "wrong-author", 1),
            ("triage", [issue()], {}, "triage", 1),
            ("already-processed", [issue()], {1: [marked()]}, "already-processed", 1),
            ("2件 新しい方に印", [issue(1), issue(2, created_at="2026-11-24T02:00:00Z")], {2: [marked()]}, "already-processed", 2),
            ("2件 新しい方に印なし", [issue(1), issue(2, created_at="2026-11-24T02:00:00Z")], {1: [marked()]}, "unexpected", 2),
            ("他者の同題が混ざっても止まらない", [issue(1, author="someone"), issue(2)], {}, "triage", 2),
            ("ログインの大文字小文字は区別しない", [issue(author=AUTHOR.upper())], {}, "triage", 1),
            ("title-mismatch", [near], {}, "title-mismatch", 5),
            ("末尾改行は不一致", [issue(6, TITLE + "\n")], {}, "title-mismatch", 6),
            ("完全一致があれば mismatch にならない", [issue(1), near], {}, "triage", 1),
            ("他者の近いタイトルは mismatch にしない", [issue(7, near.title, author="someone")], {}, "no-issue", None),
        ]
        for name, issues, comments, result, number in rows:
            with self.subTest(name):
                d = run(issues, comments)
                self.assertEqual(d.result, result)
                self.assertEqual(d.issue.number if d.issue else None, number)
                self.assertIn(result, d.summary)
                self.assertEqual(d.input is not None, result == "triage")

    def test_unexpected_has_no_input(self):
        d = run([issue(1), issue(2, created_at="2026-11-24T02:00:00Z")])
        self.assertEqual((d.result, d.input), ("unexpected", None))

    def test_marker_only_counts_from_bot_login(self):
        d = run([issue()], {1: [marked(author=AUTHOR)]})
        self.assertEqual(d.result, "triage")

    def test_marker_removed_later_is_triage(self):
        d = run([issue()], {1: [comment(id=99, author=BOT_LOGIN, body="報告 (印は編集で消えた)", created_at="2026-11-24T01:00:00Z")]})
        self.assertEqual(d.result, "triage")

    def test_marker_after_leading_whitespace_counts(self):
        d = run([issue()], {1: [comment(author=BOT_LOGIN, body="\n  " + MARKER)]})
        self.assertEqual(d.result, "already-processed")

    def test_author_config_errors(self):
        for author in ("", BOT_LOGIN):
            with self.subTest(author):
                api = FakeGitHub([issue()])
                d = decide(api, author, NOW, LIMIT)
                self.assertEqual(d.result, "config-error")
                self.assertIn("config-error", d.summary)
                self.assertEqual(api.calls, [])


class Today(unittest.TestCase):
    def test_boundary(self):
        utc = timezone.utc
        self.assertEqual(today_jst(datetime(2026, 11, 24, 14, 59, tzinfo=utc)), "2026-11-24")
        self.assertEqual(today_jst(datetime(2026, 11, 24, 15, 0, tzinfo=utc)), "2026-11-25")

    def test_decide_uses_jst_date(self):
        now = datetime(2026, 11, 24, 15, 0, tzinfo=timezone.utc)
        d = decide(FakeGitHub([issue()]), AUTHOR, now, LIMIT)
        self.assertEqual(d.result, "no-issue")


class InputAssembly(unittest.TestCase):
    def test_filters_comments(self):
        comments = [
            comment(1, AUTHOR, "後", "2026-11-24T02:00:00Z"),
            comment(2, AUTHOR, "先", "2026-11-24T01:00:00Z"),
            comment(3, "other", "他人"),
            comment(4, AUTHOR, MARKER + " 偽装", "2026-11-24T01:30:00Z"),
            comment(5, "other", MARKER + " 他人の印"),
        ]
        d = run([issue()], {1: comments})
        self.assertEqual(d.result, "triage")
        self.assertEqual([c["body"] for c in d.input["comments"]], ["先", "後"])
        self.assertEqual(d.input["removed_marked_by_author"], 1)
        self.assertEqual(set(d.input["comments"][0]), {"author", "created_at", "body"})
        self.assertEqual(d.input["issue_number"], 1)
        self.assertEqual(d.input["title"], TITLE)

    def test_none_body_and_no_comments(self):
        d = run([issue(body=None)])
        self.assertEqual(d.result, "triage")
        self.assertEqual(d.input["body"], "")
        self.assertEqual(d.input["comments"], [])
        self.assertEqual(d.input["advisory_ids"], [])

    def test_ids_from_original_and_urls_replaced(self):
        body = "see https://example.com/a?x=1 and CVE-2026-1234"
        c = comment(body="https://evil.example.org/p RUSTSEC-2026-0001")
        d = run([issue(body=body)], {1: [c]})
        self.assertEqual(sorted(d.input["advisory_ids"]), ["CVE-2026-1234", "RUSTSEC-2026-0001"])
        self.assertIn("[link: example.com]", d.input["body"])

        def strings(v):
            if isinstance(v, str):
                yield v
            elif isinstance(v, dict):
                for x in v.values():
                    yield from strings(x)
            elif isinstance(v, list):
                for x in v:
                    yield from strings(x)

        self.assertFalse([s for s in strings(d.input) if "https://" in s])

    def test_ids_inside_urls_come_from_original(self):
        body = "https://example.com/advisories/CVE-2026-9999"
        c = comment(body="https://evil.example.org/GHSA-abcd-1234-wxyz")
        d = run([issue(body=body)], {1: [c]})
        self.assertEqual(sorted(d.input["advisory_ids"]), ["CVE-2026-9999", "GHSA-abcd-1234-wxyz"])
        self.assertNotIn("CVE-2026-9999", d.input["body"])

    def test_size_measured_on_original_text(self):
        url = "https://example.com/" + "a" * 60
        for body, comments in (
            (url, {}),
            ("x", {1: [comment(body=url)]}),
        ):
            with self.subTest(body[:5]):
                # 置換後は 19 文字前後に縮むが、上限は原文の長さで測る
                d = run([issue(body=body)], comments, limit=50)
                self.assertEqual((d.result, d.input), ("input-too-large", None))

    def test_size_limit(self):
        c = comment(body="y" * 5)
        d = run([issue(body="x" * 5)], {1: [c]}, limit=10)
        self.assertEqual(d.result, "triage")
        d = run([issue(body="x" * 5)], {1: [c]}, limit=9)
        self.assertEqual((d.result, d.input), ("input-too-large", None))
        self.assertEqual(d.issue.number, 1)

    def test_size_ignores_excluded_comments(self):
        d = run([issue(body="x")], {1: [comment(author="other", body="z" * 100), marked(author=AUTHOR)]}, limit=5)
        self.assertEqual(d.result, "triage")


class Red(unittest.TestCase):
    def test_is_red(self):
        red = {r for r in SELECT_RESULTS if is_red(r)}
        self.assertEqual(red, {"wrong-author", "title-mismatch", "config-error"})


class RunSelect(unittest.TestCase):
    RAW = {"criteria": "基準", "official_sources": ["docs.python.org"], "files": ["pyproject.toml"]}

    def go(self, raw, caller, api=None):
        api = api or FakeGitHub([issue()])
        return api, run_select(raw, caller, REUSABLE, api, AUTHOR, NOW, LIMIT)

    def test_ok(self):
        api, (d, cfg) = self.go(self.RAW, valid_caller())
        self.assertEqual(d.result, "triage")
        self.assertEqual(cfg.criteria, "基準")

    def test_bad_config_does_not_touch_api(self):
        api, (d, cfg) = self.go({"criteria": ""}, valid_caller())
        self.assertEqual((d.result, cfg), ("config-error", None))
        self.assertEqual(api.calls, [])

    def test_caller_violation_does_not_touch_api(self):
        bad = valid_caller()
        bad["on"] = {"pull_request": None}
        api, (d, cfg) = self.go(self.RAW, bad)
        self.assertEqual((d.result, cfg), ("config-error", None))
        self.assertEqual(api.calls, [])

    def test_bad_author_returns_no_config(self):
        api = FakeGitHub([issue()])
        d, cfg = run_select(self.RAW, valid_caller(), REUSABLE, api, "", NOW, LIMIT)
        self.assertEqual((d.result, cfg), ("config-error", None))


if __name__ == "__main__":
    unittest.main()
