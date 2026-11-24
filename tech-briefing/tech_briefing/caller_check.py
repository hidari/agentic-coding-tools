"""呼び出し側 workflow の検査。select job が走らせ、違反があれば config-error で落とす。

権限の天井をここで守る。共有の reusable workflow を呼ぶ側が、権限を広げたり
外部入力で起動する trigger を足したりできないようにする。
"""
import re

ALLOWED_TRIGGERS = frozenset({"workflow_dispatch", "schedule"})
CALLER_PERMISSIONS = {"contents": "read", "issues": "write"}
# 呼び出し job に許す鍵。ここに無い鍵 (environment など) は検査の外で挙動を変えうるので拒否する
CALLER_JOB_KEYS = frozenset({"uses", "permissions", "secrets", "with", "name", "if"})
CALLER_SECRETS = frozenset({"ANTHROPIC_API_KEY"})
CALLER_WITH_KEYS = frozenset({"config-path", "dry-run", "runner"})
# fullmatch で使う。末尾の改行を許す `$` を避けるため
SHA_RE = re.compile(r"[0-9a-f]{40}")


def _triggers(workflow):
    # YAML 1.1 の parser は鍵 `on` を真偽値 True にするので両方を見る
    found = set()
    present = False
    for key in ("on", True):
        if key not in workflow:
            continue
        present = True
        value = workflow[key]
        if isinstance(value, str):
            found.add(value)
        elif isinstance(value, (list, tuple)):
            found.update(v if isinstance(v, str) else repr(v) for v in value)
        elif isinstance(value, dict):
            found.update(k if isinstance(k, str) else repr(k) for k in value)
        elif value is not None:
            found.add(repr(value))
    return present, found


def _has_write(permissions):
    if isinstance(permissions, str):
        return permissions == "write-all"
    if isinstance(permissions, dict):
        return any(v == "write" for v in permissions.values())
    return False


def _calls(job, reusable):
    uses = job.get("uses") if isinstance(job, dict) else None
    return isinstance(uses, str) and uses.partition("@")[0] == reusable


def check_caller(workflow, reusable):
    if not isinstance(workflow, dict):
        return ["workflow が mapping ではありません"]
    violations = []

    present, triggers = _triggers(workflow)
    extra = sorted(triggers - ALLOWED_TRIGGERS)
    if not present or not triggers:
        violations.append("trigger (on) がありません。workflow_dispatch と schedule だけを許可します")
    elif extra:
        violations.append(f"許可されない trigger があります: {', '.join(extra)}")

    if workflow.get("permissions") != {}:
        violations.append("top-level の permissions は {} でなければなりません")

    jobs = workflow.get("jobs")
    jobs = jobs if isinstance(jobs, dict) else {}
    callers = sorted(name for name, job in jobs.items() if _calls(job, reusable))
    if len(callers) != 1:
        violations.append(
            f"{reusable} を呼ぶ job はちょうど 1 つでなければなりません (現在 {len(callers)} 件)")
    for name in callers:
        job = jobs[name]
        ref = job["uses"].partition("@")[2]
        if not SHA_RE.fullmatch(ref):
            violations.append(f"job {name}: ref は 40 桁の小文字 16 進の commit SHA でなければなりません")
        if job.get("permissions") != CALLER_PERMISSIONS:
            violations.append(
                f"job {name}: permissions は contents: read と issues: write だけでなければなりません")
        # secret は名前で 1 つだけ渡す。`inherit` や他の鍵は、共有 workflow の SHA が
        # 侵害されたときに読まれる範囲を広げるため許さない
        unknown = sorted(str(k) for k in job if k not in CALLER_JOB_KEYS)
        if unknown:
            violations.append(f"job {name}: 許可されない鍵があります: {', '.join(unknown)}")
        secrets = job.get("secrets")
        if "secrets" in job and not (isinstance(secrets, dict) and set(secrets) == CALLER_SECRETS):
            violations.append(f"job {name}: secrets は ANTHROPIC_API_KEY だけを名指しで渡す mapping でなければなりません")
        with_ = job.get("with")
        if "with" in job and not (isinstance(with_, dict) and set(with_) <= CALLER_WITH_KEYS):
            violations.append(
                f"job {name}: with は {', '.join(sorted(CALLER_WITH_KEYS))} の鍵だけの mapping でなければなりません")
    for name in sorted(n for n in jobs if n not in callers):
        if _has_write(jobs[name].get("permissions") if isinstance(jobs[name], dict) else None):
            violations.append(f"job {name}: 呼び出し以外の job に write 権限は付けられません")
    return violations
