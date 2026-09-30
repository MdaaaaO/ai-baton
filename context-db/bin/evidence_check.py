#!/usr/bin/env python3
"""evidence_check.py — every Verified claim in a ticket comment or PR body cites an evidence item
(`ticket-update` § Comment grammar): `**Verified** — <claim> · <evidence>`, one line, or a bulleted list of
claims — `**Verified**` followed by one `- <claim> · <evidence>` line per claim. `<evidence>` is one of three
shapes:

  - a command with its output line: `` `cmd` → `output` `` (backticked command, a `→`, then the output);
  - an http(s) URL;
  - a PR / commit reference: `#<n>`, `<owner>/<repo>#<n>`, a 7-40 char hex commit, or a PR URL.

It checks that an item of one of these shapes is present, never that the claim is true. A file with no
`**Verified**` section passes (the section stays optional, as `ticket-update` has it today); a file whose
`**Verified**` section has every claim citing an item passes too. `ticket-update` and `ticket-close` run this
on the comment/body file before posting, the same place they already run `kit_profile.py public-text-check`.

Usage:
  evidence_check.py <file>     read the comment/body from a file
  evidence_check.py -          read it from stdin instead

Exit 0 — no Verified section, or every claim carries an evidence item.
Exit 3 — at least one claim has none; each is named on stderr, one line per claim:
  evidence_check: <line no>: <claim head>
Exit 2 — the file could not be read.
"""
from __future__ import annotations
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fsutil  # noqa: E402 — the one `<file | ->` reader

VERIFIED_HEAD = "**Verified**"
# The three evidence shapes, by shape only — never whether the claim is true.
#   1. a backtick command followed by `→` and some output (itself backticked or not);
#   2. an http(s) URL;
#   3. a bare issue/PR reference (`#123`, `owner/repo#123`) or a PR URL (already caught by #2) or a commit hash.
EVIDENCE = re.compile(
    r"`[^`\n]+`\s*→\s*\S"          # `cmd` → output
    r"|https?://\S+"                    # URL (a PR URL is one too)
    r"|(?:[\w.-]+/[\w.-]+)?#\d+\b"       # #<n>  or  owner/repo#<n>
    r"|\b[0-9A-Fa-f]{7,40}\b"            # a commit hash
)
HEAD_LEN = 72


def claim_lines(text: str) -> list[tuple[int, str]]:
    """(line no, claim text) for every claim in `text`'s Verified section (none, if there is no such section).

    A Verified section starts at a line whose stripped text starts with `**Verified**`. When that line carries
    more text after the head (the one-line form, `**Verified** — <claim> · <evidence>`), that text is itself
    claim 1. Any immediately following `- ` lines are additional claims (the bulleted form, or extra claims
    after a one-line first claim). The section ends at the first blank line, the first line that does not start
    with `- ` once inside the bulleted part, or a new `**`-prefixed field.
    """
    out: list[tuple[int, str]] = []
    in_section = False
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not in_section:
            if line.startswith(VERIFIED_HEAD):
                in_section = True
                rest = line[len(VERIFIED_HEAD):].strip().lstrip("—-:").strip()
                if rest:
                    out.append((lineno, rest))
            continue
        if not line or (line.startswith("**") and not line.startswith(VERIFIED_HEAD)):
            break
        if line.startswith("- "):
            out.append((lineno, line[2:].strip()))
        else:
            break
    return out


def missing_evidence(text: str) -> list[tuple[int, str]]:
    """Claims with no evidence item, in file order."""
    return [(n, c) for n, c in claim_lines(text) if not EVIDENCE.search(c)]


def head(claim: str) -> str:
    """The claim, trimmed of a trailing `· <evidence>` that isn't there, and capped so stderr stays scannable."""
    short = claim.split("·")[0].strip() or claim.strip()
    return short if len(short) <= HEAD_LEN else short[: HEAD_LEN - 1].rstrip() + "…"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", help="comment/body file, or - for stdin")
    a = ap.parse_args(argv)
    try:
        text = fsutil.read_arg(a.file)
    except OSError as e:
        print(f"evidence_check: {e}", file=sys.stderr)
        return 2
    missing = missing_evidence(text)
    if missing:
        for lineno, claim in missing:
            print(f"evidence_check: {lineno}: {head(claim)}", file=sys.stderr)
        return 3
    claims = claim_lines(text)
    print(f"evidence_check: ok — {len(claims)} claim(s), all cite evidence" if claims else "evidence_check: ok — no Verified section")
    return 0


if __name__ == "__main__":
    sys.exit(main())
