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

## The four layers

The kit sorts what it does into four layers. Each layer answers one question about work an agent does for a person.

- **Spec.** What do you want, and how will you know it is done? The person owns this. The agent does not guess it.
- **Verifier.** What checks the result? Set the checks before the work. Read them after it.
- **Environment.** What does the agent work in? Rules, hooks, tools and the store must stay true, or every session starts wrong.
- **Understanding.** Does the person know what changed and why? This is the keystone. A person cannot delegate understanding.

**Attribution.** "Spec / verifier / environment" is Austin Marchese's organisation of Andrej Karpathy's talks. Karpathy did
not name it as a taxonomy. His own words are "people have to be in charge of the spec and plan". He says this round of models
"can automate what you can verify". And "understanding is still the bottleneck because you cannot be a good director if
you do not understand" (Sequoia Ascent talk, Apr 2026). His own literal "three layers" are in his LLM-wiki gist: raw
sources, wiki and schema, with the operations ingest, query and lint. The fourth layer, understanding, is the kit's
keystone. A person cannot delegate understanding.

Every row below names one mechanism, the layer it serves, where it acts, its issue and its status. `existing` means it
is on `main` today. A status with a version means it shipped in that release. `planned` means it is not built yet.

| Mechanism | Layer | Surface | Issue | Status |
|---|---|---|---|---|
| goal interview | spec | ticket | #620 | shipped (`0.13.0`) |
| `ticket-open`: Goal/Out of scope/Plan block, Sizing line | spec | ticket | n/a | existing |
| `ticket-carousel`: a batch of candidates, one at a time | spec | ticket batch | #105 | existing |
| `ticket-pickup`: checks the Sizing line against `main` | spec | ticket | n/a | existing |
| `ticket-update`: one dated delta comment per step | spec | ticket | n/a | existing |
| carousel: every open decision as one question set | spec | any step | #354 | existing |
| sketch (`sketch.py`): the ticket's stamped diagram | spec | ticket | #384 | existing |
| Done-when ids (`done_when.py`): `DW-n` criteria from ticket to close | verifier | ticket, PR, close | #621 | shipped (`0.13.0`) |
| `evidence_check.py`: each Verified claim cites evidence | verifier | PR body, ticket comment | #60 | existing |
| `verified.py`: the `## Verified` lines of a PR body | verifier | PR body | #100 | existing |
| `review_gate.py`: leak shapes and version bumps | verifier | CI, PR | n/a | existing |
| `kit-verify` (`kit_verify.py`): the kit's own units | verifier | CI, machine | n/a | existing |
| miss check: a fork asks "what did you miss?" | verifier | PR, close, handoff | #622 | shipped (`0.13.0`) |
| `pr-open`: the PR checklist and body gates | verifier | PR | n/a | existing |
| `pr-watch`: surfaces only events you can act on | verifier | PR | n/a | existing |
| `pr-event-brief`: triage of one watch event | verifier | PR | n/a | existing |
| `pr-scan`: your review queue | verifier | PR queue | n/a | existing |
| `pr-review`: you review another's PR | verifier | PR | n/a | existing |
| `ticket-close`: outcome comment per criterion | verifier | ticket | n/a | existing |
| agents `triage`, `review-runner`, `auto-runner` | verifier | PR review | n/a | existing |
| evals (`evals/`): cases the reviewer must pass | verifier | CI | n/a | existing |
| `alerts-sweep`: classifies new alerts | verifier | alert channel | n/a | existing |
| `dbt-sqlfluff-fixes`: clears recurring lint findings | verifier | PR | n/a | existing |
| `WORKSPACE.md` rules and hooks | environment | every session | n/a | existing |
| `env-init` and the env fact store | environment | machine | n/a | existing |
| `kit-setup`: scaffolds the workspace | environment | machine | n/a | existing |
| `kit-health`: audits the kit on this machine | environment | machine | n/a | existing |
| knowledge lint (`kit-health lint`) | environment | context store | #626 | shipped (`0.13.0`) |
| `session-register`: the live registry | environment | session | n/a | existing |
| `session-handoff`: flushes the store, writes the next prompt | environment | session end | n/a | existing |
| `session-retro`: a session checks itself against the kit | environment | session end | #244 | existing |
| `memory-prune`: trims the auto-memory | environment | machine | #577 | existing |
| `public-text-check` (`kit_profile.py`): no environment value in public text | environment | ticket, PR text | n/a | existing |
| `gh-cli`: `gh` without the known traps | environment | machine | n/a | existing |
| `sign-queue`: commits the host must sign | environment | git | n/a | existing |
| `aws-sso-login`: cloud access by device code | environment | machine | n/a | existing |
| operator brief (`docs/operator-brief.md`): four plain lines | understanding | PR body, close, handoff | #623 | shipped (`0.13.0`) |
| `walkthrough`: leads you through a change, confirms by question | understanding | context doc | #625 | shipped (`0.13.0`) |
| cold reader: a haiku fork narrates the artefact cold | understanding | ticket, PR, handoff | #378 | existing |
| diagrams (`docs/diagrams.md`): a cold reader can draw the change | understanding | ticket, PR, handoff | #374 | existing |
| `diagram-plan.py`: the diagram set a PR needs | understanding | PR | #386 | existing |
| `length_check.py`: a reading-time budget | understanding | ticket, PR, handoff | #383 | existing |
| ledger: one line per answered decision | understanding | context doc | #379 | existing |
| assumption register: `Assumed:` lines | understanding | session, PR, handoff | #380 | existing |
| Simplified Technical English output style | understanding | every text | #542 | existing |
| explainability finding and WBD line | understanding | PR review | #387 | existing |
| `repo-docs`: a README that reads in one pass | understanding | README | n/a | existing |
| `cost-report`: spend against work shipped | understanding | machine | n/a | existing |
| `self-assessment`: your own week | understanding | week file | n/a | existing |
| `mission-control`: the store as one page | understanding | machine | n/a | existing |
| `slack-draft`: you send every message | understanding | chat | n/a | existing |
| `notion-page-review`: you approve every comment | understanding | Notion | n/a | existing |

