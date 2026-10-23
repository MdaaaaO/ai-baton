# Glossary — the kit's terms

One or two sentences each, alphabetical. Where a term is enforced by code, the file is named.

- **Always-on** — the files in every session's prefix from the first turn: the root `CLAUDE.md` and its two
  imports (`WORKSPACE.md`, `.context/reference/environment.md`), every unit's `name` + `description`, the memory
  index. Budgeted by `kit_verify.py` (`ALWAYS_ON_BUDGET`, `DESC_TOTAL_BYTES`); `docs/loading.md`.
- **BATON** — the environment variable naming the kit root, so a skill body's engine calls (`$BATON/context-db/bin/<tool>`)
  work unchanged whether the kit is a `.claude/` clone or a plugin install: a clone's `settings.json` sets it to
  `.claude`, a plugin's SessionStart hook exports it from `CLAUDE_PLUGIN_ROOT` (`kit_profile.py session-env`).
- **Capability flag** — a boolean `systems.<name>` in the env store's `config.json` (`kb.SYSTEMS`: `jira`, `slack`,
  `notion`, `datalake`, `airflow`, `dbt`, `aws_sso`, `incident_io`, `lattice`, `signed_commits`) saying whether this
  machine has that system. A unit's `metadata.requires` names the flags it needs; a false flag means *not
  applicable here*.
- **Context DB** — the `.context/` folder beside the kit: every `*.md` under it is a row whose frontmatter is the
  columns, `INDEX.md` the generated catalog. Local to the machine, never synced; spec `.context/README.md`, engine
  `context-db/`.
- **Context doc** — the private living doc for one initiative (`TYPE=epic`), one per tracker epic or tracking
  issue, with fixed sections and a dated Session log. It is the handoff document a cold session resumes from;
  history goes to `archive/<slug>-log.md` and `verify` warns past 30 KB (64 KB for `type: reference`/`repo`).
- **Discovery manifest** — `context-db/discovery/<system>.json`: for each fact the kit knows how to find, the tool
  that answers it, the verify clause, the `ttl_days` and the write target. `kb.py discover` prints a plan from it;
  `kit-verify` rejects a `metadata.facts` entry no manifest covers (`context-db/discovery/README.md`).
- **Engine** — `context-db/` (Makefile, `bin/`, `_templates/`, `discovery/`): the stdlib scripts behind the context
  DB, the env store, the session registry and the kit's own checks. Reference: `docs/engine-cli.md`.
- **Environment** — everything about *where* the kit runs: tracker, chat, org, which systems exist, extra domains,
  prose rules. One machine = one environment; it lives in the env store (values) and `environment.md` (prose),
  never in the kit (`docs/new-environment.md`). As a layer, it is the third of the four: what the agent works in must stay true
  (`docs/vision.md` § The four layers).
- **Env store (env fact store)** — `.context/reference/env/`: `config.json` with the structural switches scripts
  branch on, plus one markdown table per system (`name · value · purpose · learned-from`) holding the ids and
  names skills need. Managed by `kb.py`; read by `kit_profile.py get`; spec `docs/env-facts.md`.
- **Env var** — a process environment variable a kit script reads (`os.environ`/`${NAME:-…}`), distinct from
  an env-store fact or config key: one reference table, default, reader and purpose per name, `docs/env-vars.md`.
- **Epic** — the tracker's grouping item (an epic or a tracking issue) an initiative hangs on; the kit's context doc
  for it is `TYPE=epic` and the session registry's `EPIC=` names it.
- **Evidence (tier 1)** — `.review/evidence.md`, written by `review_evidence.py` for the CI reviewer: the changed
  units with their `requires` sets, the tier-0 verdict and the pre-flagged lines; the rules beside it are copied
  from the base branch.
- **Flush** — writing durable state to disk when a step lands: the context doc's Session log and section
  (through the ctx tools; the hooks re-index and touch the registry row), the memory index if a note changed. The guard against
  compaction drift (`WORKSPACE.md` § Cost & context hygiene).
- **Fork** — a skill with `context: fork` runs its body in a subagent under the agent its `agent:` key names
  (`triage`, `review-runner`, `auto-runner`); it returns a brief and never acts, so its reading stays out of the main
  prefix. A fork that hits a missing fact returns the NEEDS line.
- **Goal interview** — four questions the session asks the author in one carousel before `ticket-open` or
  `ticket-carousel` writes the opening block: the goal, what is out of scope, how we will know it is done, and the
  first slice that can be verified on its own. The answers become the block. A small, specified fix skips it with
  `Interview: SKIP (<reason>)`; `docs/carousel.md` § Goal interview.
- **Handoff artefact** — what a skill leaves for a step only a specific machine can perform (sign a commit, reach
  a host): a queued job, a `commit.sh` — never a pasted one-liner or a silent substitute.
- **Heartbeat** — the session registry row's liveness signal: `skills/session-register/heartbeat.sh` touches
  `.context/sessions/<name>.md` (and refreshes its `stats:` line) every interval while the owning Claude process is
  alive, and marks the row `ended` the moment it is gone, so `SESSION_INDEX.md` never lies about who is still live.
- **Install mode** — how the kit is installed on this machine: `clone` (a git checkout at `<workspace root>/.claude/`),
  `plugin` (Claude Code's plugin cache, no git checkout) or `dev-checkout` (any other kit copy — a worktree, a
  `--plugin-dir` checkout). `kit_profile.py install-mode` detects it; `setup.sh` records it as `kit.install_mode`;
  `kit-health` warns when the two disagree (`kit_profile.MODES`).
