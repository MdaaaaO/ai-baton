#!/usr/bin/env python3
"""length_check.py — a reading-time budget for a ticket, PR or handoff body: words in prose only, one
per-surface default, printed as a warning that never blocks. Sits beside `evidence_check.py` (#60) — the
other body check `pr-open`, `ticket-open` and `session-handoff` run before posting or handing off.

Counting: every whitespace-separated token outside a fenced code block, a markdown table row, an HTML
comment, or a link target counts as one prose word. A fenced block (``` or ~~~, three or more marks,
closed by a matching fence) is dropped whole — Mermaid and sample output included. A table row is any line
that starts and ends with `|` (header, separator and data rows alike). An HTML comment (`<!-- … -->`,
multi-line) is dropped whole — this is also how a diagram-plan or evidence-check marker stays uncounted. A
link target is the `(url)` half of `[text](url)`, a reference-style `[label]: url` definition line, or a
bare `http(s)://` URL — the link TEXT still counts as prose, only the target is dropped.

Budgets (`docs/delegation.md`'s ~200 words/minute, kept here as the one source): ticket 400 (~2 min), PR
1,000 (~5 min), handoff 600 (~3 min). A repo with a long PR template overrides its PR budget with the env
fact `length.repos.<owner/repo>.pr_words` (`kb.py` / `environment-template/config.json`); pass `--repo` to
look it up. No override exists for the ticket or handoff surface.

Usage:
  length_check.py <file> --surface ticket|pr|handoff [--repo <owner/repo>]
      <file> is the drafted body, or `-` for stdin. Prints exactly one line:
        LENGTH: ok
        LENGTH: over (<n> words, budget <m>)
      and exits 0 either way — a long migration PR may legitimately be over; the caller decides whether to
      trim, move detail to a linked place, or publish as is.

Exit codes: 0 always for a well-formed call (the length finding is a warning, never a failure).
            2 an unknown --surface, no --surface, or the file could not be read.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fsutil  # noqa: E402 — the one `<file | ->` reader

try:
    import kit_profile  # noqa: E402
except Exception:  # pragma: no cover — the script must work outside a workspace too
    kit_profile = None  # type: ignore[assignment]

SURFACES = ("ticket", "pr", "handoff")
DEFAULT_BUDGETS: dict[str, int] = {"ticket": 400, "pr": 1000, "handoff": 600}

HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
FENCE = re.compile(r"^([`~]{3,})[^\n]*\n.*?\n\1[ \t]*$", re.MULTILINE | re.DOTALL)
TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
REF_LINK_DEF = re.compile(r"^\s*\[[^\]]+\]:\s*\S.*$")
MD_LINK = re.compile(r"(!?)\[([^\]]*)\]\([^)]*\)")  # [text](url) or ![alt](url) — keep the bracketed text
AUTOLINK = re.compile(r"<https?://[^>\s]+>")
BARE_URL = re.compile(r"https?://\S+")


def strip_html_comments(text: str) -> str:
    return HTML_COMMENT.sub("", text)


def strip_fenced_blocks(text: str) -> str:
    return FENCE.sub("", text)


def strip_table_rows(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not TABLE_ROW.match(line))


def strip_link_targets(text: str) -> str:
    """Drop every link TARGET while keeping link TEXT: a reference-style definition line is dropped whole
    (its "label" names the target, not prose); `[text](url)`/`![alt](url)` keep only `text`/`alt`; a bare
    autolink or URL is dropped outright."""
    text = "\n".join(line for line in text.splitlines() if not REF_LINK_DEF.match(line))
    text = MD_LINK.sub(lambda m: m.group(2), text)
    text = AUTOLINK.sub("", text)
    text = BARE_URL.sub("", text)
    return text


def prose_text(text: str) -> str:
    """`text` with every fenced block, table row, HTML comment and link target removed — what's left is
    counted as prose. Order matters only in that fenced blocks and comments are dropped whole before the
    line-based table filter runs, so a table row or link fixture *inside* a fenced block is never double
    counted or half-stripped."""
    text = strip_html_comments(text)
    text = strip_fenced_blocks(text)
    text = strip_link_targets(text)
    text = strip_table_rows(text)
    return text


def word_count(text: str) -> int:
    return len(prose_text(text).split())


def budget_for(surface: str, repo: str) -> int:
    """The word budget for `surface`, the kit default unless `surface` is `pr` and `repo`'s env fact
    `length.repos.<repo>.pr_words` overrides it. No override exists for `ticket`/`handoff`."""
    default = DEFAULT_BUDGETS[surface]
    if surface != "pr" or not repo or kit_profile is None:
        return default
    override = kit_profile.get(f"length.repos.{repo}.pr_words", None)
    return int(override) if isinstance(override, (int, float)) and not isinstance(override, bool) else default


def render(n: int, budget: int) -> str:
    return "LENGTH: ok" if n <= budget else f"LENGTH: over ({n} words, budget {budget})"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", help="the drafted body, or - for stdin")
    ap.add_argument("--surface", required=True, choices=SURFACES, help="ticket | pr | handoff")
    ap.add_argument("--repo", default="", help="owner/repo — looks up a per-repo PR budget override")
    a = ap.parse_args(argv)
    try:
        text = fsutil.read_arg(a.file)
    except OSError as e:
        print(f"length_check: {e}", file=sys.stderr)
        return 2
    n = word_count(text)
    budget = budget_for(a.surface, a.repo)
    print(render(n, budget))
    return 0


if __name__ == "__main__":
    sys.exit(main())
