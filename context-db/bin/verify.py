#!/usr/bin/env python3
"""Verify the context DB — schema integrity check. Exit non-zero on any error.

Checks every content doc for:
  - a frontmatter block,
  - the required keys (title/type/domain/status/updated),
  - `type` and `status` drawn from the allowed vocabularies,
  - an ISO `updated` date that parses,
  - INDEX.md being up to date (regenerate and diff),
  - INDEX.md staying under its size budgets (non-fatal — see INDEX_SIZE_WARN / INDEX_TOTAL_SIZE_WARN below).

Size budgets (all non-fatal, all in bytes, each overridable by an environment variable of the same name; a set
but unusable value prints one `verify: ignoring NAME=value (…)` stderr line and keeps the default):
  ACTIVE_SIZE_WARN    30,000 B  one non-archived doc of any type but `reference`/`repo` (ctx-store's SIZE_GUARD)
  REFERENCE_SIZE_WARN    64 KB  one non-archived `type: reference` or `type: repo` doc (repo deep-dives, snapshots:
                                read in slices, so expected to be large)
  INDEX_SIZE_WARN        10 KB  the `status: active` and `status: closed` rows of INDEX.md. Closed rows count (they
                                are finished work, and `archive` moves them out); reference rows do not (never
                                finished — gen_index.py folds a domain's reference rows past INDEX_REFERENCE_FOLD
                                (12) into one line instead)
  INDEX_TOTAL_SIZE_WARN  2 x INDEX_SIZE_WARN  the whole INDEX.md file, with the bytes per status, so a file
                                bloated by reference rows still warns when its active rows fit
Oversized docs are reported as one line per domain (`<domain>: n docs over budget, largest <slug> (n KB)`)
followed by the remedy for that largest doc, named by what dominates it: its `## Session log` (archive the
entries), else — for a `reference`/`repo` doc — its own budget, else its largest sections. The INDEX findings
print the row count per status.

Also warns (non-fatal) on a malformed decision-ledger line in *Key decisions & gotchas*
(`docs/carousel.md` § After the answer) — a line that is ledger-shaped (starts with a bare date,
then has `→`) but is missing its `; not <rejected>` clause. Any other line in that section (a
free-form gotcha, an arrow in prose, a date in parentheses) never warns.

Also warns (non-fatal) when an `environment.md` states who owns code (`CODEOWNERS =`, "code owner is @x"): the
live CODEOWNERS file is the only source (`skills/pr-open/codeowners.py`), prose about it goes stale.

Operates on the content root gen_index takes from kit_profile.context_root() (docs/layout.md's
"Content root" paragraph). Run via `make -C $BATON/context-db verify`. Stdlib only.
"""
from __future__ import annotations
import math
import os
import re
import sys
from datetime import datetime

import frontmatter as fm  # same dir — `unquote` for a quoted scalar
import gen_index as gi  # same dir
import kit_profile as profile  # same dir — the active profile's extra domains
import log_rotate  # same dir — a log doc's `log_section:` field

TYPES = {"epic", "reference", "repo", "meeting", "1on1", "oncall",
         "self-assessment", "pr-review", "log"}  # what new.sh scaffolds; INDEX.md itself is generated, never a doc
STATUSES = {"active", "closed", "reference", "archived"}
# domain is free-ish but should be one of the known folders' vocab: the engine's core set (kit_profile.CORE_DOMAINS,
# the one list) plus whatever the env store lists under `domains`.
CORE_DOMAINS = set(profile.CORE_DOMAINS)
DOMAINS = CORE_DOMAINS | set(profile.domains())

