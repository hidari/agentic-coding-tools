# security-blue-red-team

Product-agnostic Red Team / Blue Team security testing automation for Claude Code.

This plugin runs **layered security tests** (Layer 1 static → Layer 2 passive → Layer 3 active → Layer 4 high-risk static) against any project that provides a `<project>/.claude/security-profile.yml`. It is designed for solo / small-team operations where hiring a dedicated security specialist or running an external pentest is not economically viable, but doing nothing is also not acceptable.

## What it provides

- **Subagents**: `red-team-agent` (attacker view) and `blue-team-agent` (defender view)
- **Slash commands**: `/security-redteam`, `/security-blueteam`, `/security-vulnerability-assessment`, `/security-cleanup`
- **Schemas**: `security-profile.schema.yml` (input contract), `findings.schema.json` (output contract), and `cleanup-queue.schema.json` (Layer 3 cleanup contract)

## Two-layer architecture

This plugin is the **Universal layer**. Project-specific knowledge (attack surfaces, tech stack, secrets patterns) is injected via:

1. `<project>/.claude/security-profile.yml` — declarative configuration
2. An optional **wrap skill** in the project (e.g. `<project>/.claude/skills/<product>-security-review/`) that calls this plugin and adds product-specific orchestration (issue filing, Discord notifications, etc.)

## Safety design

The plugin treats security testing as a **safety-critical** activity:

- `environment.kind: production` in the profile is **rejected at subagent startup**. No tests are run.
- HTTP requests are restricted to URLs in `environment.allow_targets`. Anything else is rejected.
- Request budgets (per test and per host) are fixed by the agents. The canonical values live in the Safety constraints section of `agents/red-team-agent.md`.
- Destructive operations (DELETE / overwriting PUT) are **planned but not executed**.
- Direct attacks on third-party services (auth / payments / CDN / cloud metadata APIs) are **forbidden**; their defenses are verified via static analysis (Layer 4).

## How it is invoked

```text
/security-redteam [--layer=1|2|3|4|all] [--target=local|staging] [--purple]
/security-blueteam [--mode=a|b] [--report=<path>]
/security-vulnerability-assessment [--target=local|staging]
/security-cleanup [--from=<path>] [--dry-run]
```

Or via natural language: "Red Team を走らせて", "Blue Team でこのレポートに対する改善計画を立てて", etc.

## Outputs

- `<output_dir>/<YYYY-MM-DD>/red-team.md` — human-readable report
- `<output_dir>/<YYYY-MM-DD>/findings.json` — machine-readable (schema in `schemas/findings.schema.json`)
- `<output_dir>/<YYYY-MM-DD>/blue-team.md` — Blue Team mode A / B output

`<output_dir>` defaults to `docs/security-reviews/` (project root relative) and is overridable per invocation.

## Boundary

This plugin **does not**:

- File issues (delegate to a wrap skill / `dev-workflow:in-repo-issue` / `gh issue`)
- Create PRs or modify code (delegate to a wrap skill / the user)
- Call other skills (`dev-workflow:pre-merge-quality-gate`, `chrome-devtools-debugger`, `playwright-test`, etc.)
- Run against production (refused at startup)

The boundary is enforced in the subagent system prompts, which are the canonical statement of it.

## Profile contract

A minimal valid profile:

```yaml
version: "1.0"
product:
  name: my-app
  repo: org/my-app
environment:
  kind: local
  allow_targets: []
stack:
  backend: nodejs
endpoints:
  auth_required: []
  public: []
```

See `schemas/security-profile.template.yml` for a fully annotated template and `schemas/security-profile.schema.yml` for the JSON Schema.

## Blue Team modes

- **Mode A** (Red Team report response): consumes `findings.json` + `red-team.md` and produces a triaged plan (P0-P3 + S/M/L/XL implementation sizes, per-finding hints). Each finding is cross-referenced by `fingerprint` so a wrap skill can dedupe against an issue tracker.
- **Mode B** (defensive surface audit): static analysis only (no HTTP). Audits five surfaces: authn/z flow trace, input validation coverage, security headers, RLS, logging coverage. Outputs a per-surface table with gaps and remediation pointers.

Mode is auto-detected when `--mode` is omitted: if `--report` is supplied or a recent `docs/security-reviews/<date>/red-team.md` exists, Mode A; otherwise Mode B.
