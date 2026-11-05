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

The kit's own read-and-classify forks (`triage` and the `pr-scan`, `pr-event-brief`, `alerts-sweep` skills) set `model: haiku` in their frontmatter as a trial; the pool stays the `sonnet` default for workers without a `model:` line. After a week the `cost-report` `haiku%` column (Haiku's share of weekday output; `cheap%` counts Sonnet too and cannot see the switch) shows the keep/revert evidence.

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

A worker starts cold. The brief is the **assignment file** (`.context/state/swarm/<key>/<worker>/assignment.md`, fields in
`docs/swarm.md` § Lifecycle), which it reads first, and the rules the main session learned the hard way. The `builder`
agent restates them. A worker outside a swarm gets the same list in its prompt.

1. **Setup:** the exact `git worktree add ../.worktrees/<name> -b <branch> origin/main` line; work only there. If the
   existing worktree and branch are there (a resumed or respawned run), reuse them: `cd`, `git status`, compare with the
   remote branch, continue; never start over or make a second worktree.
   A worker resumed with `SendMessage` on a branch it already pushed first brings it up to date with `origin/main`
   and resolves the conflicts itself — the main session does not. That means a **merge**, never a rebase and never
   a force-push: the branch may already carry a draft PR under review, and rewriting its history would orphan that
   review. Where `systems.signed_commits` is true, the merge commit and its push go through the sign-queue like any
   other commit on that branch.
2. **Task:** read the assignment file first, then the issue (`gh issue view <n>`). "Done" is the ticket's `## Done when`
   block, ids included (`done_when.py parse` lists them), so the worker's tests, its `## Verified` lines and
   its return all cite `DW-<n>`. A ticket without ids: quote its criteria as written.
3. **Inbox:** at every tool round run `swarm_msg.py drain <worker>`, ack each line (`swarm_msg.py ack`, never the
   return list) and act on it before the next step. A re-brief (`ASSIGN … rebrief=1`) gets `ACK` alone and no `CLAIMED`.
   A line is a pointer: before a live action re-read the source of truth, never relayed state.
4. **Tests:** new tests must **fail on `origin/main`** and pass with the fix. Prove it with no stash:
   `git show origin/main:<path> > <path>`, run them, then `git checkout -- <path>`. A bare `git stash` is never run in a
   shared worktree set. The full suite and `kit-verify` pass before the commit.