# Size guardrail: an *active* context doc must stay lean enough that a cold
# session can re-read it to re-ground without meaningfully denting its context
# window — an oversized doc pulled in after a compact is what makes autocompact
# thrash. Long history belongs in archive/<slug>-log.md, so archive/ and
# status: archived docs are exempt (they exist to hold the bulk). The folder decides, not the
# frontmatter: `ctx maintain` gives a new archive doc its source doc's domain and `status: active`.
# Non-fatal — a warning at flush time, not a blocker (never stand between a session and its flush).
# Each budget reads the environment variable of its own name (bytes); an unset, malformed or
# non-positive value keeps the default.
# The active default is ctx-store's SIZE_GUARD (`ctx validate` warns and `ctx maintain` rotates past it), so both
# tools warn at the same size. The kit reads no store file: a store that sets `maintain.size_guard` sets this to match.
ACTIVE_SIZE_WARN = gi.positive_int(os.environ.get("ACTIVE_SIZE_WARN"), 30000, "ACTIVE_SIZE_WARN")  # ~8k tokens
# A `type: reference` doc (a snapshot) or a `type: repo` doc (a repo deep-dive) is read in slices and is expected
# to be large: it has its own, higher budget instead of the active one.
LARGE_DOC_TYPES = ("reference", "repo")
REFERENCE_SIZE_WARN = gi.positive_int(os.environ.get("REFERENCE_SIZE_WARN"), 64 * 1024, "REFERENCE_SIZE_WARN")
# SESSION_INDEX.md is read at every session start (WORKSPACE.md § Sessions); gen_sessions.py keeps
# ended rows short and capped (MAX_ENDED), so passing this means active rows have bloated. Non-fatal.
SESSION_INDEX_WARN = 10 * 1024
# INDEX.md is read whole at every "find first" step (WORKSPACE.md § The .context/ DB). Two budgets, both non-fatal:
# the `active` + `closed` rows (live and finished work: the part `archive` can shrink) against INDEX_SIZE_WARN, and
# the whole file against INDEX_TOTAL_SIZE_WARN, so reference rows (never finished; gen_index.py folds a domain's
# surplus past INDEX_REFERENCE_FOLD) can still not bloat the file unnoticed.
INDEX_SIZE_WARN = gi.positive_int(os.environ.get("INDEX_SIZE_WARN"), 10 * 1024, "INDEX_SIZE_WARN")
INDEX_TOTAL_SIZE_WARN = gi.positive_int(os.environ.get("INDEX_TOTAL_SIZE_WARN"), 2 * INDEX_SIZE_WARN,
                                        "INDEX_TOTAL_SIZE_WARN")
LIVE_ROW_STATUSES = ("active", "closed")  # the statuses that count toward INDEX_SIZE_WARN

# Decision-ledger shape (docs/carousel.md § After the answer): "YYYY-MM-DD · <question> → <chosen>;
# not <rejected options> — <one clause why>". A line only counts as ledger-shaped (and so only then
# gets checked for the rest) when it starts with a bare date AND has the arrow — arrows in prose are common.
LEDGER_SECTION_RE = re.compile(r"^##\s+Key decisions & gotchas\s*$", re.MULTILINE)
NEXT_HEADING_RE = re.compile(r"^#{1,6}\s", re.MULTILINE)
LEDGER_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\b")
LEDGER_ARROW = "→"
LEDGER_REJECTED = "; not "


def ledger_warnings(root: str, rows: list[dict]) -> list[tuple[str, str]]:
    """Malformed decision-ledger lines under each doc's Key decisions & gotchas section."""
    found = []
    for r in rows:
        path = os.path.join(root, r["_path"])
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        m = LEDGER_SECTION_RE.search(text)
        if not m:
            continue
        rest = text[m.end():]
        nxt = NEXT_HEADING_RE.search(rest)
        body = rest[:nxt.start()] if nxt else rest
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            candidate = line[2:].strip() if line[:2] in ("- ", "* ") else line
            if not (LEDGER_DATE_RE.match(candidate) and LEDGER_ARROW in candidate):
                continue  # not ledger-shaped (a free-form gotcha, or a date in parentheses) — never warns
            if LEDGER_REJECTED not in candidate:
                found.append((r["_path"], line))  # a ledger line without its rejected options
    return found


