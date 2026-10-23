# Miss check — what the artefact left out

A fixed worker brief, not a new skill. The gates before it test the artefact's text: readable (`docs/cold-reader.md`),
evidenced (`evidence_check`), short (`length_check`). None asks what was left out. A cheap fork reads the artefact,
the ticket's `## Done when` and a diff summary and names what is missing, so the model that did the work never grades
it. `pr-open` (before ready), `ticket-close` (before the outcome comment) and `session-handoff` (before the
next-session prompt, step 10) call it and point here instead of restating it. Every machine; no env key, no system.

## Gate

Runs on every PR about to be marked ready, every ticket close that posts an outcome comment, and every handoff that
writes a `## Next session` prompt. A close with no delivering diff, or a handoff with nothing to hand over, skips it
and says so in one line.

## The brief

Call the `Agent` tool with `subagent_type: general-purpose` and the model below, the same fork as the cold reader
(`docs/cold-reader.md` § The brief): the harness offers no way to strip a fork's tools, so "use no tool" is by
instruction, and the worker gets only the pasted text. `model: haiku`; `model: sonnet` when the diff is above
`deep_lines` changed lines (`deep_lines` in pr-scan's `$PR_REVIEW_HOME/config.json`, default `.context/state/pr-review/`, 600 by default; `git diff --shortstat`
insertions plus deletions). Paste the three inputs in place of the marks: `<artefact>` is the PR body, the close
comment or the next-session prompt; `<done-when>` is the ticket's `## Done when` list (`none` when it has none);
`<diff>` is `git diff --stat` of the work, against the base. Hand it exactly this prompt:

```
Read the artefact, the done-when list and the diff summary below. Name what the artefact leaves out. Reply with
one line per miss, each `MISSED <class>: <item>`, class one of: CRITERION (a done-when item with no evidence in
the artefact), UNTESTED (a changed path in the diff that no test in the diff touches), ASSUMPTION (a call the
work took that the artefact never states), DECISION (a choice only the owner can make that the artefact leaves
unanswered). If nothing is missing, reply with exactly one line: CLEAN. Answer from this text alone - use no
tool, read nothing else. No other prose.

<artefact>

<done-when>

<diff>
```

## What a miss becomes

The main session checks each `MISSED` line against what it knows; a line it can refute is dropped, not argued
with the worker. Then, per class:

| class | action |
|---|---|
| `CRITERION` | fix: add the evidence line, or answer it as the artefact's own gap line (`DW-<n> · not verified - <why>`) |
| `UNTESTED` | fix: add or point at the test, or state why none applies in the artefact |
| `ASSUMPTION` | write an `Assumed:` line in the grammar of `docs/assumptions.md` (the surface's own checkpoint) |
| `DECISION` | a carousel item (`docs/carousel.md`), never a guess |

Fix, then re-run the brief once on the fixed artefact. Call sites use their own surface for the carousel: the PR's
`## One honest limit`, the close comment's `Out of scope / left as is`, the handoff's `### Since last handoff`.

## Second-miss rule

A `MISSED` that survives one fix round goes to the same turn's carousel ([`docs/carousel.md`](carousel.md)) as a
decision for the user, naming the item, and the result line counts it as open. There is never a third fork.

## Result line

Write exactly one line into the artefact, in the section the call site names: `Miss check: clean`, or
`Miss check: fixed <n>, open <m>` - `<n>` the `MISSED` lines fixed, `<m>` those still open (carried to the
carousel). An open item is in the same turn's carousel before the artefact goes out: a ready PR, a posted close
comment or a handed-over prompt never carries `open <m>` with no carousel behind it.

## The log — twenty runs before it may block

The check never blocks a ready, a close or a handoff until a follow-up issue has read the log below and decided.
Every run, for the first twenty, appends one entry to the store doc `kit-health/miss-check-log` (type `log`, domain
`kit-health`, slug `miss-check-log`; scaffold it once with `make -C $BATON/context-db new TYPE=log DOMAIN=kit-health
SLUG=miss-check-log`). Writes go through `ctx_log`, never by editing the file:

```
<date> · <surface: pr-open|ticket-close|session-handoff> · <link to the PR/ticket> · <the MISSED lines, or CLEAN> · <verdict>
```

`<verdict>` is the author's, set after reading the lines: `useful` (a real miss, fixed or carried), `noise` (true
but not worth the turn) or `wrong` (refuted). The brief is tuned on this log, not before; past twenty entries the
log stops growing until the follow-up reads it.
