# evals/ — the eval suite, one case per directory, per skill

Cases live **outside** `skills/<name>/` so the installed skill stays lean (docs/contributing.md § Skills). The format is
the one `claude plugin eval` runs: `evals/<skill>-<case>/prompt.md` (frontmatter `max_turns`, `timeout_seconds`,
`allowed_tools`, `tags`; the body is what the user says) plus `graders/*.md` (frontmatter `type` — `llm`, `tool_used`,
`regex`, … — and `weight`; an `llm` grader's body is the pass criterion). `claude plugin eval init --bare
<skill>-<case>` writes a blank case; the plugin manifest names this directory (`"experimental": {"evals": "evals"}`)
and the runner writes `evals/results/` (not committed).

**Trigger suites.** Every **new or changed** skill ships ≥ 10 trigger cases, at least three positives and three
**same-domain near misses** (`EVAL_MIN_CASES` / `EVAL_MIN_EACH` lower the bar locally while a new suite is built up; CI uses the defaults) (the description's "Not for …" clause, or a neighbouring skill's own trigger, as a case that must not fire).
Most existing skills predate the suite and have none yet — `eval_check.py` notes that rather than failing the PR,
until `REQUIRE_ALL=1` flips it to a gate (below).
A trigger case is tagged `trigger` plus `positive` or `near-miss` and carries two graders: `fired.md`, a
`tool_used` check on the `Skill` call naming the skill — its `input_match` must match the skill and not a longer name that
contains it, e.g. `'(?<![\w-])<skill>(?![\w-])'` (`min: 1` for a positive, `min: 0` + `max: 0` for a near miss,
`arm: both` so it is scored), and `criteria.md`, an `llm` grader on the outcome. `docs/templates/evals/` holds one of
each — copy here, rename to `<skill>-<case>`, fill the `<…>` marks (the scaffold sits under `docs/` so the runner never
scores a placeholder). No case carries a real id, host, person or organisation — placeholders only (`acme/widgets`,
`ABC-123`, `#42`). A case that needs a file in place before the agent starts (a diff to react to, not a tool it has
to invent) adds a `case.yaml` next to its `prompt.md` — `schema_version` plus `context: {scaffold_script: fixture.sh}`
— and `fixture.sh` writes that file; the runner merges the two, and `fixture.sh` only runs under `--scaffold`
(default on for `make eval`, see below) since it is author-supplied bash run as the invoking user. Every skill now has a trigger suite; `kit-review-*` are the reviewer's
proof cases (`docs/REVIEW.md`), and `<skill>-behaviour-<case>` are behaviour cases (below) — neither is a
trigger suite.