# An environment doc must not carry a CODEOWNERS fact: pr-open derives reviewers from the repo's live file, and
# a line of prose naming an owner outlives the file's own edits. Matches CLAIM shapes only, any case:
# `CODEOWNERS =`/`CODEOWNERS:`, "code owner(s) is/are/:", and "code owner" / "CODEOWNERS file|entry" / "owns" /
# "owner of" followed on the same line by an `@owner`. A line that merely mentions code owners never warns.
OWNER_CLAIM_RE = re.compile(
    r"CODEOWNERS\s*[=:]"
    r"|\bcode[ -]owners?\s*(?:is|are|:|=)\s"
    r"|(?:\bcode[ -]owners?\b|\bCODEOWNERS\s+(?:file|entry)\b|\bowns\b|\bowner\s+of\b)[^\n]*?@[\w-]",
    re.IGNORECASE)


def owner_claim_warnings(root: str, rows: list[dict]) -> list[tuple[str, str]]:
    """(path, line) for each line of an `environment.md` doc that claims who owns code."""
    found = []
    for r in rows:
        if os.path.basename(r["_path"]) != "environment.md":
            continue
        try:
            with open(os.path.join(root, r["_path"]), encoding="utf-8") as f:
                lines = f.read().splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        found += [(r["_path"], l.strip()) for l in lines if OWNER_CLAIM_RE.search(l)]
    return found

SESSION_LOG_RE = re.compile(r"^##\s+Session log\s*$", re.IGNORECASE)
SECTION_RE = re.compile(r"^##\s+(.*?)\s*$")


def kb(n: int) -> int:
    """Whole KB, rounded up (so a doc just over a budget never reads as 0 KB or as under it)."""
    return math.ceil(n / 1024)


def section_sizes(text: str) -> list[tuple[str, int]]:
    """(heading, bytes) of every `## ` section of a doc, heading line included; text before the first one is left out."""
    sections: list[tuple[str, int]] = []
    for line in text.splitlines(keepends=True):
        m = SECTION_RE.match(line) if line.startswith("## ") else None
        if m:
            sections.append((m.group(1), 0))
        if sections:
            name, size = sections[-1]
            sections[-1] = (name, size + len(line.encode("utf-8")))
    return sections


def size_remedy(path: str, doc_type: str, size: int, text: str) -> str:
    """What to do about one oversized doc, named by what dominates it."""
    slug = os.path.splitext(os.path.basename(path))[0]
    sections = section_sizes(text)
    if doc_type == "log":  # `ctx_maintain` rotates a log doc only through its own `log_section:` field
        field = log_rotate.fields(text).get("log_section", "")
        if field:
            return f"`ctx_maintain` rotates its `## {field}` section (`log_section:`) into archive/{slug}-log.md"
        return ("`ctx_maintain` cannot rotate it: no `log_section:` in its frontmatter — add `log_section: "
                f"<heading>` (the `## ` section to rotate) or split it by hand into archive/{slug}-log.md")
    log = sum(n for name, n in sections if SESSION_LOG_RE.match("## " + name))
    if log and log * 2 >= size:
        return f"the session log is {kb(log)} KB of it: move its entries to archive/{slug}-log.md"
    if doc_type in LARGE_DOC_TYPES:
        return (f"a `type: {doc_type}` doc has its own {kb(REFERENCE_SIZE_WARN)} KB budget: split it, "
                f"or `make -C $BATON/context-db archive SLUG={slug}` once it is finished")
    biggest = sorted(sections, key=lambda s: s[1], reverse=True)[:2]
    if not biggest:
        return "no `## ` sections to split it by"
    return "largest sections: " + ", ".join(f"{name} ({kb(n)} KB)" for name, n in biggest)


