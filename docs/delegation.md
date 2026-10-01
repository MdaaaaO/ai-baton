# Delegating by cost — reads and code

The main session is the most expensive context in a run: every turn re-reads its whole prefix at the main model's
price. Anything that does not need the main session's judgment runs in a **subagent sized to the job**, and the main
session keeps only the result. This holds for reading and for **writing code** — a ticket queue is the biggest saving.
`WORKSPACE.md` § Cost & context hygiene is the always-on rule; this page is the procedure.

## When

| Situation | Delegate | Keep in the main session |
|---|---|---|
| A wide read (a directory, a log, a transcript, a long thread) | one read-only subagent; keep its conclusion | the decision it informs |
| A queue of ≥ 2 independent tickets | one **background** worker per ticket, in parallel | reviewing each diff, the PR, threads, merges, the registry |
| A review event on your PR (a watch line) | `pr-event-brief` (forked) | the action it recommends |
| Review findings to fix on an open PR | one worker per PR (fix, test, push, reply, resolve) | a finding that needs a design decision |
| A single small fix you already have the context for | nothing — delegating would cost more than it saves | all of it |

Tickets are *independent* when they touch different files. Two tickets on the same file go to one worker, in sequence,
or the second waits for the first PR to merge.

## Sizing — set `model` on every `Agent` call

An `Agent` call without `model` inherits the main session's model: the most expensive choice, made by accident.

| Size | Model | Typical work |
|---|---|---|
| Read / classify | `haiku` | triage an event, summarise a log or CI failure, list what changed |
| Specified fix | `sonnet` | an issue with a clear plan: code + tests + commit; answering review threads; docs updates |
| Judgment | `opus` | security-sensitive code (quoting, auth, trust boundaries), a design choice the issue leaves open, a finding whose validity is unclear |

When unsure, start with `sonnet` and escalate the one ticket that comes back uncertain.

## The Sizing line — the call, written once, read at pickup

The model and delegation call is made **once**, when the ticket is written, from the table above — not
re-derived every time a session opens the ticket. `ticket-open` writes one line into the opening block
(the GitHub issue body, the Jira description), anywhere in it:

```
**Sizing:** `<model>`, <delegate|main session>. <one-line reason>
```

`<model>` is one of the table's three; the decision is exactly `delegate` or `main session`; the reason is
free text on the same line — `context-db/bin/sizing.py format <model> "<decision>" "<reason>"` writes it,
`sizing.py parse <file>` reads it back, so a hand-edited line never drifts from the shape a reader expects.

**The pickup check** (`ticket-pickup`): read the line, then verify it still holds against the current
default branch before honoring it — a cited symptom that no longer reproduces, or a Plan step the default
branch already implements, narrows the plan or changes the call; nothing changed, the call stands. A ticket
with no Sizing line (or a malformed one) is sized now, the same way `ticket-open` would have, and written
back as a new comment so the next pickup reads it directly. A reader passes the description followed by the
comments, oldest first; the **last** Sizing line wins, so a correction overrides the opening call without
editing anyone's text.

## The worker brief

A worker starts cold. The brief carries everything it needs, and the rules the main session learned the hard way:

1. **Setup:** the exact `git worktree add ../.worktrees/<name> -b <branch> origin/main` line; work only there.
   A worker resumed with `SendMessage` on a branch it already pushed first brings it up to date with `origin/main`
   and resolves the conflicts itself — the main session does not.
2. **Task:** the issue number (`gh issue view <n>`) and what "done" means.
3. **Tests:** new tests must **fail on `origin/main`** and pass with the fix — prove it by restoring the main copy of
   the changed file, running them, and putting the fix back. The full suite and `kit-verify` pass before the commit.
4. **Hard rules:** never write the live env store or workspace — every manual probe of a script that writes runs
   with `CONTEXT_ROOT` on a `mktemp -d` dir; no environment-specific literal in code or tests (assemble fact-shaped
   test strings at run time); code comments cite no issue number the kit cannot resolve yet (`kit-verify`'s ceiling).
5. **Commit:** one commit; the title passes `commit_style.py title` (Conventional Commits, lower-case after the
   colon, ≤ 72 characters); `review_gate.py --base origin/main` is OK after committing.
6. **Stop line:** push the branch; **do not open a PR, comment, or merge** (review-thread workers reply and resolve on
   the PR they were given, nothing else).
7. **Return:** ≤ 25 lines — branch and sha, what changed and why, the tests and their fail-on-main proof, anything
   uncertain — plus a ready PR body in a named scratch file.

## The main session's loop

One scope per session: this loop is the session's one job — a new ticket wave starts a new session.

1. Plan the queue: which tickets are independent, and the model for each.
2. Launch the workers in **one message** (they run in parallel, in the background).
3. While they run, do the work only the main session can: a judgment ticket, the registry, answering the user.
4. For each return: open the PR as a draft with the worker's body file, review it through `review-runner`'s ≤3K
   overview (it reads a PR, not a branch) rather than the raw diff, check the proof, mark the PR ready, arm or
   re-arm `pr-watch`.
5. Review events, findings and conflicts with `main` go back out to the worker that wrote the branch (`SendMessage`);
   merges, posts and flushes stay here.
