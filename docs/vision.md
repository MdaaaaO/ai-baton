# Vision

What the kit is, who it is for, and what it will not become.

## What it is

ai-baton is a workspace kit for Claude Code. It gives every session the same skills, rules and memory. A session
hands off to the next through a markdown database, `.context/`. Each machine keeps its own values in one env
store and checks and updates its own copy of the kit. The kit holds no name, id or org setting, so a teammate or a
second machine uses it unchanged.

## Who it is for

Engineers who run several Claude Code sessions across several repos, with a tracker (GitHub issues or Jira) behind
the work. It fits a solo developer with a few side projects and a team that shares one workspace layout. For one
repo and one person, a plain `CLAUDE.md` is enough.

## What it keeps that nothing else has

Other projects do single pieces well. The kit keeps the combination, and these parts in particular:

- **The env fact store.** Per-machine values with discovery manifests, a TTL and provenance, plus the `requires:`
  gate that makes a skill say "not applicable here" instead of improvising.
- **The `.context/` markdown database.** One handoff doc per initiative and a live registry of running sessions.
- **`pr-event-brief`.** A cheap forked triage of one PR event that returns one action.
- **A tracker-agnostic ticket grammar.** The same open, pick up, update and close steps on GitHub issues or Jira.
- **`sign-queue`.** Commits that must be signed on the host go through a queue, never a pasted command.
- **The `kit-health` leak scan.** It finds environment values that crept into the kit.
- **`cost-report`.** Spend per PR and per ticket, set against the work shipped.

## Principles

- **Markdown is the source of truth.** Everything the kit knows is a file you can read, diff and fix by hand.
  Indexes are generated from the files, never the other way round.
- **Bodies state the current rule.** A skill, rule or doc says what holds now. History lives in the changelog and
  decision notes.
- **Env facts never enter the kit.** A value that differs per machine is read from the env store. The kit ships
  only the question and the tool that finds the answer.
- **Cost-aware.** Load context by task, not all at once. Delegate by size: the expensive model reviews and decides,
  cheaper models read and do the mechanical work.
- **Verification over claims.** A PR body shows the command and its output. A gate runs a check instead of
  trusting a statement.

## Non-goals

- **No orchestration server or daemon.** Nothing runs between sessions. A session does the work, then stops.
- **No database as the source of truth.** An index or search layer may sit over the markdown. It never replaces it.
- **No automation writing into the store.** Only sessions write `.context/`. No cron job, webhook or ingest
  tool posts facts into it.
- **No second harness adapter.** The kit targets Claude Code.
- **No code graph.** The kit keeps no structural map of your code. Repo notes stay hand-written.
- **No second install clone.** The plugin is the primary install. A clone is either the install itself
  (`.claude/` is the kit) or a contributor checkout beside your repos (`CONTRIBUTING.md`), never a second
  install of the same kit.
- **No third-party cost source.** `cost-report` uses its built-in estimator with dated prices, so its numbers do
  not depend on another service.

## Later

These are open, and none is scheduled. Each waits for evidence.

- **A harness-neutral core.** A second harness comes only after a real tester reports what breaks.
- **Temporal facts and ownership expiry.** Wanted only if overlapping sessions on several machines show the need.
- **Repo-scoped sessions.** Sessions run at the workspace root today. This is a known gap.

## Landscape

About 25 comparable projects fall into four camps. None combines the kit's pieces, and several do one piece better.

| Camp | Examples | What the kit borrows |
|---|---|---|
| Methodology and orchestration | [superpowers](https://github.com/obra/superpowers), [BMAD-METHOD](https://github.com/bmad-code-org/BMAD-METHOD), [SuperClaude](https://github.com/SuperClaude-Org/SuperClaude_Framework), [ruflo](https://github.com/ruvnet/ruflo) | The skills layer, re-grounding after compaction, a `doctor` check (our `kit-health`) |
| Spec pipelines | [spec-kit](https://github.com/github/spec-kit), [compound-engineering](https://github.com/EveryInc/compound-engineering-plugin), [FRAME](https://github.com/andrey-chmerev/the-frame-ai) | State files (our context docs), evidence lists, a checkpoint before compaction |
| Memory layers | [claude-mem](https://github.com/thedotmack/claude-mem), [Mem0](https://docs.mem0.ai/integrations/claude-code), [basic-memory](https://github.com/basicmachines-co/basic-memory), Anthropic's [memory tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool) | Progressive retrieval, hook-driven capture |
| Workflow, QA and cost | [claude-gh-monitor](https://github.com/kawaz/claude-gh-monitor), [claude-code-action](https://github.com/anthropics/claude-code-action), [skill-lint](https://github.com/himself65/skill-lint), [ccusage](https://github.com/ryoppippi/ccusage) | The ideas behind `pr-watch`, `kit-verify` and `cost-report` |

The orchestration platforms in the first camp that run a server with a database and a control-plane UI stay out of
scope (see Non-goals). Their ideas, such as a hard budget stop or a lock per epic, can still be borrowed without
the server.
