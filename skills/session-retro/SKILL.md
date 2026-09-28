---
name: session-retro
description: "Sonnet-forked self-check of this session against the kit: corrections, denials, failures and rule slips from the transcript, each judged kit gap or session slip. Use when winding down after session-handoff, or on \"run a retro\" / \"did I follow the kit?\". Not for the weekly self-assessment or the handoff."
metadata:
  version: "1"
  updated: "2026-09-27"
  reviewed: "2026-09-27"
user-invocable: true
argument-hint: "[<session-id> | <transcript.jsonl>]"
context: fork
agent: triage
model: sonnet
effort: medium
background: false
---

# session-retro — did this session follow the kit, and where did the kit fail it?

You are a read-only fork. You read this session's transcript through a script, never whole, judge what it found against
the kit's rules and return a short list of findings. The transcript never reaches the main session: only your list
does. Never post, create, edit or remember anything; the main session does that after you return (§ 5).

## 1. Extract

Run `python3 $BATON/context-db/bin/session_retro.py`. It reads the session in `$CLAUDE_CODE_SESSION_ID`. When
`$ARGUMENTS` is given, pass it as `--transcript <path>` if it ends in `.jsonl`, else as `--session-id <id>`.

- Exit 3 → return exactly `session-retro: no transcript found — nothing to check` and stop.
- The text report has: a summary line, `skills invoked`, `rule hits (candidates)`, `denials`, `failures` and
  every user message. `*` marks a correction hint, and such a message carries the assistant turn before it.
- When a finding needs the calls around a turn, re-run with `--json` and read the `tool_calls` for that turn.
  Never read the `.jsonl` itself.

## 2. Load the rules the session was under

- `$BATON/WORKSPACE.md` § Rules and § Cost & context hygiene: the always-on rules.
- `.context/reference/environment.md` § Rules, where the file exists: this environment's rules.
- The `SKILL.md` of every kit skill in `skills invoked`, under `$BATON/skills/`. A skill from another plugin is
  not the kit's: skip its findings.

## 3. Judge every candidate

Candidates are the rule hits, the denials, the failures, and every user message, not only the starred ones. The
hint regex misses polite corrections ("hm, I'd rather…"), so read them all.

1. **Is it real?** A rule hit is only a candidate. Confirm that a rule you loaded in § 2 asks for it, and cite that
   rule's section. No rule, no finding. For example: the footer check when no loaded rule asks for a footer, or
   `agent-no-model` for an agent whose definition pins its own model.
2. **Drop the noise.** A failure is ordinary iteration when a test fails before the fix, a `grep` exits 1, or a
   probe fails and the session adapts. It counts only when a rule covers it or the same error repeats three or
   more times. A denial counts when the session walked into a block that a rule had already named.
3. **Kind.** `correction` means the user had to correct or stop the session. `miss` means a rule or skill step
   was not followed and nobody said so. `denial` means a permission rule or hook blocked the call.
4. **Kit gap or session slip.** It is a **kit gap** when the kit's text was missing, ambiguous or
   self-contradictory, or when a kit script or skill step led the session wrong. A kit change would stop every
   session repeating it. It is a **session slip** when the rule was clear and loaded, and the session did not
   follow it. Undecided counts as a slip. A gap in `environment.md` or the env store is a slip for that machine,
   not a kit gap.
5. **Merge.** Repeats of the same rule and cause make one finding, with the count (`×4`) and the first turn.

## 4. Return

At most 15 lines of findings, the kit gaps first, one line each:

```
<correction|miss|denial> · <rule § section | skill § step> · t<turn> <MM-DD HH:MM> · kit gap | session slip — <what happened, ≤20 words>
  gap: <kit gaps only: what the kit should say or do instead, ≤20 words>
```

Then this line, verbatim, so the main session knows what comes next:
`NEXT (main session): offer one kit issue per kit gap (ticket-open) and one feedback memory per session slip; ask first.`

Nothing survived § 3 → return exactly `CLEAN`.

Paraphrase what the user said and what commands printed. Quote at most 8 words, and never a secret, token or
credential value. The main session may paste your lines into an issue draft, so keep them paraphrased.

## 5. After the fork (the main session)

The main session shows the findings as returned, then asks once, listing every proposed action:

- **Per kit gap: an issue on the kit repo.** The repo is `repository` in `$BATON/.claude-plugin/plugin.json`. Use
  `ticket-open` conventions: a Conventional title, type and area labels, the current open milestone, and a lean
  body (Goal, Evidence, Plan). **Never quote private transcript content, never cite a `.context/` or workspace
  path, never name a person, repo or id from the session.** Paraphrase the evidence into a generic scenario (the
  leak rules in WORKSPACE.md § Rules). Before creating, search the kit's open issues for the same gap. When one
  exists, add a `ticket-update` comment instead.
- **Per session slip: a feedback memory.** Write a short note (the rule, how the session slipped, the signal to
  catch it next time) and add its `MEMORY.md` line (`session-handoff` step 4). Update an existing note rather than
  adding a duplicate. When the rule is already in § Rules word for word, offer nothing: it was a one-off.
- `CLEAN`: say so in one line. Offer nothing.

## Related

- `session-handoff`: its last, optional step offers this retro. Run the retro after the flush, not instead of it.
- `ticket-open` / `ticket-update`: how a kit-gap issue is written.
- `self-assessment`: the weekly review of the work shipped. This skill reviews one session's conduct.
