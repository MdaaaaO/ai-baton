# Workspace — shared Claude Code context (core)

The **environment-agnostic body** of the workspace `CLAUDE.md`. The root `CLAUDE.md` (personal) says
who the user is, then imports this file and the environment's prose (`.context/reference/environment.md`).
Everything below holds everywhere; whatever differs between environments — tracker, chat, ids, repo
map, capabilities — lives in the env store (values) and `environment.md` (prose), local to the machine.
Identity is `WORKSPACE_*` (plugin `/config`, else `.claude/settings.local.json`), never here.
Kit reference: `$BATON/README.md`; `docs/contributing.md` § Versioning.

## Layout

The workspace root holds the repos, `CLAUDE.md`, a `Makefile` and the infra dirs `.claude/` (this kit,
its own git repo), `.context/` (the knowledge base, below), `.worktrees/` and `exports/` (bulk data).
**Nothing new at the root** — durable knowledge goes under `.context/`, raw data under `exports/`.

## The `.context/` DB — find first, persist durable knowledge

Each `*.md` under `.context/` is a row; its frontmatter is the columns. Don't load it by default:

1. **Find first:** read `.context/INDEX.md` (the generated catalog) and open only the leaf docs the
   task needs. Narrow with `make -C $BATON/context-db find DOMAIN=<domain>` or `find TAG=<tag>`.
2. **Persist:** `make -C $BATON/context-db new TYPE=<type> DOMAIN=<domain> SLUG=<slug> TITLE="…"`
   (types: epic, reference, repo, meeting, 1on1, oncall, self-assessment, pr-review, log), edit the
   scaffold, then `make -C $BATON/context-db index` (also after any hand edit); `make -C
   $BATON/context-db verify` is the schema/freshness gate.
3. **Domains are folders:** core `reference/`, `repos/`, `pr-reviews/`, `meetings/`, `1on1/`,
   `self-assessment/`, `on-call/`, `archive/`; the environment adds its own (`domains` in the env
   config). Repo deep-dives are `.context/repos/<repo>.md` (links + TLDRs; the repo's own `CLAUDE.md`
   rules inside it). Spec: `.context/README.md`.

**Context doc** = the private living doc for one epic / tracking issue (not the tracker item, which
gets its own update). One per initiative, `TYPE=epic`, fixed sections *Tracker & links · Goal · What
was built (PRs per repo) · Key decisions & gotchas · Infra/secrets locations · Remaining work · Session
log* (dated one-liners, newest first). Keep it the lean current state — history goes to
`.context/archive/<slug>-log.md`; `verify` warns past 30 KB. It is the **handoff document**: a cold
session must pick up from it alone, so it is updated at every step, not only at the end.

## Environment facts — never hardcode, never guess

- **Values live in the env store** `.context/reference/env/`: `config.json` (structural switches —
  `tracker.*`, `github.*`, `systems.*`, `domains`, …) plus one `name · value · purpose · learned-from`
  table per system. Scripts read them (`kit_profile.py get <key>`, `kb.py get <system>.<kind> <name>`);
  prose cites keys. A value that differs between environments never appears in a SKILL.md, agent,
  engine script or this file (`kit-health` scans for leaks).
- **A fact a skill needs** (channel id, field id, account id…): `kb.py get` → missing → `kb.py discover
  <system>.<kind> <name>` prints the discovery plan (tool, verify clause) → run it and verify → only if
  no tool settles it, ask the user **once** → `kb.py set … --from tool:<name>|user|derived:<key>`.
  A forked worker returns `NEEDS <system>.<kind> <name>`; the main session resolves it (`env-init`).
- **`requires` is the only gate.** The kit never names an environment; a skill that needs a system
  says `metadata.requires: "slack"` (a `systems.*` flag). A false flag means **not applicable here**: say
  so in one line and stop; never improvise a substitute.

Specs: `docs/env-facts.md`, `docs/new-environment.md`.

## Sessions — the live registry

- **Read `.context/SESSION_INDEX.md` at the start** of any session that touches a shared epic, PR or
  worktree; a `⚠ STALE` row (no heartbeat >12 h) may be dead — re-verify with `ListAgents` by **ref** (main session only).
- **Register** with the `session-register` skill and keep the ≤12 h heartbeat (each session writes only
  its own `.context/sessions/<name>.md`). If another active session owns the epic/PR/worktree, agree
  ownership in one `SendMessage` before editing — a worktree isolates branch/HEAD, not directory access.
- **PR watches:** one multi-PR `pr-watch` Monitor per repo per session; every open PR has exactly one
  watcher across sessions. Watches die with their session — the successor re-arms them at startup.
- **Kit changes are PR-only.** The kit checkout (`.claude/`, or a clone on a plugin install) stays on `main`;
  edit in `.worktrees/kit_<topic>` off `origin/main`, open a PR, let the user merge (`CONTRIBUTING.md`). `.context/` content is local, never synced.

