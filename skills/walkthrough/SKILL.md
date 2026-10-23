---
name: walkthrough
description: "Leads a linear walkthrough of a PR, repo area or epic, then three questions in one carousel; confirmed answers land in the context doc's `## In plain words`. Use when asked to walk through, explain or teach back a change. Not for reviewing."
metadata:
  version: "1"
  updated: "2026-10-23"
  reviewed: "2026-10-23"
user-invocable: true
argument-hint: "<pr|area|epic>"
---

# walkthrough — lead the operator through a change, then check it landed

The record (diagrams, `## Verified`, the epic doc) is what a session reads; this checks that the operator took it
in, and keeps only what they confirmed. One bounded conversation: three steps, one carousel, one write.

## 1. Resolve the target

- `$ARGUMENTS` names a PR (`<owner/repo>#<n>`, URL, or a number in the current repo), an epic (ticket key or
  context doc) or a repo area (a repo and a path or topic). Anything else, or two readings: ask once through a
  carousel (`docs/carousel.md`), then go.
- `<target>` is the label used in every recorded line: `#<n>` for a PR, the key for an epic, the area name.
- The **home doc** is where the result lands: the epic context doc for a PR or epic (found via `.context/INDEX.md`
  or `ctx_resolve`), the repo card for an area. No home doc: walk anyway, record nothing, and say so at the end.

## 2. Gather — artefacts first, never the raw diff first

- A PR: the body's `## Diagrams`, `## For the operator` (older PRs have none — skip it), `## Verified`, via
  `gh pr view <n> -R <repo> --json title,body,files` (`gh-cli` for the traps).
- The home doc: `## Goal`, `## What was built`, `## Key decisions & gotchas`, `## Architecture` and any
  `## In plain words` lines already there. A repo card: its sections.
- The diff (`gh pr diff`) only when a step needs a line the artefacts lack, and only that file's hunk. The
  artefacts silent on a point: say "the artefacts do not say", read that hunk, never fill the gap from memory.

## 3. Walk — one step per turn

Three steps, in this order, one per turn; after each, stop and wait (the operator may interrupt with a question:
answer from the artefacts, then continue).

1. **Purpose** — what it is for and who or what changes because of it. Plain words, no file names.
2. **How it runs** — one run end to end, from `## Diagrams` and `## For the operator`: name the picture, do not
   redraw it.
3. **Where it breaks** — the failure paths and guards, and what `## Verified` proves and does not.

A step is at most 8 lines. It states claims the artefacts back, so the questions below have an answer in them.

## 4. Ask — three questions, one carousel

After step 3, one `AskUserQuestion` call (`docs/carousel.md`) with exactly three questions:

| # | Kind | Asks | Belongs to step |
|---|---|---|---|
| 1 | behaviour | what happens in one named situation | 2 |
| 2 | failure | what goes wrong, or is caught, in one named case | 3 |
| 3 | see it | what the operator would run to see it work | 3 |

- Each question has **3 options**: one right, two plausible near misses (a real mis-reading of the same
  artefacts, never an absurd or trick option). `Other` is the operator's own words. Vary where the right option
  sits; this is a check, not a recommendation, so **no option is marked `(Recommended)`** and none hints at
  the answer in its description. `header` ≤ 12 characters.
- Headless (no `AskUserQuestion`): end the reply with the numbered `Decisions` block in the same shape
  (`docs/carousel.md` § No tool), no `Recommended` mark, and record nothing until it is answered.
- **Confirmed** = the right option, whose wording becomes the line, or `Other` words that agree with the
  artefacts, which become the line as written (trim for length only, never rephrase into model prose).
  `Other` words that contradict them are a miss.

## 5. A miss — re-open once, never a third loop

- No blame and no score talk: say what the artefact shows in one or two lines, then re-open that question's
  step with a **different explanation** (another angle, example or picture, not the same words).
- Ask that question once more, with fresh near misses; several misses go in one carousel.
- Right now: confirmed. A second miss: park the step as **open** and say what stayed unclear. Stop there.
- A wrong answer is never written anywhere.

## 6. Record — only what was confirmed

All writes go through the ctx tools (`ctx_str_replace`, `ctx_insert`, `ctx_log`, naming `actor` when the session
is registered), never `Write`/`Edit` (`WORKSPACE.md` § The `.context/` DB).

- `## In plain words` in the home doc, one line per confirmed answer:
  `- <date> — <line> (walkthrough <target>)`, `<date>` today. No section yet: `ctx_insert` the heading right
  after `## Key decisions & gotchas` (epic) or `## What it is` (repo card).
- **At most 10 lines.** A line the new answer supersedes (same subject) is replaced with `ctx_str_replace`,
  never appended beside it; at 10, replace the oldest line instead of adding an 11th.
- Nothing else goes in: no wrong answer, no parked step, no score, no line the operator did not confirm.
- One Session log line through `ctx_log`: `walkthrough <target>: 3/3 confirmed, 1 re-opened (<step>)`.
  No re-open: `walkthrough <target>: 3/3 confirmed`. A parked step: `walkthrough <target>: 2/3 confirmed, 1
  re-opened (<step>), 1 parked (<step>)`.

## 7. Report

One message: the lines recorded (or "none — no home doc" / "none — headless, awaiting answers"), the count line,
any parked step with what stayed unclear, and the home doc.

## Not in scope

Reviewing someone's PR → `pr-review`. Flushing a session → `session-handoff`, which keeps `## In plain words`
current but never adds a line. A PR body's own operator brief → `pr-open`.
