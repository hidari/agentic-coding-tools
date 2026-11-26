"""Fake の側の契約テスト。アダプタと Fake が同じ契約 (全ページ・PR を除く・created_at は Z・応答の形) を守ることを固定する。

ここではアダプタを読まない。外と交わす形の純粋な部分 (tech_briefing.wire) をアダプタと Fake が
共有するので、ここではそれと、それを通した Fake を検査する。実物の側の契約テストは鍵と通信が要るので手で回す。
"""

import io
import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from fakes import BLOCK_TYPES, STOP_REASONS, USAGE_KEYS, FakeGitHub, FakeMessages  # noqa: E402
from tech_briefing import triage, wire  # noqa: E402
from tech_briefing.constants import BOT_LOGIN, MARKER  # noqa: E402
from tech_briefing.protocols import CommentRef, FetchError, IssueRef  # noqa: E402
from tech_briefing.selection import decide  # noqa: E402

AUTHOR = "briefer"
NOW = datetime(2026, 11, 24, 0, 0, tzinfo=timezone.utc)
TITLE = "[tech-briefing] 2026-11-24"
ISSUE = IssueRef(7, TITLE, AUTHOR, "2026-11-24T00:00:00Z", "本文")


def comment(i, author=AUTHOR, body=None):
    return CommentRef(i, author, body if body is not None else f"コメント{i}", f"2026-11-24T00:{i // 60:02d}:{i % 60:02d}Z")


def many(n):
    return [comment(i) for i in range(n)]


class PagedFakeGitHub(unittest.TestCase):
    def test_decide_sees_marker_beyond_first_page(self):
        comments = many(150)
        comments[120] = comment(120, author=BOT_LOGIN, body=MARKER + "\n報告")
        api = FakeGitHub([ISSUE], {7: comments}, page_size=100)
        self.assertEqual(decide(api, AUTHOR, NOW, 1_000_000).result, "already-processed")
        self.assertEqual(api.pages_served, 2 + 1)

    def test_decide_input_includes_comments_beyond_first_page(self):
        comments = many(150)
        comments[140] = comment(140, body="陽性の対照の語")
        api = FakeGitHub([ISSUE], {7: comments}, page_size=100)
        d = decide(api, AUTHOR, NOW, 1_000_000)
        self.assertEqual(d.result, "triage")
        self.assertEqual(len(d.input["comments"]), 150)
        self.assertIn("陽性の対照の語", [c["body"] for c in d.input["comments"]])

    def test_open_issues_beyond_first_page_and_without_pull_requests(self):
        others = [IssueRef(100 + i, f"別の Issue {i}", "someone", "2026-11-23T00:00:00Z", None) for i in range(120)]
        # 同じタイトルの PR が混ざっても、Issue として数えない (数えると unexpected になる)
        pr = dict(wire_issue(IssueRef(9, TITLE, AUTHOR, "2026-11-24T01:00:00Z", "PR")), pull_request={"url": "x"})
        api = FakeGitHub(others + [ISSUE], {7: []}, page_size=50, raw_issues=[pr])
        d = decide(api, AUTHOR, NOW, 1_000_000)
        self.assertEqual((d.result, d.issue.number), ("triage", 7))
        self.assertGreaterEqual(api.pages_served, 3)

    def test_unpaged_fake_is_unchanged(self):
        api = FakeGitHub([ISSUE], {7: many(3)})
        self.assertEqual(len(api.comments(7)), 3)
        self.assertEqual(api.pages_served, 0)


def wire_issue(issue):
    return {
        "number": issue.number,
        "title": issue.title,
        "user": {"login": issue.author},
        "created_at": issue.created_at,
        "body": issue.body,
    }


class Paginate(unittest.TestCase):
    def pages(self, *bodies):
        urls = [f"{wire.API_ROOT}/p{i}" for i in range(len(bodies))]
        served = []

        def get_page(url):
            i = urls.index(url)
            served.append(url)
            link = f'<{urls[i + 1]}>; rel="next", <{urls[-1]}>; rel="last"' if i + 1 < len(urls) else None
            return bodies[i], link

        return urls[0], get_page, served

    def test_follows_next_until_exhausted(self):
        first, get_page, served = self.pages([1, 2], [3], [4])
        self.assertEqual(wire.paginate(get_page, first), [1, 2, 3, 4])
        self.assertEqual(len(served), 3)

    def test_page_cap_raises(self):
        first, get_page, _ = self.pages([1], [2], [3])
        with self.assertRaises(ValueError):
            wire.paginate(get_page, first, max_pages=2)
        self.assertEqual(wire.paginate(get_page, first, max_pages=3), [1, 2, 3])

    def test_next_on_another_origin_raises(self):
        # token を付けたまま別のホストへ行かない
        first = f"{wire.API_ROOT}/p0"
        for nxt in ("https://api.github.com.example.org/p", "http://api.github.com/p", "https://example.org/p"):
            with self.subTest(nxt):
                requested = []

                def get_page(url):
                    requested.append(url)
                    # 外のページは最後のページとして返す。ページの上限で落ちる経路と区別するため
                    return ([1], f'<{nxt}>; rel="next"') if url == first else ([2], None)

                with self.assertRaises(ValueError):
                    wire.paginate(get_page, first)
                self.assertEqual(requested, [first])

    def test_non_list_page_raises(self):
        with self.assertRaises(ValueError):
            wire.paginate(lambda url: ({"message": "x"}, None), f"{wire.API_ROOT}/p0")

    def test_next_link(self):
        cases = {
            None: None,
            "": None,
            '<https://api.github.com/a>; rel="last"': None,
            '<https://api.github.com/a>; rel="next"': "https://api.github.com/a",
            '<https://api.github.com/b>; rel="prev", <https://api.github.com/a>; rel="next"': "https://api.github.com/a",
        }
        for header, expected in cases.items():
            with self.subTest(header):
                self.assertEqual(wire.next_link(header), expected)


