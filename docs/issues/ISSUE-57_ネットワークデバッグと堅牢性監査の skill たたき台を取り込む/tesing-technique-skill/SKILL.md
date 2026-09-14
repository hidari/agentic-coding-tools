---
name: robustness-audit
description: Analyze a codebase to find missing or weak test coverage and recommend which testing methods to add for robustness. Use whenever the user wants a testing-strategy audit, asks "what tests are we missing", "how do I harden this", "where should I add fuzzing/property tests", wants to strengthen a codebase's robustness, or wants to generate a project-specific testing skill. Orchestrates the audit as a controller dispatching fresh subagents per phase (analyze -> recommend -> scaffold -> verify -> report), keeping the controller's context clean by passing work via files. Distinguishes machine-detectable gaps from judgments requiring human input, and never over-prescribes.
---

# Robustness Audit

A controller workflow for auditing a codebase's testing posture and recommending targeted robustness improvements. This skill is the **controller**: it reads the situation, dispatches a fresh subagent per phase, and coordinates. It does not do the heavy lifting inline.

Built around a two-layer map of testing methods (a **spine** built up in order, plus **options** inserted when a firing condition fires). Read `references/testing-map.md` for the full map before recommending.

## Core principle: epistemic honesty over false confidence

The single most important behavior: **distinguish what can be judged mechanically from what needs human domain knowledge, and never over-prescribe.** A tool that says "add fuzzing everywhere" destroys its own credibility. When a recommendation depends on semantics the code can't reveal (is this a trust boundary? is this output undefinable? is this constraint safety-critical?), surface it as a *question for the human*, not a verdict.

Label every finding:
- 🤖 **machine-detected** — from static analysis / dependency signatures / test inventory. State the evidence.
- 🧠 **needs-human-judgment** — a candidate depending on domain meaning. Ask; don't assert.
- ⚠️ **unverified** — something this skill cannot confirm (tool maturity, whether a firing condition truly holds). Say so.

## Controller / subagent architecture

This skill borrows the dispatch discipline of subagent-driven development, adapted for auditing rather than implementation.

**Why subagents:** each phase is delegated to a specialized agent with isolated context. The controller constructs exactly what that agent needs — never its own session history — so the agent stays focused and the controller's context stays clean for coordination and human dialogue.

**Four rules, non-negotiable:**

1. **No context inheritance.** A dispatch prompt describes one phase: its inputs, the paths it touches, and the global constraints (project philosophy, no-prod-access). Never paste accumulated prior-phase narrative into a later dispatch. A fresh subagent needs its task and the interfaces it touches — nothing else.
2. **Hand off via files, not context.** A subagent writes its output to a uniquely-named file and returns the path. The controller passes that path to the next phase. Large inventories and diffs never enter the controller's context — only paths do. (Mirrors superpowers' review-package pattern: write diff to a file, pass the path, the content never pollutes the coordinator.)
3. **Status protocol.** Every subagent reports one of four statuses; the controller responds:
   - `DONE` — output file written. Proceed to the next phase with the path.
   - `DONE_WITH_CONCERNS` — completed but flagged doubts. Read them. If about correctness/scope, resolve before proceeding; if observational, note and proceed.
   - `NEEDS_CONTEXT` — missing input. Supply it and re-dispatch. Never guess.
   - `BLOCKED` — cannot complete. **Golden rule of escalation:** never ignore it, and never force the same model to retry unchanged. Surface to the human.