The layers are not equally thick. Most rows record or gate. Few ask the person to act. The miss check closes the
last gap in the verifier layer: a fork that grades the work before its author signs it.

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

About 25 comparable projects fall into four camps; the last row is a practice, not a project. None combines the kit's pieces, and several do one piece better.

| Camp | Examples | What the kit borrows |
|---|---|---|
| Methodology and orchestration | [superpowers](https://github.com/obra/superpowers), [BMAD-METHOD](https://github.com/bmad-code-org/BMAD-METHOD), [SuperClaude](https://github.com/SuperClaude-Org/SuperClaude_Framework), [ruflo](https://github.com/ruvnet/ruflo) | The skills layer, re-grounding after compaction, a `doctor` check (our `kit-health`) |
| Spec pipelines | [spec-kit](https://github.com/github/spec-kit), [compound-engineering](https://github.com/EveryInc/compound-engineering-plugin), [FRAME](https://github.com/andrey-chmerev/the-frame-ai) | State files (our context docs), evidence lists, a checkpoint before compaction |
| Memory layers | [claude-mem](https://github.com/thedotmack/claude-mem), [Mem0](https://docs.mem0.ai/integrations/claude-code), [basic-memory](https://github.com/basicmachines-co/basic-memory), Anthropic's [memory tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool) | Progressive retrieval, hook-driven capture |
| Workflow, QA and cost | [claude-gh-monitor](https://github.com/kawaz/claude-gh-monitor), [claude-code-action](https://github.com/anthropics/claude-code-action), [skill-lint](https://github.com/himself65/skill-lint), [ccusage](https://github.com/ryoppippi/ccusage) | The ideas behind `pr-watch`, `kit-verify` and `cost-report` |
| Layered practice (a way of working, not a project) | Karpathy's talks and LLM-wiki gist, read through Marchese's spec / verifier / environment | The four layers: spec, verifier, environment, understanding (§ The four layers) |

The orchestration platforms in the first camp that run a server with a database and a control-plane UI stay out of
scope (see Non-goals). Their ideas, such as a hard budget stop or a lock per epic, can still be borrowed without
the server.