class Conversion(unittest.TestCase):
    def test_issue(self):
        item = wire_issue(ISSUE)
        self.assertEqual(wire.to_issue(item), ISSUE)
        self.assertIsNone(wire.to_issue(dict(item, pull_request={})))
        self.assertEqual(wire.to_issue(dict(item, body=None)).body, None)
        # 消えたアカウント (ghost) は user が null で届く
        self.assertEqual(wire.to_issue(dict(item, user=None)).author, "")

    def test_created_at_must_be_z_form(self):
        # selection は文字列順を時刻順に使うので、Z 以外の形は受けずに落とす
        for value in ("2026-11-24T00:00:00+00:00", "2026-11-24 00:00:00Z", "2026-11-24T00:00:00.123Z", None):
            with self.subTest(value):
                with self.assertRaises(ValueError):
                    wire.to_issue(dict(wire_issue(ISSUE), created_at=value))
                with self.assertRaises(ValueError):
                    wire.to_comment({"id": 1, "user": {"login": "a"}, "body": "x", "created_at": value})

    def test_comment(self):
        item = {"id": 3, "user": {"login": "a"}, "body": "x", "created_at": "2026-11-24T00:00:00Z"}
        self.assertEqual(wire.to_comment(item), CommentRef(3, "a", "x", "2026-11-24T00:00:00Z"))
        self.assertEqual(wire.to_comment(dict(item, user=None, body=None)), CommentRef(3, "", "", "2026-11-24T00:00:00Z"))

    def test_urls(self):
        self.assertEqual(wire.issues_url("o/r"), f"{wire.API_ROOT}/repos/o/r/issues?state=open&per_page=100")
        self.assertEqual(wire.comments_url("o/r", 7), f"{wire.API_ROOT}/repos/o/r/issues/7/comments?per_page=100")
        for bad in ("o", "o/r/x", "../r", "o/r?x=1", "", "o/ r"):
            with self.subTest(bad):
                with self.assertRaises(ValueError):
                    wire.issues_url(bad)


class ReadJsonLimited(unittest.TestCase):
    def test_at_limit_is_read(self):
        data = b'{"a": 1}'
        self.assertEqual(wire.read_json_limited(io.BytesIO(data), len(data)), {"a": 1})

    def test_over_limit_raises_fetch_error(self):
        data = b'{"a": 1}'
        with self.assertRaises(FetchError):
            wire.read_json_limited(io.BytesIO(data), len(data) - 1)

    def test_bad_json_and_bad_utf8_raise_fetch_error(self):
        for data in (b"<html>", b'"\xff"', b""):
            with self.subTest(data):
                with self.assertRaises(FetchError):
                    wire.read_json_limited(io.BytesIO(data), 100)


class FakeMessagesShape(unittest.TestCase):
    def test_loop_assumptions_are_within_fake_shape(self):
        # ループが読む usage の鍵・stop_reason・ブロックの type が、Fake の受ける形の中にある
        self.assertLessEqual(set(triage._TOKEN_KEYS) | {"server_tool_use"}, USAGE_KEYS)
        self.assertLessEqual({"tool_use", "pause_turn", "refusal", "end_turn", "max_tokens"}, STOP_REASONS)
        self.assertLessEqual(set(triage._LOG_KINDS), BLOCK_TYPES)

    def test_fake_rejects_responses_outside_api_shape(self):
        good = {"content": [{"type": "text", "text": "x"}], "stop_reason": "end_turn", "usage": {"input_tokens": 1}}
        self.assertEqual(FakeMessages([good]).create({}), good)
        bad = {
            "misspelled usage key": dict(good, usage={"input_token": 1}),
            "unknown stop_reason": dict(good, stop_reason="done"),
            "unknown block type": dict(good, content=[{"type": "tool_call"}]),
            "content not list": dict(good, content={"type": "text"}),
            "missing usage": {k: v for k, v in good.items() if k != "usage"},
        }
        for label, resp in bad.items():
            with self.subTest(label):
                with self.assertRaises(AssertionError):
                    FakeMessages([resp]).create({})


if __name__ == "__main__":
    unittest.main()
