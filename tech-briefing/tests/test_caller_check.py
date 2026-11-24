import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tech_briefing.caller_check import check_caller  # noqa: E402

REUSABLE = "owner/repo/.github/workflows/tech-briefing-triage.yml"
SHA = "0123456789abcdef0123456789abcdef01234567"


def valid():
    return {
        "name": "tech-briefing",
        "on": {"workflow_dispatch": None, "schedule": [{"cron": "0 0 * * *"}]},
        "permissions": {},
        "jobs": {
            "triage": {
                "uses": f"{REUSABLE}@{SHA}",
                "permissions": {"contents": "read", "issues": "write"},
                "secrets": {"ANTHROPIC_API_KEY": "${{ secrets.ANTHROPIC_API_KEY }}"},
                "with": {"config-path": ".github/tech-briefing.yml"},
            }
        },
    }


def mutated(fn):
    wf = copy.deepcopy(valid())
    fn(wf)
    return wf


def set_uses(value):
    return lambda wf: wf["jobs"]["triage"].update(uses=value)


def set_on(value):
    return lambda wf: wf.update(on=value)


def set_job_perms(value):
    return lambda wf: wf["jobs"]["triage"].update(permissions=value)


def add_second_caller(wf):
    wf["jobs"]["second"] = dict(wf["jobs"]["triage"])


def drop_top_permissions(wf):
    del wf["permissions"]


def drop_job_permissions(wf):
    del wf["jobs"]["triage"]["permissions"]


def drop_trigger(wf):
    del wf["on"]


def drop_jobs(wf):
    wf["jobs"] = {}


def true_key(wf):
    wf[True] = wf.pop("on")


def other_job_with(perms):
    return lambda wf: wf["jobs"].update(lint={"runs-on": "ubuntu-latest", "permissions": perms})


BROKEN = {
    "tag": mutated(set_uses(f"{REUSABLE}@v1")),
    "branch": mutated(set_uses(f"{REUSABLE}@main")),
    "short sha": mutated(set_uses(f"{REUSABLE}@0123456")),
    "upper sha": mutated(set_uses(f"{REUSABLE}@{SHA.upper()}")),
    "trailing newline": mutated(set_uses(f"{REUSABLE}@{SHA}\n")),
    "no ref": mutated(set_uses(REUSABLE)),
    # 別ファイルを呼ぶ job が write を持つと違反が増えるので、権限は外して 1 件に絞る
    "other file": mutated(lambda wf: (set_uses(f"owner/repo/.github/workflows/other.yml@{SHA}")(wf),
                                      drop_job_permissions(wf))),
    "push (str)": mutated(set_on("push")),
    "push (list)": mutated(set_on(["workflow_dispatch", "push"])),
    "pull_request (map)": mutated(set_on({"workflow_dispatch": None, "pull_request": {}})),
    "issue_comment": mutated(set_on({"issue_comment": {"types": ["created"]}})),
    "no trigger": mutated(drop_trigger),
    "top permissions absent": mutated(drop_top_permissions),
    "top permissions write-all": mutated(lambda wf: wf.update(permissions="write-all")),
    "top permissions contents": mutated(lambda wf: wf.update(permissions={"contents": "read"})),
    "job permissions extra": mutated(set_job_perms(
        {"contents": "read", "issues": "write", "pull-requests": "write"})),
    "job permissions missing issues": mutated(set_job_perms({"contents": "read"})),
    "job permissions contents write": mutated(set_job_perms({"contents": "write", "issues": "write"})),
    "job permissions absent": mutated(drop_job_permissions),
    "no calling job": mutated(drop_jobs),
    "two calling jobs": mutated(add_second_caller),
    "other job write": mutated(other_job_with({"contents": "write"})),
    "other job write-all": mutated(other_job_with("write-all")),
    "secrets inherit": mutated(lambda wf: wf["jobs"]["triage"].update(secrets="inherit")),
    "extra secret": mutated(lambda wf: wf["jobs"]["triage"]["secrets"].update(OTHER="x")),
    "unknown with key": mutated(lambda wf: wf["jobs"]["triage"]["with"].update(evil="x")),
    "with not a mapping": mutated(lambda wf: wf["jobs"]["triage"].update({"with": "x"})),
    "unknown job key": mutated(lambda wf: wf["jobs"]["triage"].update(environment="prod")),
    "not a mapping": [],
}


class CheckCaller(unittest.TestCase):
    def test_valid_passes(self):
        self.assertEqual(check_caller(valid(), REUSABLE), [])

    def test_true_key_for_on_passes(self):
        self.assertEqual(check_caller(mutated(true_key), REUSABLE), [])

    def test_true_key_is_still_checked(self):
        def fn(wf):
            true_key(wf)
            wf[True] = "push"
        self.assertEqual(len(check_caller(mutated(fn), REUSABLE)), 1)

    def test_trigger_shapes_pass(self):
        for on in ["workflow_dispatch", ["workflow_dispatch", "schedule"], {"schedule": []}]:
            with self.subTest(on=on):
                self.assertEqual(check_caller(mutated(set_on(on)), REUSABLE), [])

    def test_other_job_without_write_passes(self):
        wf = mutated(other_job_with({"contents": "read"}))
        self.assertEqual(check_caller(wf, REUSABLE), [])
        wf["jobs"]["lint"].pop("permissions")
        self.assertEqual(check_caller(wf, REUSABLE), [])

    def test_each_break_gives_exactly_one_violation(self):
        for name, wf in BROKEN.items():
            with self.subTest(name=name):
                result = check_caller(wf, REUSABLE)
                self.assertEqual(len(result), 1, result)
                self.assertTrue(all(isinstance(m, str) and m for m in result))

    def test_deterministic_order(self):
        wf = mutated(lambda w: (set_on("push")(w), drop_top_permissions(w), set_uses("x@v1")(w)))
        first = check_caller(wf, REUSABLE)
        self.assertGreater(len(first), 1)
        self.assertEqual(first, check_caller(copy.deepcopy(wf), REUSABLE))


if __name__ == "__main__":
    unittest.main()
