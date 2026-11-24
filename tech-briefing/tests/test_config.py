import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tech_briefing.config import Config, ConfigError, FileRule, load_config  # noqa: E402

REJECT = [
    "github.com", "com", "gist.github.com", "user.github.io", "github.io",
    "https://docs.python.org", "*.example.com", "docs.example.com/blog",
    "аmazon.com", "", "example..com", "1.2.3.4", "xn--80ak6aa92e.com",
]
ACCEPT = ["docs.github.com", "docs.python.org", "nvd.nist.gov", "osv.dev"]


def valid(**overrides):
    raw = {
        "criteria": "セキュリティ修正を優先する",
        "official_sources": ["docs.python.org"],
        "files": ["pyproject.toml"],
    }
    raw.update(overrides)
    return raw


class Domains(unittest.TestCase):
    def test_domain_table(self):
        for d in REJECT:
            with self.assertRaises(ConfigError, msg=d):
                load_config(valid(official_sources=[d]))
        self.assertEqual(load_config(valid(official_sources=ACCEPT)).official_sources, tuple(ACCEPT))

    def test_more_rejections(self):
        for d in ["raw.githubusercontent.com", "githubusercontent.com", "x.gitlab.io",
                  "a.b.pages.dev", "netlify.app", "foo.vercel.app", "example.com:8080",
                  "-a.example.com", "a_b.example.com", ".example.com", "example.com.",
                  "localhost", 1, None]:
            with self.assertRaises(ConfigError, msg=repr(d)):
                load_config(valid(official_sources=[d]))

    def test_case_is_lowered(self):
        self.assertEqual(load_config(valid(official_sources=["Docs.Python.ORG"])).official_sources,
                         ("docs.python.org",))
        with self.assertRaises(ConfigError):
            load_config(valid(official_sources=["GitHub.com"]))
        with self.assertRaises(ConfigError):
            load_config(valid(official_sources=["User.GitHub.IO"]))

    def test_empty_or_non_list_sources(self):
        for v in ([], "docs.python.org", None):
            with self.assertRaises(ConfigError, msg=repr(v)):
                load_config(valid(official_sources=v))


class Shape(unittest.TestCase):
    def test_full_config(self):
        c = load_config(valid(
            files=["a/*.toml", {"glob": "b/**", "search": True}, {"glob": "c"}],
            issues_glob="docs/issues/*", registries=["npm", "pypi"]))
        self.assertEqual(c, Config(
            criteria="セキュリティ修正を優先する",
            official_sources=("docs.python.org",),
            files=(FileRule("a/*.toml", False), FileRule("b/**", True), FileRule("c", False)),
            issues_glob="docs/issues/*",
            registries=("npm", "pypi")))

    def test_defaults(self):
        c = load_config(valid())
        self.assertIsNone(c.issues_glob)
        self.assertEqual(c.registries, ())

    def test_not_a_mapping(self):
        for raw in (None, [], "x"):
            with self.assertRaises(ConfigError):
                load_config(raw)

    def test_missing_required(self):
        for k in ("criteria", "official_sources", "files"):
            raw = valid()
            del raw[k]
            with self.assertRaises(ConfigError, msg=k):
                load_config(raw)

    def test_unknown_key(self):
        with self.assertRaises(ConfigError):
            load_config(valid(extra=1))

    def test_criteria_type(self):
        for v in ("", 1, None):
            with self.assertRaises(ConfigError, msg=repr(v)):
                load_config(valid(criteria=v))

    def test_registries(self):
        for v in (["rubygems"], ["npm", "npm"], "npm", [1]):
            with self.assertRaises(ConfigError, msg=repr(v)):
                load_config(valid(registries=v))
        self.assertEqual(load_config(valid(registries=["npm", "crates", "pypi"])).registries,
                         ("npm", "crates", "pypi"))

    def test_files_invalid(self):
        bad = [[], "a", ["../x"], ["/etc/passwd"], ["a/../b"], [""], [1],
               [{"glob": "a", "search": "yes"}], [{"glob": "a", "x": 1}], [{"search": True}],
               [{"glob": "../a"}]]
        for v in bad:
            with self.assertRaises(ConfigError, msg=repr(v)):
                load_config(valid(files=v))

    def test_issues_glob_invalid(self):
        for v in (1, "", "../x", "/abs", ["a"]):
            with self.assertRaises(ConfigError, msg=repr(v)):
                load_config(valid(issues_glob=v))


if __name__ == "__main__":
    unittest.main()