4. **Model selection by task nature.** Mechanical phase (analyze: static inventory) -> fast/cheap model. Standard phase (scaffold: skeleton generation) -> standard model. Judgment-heavy phase (recommend's 🧠 mapping, safety-critical design review) -> most capable model.

**Where this DIVERGES from superpowers (intentional):** subagent-driven development runs continuously, without pausing for the human between tasks. **This skill does not.** Auditing has mandatory stop points that code implementation lacks:
- recommend's 🧠 items require human confirmation (over-prescription guard).
- verify must not touch production without explicit, human-authorized targets.
The controller keeps these two conversations for itself and stops for them. Continuous execution is the wrong default when the deliverable is a judgment, not a passing test.

## The five phases

Run in order. Each is dispatched to a fresh subagent (except the human-dialogue parts of recommend, which the controller holds). Each produces a file.

### 1. analyze (subagent · mechanical · cheap model)
Dispatch a subagent to inventory the codebase mechanically. No recommendations.
- Detect language/stack from manifests.
- Enumerate entry points parsing/deserializing untrusted input (serde, extractors, parser crates) -> fuzzing candidates.
- Inventory existing test kinds (unit, property dirs, snapshot, fuzz targets, mutation config).
- Detect twin implementations -> differential-testing candidates.
Output: a factual inventory file, every item 🤖 with evidence. Returns the path. **No value inferred.**

### 2. recommend (controller holds human dialogue · capable model for mapping)
Map the inventory onto the method map. Split by label:
- 🤖 gaps (e.g. "entry point, no fuzz harness") -> state finding + evidence in the candidate file.
- 🧠 items (undefinable-output -> metamorphic; safety-critical design -> formal methods; is-this-really-untrusted) -> **the controller asks the human.** Do not dispatch these to a subagent; dialogue belongs with the coordinator. See `references/questionnaire.md`.
- Apply project constraints as a suppression filter.
Output: candidate file, each item tagged, firing conditions marked for human confirmation.

### 3. scaffold (subagent · standard model)
Dispatch a subagent to generate **skeletons only**.
- Property-test and fuzz-harness skeletons: fine.
- **Never auto-fill the invariant/property body** — a machine-written property becomes a meaningless tautology. Leave a clearly-marked TODO for a human to supply the real invariant.
Output: skeleton files with TODO markers; returns paths.

### 4. verify (subagent · SAFETY-CRITICAL isolation · standard model)
Dispatch a subagent to run the added tests and measure effect. **Must never touch production.**
- Runs only on human-authorized targets (local / ephemeral-CI / staging-copy) — never production object storage, production databases, or user data. Targets and access come from the project-specific skill as **references** (op:// items, env var names), never hardcoded credentials.
- The controller confirms the target with the human before dispatching (a stop point).
- Measure: mutation-score delta, coverage delta, new crashes. Objective numbers.
Output: before/after metrics file.

### 5. report (subagent · standard model)
Dispatch a subagent to fold everything into a document sorted **confirmed / inferred / unverified** (matching the user's documentation standard). Promote any fuzz-discovered crash input into a regression test (spine L1) as a follow-up.

## Generic controller + project-specific skill

This skill is the **generic, project-independent methodology**. Project facts (trust boundaries, safety-critical constraints, non-production access, philosophy) live in a **separate project-specific skill** consumed here as contract-satisfied data. The boundary is a contract: the generic side asks a fixed question set; the project side answers — a wrapper-plus-contract (library-contracts) shape. See `references/questionnaire.md`.

To generate a project-specific skill, use `references/generator.md`. It structurally enforces two rules so discipline rides on the mechanism:
1. **Never hardcode credentials** — access info as references (op://vault/item/field, env var names) only.
2. **Ship a loose secret-detection script** (`scripts/detect_secrets.py`) — a *warning* against accidental hardcoding, not perfect detection. "Passed the scan" is explicitly not "safe" (the script says so). Wire the same heuristic into pre-commit (Lefthook) for a second layer.

## What not to do

- Don't recommend fuzzing for business-logic / authorization correctness — it can't catch "wrong but didn't crash". That's the formal-methods lane.
- Don't paste prior-phase history into a subagent dispatch. One phase, its interfaces, the global constraints. Nothing else.
- Don't let a subagent inherit the controller's context, or let subagent output flood the controller — hand off via file paths.
- Don't run verify against anything the project skill didn't explicitly authorize, and don't skip the human confirmation stop point.
- Don't present 🧠 items as 🤖 findings. The audit's honesty rests on this.

## Unverified (honesty about this skill's own foundations)

- Whether microsoft/apm cleanly expresses "a controller skill dispatching subagents" and "a plugin bundling a generator that emits another skill" is not confirmed here. The subagent dispatch model is drawn from superpowers, which targets native harness plugin mechanisms (Claude Code etc.), not apm. Confirm against apm's actual conventions before relying on it.
- The superpowers patterns above were read from documentation and search excerpts, not a full read of every source file. Verify against the upstream SKILL.md before implementing.