**Behaviour cases.** Tagged `behaviour` instead of `trigger` (so `eval_check.py` counts them as "other
cases", never against a trigger suite's minimums) with a single `criteria.md` grader, same shape as
`kit-review-*`: no `fired.md`, since the point isn't whether the skill fires but what it does once it has.
Three kinds live here: (1) the six gated skills (`metadata.requires` set) each get one case whose prompt
states the required `systems.*` flag is false and asks for the skill's work anyway — the pass criterion is
the skill's own one-line stop (quoted from its SKILL.md), never an improvised substitute; (2) the four
create/merge/sign paths whose gates are easy to get wrong under pressure — `pr-watch` merging only once
`pr-merge.sh`'s gates actually hold (a case with an open thread or a pending check must not merge),
`pr-review` never posting before the user has walked and approved the findings (even when asked to skip
the walk, a STOP finding still forces it), `sign-queue` enqueuing a job instead of hand-rolling or
pasting `git commit`/`git push` for the user, and `ticket-carousel` never creating a ticket before its
own walk ends, even when told to just file them; (3) `pr-review`'s Explainability gate
(`reference/scope.md` § Explainability's own gate, docs/diagrams.md) — an ordinary small fix with no
diagram in the body must raise no Explainability finding, since that class fires only on a `--deep`
review or an Interface/contract change; (4) the cold-reader brief (`docs/cold-reader.md`) — there is no
skill to fire, so the case feeds the brief's own prompt an artefact body and checks its one-line output:
a body that answers all three questions gets three lines back, a body that names files but never the
plug-in point gets `UNSURE <question>` instead of a guess; these two cases (filed under `pr-open-*` since
that is where the brief's call site will live) exercise the brief itself, which `pr-open` adopts in a
follow-up; (5) `pr-event-brief`'s `DIAGRAMS:` line — a
drifted plan on a real `HEAD MOVED` event reads `DRIFT` and picks `REDRAW`, and a stale-review event
(marked `(on older head <sha>)`, never a `HEAD MOVED` line) carries no `DIAGRAMS:` line at all.

**MCP mocks.** `claude plugin eval` starts no MCP server the plugin ships unless `evals/mocks/<server>/` stands in for it;
without one the server's tools are absent from every case and the runner warns `has no mock and is NOT started`.
`mocks/ctx/` answers the `ctx` server: one `<tool>.md` per tool (an empty-store read or an acknowledged write) and
`_tools.json`, the server's saved `tools/list` answer, so the model sees the real tool schemas. A mocked tool is
allowed automatically — never add it to a case's `allowed_tools`. When the pinned ctx-store version changes its
tool set, regenerate `_tools.json` and add or drop the matching `<tool>.md`; `eval_check.py` fails a manifest server
with no mock and a `_tools.json` that disagrees with the files beside it. The file carries the ctx-store version it was
saved from (`ctx_version`), and `eval_check.py` fails when that differs from `CTX_VERSION` in `ctx_adapter.py` — regenerate
the file whenever `CTX_VERSION` is bumped.

| run | what | tokens |
|---|---|---|
| `make -C $BATON/context-db eval-check` | every case loads (prompt keys, grader types), every trigger suite has ≥ 10 cases with both kinds; skills without a suite and descriptions with no recognized trigger phrase (`TRIGGER_WHEN` / `TRIGGER_OTHER`) are notes on a bare run, but `ci.yml` runs `REQUIRE_ALL=1`, so both are a failure there. Part of `make ci` and `ci.yml` | none |
| `make -C $BATON/context-db eval SKILL=<skill> [MODEL=<id>] [RUNS=<n>] [JUDGE=<model>] [JOBS=<n>] [ALLOW_TOOLS=Bash] [JSON_DIR=<dir>]` | `claude plugin eval . --ablation none --judge-model sonnet --case '<skill>-*' --no-publish` — the trigger rate on your machine. `JUDGE` is the model the `llm` graders vote with (default `sonnet`; the CLI's smaller default judge fails criteria with several conditions on answers that meet them); `MODEL` pins the agent model (default `sonnet` — the CLI's own default model takes more turns on some cases than `max_turns` allows; set `MODEL=` empty for the CLI's default); `JOBS` runs that many agent runs at once; `SCAFFOLD` runs each case's `scaffold_script` (default on — set `SCAFFOLD=` empty to skip, same as `--no-scaffold`); `JSON_DIR`, if set, also writes the run's full result JSON there (`--json`). `ALLOW_TOOLS` (default `Bash`) is the operator grant on this one invocation; the CLI hands each case the intersection of it and that case's own `allowed_tools`, so a case that does not list `Bash` itself never gets it, no matter what `ALLOW_TOOLS` names — one invocation already grants `Bash` per case, never as a blanket | yes |
| `evals` workflow (Actions → evals → Run workflow; inputs `skill`, `models`, `judge_model`, `runs`) | the same run on a hosted runner with the `CLAUDE_CODE_OAUTH_TOKEN` secret, four agent runs at once, one pass per model, pinned to `sonnet` unless `models` names others; the output is the job summary, and each model's full result JSON is also uploaded as a workflow artifact. Run it per skill: with `skill` empty it runs every case, which takes hours. Models and runs multiply the time; a combination that cannot fit the job's time limit is refused before it spends anything. `claude plugin eval` keeps the credential out of a case's own `Bash` itself (token by file descriptor, allow-listed environment, a bwrap sandbox per tool call), and the workflow probes bwrap before spending; do not add `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB` on top, it nests a second sandbox that breaks every case's `Bash` | yes |

A change to a skill's `description:` cites a green run of that skill's suite (the workflow or `make eval`) in its PR:
the suite is the acceptance test for shortening or rewording a trigger.

## Coverage

One row per skill: its trigger suite size (positive+near-miss) and its behaviour cases, if any (61 total,
counting every `evals/*behaviour*` directory: the six gated skills' one-line stop, the four create/merge/sign
paths, `pr-review`'s Explainability small-fix gate, the cold-reader brief's two cases, the `pr-event-brief`
diagrams-line pair, `ticket-update`'s Pivot-restates-not-delta near miss, `session-handoff`'s first-handoff
case, and the assumption-register near miss — a session proceeds on an unstated assumption and the PR body
carries no `Assumed:` line (§ above), and `pr-open`'s done-when near miss — the PR verifies a non-criterion and leaves a `DW-<n>` criterion with no `## Verified` line and no `## One honest limit` line (§ above), and `walkthrough`'s unconfirmed-plain-words near miss and second-miss-parks case, and the knowledge lint's pair (`docs/knowledge-lint.md`): a fixture store with one finding of each mechanical kind is reported exactly, and a request to skip the question round never lets a fix land before the carousel has answered, and the goal interview's three — `ticket-open` drafting a sonnet-sized ticket without asking the four questions, `ticket-open` correctly writing `Interview: SKIP` on a haiku-sized one, and `ticket-carousel` drafting without interviewing (`docs/carousel.md` § Goal interview), and `pr-open`'s operator-brief near miss — the `## For the operator` block restates the diff ("renamed X to Y") instead of what the operator can do, must do differently and checks, and the miss check's three cases (`docs/miss-check.md`): the brief names a done-when item with no evidence, answers `CLEAN` on a complete body, and a second miss goes to the carousel instead of a third fork, and `swarm-run`'s ten tick cases (assigns within the cap, nothing before the plan's go, a stall re-briefed then parked, a protected path left to the owner, an author-only code owner, an incident lane, a headless `Decisions` block, a small diff's `sonnet` review, a path the repo's auto-merge does not reserve kept in draft, the epic close comment and the digest carry an operator block that passes its brief), and `swarm-inbox`'s three (a scope-adding human comment becomes a delta and a bot comment is dropped, the chat tier stops in one line without chat, a pending draft is read back) and `swarm-status`'s three (a digest not yet due posts nothing, the chat draft stops without chat, the digest keeps its marker), and `epic-discover`'s five (the first round asks the four core questions of the epic, a headless run stops at its `Decisions` block and creates nothing, a migration epic is offered the data-migration template, a code epic is not, a human gate's signature is refused as a grant), and `epic-decompose`'s four (two children whose areas overlap in one wave are refused with the overlap named, a four-child epic splits into two waves with caps inside the epic budget, a re-plan that adds a child writes a dated delta and needs a second go, a headless run stops at its `Decisions` block and creates nothing). Generated by hand against the `evals/` tree; re-check it whenever a
suite or a behaviour case is added or renamed.

| skill | trigger suite (pos+near) | behaviour cases |
|---|---|---|
| `alerts-sweep` | 5+5 | `alerts-sweep-behaviour-not-applicable` |
| `aws-sso-login` | 5+5 | `aws-sso-login-behaviour-not-applicable` |
| `cost-report` | 5+5 | — |
| `dbt-sqlfluff-fixes` | 5+5 | `dbt-sqlfluff-fixes-behaviour-not-applicable` |
| `env-init` | 5+5 | — |
| `epic-decompose` | 5+5 | `epic-decompose-behaviour-overlap-refused`, `epic-decompose-behaviour-four-into-two-waves`, `epic-decompose-behaviour-replan-second-go`, `epic-decompose-behaviour-headless-decisions` |
| `epic-discover` | 5+5 | `epic-discover-behaviour-first-round`, `epic-discover-behaviour-headless-decisions`, `epic-discover-behaviour-migration-template`, `epic-discover-behaviour-code-epic-no-migration`, `epic-discover-behaviour-grant-not-a-gate` |
| `gh-cli` | 5+5 | — |
| `kit-health` | 5+5 | `kit-health-behaviour-lint-fixture-store`, `kit-health-behaviour-lint-no-apply-before-carousel` |
| `kit-setup` | 5+5 | — |
| `memory-prune` | 6+6 | — |
| `mission-control` | 5+5 | — |
| `notion-page-review` | 5+5 | `notion-page-review-behaviour-not-applicable` |
| `pr-event-brief` | 5+5 | `pr-event-brief-behaviour-head-moved-redraw`, `pr-event-brief-behaviour-stale-review-no-diagrams` |
| `pr-open` | 6+6 | `pr-open-behaviour-cold-reader-ok`, `pr-open-behaviour-cold-reader-unsure`, `pr-open-behaviour-missing-assumed-line`, `pr-open-behaviour-missing-gap-line`, `pr-open-behaviour-operator-brief-restates-diff`, `pr-open-behaviour-miss-check-names-unverified`, `pr-open-behaviour-miss-check-clean`, `pr-open-behaviour-miss-check-second-miss-carousel` |
| `pr-review` | 5+5 | `pr-review-behaviour-no-post-before-approval`, `pr-review-behaviour-no-explainability-small-fix` |
| `pr-scan` | 5+5 | — |
| `pr-watch` | 5+5 | `pr-watch-behaviour-no-merge-when-gate-open` |
| `repo-docs` | 5+5 | — |
| `self-assessment` | 5+5 | — |
| `session-handoff` | 7+6 | `session-handoff-behaviour-first-handoff` |
| `session-register` | 5+5 | `session-register-behaviour-narrate-back-first`, `session-register-behaviour-no-work-before-narrate-back`, `session-register-behaviour-resume-reconfirms`, `session-register-behaviour-successor-rebriefs` |
| `session-retro` | 5+5 | — |
| `sign-queue` | 8+4 | `sign-queue-behaviour-enqueue-not-paste`, `sign-queue-behaviour-not-applicable` |
| `slack-draft` | 5+5 | `slack-draft-behaviour-not-applicable` |
| `swarm-inbox` | 5+5 | `swarm-inbox-behaviour-delta-and-bot`, `swarm-inbox-behaviour-chat-off`, `swarm-inbox-behaviour-draft-readback` |
| `swarm-run` | 5+5 | `swarm-run-behaviour-assign-within-cap`, `swarm-run-behaviour-no-assign-before-go`, `swarm-run-behaviour-stall-rebrief-then-park`, `swarm-run-behaviour-protected-path-waits`, `swarm-run-behaviour-author-only-owner`, `swarm-run-behaviour-incident-own-lane`, `swarm-run-behaviour-headless-decisions`, `swarm-run-behaviour-small-diff-sonnet-review`, `swarm-run-behaviour-repo-gate-reserves-path`, `swarm-run-behaviour-operator-block` |
| `swarm-status` | 5+5 | `swarm-status-behaviour-not-due`, `swarm-status-behaviour-slack-off`, `swarm-status-behaviour-keep-marker` |
| `ticket-carousel` | 5+5 | `ticket-carousel-behaviour-no-create-before-walk-ends`, `ticket-carousel-behaviour-interview-before-draft` |
| `ticket-close` | 5+5 | — |
| `ticket-open` | 6+5 | `ticket-open-behaviour-sonnet-no-interview`, `ticket-open-behaviour-haiku-skip` |
| `ticket-pickup` | 6+6 | — |
| `ticket-update` | 6+6 | `ticket-update-behaviour-pivot-no-marker-no-redraw`, `ticket-update-behaviour-pivot-restates-not-delta` |
| `walkthrough` | 6+6 | `walkthrough-behaviour-no-unconfirmed-plain-words`, `walkthrough-behaviour-second-miss-parks-step`, `walkthrough-behaviour-epic-plan` |

Plus `kit-review-*` (8 cases, `docs/REVIEW.md` proof cases — not tied to one skill).

Plus the swarm's two agents, whose cases are named `<agent>-<case>` and tagged `behaviour` (so they count in neither a
trigger suite nor the behaviour total above):

| agent | cases | of which near misses | graded against |
|---|---|---|---|
| `builder` | 14 | 6 | `agents/builder.md` |
| `verifier` | 10 | 3 | `agents/verifier.md` |

**Framing an agent case.** Inside a `claude plugin eval` run the model cannot search the plugin, so a prompt that only says
"write what the builder returns" never reaches `agents/builder.md` and grades 0 (measured before the framing: `builder-stop-reports-ran`
0/2, `verifier-head-moved-fail` 0/3, `builder-rebrief-ack-alone` 0/3; dispatching `ai-baton:builder` through the `Agent` tool
was 0/2 too). The model can read its own working directory, so every `builder-*` and `verifier-*` case carries a
`case.yaml` (`context: {scaffold_script: fixture.sh}`) whose `fixture.sh` runs `cp "$(dirname "$0")/../../agents/<role>.md"
./<role>.md` in the scratch workspace, and its prompt ends with "You are the <role> on this ticket. ./<role>.md is your contract: read it first, then answer as the <role>, bound by it." (the bare pointer "The <role>'s contract is in ./<role>.md" gave the contract but not the role: `builder-near-miss-force-push` answered as a general assistant and graded 0/3, with the role line 3/3)
The file is copied from the plugin under test at run time, so no copy of the contract is checked into `evals/` to drift,
and `allowed_tools: [Read, Glob, Grep]` is enough (run with `ALLOW_TOOLS=`). The judge cannot read the plugin either, so an
agent case's `criteria.md` states the pass line in full instead of leaning on a citation. `eval-check` enforces the framing
(`scaffold_script` named, a fixture that copies `agents/<role>.md`, a prompt pointing at `./<role>.md`) for every case tagged
`behaviour` plus `builder` or `verifier`. Live grades after the change (local, `RUNS=3` sweep of all 24, then `RUNS=6` on each case a fix touched): 21 of 24 cases
pass at least 5 of 6, or 3 of 3. Three stay under the bar because the model's answer is off the contract, not because the
framing fails: `builder-near-miss-prose-return` (4/6, adds prose after the `READY` line), `verifier-gates-not-applicable`
(4/6, words the FAIL tail its own way), `builder-owner-choice-decision` (2/3, omits the `DECISION` line the lease rule asks
for) . A case below the bar is a finding on `agents/*.md` or on its
criteria, not on the framing; do not loosen its criteria to make it pass.
