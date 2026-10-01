# Assumptions — `Assumed:` lines for a call the session took itself

Owner decision, 2026-09-30: an assumption is a decision (`docs/carousel.md` § What counts as a
decision) that the session made itself under standing-go rather than asking first — the fix for an
assumed call that is never surfaced, which is indistinguishable from a decision nobody saw.

## Grammar

```
Assumed: <one line> (revisit: pr-open | ticket-update | session-end)
```

One line: the call, then the checkpoint that will next ask about it. `<one line>` names the call and
what was picked, in the same register as a ledger line (`docs/carousel.md` § After the answer).

## What qualifies — the bound

For a call a reasonable owner might have made differently, not for every default the session had to
pick anyway (that is just work, reported, not asked). More than 4 open lines at one checkpoint means
the session should have asked earlier, not batched it here — the same cap as `docs/carousel.md` §
Shape; the eval near miss this guards is the opposite failure, an unstated assumption with no line.

## Storage

Open lines live under `## Assumptions` in the session's own registry file, next to `## Open
decisions` — same file, same reason: it survives a compaction (`docs/carousel.md` § No tool — #56
re-prints both) and a session end.

## Checkpoints — where a due line surfaces

| checkpoint | renders into |
|---|---|
| `pr-open` | the PR body's `## One honest limit` section (#100) |
| `ticket-update` | the DELTA comment |
| `session-end` | `session-handoff`'s next-session prompt, `### Since last handoff` (#381) |

## Asking and answering

At a line's checkpoint the main session asks every due line in one carousel (`docs/carousel.md` §
Shape), folded in with any other open decision due the same turn. Answered → struck from the `##
Assumptions` list and ledgered in the decision's own grammar (`docs/carousel.md` § After the answer).
Unanswered at session end → carried forward, never dropped.
