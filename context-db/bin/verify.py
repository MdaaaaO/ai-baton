#!/usr/bin/env python3
"""Verify the context DB — schema integrity check. Exit non-zero on any error.

Checks every content doc for:
  - a frontmatter block,
  - the required keys (title/type/domain/status/updated),
  - `type` and `status` drawn from the allowed vocabularies,
  - an ISO `updated` date that parses,
  - INDEX.md being up to date (regenerate and diff).

Operates on the content root gen_index takes from kit_profile.context_root() (docs/layout.md's
"Content root" paragraph). Run via `make -C $BATON/context-db verify`. Stdlib only.
"""
from __future__ import annotations
import os
import sys
from datetime import datetime

import gen_index as gi  # same dir
import kit_profile as profile  # same dir — the active profile's extra domains

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
# status: archived docs are exempt (they exist to hold the bulk). Non-fatal —
# a warning at flush time, not a blocker (never stand between a session and its flush).
ACTIVE_SIZE_WARN = 30 * 1024  # ~8k tokens
# SESSION_INDEX.md is read at every session start (WORKSPACE.md § Sessions); gen_sessions.py keeps
# ended rows short and capped (MAX_ENDED), so passing this means active rows have bloated. Non-fatal.
SESSION_INDEX_WARN = 10 * 1024


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
            try:
                datetime.strptime(u, "%Y-%m-%d")
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

    # Size guardrail (non-fatal) — surface active docs that have grown past the
    # lean-handoff threshold, whatever the schema verdict is.
    oversized = []
    for r in rows:
        if r.get("domain") == "archive" or r.get("status") == "archived":
            continue
        try:
            sz = os.path.getsize(os.path.join(gi.ROOT, r["_path"]))
        except OSError:
            continue
        if sz > ACTIVE_SIZE_WARN:
            oversized.append((sz, r["_path"]))
    if oversized:
        print(f"⚠ {len(oversized)} active doc(s) over "
              f"{ACTIVE_SIZE_WARN // 1024}KB — trim to keep re-reads cheap "
              f"(move old Session-log entries to archive/<slug>-log.md):",
              file=sys.stderr)
        for sz, p in sorted(oversized, reverse=True):
            print(f"  - {p}: {sz // 1024}KB", file=sys.stderr)
    try:
        si = os.path.getsize(os.path.join(gi.ROOT, "SESSION_INDEX.md"))
    except OSError:
        si = 0
    if si > SESSION_INDEX_WARN:
        print(f"⚠ SESSION_INDEX.md is {si // 1024}KB (> {SESSION_INDEX_WARN // 1024}KB) — it is read at every "
              f"session start; trim the active rows' working_on/responsibilities or lower MAX_ENDED",
              file=sys.stderr)

    if errors:
        print(f"FAIL — {len(errors)} problem(s):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print(f"OK — {len(rows)} docs, frontmatter valid, INDEX.md fresh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