5. **Hard rules:** never write the live env store or workspace — every manual probe of a script that writes runs
   with `CONTEXT_ROOT` on a `mktemp -d` dir; no environment-specific literal in code or tests (assemble fact-shaped
   test strings at run time); code comments cite no issue number the kit cannot resolve yet (`kit-verify`'s ceiling).
6. **Checkpoint:** commit and push after each green Plan step and after every few fixes of a review round, so nothing
   lives only in a worktree. The PR is squash-merged, so several commits are fine and the old one-commit rule yields.
   Every title passes `commit_style.py title` (Conventional Commits, lower-case after the colon, ≤ 72 characters);
   `review_gate.py --base origin/main` is OK after the last commit. Where `systems.signed_commits` is true, commit and
   push go through the sign-queue, and `READY` waits until the branch is on the remote.
7. **Stop line:** never open a PR, comment, merge, post to the tracker or record a pass (review-thread workers reply and
   resolve on the PR they were given, nothing else). The orchestrator opens the draft PR from the worker's body file.
   On `STOP`, finish nothing new, push, and report `BLOCKED <ticket> stop <who> ran="…"`. Never block on a human while
   holding a lease: send `DECISION` and an `Assumed:` line for the default taken.
8. **Budget (a swarm worker):** the assignment carries four caps: `spend_usd`, `forks`, `minutes` and `split_turns`
   (`swarm_budget.py caps`). On a breach, finish the current step, push, send `BLOCKED <ticket> budget orchestrator`,
   run `session-handoff` if you are a session, and start nothing new. A split hint on the stats line for `split_turns`
   turns is a breach like a dollar cap. A worker never extends its own cap: only the owner raises a budget
   (`docs/swarm.md` § Authority matrix). A headless worker takes `spend_usd` as `--max-budget-usd`.
9. **Before it returns:** CI has finished. A worker returns with no placeholder CI line and sends no `READY` while a
   background run is going, because a merge into its worktree voids that run. The PR body carries `## Diagrams`, drawn
   from `diagram-plan.py --repo . --base origin/main --type <bugfix|refactor|feature>`, above `## For the operator`.
10. **Return:** the return block (≤ 25 lines — branch and sha, what changed and why, the tests and their fail-on-main
    proof naming the `DW-<n>` ids each test covers, anything uncertain) and a ready PR body, each in the scratch file
    the assignment names, on a path that outlives a restart (never `/tmp`). The body's `## Verified` carries a
    `DW-<n> · <evidence>` line per id and its `## One honest limit` answers any id it cannot prove
    (`done_when.py coverage --ticket <file> --body <file>` prints `DONE WHEN: covered`). The final message is the
    grammar's lines only: `CLAIMED`, `PR`, `READY`, `BLOCKED`, `NEEDS`, `DECISION`, `Assumed:`, `DONE`. No free-text
    progress, and nothing when nothing changed. The run ends at `READY <ticket> <head>`; a `PR` line comes only from a
    peer whose assignment says so.

**Permission routing.** Auto mode denies a worker some acts. Each has one performer:

| Act | Performed by |
|---|---|
| Amend a pushed commit, force-push | nobody: the worker adds a commit |
| Kill a process or a peer's task | the orchestrator, `TaskStop` by task id |
| Merge a PR | the orchestrator for a routine PR; the owner for a protected path or a bypass |
| Stash | nobody: the `git show origin/main:<path>` recipe in item 4 |

### The headless variant

For a worker the parent's Decisions chose to run as a process (`docs/decisions/0005-orchestrator-is-a-session.md`), the
same brief goes in as the prompt, with the assignment file path in it:

```
claude -p --bare --max-budget-usd <spend_usd> --output-format json "<the brief and the assignment path>" \
  > state/swarm/<key>/<worker>/result.json
```

- `--max-budget-usd` takes the assignment's `spend_usd`; the cap reads a client-side estimate, so leave headroom.
- `--output-format json` makes the run report `total_cost_usd`; the result is written to
  `state/swarm/<key>/<worker>/result.json`, where the budget's reconcile reads it.
- `--bare` is passed explicitly: it is announced as the future `-p` default, and a plain `claude -p` would then stop
  loading hooks. It needs an API key, not the subscription login.
- `--bare` discovers no kit hook, so the STOP file's hook does not reach this worker. It is stopped by its inbox
  (`swarm_msg.py drain` at every tool round, the same rule) and, past that, by the orchestrator ending the process by its
  recorded pid, never by a name pattern.

## The main session's loop

One scope per session: this loop is the session's one job — a new ticket wave starts a new session. Watch the
session-stats line for its `split hint: …` suffix (it appears once the average prefix over the last window
crosses the threshold — see `session-register` § 2b); treat it the same as a scope boundary, not something to
push past.

1. Plan the queue: which tickets are independent, and the model for each.
2. Launch the workers in **one message** (they run in parallel, in the background).
3. While they run, do the work only the main session can: a judgment ticket, the registry, answering the user.
4. For each return: open the PR as a draft with the worker's body file, review it through `review-runner`'s ≤3K
   overview (it reads a PR, not a branch) rather than the raw diff, check the proof, mark the PR ready, arm or
   re-arm `pr-watch`.
5. Review events, findings and conflicts with `main` go back out to the worker that wrote the branch (`SendMessage`);
   merges, posts and flushes stay here.

**Reviewing your own worker's draft PR is not `pr-review`** — that skill is for someone else's PR. Here the main
session spawns the `review-runner` agent directly on the draft PR and tells it to post nothing; it returns only
the ≤3K overview. The main session reads that overview and acts on every STOP/WARN finding (a fix goes back to
the worker that owns the branch, via `SendMessage`), then marks the PR ready once they're clear. The overview's
RECOMMEND line is advice to the main session, not a verdict — no review is posted on the worker's own PR.