## Skills

Only names/descriptions load each session; the body loads on invoke. **Never rely on a skill for an
always-on rule** — those are § Rules below (and in `environment.md`). Triggers:

- session start → `session-register` · a step lands / closing → `session-handoff`
- own PR → `pr-open`, then `pr-watch`; a watch line → `pr-event-brief`
- someone else's PR → `pr-scan` (queue) / `pr-review` (never for the user's own PRs)
- tracker step → `ticket-open` / `ticket-update` / `ticket-close`
- any non-trivial `gh` loop or count → `gh-cli` first
- `NEEDS <fact>` or a new machine → `env-init` · after a sync or kit change → `kit-health`
- signed-commits repos → `sign-queue` · weekly review → `self-assessment` · spend → `cost-report`

## Rules (always on)

**Communication**
- Never append a "Sent using Claude"/AI-attribution footer to anything sent on the user's behalf.
- Asks/status pings default to 1–3 plain sentences — link + one clause + who must act, no bold
  field-label templates; point to where detail already lives, never restate it. Reserve
  headers/bullets/tables for messages that genuinely bundle several items.
- Refer to work by its tracker key (`ABC-123`, `#123`), never a bare feature nickname; every tracker
  key and PR reference is a **clickable link on every surface** (`[KEY](url)`); only PR titles stay bare.
- Right-size to the reader: senior/expert readers get the TLDR only; hold detail until asked.
- Don't pester people: one ask in the right channel, no nudge DMs, batch everything for one person,
  prefer removing the dependency over chasing it.
- Never cite local `.context/` or workspace paths on any external surface — link the ticket/PR or
  inline the evidence.
- Never print secret values (parameter-store values, tokens, `settings.local.json` values) into the transcript —
  compare in-shell, print only match/no-match.

**Workflow & scope**
- A misrouted prompt (another session's lane/repo/ticket/PR) → ask "handle here or ignore?" before
  reading, editing, relaying or drafting anything.
- Standing go: once a step is on the agreed order and its gates are met, start it and prep everything —
  stop only for signing, unplanned destructive prod actions, or genuine scope changes.
- Every PR a session opens carries labels — type + area (+ risk when it applies) from the repo's own
  label set, in every repo; a PR without labels is not open (`pr-open` step 3).
- Every commit and PR title follows the repo's **commit style** — Conventional Commits
  (`type(scope): description`, tracker key inside, ≤72 chars) unless the repo overrides it;
  `commit_style.py resolve|check|title` decides; never infer it from the log, never add a marker to
  someone else's repo. Spec: `docs/commit-style.md`.
- A kit behaviour change bumps its `metadata.version` and adds a `docs/CHANGELOG.md` line.
- Never resolve a PR review thread waiting on a named third party — the open thread is the merge gate.

**Verification**
- Never assert infra/data-location/deploy/run state from a proxy signal — verify against the system of
  record, or label it explicitly unverified.
- Copying precedent code is not verification — check it against the documented spec or real data
  before trusting or extending it.
- Never let a swallowed error read as a negative result — check exit status separately from the
  value; never `2>/dev/null` a query whose emptiness is meaningful.

## Cost & context hygiene

Caching: a stable prefix re-reads each turn at ~0.1× input (writes 1.25×, 5-min TTL; 2×, 1 h). The
cliff is invalidation (tools → system → messages): editing `CLAUDE.md`, an import, a skill description
or the tool list re-bills every open session in full next turn — **batch always-on edits into one PR**.
Position matters too: long contexts recall their middle worst.

- **Load by task:** `INDEX.md` first, then only the leaf docs needed. **Read slices** (`grep`,
  `Read` with offset+limit) and pipe big output to a count or scratch file.
- **Delegate by cost — reads and code.** The main model costs most: wide reads go to a
  subagent (keep its conclusion); a queue of ≥ 2 independent tickets goes to one background worker each
  (own worktree; pushes a branch, no PR). **Set `model` on every `Agent` call** by size: Haiku to
  read/classify, Sonnet for a specified fix, Opus for judgment/security. The main model reviews
  each diff and keeps every PR, post, merge and flush. Brief: `docs/delegation.md`. Heartbeats: shell, no `/loop`.
- **Flush at every step.** When a ticket/PR/epic step lands: context doc (Session log + section), tick
  `.context/reference/priorities.md` where it exists, `make -C $BATON/context-db index`, then
  `session-touch NAME=<name>`; the `MEMORY.md` index if a memory note changed.
- **Scope → flush → end.** Work a ticket/PR, flush, end the session. Don't wake other sessions
  needlessly — coordinate through the registry and context doc.

## Repos

Environment-specific: `.context/reference/environment.md` § Repos and `.context/repos/README.md`.
