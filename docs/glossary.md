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
  history goes to `archive/<slug>-log.md` and `verify` warns past 30 KB.
- **Discovery manifest** — `context-db/discovery/<system>.json`: for each fact the kit knows how to find, the tool
  that answers it, the verify clause, the `ttl_days` and the write target. `kb.py discover` prints a plan from it;
  `kit-verify` rejects a `metadata.facts` entry no manifest covers (`context-db/discovery/README.md`).
- **Engine** — `context-db/` (Makefile, `bin/`, `_templates/`, `discovery/`): the stdlib scripts behind the context
  DB, the env store, the session registry and the kit's own checks. Reference: `docs/engine-cli.md`.
- **Environment** — everything about *where* the kit runs: tracker, chat, org, which systems exist, extra domains,
  prose rules. One machine = one environment; it lives in the env store (values) and `environment.md` (prose),
  never in the kit (`docs/new-environment.md`).
- **Env store (env fact store)** — `.context/reference/env/`: `config.json` with the structural switches scripts
  branch on, plus one markdown table per system (`name · value · purpose · learned-from`) holding the ids and
  names skills need. Managed by `kb.py`; read by `kit_profile.py get`; spec `docs/env-facts.md`.
- **Epic** — the tracker's grouping item (an epic or a tracking issue) an initiative hangs on; the kit's context doc
  for it is `TYPE=epic` and the session registry's `EPIC=` names it.
- **Evidence (tier 1)** — `.review/evidence.md`, written by `review_evidence.py` for the CI reviewer: the changed
  units with their `requires` sets, the tier-0 verdict and the pre-flagged lines; the rules beside it are copied
  from the base branch.
- **Flush** — writing durable state to disk when a step lands: the context doc's Session log and section,
  `make -C .claude/context-db index`, `session-touch`, the memory index if a note changed. The guard against
  compaction drift (`WORKSPACE.md` § Cost & context hygiene).
- **Fork** — a skill with `context: fork` runs its body in a subagent under the agent its `agent:` key names
  (`triage`, `review-runner`, `auto-runner`); it returns a brief and never acts, so its reading stays out of the main
  prefix. A fork that hits a missing fact returns the NEEDS line.
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
- **Lane** — a continuing thread of work under one session registry `NAME`, spanning a chain of sessions: a
  predecessor ends leaving a next-session prompt (`session-handoff`), a successor registers under the same name and
  starts from it (`session-register`). Not one session's lifetime — the name's.
- **NEEDS return** — the exact line `NEEDS <system>.<kind> <name>` a forked worker returns when a fact it needs is
  missing from the env store; the main session resolves it (`env-init`) and re-runs.
- **Not applicable** — the one-line stop `<skill>: not applicable here — <why>` a capability-tier unit prints when
  a required flag is false. Never improvise around it.
- **`/plugin configure ai-baton`** — the Claude Code command that opens the plugin's `userConfig` dialog on a
  plugin install (`user_name`, `github_login`, `tz`, `slack_self_dm`, `slack_lattice_dm`); `/config` is Claude
  Code's own settings command (theme, model, …), a different command — never write that spelling for identity.
- **Provenance** — the `learned-from` cell of an env-store row: `<who> <YYYY-MM-DD>` with who = `tool:<name>`,
  `user`, `import:<env>` or `derived:<key>` (`kb.py set --from`). `kb.py stale` measures the date against the
  manifest's `ttl_days`; a `user` row is never stale.
- **Review gate (tier 0)** — `review_gate.py`, run by `ci.yml` and `make -C .claude/context-db review-gate`: leak
  and PII shapes on every added line, and a version / `updated` bump for every changed unit. Tiers 1–3
  are the evidence file, the Claude review and the `evals/kit-review-*` cases (`docs/REVIEW.md`).
- **Session registry** — `.context/sessions/<name>.md`, one file per live session (what it owns, a ≤ 12 h heartbeat,
  a `stats:` line), aggregated into `SESSION_INDEX.md` by `gen_sessions.py`; managed by `session.py` and the
  `session-register` / `session-handoff` skills.
- **Stamp** — `.context/kit-health/HEALTH-<env>.md`, written by `kit-health --stamp` after a run with no errors:
  the kit commit and version this environment was last green at.
- **Ticket** — one tracker item (a Jira issue or a GitHub issue, per `tracker.kind`), always cited by its key as a
  link. `ticket-open` / `ticket-update` / `ticket-close` act on it; `tracker.key_regex` recognises it.
- **Unit** — one skill (`skills/<name>/SKILL.md`) or one agent (`agents/<name>.md`): the thing `kit-verify`
  validates and `review_gate.py` bumps by version.
- **Wording-only** — a PR that changes text but no behaviour: no version bump, declared with the `wording` label or
  `[skip-bump]` in the title or body (`review_gate.py --skip-bump`); the reviewer may question the claim.
- **Workspace root** — the directory Claude Code sessions run in: the repos, the root `CLAUDE.md` and `Makefile`,
  `.claude/` (the kit) and `.context/` (the DB). Every kit path is relative to it.
