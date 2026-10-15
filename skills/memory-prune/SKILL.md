---
name: memory-prune
description: "Audits the harness auto-memory against the always-on prefix, skills and `.context/`, then rewrites notes and `MEMORY.md` to their residue after one carousel, archiving originals first. Use when kit-health warns the auto-memory is over budget, or after a session that added notes. Not for session-retro or session-handoff."
metadata:
  version: "1"
  updated: "2026-10-15"
  reviewed: "2026-10-15"
user-invocable: true
argument-hint: "[--check]"
---

# memory-prune — `MEMORY.md` holds only what nothing else already loads

`MEMORY.md` loads on every turn, and its notes drift into copies of `WORKSPACE.md` § Rules, skills and `.context/`
docs. This skill measures the memory dir, has one Sonnet worker classify every note against what is already
loaded, parks every original in `archive/`, asks once, then rewrites the notes and the index to their residue.

## 1. Check

Run `python3 $BATON/skills/memory-prune/memory_check.py` (read-only; `--dir <path>` or `MEMORY_DIR` overrides the dir
`setup.sh` wires). Keep its output verbatim: it is the "before" of § 5.

- Exit 2 → print `memory-prune: no memory dir — nothing to prune` and stop.
- `$ARGUMENTS` is `--check` → show the output and stop.
- Exit 0 with no finding and the user asked for no prune → show the summary line and stop.
- Otherwise go on. Findings: `INDEX_BUDGET`/`LONG_LINE` (exit 1, past `MEMORY_INDEX_WARN`/`MEMORY_LINE_WARN`),
  `NO_FILE`, `NO_INDEX`, `DANGLING`, `FRONTMATTER`, `RULES_QUOTE`, `SECRET` (kind and line, never the value),
  `LARGE` (> 10 KB: judged per section).

## 2. Classify — one Sonnet worker

Spawn ONE `Agent` (`subagent_type: general-purpose`, `model: sonnet`, foreground). Brief it per
`docs/delegation.md` § The worker brief, with no worktree (it edits no repo), and include:

1. **Read once:** `$BATON/WORKSPACE.md`, `.context/reference/environment.md` where it exists, the skills index
   (`$BATON/docs/skills.md`), `.context/INDEX.md`, the § 1 output, then `MEMORY.md` and every note in the dir.
2. **Return one row per note**, nothing else changed in this pass:
   `<note> · DROP|MERGE→<x>|SHRINK ≤<N>|KEEP · covered_by "<quote ≤ 12 words>" (<file § section>) · residue: <what only
   this note holds, ≤ 15 words> · stale: no|yes — <why>`. A `LARGE` note gets one row per `## ` section
   (`<note>#<section>`). Then the counts per verdict and the projected index bytes.
3. **Guardrails, verbatim:** never drop a note whose rule is NOT in the always-on prefix; MERGE keeps every
   id/command of both sources; archive is written BEFORE any delete; the worker touches nothing outside the
   memory dir. A rule found only in a skill or a `.context/` doc is SHRINK to the rule line plus a pointer, never DROP.
4. **Never quote** a secret-shaped string: name its note and line.
5. **Then wait** for the second message (§ 4); write nothing before it.

## 3. Archive — before anything changes

1. `SCRATCH=$(python3 $BATON/context-db/bin/kit_profile.py scratch)`, then
   `python3 $BATON/skills/memory-prune/memory_check.py --dump > "$SCRATCH/memory-pruned.md"`: every note and the
   index verbatim, each fenced under `### <file>`, secret-shaped values already replaced by `<redacted: <kind>>`.
2. `make -C $BATON/context-db new TYPE=log DOMAIN=archive SLUG=memory-pruned-<YYYY-MM-DD> TITLE="Memory pruned
   <YYYY-MM-DD>"`. An earlier run today: `new` refuses, keep the existing doc.
3. `ctx_insert` the dump after the title line (Bash: `python3 $BATON/context-db/bin/ctx_adapter.py ctx insert
   archive/memory-pruned-<YYYY-MM-DD> --line <n> --from "$SCRATCH/memory-pruned.md"`), then `ctx_fm
   archive/memory-pruned-<YYYY-MM-DD> status archived`.
4. **Verify:** every note name from § 1 appears as a `### <file>` heading in the archive doc. The store refused the
   insert (a secret the dump did not redact), or a heading is missing → stop and report; change nothing.

## 4. One carousel, then the rewrite

Show the worker's table, then ask ONE `AskUserQuestion` (shape: `docs/carousel.md`) with two questions:

- **Prune depth** — *All verdicts* (Recommended: DROP, MERGE and SHRINK as classified) · *Conservative* (DROP and
  MERGE only; SHRINK touches the index line) · *Index only* (rewrite `MEMORY.md`, notes untouched) · *Stop* (the
  archive stays, nothing else changes).
- **Index-line shape** — *Hook line* (Recommended: `- [Title](<note>.md) — <hook>` ≤ 150 chars, grouped by type
  user → feedback → project → reference) · *Title only* (`- [Title](<note>.md)`, ≤ 80 chars). `preview` shows
  two sample lines of each.

Send both answers to the SAME worker (`SendMessage`), with this rewrite contract:

- Frontmatter kept valid: `name`, `description`, `metadata.type` (`memory_check.py`'s `FRONTMATTER` rule).
- Body = `Rule: WORKSPACE.md § Rules (<quote>)` where a prefix rule applies, plus the residue only; no incident
  narrative over one sentence; no dated logs (they are in the archive).
- MERGE: the target keeps every id, command and link of both sources; the merged-away note is deleted.
- DROP deletes the note and its index line. `[[links]]` to a deleted note are repointed or removed.
- The index: one line per note in the chosen shape, nothing else but its heading.
- Writes only inside the memory dir § 1 names; it returns the files changed, deleted and merged.

## 5. Re-check and report

Re-run § 1's command and paste its output beside the "before". A finding left over: one more `SendMessage` round
for that note, never a hand edit. Log the run in the day's kit-health doc (`kit-health/<YYYY-MM-DD>-<env>`, made
as `kit-health` § Report says): before/after summary lines, verdict counts, the archive key. Report in three
lines: index bytes before → after, notes before → after, the archive key.

## Related

- `kit-health` — its § 4 runs `memory_check.py` and warns past the budget, naming this skill.
- `session-retro` — suggests this skill after a session that added notes.
- `session-handoff` — writes notes and index lines; this skill trims them later.
