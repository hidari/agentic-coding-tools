import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tech_briefing.textproc import extract_advisory_ids, replace_urls  # noqa: E402


class AdvisoryIds(unittest.TestCase):
    def test_ids_of_three_kinds_in_order(self):
        t = "see GHSA-abcd-efgh-ijkl and cve-2026-12345, RUSTSEC-2026-0001; again CVE-2026-12345"
        self.assertEqual(
            extract_advisory_ids(t),
            ["GHSA-abcd-efgh-ijkl", "CVE-2026-12345", "RUSTSEC-2026-0001"],
        )

    def test_no_ids(self):
        self.assertEqual(extract_advisory_ids("nothing here"), [])

    def test_long_cve_sequence(self):
        self.assertEqual(extract_advisory_ids("CVE-2026-1234567"), ["CVE-2026-1234567"])

    def test_short_cve_sequence_is_not_an_id(self):
        self.assertEqual(extract_advisory_ids("CVE-2026-123"), [])

    def test_ghsa_is_lowercase_body_as_written(self):
        self.assertEqual(extract_advisory_ids("ghsa-abcd-efgh-ijkl"), ["GHSA-abcd-efgh-ijkl"])


class ReplaceUrls(unittest.TestCase):
    def test_url_forms_become_host_only(self):
        cases = {
            "[docs](https://example.com/a?b=1)": "[link: example.com]",
            "<https://example.com/a>": "[link: example.com]",
            "see https://sub.example.org/x/y.": "see [link: sub.example.org].",
            "(https://example.net/z)": "([link: example.net])",
            "https://例え.jp/パス、続き": "[link: 例え.jp]、続き",
        }
        for src, want in cases.items():
            self.assertEqual(replace_urls(src), want, src)

    def test_trailing_ascii_punctuation_stays_in_text(self):
        for p in ".,;:!?":
            self.assertEqual(replace_urls(f"https://a.example/x{p}"), f"[link: a.example]{p}", p)

    def test_fullwidth_terminators(self):
        for p in "、。，．）」　":
            self.assertEqual(replace_urls(f"https://a.example/x{p}あ"), f"[link: a.example]{p}あ", p)

    def test_port_and_userinfo_are_dropped(self):
        self.assertEqual(replace_urls("https://u:p@a.example:8443/x"), "[link: a.example]")

    def test_unparsable_host_never_raises_and_leaves_no_url(self):
        cases = {
            "https://[::1]/x": "[link: ::1]",
            "https://[::1/x": "[link: unknown]",
            "https://[bad/x y": "[link: unknown] y",
            "https://a\ufe56b/x": "[link: unknown]",
            "[t](https://[bad/x)": "[link: unknown]",
            "<https://a\ufe56b/x>": "[link: unknown]",
        }
        for src, want in cases.items():
            self.assertEqual(replace_urls(src), want, src)

    def test_dots_only_host_is_unknown(self):
        cases = {
            "https://.": "[link: unknown].",
            "https://...": "[link: unknown]...",
            "https://./x": "[link: unknown]",
            "https:///x": "[link: unknown]",
        }
        for src, want in cases.items():
            self.assertEqual(replace_urls(src), want, src)

    def test_no_url_unchanged(self):
        self.assertEqual(replace_urls("plain text"), "plain text")

    def test_multiple_urls(self):
        self.assertEqual(
            replace_urls("http://a.example/1 and https://b.example/2"),
            "[link: a.example] and [link: b.example]",
        )

    def test_ids_are_taken_before_replacement(self):
        t = "https://github.com/advisories/GHSA-abcd-efgh-ijkl"
        self.assertEqual(extract_advisory_ids(t), ["GHSA-abcd-efgh-ijkl"])
        self.assertNotIn("GHSA", replace_urls(t))


if __name__ == "__main__":
    unittest.main()