- **Kit** — this repository, installed as `<workspace root>/.claude/` (clone path) or as the `ai-baton`
  plugin: skills, agents, engine, settings, docs. Machine- and user-agnostic by construction; `main` is PR-only.
- **Knowledge lint** — `/kit-health lint`: a check that the context store still says what is true. A script finds
  orphan docs, dead links and tracker claims the tracker contradicts. A few forks read what only a reader can
  judge. The session walks every finding in one carousel and applies only the accepted ones. It reads
  current-state sections only, never history; `docs/knowledge-lint.md`.
- **Lane** — a continuing thread of work under one session registry `NAME`, spanning a chain of sessions: a
  predecessor ends leaving a next-session prompt (`session-handoff`), a successor registers under the same name and
  starts from it (`session-register`). Not one session's lifetime — the name's.
- **Miss check** — a fork (`docs/miss-check.md`) that asks "what did you miss?" of the artefact, the ticket's `## Done when`
  and a diff summary, before a PR is marked ready, a ticket is closed or a handoff is written. It returns `MISSED`
  lines or `CLEAN`. The model that did the work never grades it.
- **NEEDS return** — the exact line `NEEDS <system>.<kind> <name>` a forked worker returns when a fact it needs is
  missing from the env store; the main session resolves it (`env-init`) and re-runs.
- **Not applicable** — the one-line stop `<skill>: not applicable here — <why>` a capability-tier unit prints when
  a required flag is false. Never improvise around it.
- **Operator brief** — the four plain lines that end every landed change, for the person who runs the result:
  `Can now`, `Do differently`, `Check with`, `Not covered`. Each line is at most 20 words. It is `## For the
  operator` in the PR body, copied into the close comment and the next-session prompt; `docs/operator-brief.md`.
- **`/plugin configure ai-baton`** — the Claude Code command that opens the plugin's `userConfig` dialog on a
  plugin install (`user_name`, `github_login`, `tz`, `slack_self_dm`, `slack_lattice_dm`); `/config` is Claude
  Code's own settings command (theme, model, …), a different command — never write that spelling for identity.
- **Provenance** — the `learned-from` cell of an env-store row: `<who> <YYYY-MM-DD>` with who = `tool:<name>`,
  `user`, `import:<env>` or `derived:<key>` (`kb.py set --from`). `kb.py stale` measures the date against the
  manifest's `ttl_days`; a `user` row is never stale.
- **Review gate (tier 0)** — `review_gate.py`, run by `ci.yml` and `make -C $BATON/context-db review-gate`: leak
  and PII shapes on every added line, and a version / `updated` bump for every changed unit. Tiers 1–3
  are the evidence file, the Claude review and the `evals/kit-review-*` cases (`docs/REVIEW.md`).
- **Session registry** — `.context/sessions/<name>.md`, one file per live session (what it owns, a ≤ 12 h heartbeat,
  a `stats:` line), aggregated into `SESSION_INDEX.md` by `gen_sessions.py`; managed by `session.py` and the
  `session-register` / `session-handoff` skills.
- **Spec** — the first layer: what the person wants and how they will know it is done. The person owns it; the
  agent does not guess it. The kit's mechanisms are the goal interview, the Goal/Plan block and the Sizing line
  (`docs/vision.md` § The four layers).
- **Stamp** — `.context/kit-health/HEALTH-<env>.md`, written by `kit-health --stamp` after a run with no errors:
  the kit commit and version this environment was last green at.
- **Ticket** — one tracker item (a Jira issue or a GitHub issue, per `tracker.kind`), always cited by its key as a
  link. `ticket-open` / `ticket-update` / `ticket-close` act on it; `tracker.key_regex` recognises it.
- **Understanding** — the fourth layer and the kit's keystone: the person knows what changed and why. A person cannot
  delegate it. The operator brief, the walkthrough and the diagrams exist for it (`docs/vision.md` § The four layers).
- **Unit** — one skill (`skills/<name>/SKILL.md`) or one agent (`agents/<name>.md`): the thing `kit-verify`
  validates and `review_gate.py` bumps by version.
- **Verifier** — the second layer: the checks that say whether the result is right, set before the work and read
  after it. The kit's mechanisms are the `## Done when` criteria with their `DW-n` ids, `evidence_check.py`,
  `## Verified` and the review gates (`docs/vision.md` § The four layers).
- **Walkthrough** — the `walkthrough` skill: it leads the operator through a PR, a repo area or an epic in three
  steps (purpose, how it runs, where it breaks), then asks three questions in one carousel. A wrong answer
  re-opens the step. Only what the operator confirms is kept, as `## In plain words` in the context doc.
- **Wording-only** — a PR that changes text but no behaviour: no version bump, declared with the `wording` label or
  `[skip-bump]` in the title or body (`review_gate.py --skip-bump`); the reviewer may question the claim. `ci.yml`
  grants the same `--skip-bump` a third way, computed rather than declared: a Dependabot npm PR whose diff touches
  only a unit's `package.json` / `package-lock.json`.
- **Workspace root** — the directory Claude Code sessions run in: the repos, the root `CLAUDE.md` and `Makefile`,
  `.claude/` (the kit) and `.context/` (the DB). Every kit path is relative to it.