def oversized_findings(root: str, rows: list[dict]) -> tuple[int, list[str]]:
    """(count of oversized docs, one folded line per domain). A `type` in LARGE_DOC_TYPES is held to
    REFERENCE_SIZE_WARN, any other to ACTIVE_SIZE_WARN; docs under archive/ (whatever their frontmatter says),
    `domain: archive` and `status: archived` docs are exempt."""
    by_domain: dict[str, list[tuple[int, dict]]] = {}
    total = 0
    for r in rows:
        in_archive = r["_path"].replace(os.sep, "/").split("/", 1)[0] == "archive"
        if in_archive or r.get("domain") == "archive" or r.get("status") == "archived":
            continue
        budget = REFERENCE_SIZE_WARN if r.get("type") in LARGE_DOC_TYPES else ACTIVE_SIZE_WARN
        try:
            sz = os.path.getsize(os.path.join(root, r["_path"]))
        except OSError:
            continue
        if sz > budget:
            by_domain.setdefault(r.get("domain", "?"), []).append((sz, r))
            total += 1
    lines = []
    for domain in sorted(by_domain):
        docs = sorted(by_domain[domain], key=lambda d: (-d[0], d[1]["_path"]))
        sz, big = docs[0]
        slug = os.path.splitext(os.path.basename(big["_path"]))[0]
        try:
            with open(os.path.join(root, big["_path"]), encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            text = ""
        n = len(docs)
        lines.append(f"{domain}: {n} doc{'s' if n != 1 else ''} over budget, largest {slug} ({kb(sz)} KB) — "
                     + size_remedy(big["_path"], big.get("type", ""), sz, text))
    return total, lines


def status_counts(rows: list[dict]) -> str:
    """`active 36, closed 2, reference 51, archived 5` — the row count per status, the vocabulary's order first."""
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.get("status", "?")] = counts.get(r.get("status", "?"), 0) + 1
    order = ["active", "closed", "reference", "archived"]
    order += sorted(k for k in counts if k not in order)
    return ", ".join(f"{k} {counts.get(k, 0)}" for k in order if k in counts or k in STATUSES)


def listed_bytes(rows: list[dict]) -> dict[str, tuple[int, int]]:
    """`{status: (rows listed in INDEX.md, bytes they take)}` — the rows gen_index.py lists after folding, one
    byte per row for its newline. Folded reference rows and archived rows take no row bytes."""
    domains: dict[str, list[dict]] = {}
    for r in rows:
        domains.setdefault(r.get("domain", "?"), []).append(r)
    out: dict[str, tuple[int, int]] = {}
    for domain_rows in domains.values():
        for r in gi.fold_plan(domain_rows)[1]:
            n, size = out.get(r.get("status", "?"), (0, 0))
            out[r.get("status", "?")] = (n + 1, size + len(gi.row_line(r).encode("utf-8")) + 1)
    return out


def index_warnings(rows: list[dict], rendered: str) -> None:
    """The two INDEX.md size findings (stderr, non-fatal): the live rows against INDEX_SIZE_WARN, the whole file
    against INDEX_TOTAL_SIZE_WARN."""
    listed = listed_bytes(rows)
    live_n = sum(listed.get(s, (0, 0))[0] for s in LIVE_ROW_STATUSES)
    live_bytes = sum(listed.get(s, (0, 0))[1] for s in LIVE_ROW_STATUSES)
    total = len(rendered.encode("utf-8"))
    if live_bytes > INDEX_SIZE_WARN:
        print(f"⚠ INDEX.md: its {live_n} active/closed rows take {live_bytes} bytes "
              f"(> {kb(INDEX_SIZE_WARN)} KB; the file is {total} bytes) — it is read whole at every "
              f"\"find first\" step; rows by status: {status_counts(rows)}; "
              f"archive finished docs (`make -C $BATON/context-db archive SLUG=<slug>`) to bring it back under budget "
              f"(reference rows are not counted: they fold per domain past {gi.INDEX_REFERENCE_FOLD})",
              file=sys.stderr)
    if total > INDEX_TOTAL_SIZE_WARN:
        parts = [f"{s} {listed[s][0]} row{'' if listed[s][0] == 1 else 's'}, {kb(listed[s][1])} KB" for s in sorted(listed, key=lambda k: -listed[k][1])]
        other = total - sum(b for _, b in listed.values())
        print(f"⚠ INDEX.md is {total} bytes (> {kb(INDEX_TOTAL_SIZE_WARN)} KB for the whole file) — it is read whole "
              f"at every \"find first\" step; listed: {', '.join(parts)}, headings and fold lines {kb(other)} KB; "
              f"lower INDEX_REFERENCE_FOLD / archive closed docs "
              f"(`make -C $BATON/context-db archive SLUG=<slug>`)",
              file=sys.stderr)


