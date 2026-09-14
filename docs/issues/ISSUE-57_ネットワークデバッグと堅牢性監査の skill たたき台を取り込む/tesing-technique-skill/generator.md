# Project-Specific Skill Generator

Guidance for producing a **project-specific skill** that satisfies the robustness-audit contract. The generic skill supplies methodology; the generated skill supplies this project's answers (trust boundaries, safety-critical constraints, environment access, philosophy).

## How to generate

Walk the user through `questionnaire.md` top to bottom. The label on each question determines behavior:
- 🤖 items: present the `analyze`-phase draft and ask the user to confirm/correct. Don't make them type what the machine can infer.
- 🧠 items: **always ask the human.** Never fill these from code. These are the firing conditions for metamorphic / formal-methods / trust-boundary decisions.
- 🔒 items: force reference form. Reject any answer that looks like a raw credential; require op:// or an env var name.

## What the generated skill must always contain

Two fixed blocks, inserted regardless of answers, so discipline rides on the mechanism:

### 1. Header declaration (verbatim)
```
# ⚠️ Do not hardcode credentials in this file.
# Write access info as references (op://... or env var names) only.
# The bundled secret scan is a warning, not a safety guarantee.
```

### 2. Bundled loose secret scan
Copy `scripts/detect_secrets.py` alongside the generated skill, and instruct the user to wire the same heuristic into pre-commit (Lefthook) — generation-time check plus commit-time check = two layers. The script prints its own "passing ≠ safe" disclaimer; keep that.

## Output shape of a project-specific skill

```
<project>-robustness/
├── SKILL.md          # answers to the questionnaire, organized by section
│   ├── header declaration (verbatim, above)
│   ├── §1 trust boundaries (→ fuzzing targets)
│   ├── §2 undefinable-output domains (→ metamorphic)
│   ├── §3 safety-critical constraints (→ formal methods)
│   ├── §4 existing-test inventory + twin implementations (→ mutation / differential)
│   ├── §5 verify execution targets + access REFERENCES (🔒)
│   └── §6 project constraints/philosophy (→ recommend suppression filter)
└── scripts/
    └── detect_secrets.py   # copied from the generic plugin
```

## Reminder on limits (state honestly in the generated skill)

- The secret scan is heuristic; it misses reference-shaped secrets and low-entropy secrets. Not a substitute for gitleaks/trufflehog.
- Firing conditions the human confirmed are only as good as the human's knowledge at generation time; re-run the audit when the codebase's shape changes.
- ⚠️ Whether microsoft/apm cleanly expresses "a plugin bundling a generator that emits another skill" is unverified here — confirm against apm's actual conventions.
