# Composing the week — config, boundary, tie-breaks, sections, delivery

Loaded from `SKILL.md` steps 0, 1, 4, 5 and 9 for the detail behind each rule.

## Config keys

`python3 $BATON/context-db/bin/kit_profile.py get self_assessment`:

- `scope` — the one sentence that defines what counts as "work" this week; quoted at the top of the method.
- `sources` — gates each sweep in step 3; an unlisted source is skipped silently.
- `report` — `lattice` or `week-file`, the deliverable in step 9.
- `ledger` (bool) — on means this machine has the `Artifact` tool and wants every composed week on the
  update-ledger page; off or absent means step 10 is not applicable here. Turn it on with
  `python3 $BATON/context-db/bin/kb.py config-set self_assessment.ledger true`.
- `ledger_url` — the page step 10 republishes; empty until the first run creates it.
- `report_url` — the form the ledger links to (optional).
- `sections` — the report block's ordered headings (§ Report sections).

Without a charter (`.context/self-assessment/README.md`), "work" is whatever `scope` names, and there is no
initiatives model or contribution protocol to honour. `setup.sh` seeds the charter from
`$BATON/context-db/_templates/self-assessment-charter.md` on a fresh environment.

## Window boundary

- Something merged/closed just after the window (e.g. the next Monday) is **excluded** from the week and goes
  in the "Landed just after (W_n+1_)" tail if it gives context.
- **Status as of the window end:** a PR "in review" on Sunday that merged Tuesday is *"in review (merged Tue,
  W_n+1_)"*, not "merged". A cold session run weeks later gets this wrong without the rule.
- **Retraction carve-out:** a later retraction/correction of an in-window claim applies to the week regardless
  of when it landed — the week reports what turned out to be **true**, not what was believed Sunday night (a
  number that looked like a finding on Sunday can turn out to be a measurement confound on Tuesday). Grep the
  days after the window on the week's items for `retract`, `confound`, `was wrong`, `corrected`, `superseded`.
- The sweeps *will* surface items just outside the window (a tracker query bounded on `updated` catches
  next-week resolutions): they go in the labelled tail, never silently merged in, never dropped.

## Tie-break

Cross-sweep numeric conflicts are routine; apply the fixed order, never average or guess:

- **PR line/file stats → GitHub** (the diff is authoritative over a ticket comment's recollection).
- **Ticket-scoped measurements → that ticket's own closing comment** (row counts, per-event shares).
- **A derived/ratio figure in the same comment arbitrates** before choosing — a line stating one figure as a
  multiple of the other pins which of two candidates is right.
- **Nothing arbitrates → drop the contested digit** — a vaguer true statement beats a precise wrong one.

## Week file

- `make -C $BATON/context-db new TYPE=self-assessment …` always scaffolds into `weeks/`, creating that folder
  the first time. Write through `ctx_str_replace` / `ctx_insert`, or `ctx_create` with the whole text.
- `status: archived` marks a completed week as a closed record and self-exempts it from the 30 KB verify
  guardrail.
- A mid-week compose stays `status: active`; the Monday re-run folds the weekend, re-verifies every status
  claim and flips it to archived.

## Report sections

`python3 $BATON/context-db/bin/kit_profile.py get self_assessment.sections` — an ordered list of headings set by
the environment for its own review form, used verbatim and in order. Empty or absent falls back to the kit's
four: `What I worked on`, `Next week`, `Blockers / risks`, `Is there anything else on your mind you'd like to
share?`. The last is the form's free-text prompt: **1–3 sentences of reflection** — a learning, a concern about
direction, a thank-you, a process observation — sourced from the week's *Learnings*, 1:1 notes and meeting
outcomes. It never restates a shipped item or a blocker already listed; when the week gives nothing, write one
plain sentence ("Nothing beyond the above this week.") rather than dropping the heading — the ledger card and the
form both expect it.

## Delivery

`lattice`: the review tool has no connector — the user pastes the block into the form its bot DM
(`WORKSPACE_SLACK_LATTICE_DM`) links to. The block is an external surface (`WORKSPACE.md` § Rules), so no local
paths. A nudge from the tool's own bot is not evidence of submission, and silence there means nothing.
