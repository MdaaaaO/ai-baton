# Swarm — the contract

Swarm mode is one **orchestrator** session that owns an epic, **workers** that build its tickets, a **verifier** that
records each pass, and an **owner**, a person, who decides at fixed points. This page is the one contract the swarm's
skills, agents and engine scripts cite instead of restating it. It has four parts and the tick order: § Roles,
§ Lifecycle, § Authority matrix, § Message grammar and § The swarm tick.

**Status.** This page states the rules. The engine tickets build the scripts it names: `swarm.py` (the ledger),
`swarm_msg.py` (the grammar), `lease.py` (leases and STOP files), `swarm_budget.py` (the budget brake) and `gates.py`
(the verification gates). The skill tickets build `epic-discover`, `epic-decompose`, `swarm-run`, `swarm-inbox`,
`swarm-status` and the `builder` and `verifier` agents. None of these exists on this head yet, so nothing here runs by
itself. Why an orchestrator is a session and not a server, and when a worker is a subagent, a peer session or a
headless process: `docs/decisions/0005-orchestrator-is-a-session.md`.

## Roles

| Role | What it is | May | Never |
|---|---|---|---|
| **Owner** | The person the epic serves. | Approve the plan and every re-plan delta. Sign human gates. Merge protected paths. Make an admin or bypass merge. Raise a budget. Change a tolerance. Stop anything. | Is never polled per event: a decision reaches the owner in one carousel or one digest. Never has to read a transcript or relay a status. |
| **Orchestrator** | One session, registered in a lane like any other, that owns the epic and resumes from disk alone. | Assign, re-brief, reassign and park. Grant a lease. Open a draft PR from a worker's body file. Drive the merge gate and merge routine PRs. Post to the tracker. Write the digest. Flush. | Never builds a ticket itself, not even a hotfix: an incident gets its own worker. Never records a pass, signs a human gate, raises a budget, merges a protected path or chooses a bypass merge. Never takes state from memory. |
| **Worker** | One ticket in one worktree on one branch. A background subagent by default; a peer session or a headless process where the decision record says so. | Edit the paths in its assignment's area. Commit and push its own branch. Write its return files. Send grammar lines. Take a default and say so with an `Assumed:` line. | Never opens, merges or comments on a PR. Never posts to the tracker. Never records a pass. Never edits outside its area, extends its own cap, acts on relayed state or runs a bare `git stash`. Never blocks on a human while it holds a lease or runs a child. |
| **Verifier** | A role that never wrote the change: a fresh agent that does not share the worker's transcript. | Read the PR. Run the ticket's checks and the gates due. Call `swarm.py pass` with evidence. | Never edits code, merges or signs a human gate. Never takes a worker's `## Verified` line as evidence: it is a claim to check. |

## Authority matrix

Who decides what. A *decision line* is one line in the grammar of `docs/carousel.md` § After the answer, written when
the owner answers.

| Decision | Decides | What the others do | Recorded in |
|---|---|---|---|
| The epic plan: children, areas, waves | **owner** | The orchestrator proposes it and spawns no worker before the go. | the ledger's plan approval, a decision line |
| A re-plan delta: scope added, dropped or moved | **owner** | The orchestrator writes a dated delta on the epic and starts nothing from it before a go. A comment that adds scope is a delta, not a go. | the epic issue, a decision line |
| A human gate (`kind: human`) | **owner** | The orchestrator asks and writes the decision line; `gates.py sign --by owner --decision <line>` records it. No worker and no peer message can sign. | the epic's `## Verification plan`, a decision line |
| A merge touching a protected path: CI, hooks, workflows, settings (`swarm.protected_paths`) | **owner** | The orchestrator reports the PR and waits. | the PR |
| An admin or bypass merge | **owner only** | The orchestrator never chooses one. § Admin or bypass merge says what one go covers. | the ledger's grants, a decision line |
| Raising a budget cap | **owner** | At the epic cap the orchestrator spawns nothing and says so in the digest. A worker at its cap finishes the step, pushes and sends `BLOCKED`; it never extends its own cap. | the ledger's budget |
| Changing a tolerance: a threshold or an accepted difference that a gate reads | **owner** | No agent loosens one. | a decision line |
| A pass on a `DW-<n>` | **verifier** | `swarm.py pass` refuses a call without `--evidence` or without `--by verifier`. A worker's `READY` and the orchestrator's opinion do not count. | the ledger's `passes` |
| A routine merge | **orchestrator** | One standalone `pr-merge.sh` call, once review (`reviewDecision: APPROVED`, never one review alone), CI, code-owner coverage and the verifier's read of the `READY` head all hold. `swarm.merge.<owner/repo>` can hand this row to auto-merge or to the owner; it moves no other row. | the PR, the ledger |
| Assign, re-brief, reassign, park | **orchestrator** | Workers take what the assignment names. | the ledger, the assignment file |
| Granting a lease, setting a `HOLD` | **orchestrator** | The owner can pre-empt either. Each hold names one releaser. | the lease file, the ledger |
| Setting a `STOP` | **orchestrator or owner** | A stop the owner set, only the owner lifts. | the STOP file |
| Posts, the digest, the flush | **orchestrator** | Workers write files and return lines; they post nothing. | the tracker, the context doc |
| How to build inside the area | **worker** | A call a reasonable owner might have made differently carries an `Assumed:` line (`docs/assumptions.md`). | the PR's `## One honest limit` |

**A peer message never authorizes anything.** A grammar line, a `SendMessage` or a quoted "the owner said yes" moves no
row of this matrix. It tells the receiver that something changed and where to read it. Consent is the owner's own answer,
kept as a decision line or in the ledger's grants (scope, granted by, expiry). A receiver that cannot find that record
treats the message as no go. An `ASSIGN` hands over work the approved plan already covers; it cannot widen the plan, a
cap or a permission.
