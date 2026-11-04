# 0005 — An orchestrator is a session, not a server

## Status

Accepted

## Context

`docs/vision.md` § Non-goals says "No orchestration server or daemon. Nothing runs between sessions." Swarm mode (#674)
has one orchestrator own an epic for days: it assigns tickets to workers, watches the repo and the tracker, and has
each result verified by a role that never wrote it. A reader will ask whether that breaks the non-goal.

Designs in the field keep a coordination record, and not always one a later run can read. Magentic-One's task ledger is
"short-term memory for the duration of the task". LangGraph's checkpoints outlive a restart only on a persistent
backend. The task list of Agent Teams stays on disk, but the team ends with its session. Swarm mode's record is a file
the next session reads. A process that watches the queue while no session runs would break the rule.

Peer sessions contending for one serial resource spend most of the split on coordination, so that work belongs to one
session. Fan-out pays off for parallel work that is disjoint on files, one worktree per background worker. The kit has
no written rule for when a worker is a subagent and when it is a peer.

## Decision

**The orchestrator.** An orchestrator is a session in a lane, registered like any other. It runs only while its session
runs and resumes from disk alone: every swarm tick re-reads the ledger, the tracker and git, never memory. The ledger is
a file a session writes, `.context/state/swarm/<key>.json`, rendered as a table in the epic's context doc. Nothing runs
between sessions. There is no control-plane UI and no database. Roles, lifecycle, authority and message grammar are the
contract, `docs/swarm.md` (#676).

**A wake-up is not a daemon.**

| | A session's scheduled wake-up: allowed | A daemon: out of scope |
|---|---|---|
| What acts | the session, one swarm tick | a process with no session |
| Who holds the timer | the harness; the kit ships no process | the kit |
| Where state lives | ledger, tracker and git, re-read every tick | memory or a private store |
| When the session exits | the wake-up stops firing; the next session resumes from disk | it keeps watching the queue |
| How long it lasts | a recurring task expires 7 days after creation: one final fire, then it deletes itself. A multi-day orchestrator re-arms it before then, or it stops ticking with no alert. | until someone stops it |

A helper that stops with its session belongs to that session (`skills/session-register/heartbeat.sh`). A timer the
person sets outside the kit (a cloud or desktop scheduled task) starts a new session, and the same rule applies to that
session; the kit ships no such timer.

**The worker rule (subagent versus peer).** A worker holds one assignment. The subagent is the default; the other two
runtimes each need the reason named here.

| Worker runtime | Use it | Bound by | What it costs |
|---|---|---|---|
| Background subagent in its own worktree | by default | `swarm.max_workers` (default 4); a budget per assignment | A restart ends the run and its scratch; the worktree, the pushed branch and the saved transcript survive. Uncommitted work is safe only if the worktree is intact and the worker is resumed (#686). It cannot ask the owner. Spend is counted at returns and ticks, so a cap can be overshot by one step (#680). |
| Peer session | for a separate repo or lane; it holds a lease only when its work touches a serial resource | its own lane; a lease with a holder, an expiry and a named releaser when it holds one | A full session's prefix. A message to it can be delivered and not read. It can vanish with no notice. |
| Headless process: `claude -p` with `--max-budget-usd` | when a dollar cap is required | the flag; the run reports `total_cost_usd` | The cap reads a client-side estimate, not the bill, and spend can pass it, so leave headroom. With `--bare` no hook runs, so a hook-based stop does not reach it, and it needs an API key, not the subscription login. Without `--bare` it loads the project's hooks and MCP servers, also in an untrusted folder. `--bare` is announced as the future `-p` default, which would drop a hook-based stop from a plain `claude -p` worker, so a worker's command picks the flag explicitly. |

**The defaults.** The mode is not built yet (#674). The worker runtime, the ledger home and `swarm.max_workers` are
defaults in the Decisions block of #674. The owner may override any of them there, and an override changes this note.

**The fan-out guard.** A swarm is used only when the work is parallel, independent by file area and larger than a
handful of tool calls per ticket. Otherwise one session does it. The guard bars parallel workers on one serial
resource; one lease holder working that resource serially stays allowed.

## Consequences

| Effect | Detail |
|---|---|
| No server | Nothing to install, secure or keep alive on either install path. State is files a person can read, diff and fix by hand. |
| No session, no progress | A finished CI run, a stalled worker or a lost orchestrator waits for the next session, and the kit sends no alert in that gap. Accepted: a successor registers with `SUCCESSOR_OF=` and resumes from the ledger (#686). |
| Restarts | A restart stops the background workers, so each worker pushes at every checkpoint and `swarm.py resume` (#677) compares the ledger with the pushed branches. A stopped worker resumes in place, on its worktree and saved transcript (#686). |
| Markdown as source of truth | The ledger is JSON under `.context/state/`, beside the kit's other state, and only sessions write it. The context doc's table is rendered from it, never the reverse. |
| Not Agent Teams | It is experimental, a team is scoped to its session, `/resume` does not restore in-process teammates, the lead is fixed and no teammate spawns under `-p`. A successor cannot take a team over. |
| If reversed | A daemon needs an install on both paths, credentials and a supervisor. `docs/vision.md` § Non-goals changes, and a new note supersedes this one. |

## Sources

- `docs/vision.md` § Non-goals, `docs/delegation.md` § When, `skills/session-register/heartbeat.sh`
- #674 (the mode, the defaults), #676 (contract), #677 (ledger), #679 (lease), #680 (budget), #686 (failover); related #41, #62, #103, #355
- [Magentic-One](https://arxiv.org/html/2411.04468v1): task ledger as short-term memory, progress ledger, stall counter
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence): checkpoints per thread, lost on restart with an in-memory saver
- Claude Code: [agent teams](https://code.claude.com/docs/en/agent-teams), [subagents](https://code.claude.com/docs/en/sub-agents), [headless](https://code.claude.com/docs/en/headless), [CLI reference](https://code.claude.com/docs/en/cli-reference), [scheduled tasks](https://code.claude.com/docs/en/scheduled-tasks)
- [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system): about 15 times the tokens of a chat; a poor fit where agents share context
- [Don't build multi-agents](https://cognition.com/blog/dont-build-multi-agents): conflicting assumptions between parallel subagents