def main() -> int:
    rows, problems = gi.collect()
    errors = list(problems)

    for r in rows:
        p = r["_path"]
        t = r.get("type")
        if t and t not in TYPES:
            errors.append(f"{p}: type '{t}' not in {sorted(TYPES)}")
        s = r.get("status")
        if s and s not in STATUSES:
            errors.append(f"{p}: status '{s}' not in {sorted(STATUSES)}")
        d = r.get("domain")
        if d and d not in DOMAINS:
            errors.append(f"{p}: domain '{d}' not in {sorted(DOMAINS)}")
        u = r.get("updated")
        if u:
            try:  # a YAML-quoted date ("2026-10-23") is the same date as the bare one
                datetime.strptime(fm.unquote(u), "%Y-%m-%d")
            except ValueError:
                errors.append(f"{p}: updated '{u}' is not YYYY-MM-DD")

    # INDEX freshness
    index_path = os.path.join(gi.ROOT, "INDEX.md")
    fresh = gi.render(rows) + "\n"
    current = ""
    if os.path.exists(index_path):
        with open(index_path, encoding="utf-8") as f:
            current = f.read()
    # compare ignoring the generated-date line (changes daily) — exactly that line, not every `_…` line
    def strip_date(s: str) -> str:
        return "\n".join(l for l in s.splitlines() if not gi.GENERATED_LINE.match(l))
    if strip_date(fresh) != strip_date(current):
        errors.append("INDEX.md is stale — run `make index`")

    # Size guardrail (non-fatal) — surface docs that have grown past their type's budget, whatever the
    # schema verdict is: one folded line per domain, not one per doc.
    n_over, over_lines = oversized_findings(gi.ROOT, rows)
    if n_over:
        print(f"⚠ {n_over} doc(s) over budget ({kb(ACTIVE_SIZE_WARN)} KB; "
              f"{kb(REFERENCE_SIZE_WARN)} KB for `type: reference`/`repo`) — trim to keep re-reads cheap:",
              file=sys.stderr)
        for line in over_lines:
            print(f"  - {line}", file=sys.stderr)
    try:
        si = os.path.getsize(os.path.join(gi.ROOT, "SESSION_INDEX.md"))
    except OSError:
        si = 0
    if si > SESSION_INDEX_WARN:
        print(f"⚠ SESSION_INDEX.md is {si // 1024}KB (> {SESSION_INDEX_WARN // 1024}KB) — it is read at every "
              f"session start; trim the active rows' working_on/responsibilities or lower MAX_ENDED",
              file=sys.stderr)
    index_warnings(rows, fresh)

    # Decision-ledger shape (non-fatal) — a line that already starts with a date and has the
    # `→` arrow (so it looks like a ledger entry) but is missing its `; not <rejected>` clause
    # (docs/carousel.md § After the answer).
    bad_ledger = ledger_warnings(gi.ROOT, rows)
    if bad_ledger:
        print(f"⚠ {len(bad_ledger)} Key decisions & gotchas line(s) look ledger-shaped (a date, "
              f"then `→`) but miss the `; not <rejected>` clause:", file=sys.stderr)
        for p, line in bad_ledger:
            print(f"  - {p}: {line}", file=sys.stderr)

    claims = owner_claim_warnings(gi.ROOT, rows)
    if claims:
        print(f"⚠ {len(claims)} environment.md line(s) state who owns code — drop them; pr-open reads the repo's "
              f"live CODEOWNERS file (skills/pr-open/codeowners.py), and prose about it goes stale:", file=sys.stderr)
        for p, line in claims:
            print(f"  - {p}: {line}", file=sys.stderr)

    if errors:
        print(f"FAIL — {len(errors)} problem(s):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print(f"OK — {len(rows)} docs, frontmatter valid, INDEX.md fresh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
