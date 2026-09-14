#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""Loose secret detector — a warning against accidental hardcoding, NOT a guarantee.

Deliberately heuristic and imperfect. Its purpose is to catch the naive
"pasted a credential straight in" mistake, not to be a real secret scanner.

IMPORTANT (epistemic honesty): passing this scan does NOT mean a file is safe.
It will miss:
  - secrets that look like references
  - low-entropy secrets (short tokens, passphrases made of real words)
For real coverage use a dedicated tool (gitleaks / trufflehog) in addition.

Exit codes: 0 = nothing flagged, 1 = something flagged (review it).
Intended to run both at skill-generation time and in pre-commit (Lefthook).
"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path

# 1. Known credential prefixes (high signal, low false-positive).
KNOWN_PREFIXES = re.compile(
    r"\b("
    r"AKIA[0-9A-Z]{12,}"       # AWS access key id
    r"|sk-[A-Za-z0-9]{16,}"    # OpenAI-style secret key
    r"|ghp_[A-Za-z0-9]{20,}"   # GitHub personal access token
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"  # Slack token
    r")"
)

# 3. Key-ish name next to a long quoted value.
#    Deliberately narrow to reduce noise. Ignores obvious reference forms.
KEYISH = re.compile(
    r"""(?ix)
    \b(api[_-]?key|secret|token|password|passwd|private[_-]?key|client[_-]?secret)\b
    \s*[:=]\s*
    ["']([^"']{12,})["']
    """
)

# Reference forms we treat as OK (the *encouraged* pattern, not a secret).
LOOKS_LIKE_REFERENCE = re.compile(
    r"""(?ix)
    ^\s*["']?                       # optional opening quote
    (op://                          # 1Password secret reference
    |\$\{?[A-Z_][A-Z0-9_]*\}?       # ${ENV_VAR} or $ENV_VAR
    |env:|process\.env\.|std::env)  # common env accessors
    """
)


def shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    freq = {c: s.count(c) for c in set(s)}
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in freq.values())


# 2. High-entropy long run — the fuzzy one, most prone to false positives,
#    so we keep the bar high and skip anything that reads like a reference.
TOKEN = re.compile(r"[A-Za-z0-9+/=_\-]{24,}")
ENTROPY_THRESHOLD = 4.0  # bits/char; random-ish base64/hex clears this.


def scan_line(line: str) -> list[str]:
    findings: list[str] = []

    for m in KNOWN_PREFIXES.finditer(line):
        findings.append(f"known-prefix: {m.group(1)[:8]}…")

    for m in KEYISH.finditer(line):
        value = m.group(2)
        if LOOKS_LIKE_REFERENCE.match(value):
            continue  # this is the good pattern — a reference, not a secret
        findings.append(f"key-with-value: {m.group(1)}=…({len(value)} chars)")

    if not LOOKS_LIKE_REFERENCE.match(line.strip()):
        for m in TOKEN.finditer(line):
            tok = m.group(0)
            if shannon_entropy(tok) >= ENTROPY_THRESHOLD:
                findings.append(f"high-entropy: …{tok[-6:]} ({len(tok)} chars)")

    return findings


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv[1:]] or [Path(".")]
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            files.extend(fp for fp in p.rglob("*") if fp.is_file())
        elif p.is_file():
            files.append(p)

    flagged = False
    for fp in files:
        try:
            text = fp.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for finding in scan_line(line):
                flagged = True
                print(f"{fp}:{i}: {finding}")

    print("---")
    if flagged:
        print("Potential hardcoded secrets flagged above. REVIEW them.")
    else:
        print("Nothing flagged.")
    # The crucial disclaimer, always printed:
    print(
        "NOTE: passing this scan is NOT proof of safety. This is a loose "
        "heuristic warning; use gitleaks/trufflehog for real coverage."
    )
    return 1 if flagged else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
