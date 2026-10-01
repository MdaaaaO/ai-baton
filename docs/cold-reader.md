# Cold reader — narrate the artefact cold

Owner decision, 2026-09-30 (design comment on #378, recorded against the `docs/diagrams.md` contract):
the gate is a fixed worker brief, not a new skill. A `haiku` fork reads **only** the artefact text — no
repo, no transcript, no context doc, no tools — and answers three fixed questions in one line each. The
main session compares the answers to what it knows; `ticket-open`, `pr-open` and `session-handoff` call
this brief as their last relevant step and point here instead of restating it.

## Gate

Runs only on an artefact that is not `SKIP` under the `docs/diagrams.md` contract: a design-sized ticket
(Sizing `opus`, carries a Sketch), a PR whose diagram plan is not `SKIP`, or a `## Next session` handoff
prompt that hands over open work. Everything else costs nothing — skip the gate.

## The brief

Call the `Agent` tool with `model: haiku` and no tools (`docs/delegation.md` § Sizing — a read/classify
job). The input is the artefact text only, pasted in place of `<artefact>`; hand it exactly this prompt:

```
Read the following artefact text. Answer in exactly three lines, one each: WHERE it plugs in, WHAT
changes, WHAT could break. If the text does not say, reply with exactly one line instead: UNSURE
<question> naming what is missing. No other prose — you have no tools and nothing else to read.

<artefact>
```

## Mismatch protocol

The main session checks the three lines (or the `UNSURE` line) against what it already knows about the
change. A wrong or missing answer means the artefact is not done: fix the body, then re-run the brief
once. A second miss is not silently accepted — it goes to the user as a decision
([`docs/carousel.md`](carousel.md)), not a guess.

## Result line

Write exactly one line into the artefact: `Cold read: ok`, or `Cold read: fixed <question>` naming the
question the fix answered.
