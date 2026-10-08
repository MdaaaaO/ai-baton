# Workspace — shared Claude Code context (core)

The **environment-agnostic body** of the workspace `CLAUDE.md`. The root `CLAUDE.md` (personal) says
who the user is, then imports this file and the environment's prose (`.context/reference/environment.md`).
Everything below holds everywhere; environment differences live in the env store and `environment.md`.
Identity is `WORKSPACE_*` (`/plugin configure ai-baton`, else `.claude/settings.local.json`), never here.
Kit reference: `$BATON/README.md`.

## Layout

The workspace root holds the repos, `CLAUDE.md`, a `Makefile` and infra dirs `.claude/` (this kit),
`.context/` (the knowledge base, below), `.worktrees/` and `exports/`. **Nothing new at the root.**

## The `.context/` DB — find first, persist durable knowledge

Each `*.md` under `.context/` is a row; its frontmatter is the columns. Don't load it by default:

1. **Find first:** read `.context/INDEX.md` (the generated catalog) and open only the leaf docs the
   task needs. Narrow with `make -C $BATON/context-db find DOMAIN=<domain>` or `find TAG=<tag>`.
2. **Persist:** new doc `make -C $BATON/context-db new TYPE=<type> DOMAIN=<domain> SLUG=<slug>
   TITLE="…"` (types: `.context/README.md`). **Writes to `.context/` docs go through ctx tools** —
   `ctx_str_replace`, `ctx_insert`, `ctx_log`, `ctx_fm`, `ctx_create` (Bash: `$BATON/context-db/bin/ctx_adapter.py
   ctx <verb>`); `Write`/`Edit` is denied outside `sessions/`. Hooks validate, re-index and sync a
   git-versioned `.context/` (`$BATON/docs/context-sync.md`).
3. **Domains are folders:** core `reference/`, `repos/`, `pr-reviews/`, `meetings/`, `1on1/`,
   `self-assessment/`, `on-call/`, `archive/`; the environment adds its own (`domains` in the env
   config). Repo deep-dives are `.context/repos/<repo>.md` (links + TLDRs; the repo's own `CLAUDE.md`
   rules inside it). Spec: `.context/README.md`.

**Context doc** = the private living doc for one epic / tracking issue (not the tracker item, which
gets its own update). One per initiative, `TYPE=epic`, fixed sections *Tracker & links · Goal · What
was built (PRs per repo) · Key decisions & gotchas · Infra/secrets locations · Remaining work · Session
log* (dated one-liners, oldest first). Keep it lean — history to `.context/archive/<slug>-log.md`
(`verify` warns past 30 KB). It is the **handoff document**: a cold session picks it up alone, so
update it at every step.

## Environment facts — never hardcode, never guess

- **Values live in the env store** `.context/reference/env/`: `config.json` (structural switches —
  `tracker.*`, `github.*`, `systems.*`, `domains`, …) plus one `name · value · purpose · learned-from`
  table per system. Scripts read them (`kit_profile.py get <key>`, `kb.py get <system>.<kind> <name>`);
  prose cites keys. An environment-specific value never appears in a SKILL.md, agent, engine script
  or this file (`kit-health` scans for leaks).
- **A fact a skill needs** (an id…): `kb.py get` → missing → `kb.py discover <system>.<kind> <name>`
  prints the plan → run it, verify → only if no tool settles it, ask the user
  **once** → `kb.py set … --from tool:<name>|user|derived:<key>`.
  A forked worker returns `NEEDS <system>.<kind> <name>`; the main session resolves it (`env-init`).
- **`requires` is the only gate.** The kit never names an environment; a skill needing a system says
  `metadata.requires: "slack"` (a `systems.*` flag). A false flag means **not applicable here**: say so
  in one line and stop; never improvise a substitute.

Specs: `docs/env-facts.md`, `docs/new-environment.md`.

## Sessions — the live registry

- **Read `.context/SESSION_INDEX.md` at the start** of any session that touches a shared epic, PR or
  worktree; a `⚠ STALE` row (no heartbeat >12 h) may be dead — re-verify with `ListAgents` by **ref** (main session only).
- **Register** with `session-register` and keep the ≤12 h heartbeat (each session writes only
  its own `.context/sessions/<name>.md`). If another active session owns the epic/PR/worktree, agree
  ownership in one `SendMessage` before editing — a worktree isolates branch/HEAD, not directory access.
- **PR watches:** one multi-PR `pr-watch` Monitor per repo per session; every open PR has exactly one
  watcher. Watches die with their session — the successor re-arms them at startup.
- **Kit changes are PR-only.** The kit checkout (`.claude/` or a clone) stays on `main`;
  edit in `.worktrees/kit_<topic>` off `origin/main`, open a PR, the user merges (`CONTRIBUTING.md`).

## Skills

Only names/descriptions load each session; the body loads on invoke. **Never rely on a skill for an
always-on rule** — those are § Rules below (and in `environment.md`). Triggers:

- session start → `session-register` · a step lands / closing → `session-handoff`, then `session-retro`
- own PR → `pr-open`, then `pr-watch`; a watch line → `pr-event-brief`
- someone else's PR → `pr-scan` (queue) / `pr-review` (never the user's own)
- tracker step → `ticket-open` / `ticket-pickup` / `ticket-update` / `ticket-close`
- any non-trivial `gh` loop or count → `gh-cli` first · a README or CONTRIBUTING → `repo-docs`
- `NEEDS <fact>` or a new machine → `env-init` · plugin install → `kit-setup` · after a sync or kit change → `kit-health`
- signed-commits repos → `sign-queue` (why: `signed-git-commits`) · weekly review → `self-assessment` · spend → `cost-report`
- chat draft → `slack-draft` · docs-page comments → `notion-page-review` · `requires`-gated rest: `environment.md`

## Rules (always on)

**Communication**
- Open decisions go in one `AskUserQuestion` carousel (top option marked `(Recommended)`), never a
  prose question; no tool → a numbered `Decisions` block (`docs/carousel.md`).
- No AI attribution anywhere; own PRs/comments end with `kit_profile.py footer` only.
- Asks/status pings default to 1–3 plain sentences — link + one clause + who must act, no bold
  templates; point to existing detail, don't restate it. Save headers/bullets/tables for messages
  that bundle several items.
- Refer to work by its tracker key (`ABC-123`, `#123`), never a bare nickname; every tracker key and
  PR reference is a **clickable link on every surface** (`[KEY](url)`); only PR titles stay bare.
- Right-size to the reader: experts get the TLDR only; hold detail until asked.
- Don't pester: one ask in the right channel, no nudge DMs, batch per person, prefer removing the
  dependency over chasing it.
- Never cite local `.context/` or workspace paths on any external surface — link the ticket/PR or
  inline the evidence.
- Never print secret values (parameter-store, tokens, `settings.local.json`) into the transcript —
  compare in-shell, print only match/no-match.

**Workflow & scope**
- A misrouted prompt (another session's lane/repo/ticket/PR) → ask before reading, editing,
  relaying or drafting.
- Standing go: once a step is agreed and gated, start it and prep everything — stop only for
  signing, unplanned destructive prod actions, or genuine scope changes.
- Every PR a session opens carries labels — type + area (+ risk where it applies) from the repo's
  label set, in every repo; a PR without labels is not open (`pr-open` step 3).
- Every commit/PR title follows the repo's **commit style** — Conventional Commits
  (`type(scope): description`, tracker key inside, ≤72 chars) unless overridden;
  `commit_style.py resolve|check|title` decides; never infer from the log or add a marker to
  someone else's repo. Spec: `docs/commit-style.md`.
- A kit behaviour change bumps its `metadata.version` and `metadata.updated`.
- Never resolve a PR review thread waiting on a named third party — the open thread is the merge gate.

**Verification**
- Never assert infra/data-location/deploy/run state from a proxy signal — verify against the system
  of record, or label it unverified.
- Copying precedent code is not verification — check it against the documented spec or real data first.
- Never let a swallowed error read as a negative result — check exit status separately; never
  `2>/dev/null` a query whose emptiness is meaningful.

## Cost & context hygiene

Caching: a stable prefix re-reads each turn at ~0.1× input; editing `CLAUDE.md`, an import, skill
description or tool list invalidates it, re-billing every open session next turn —
**batch always-on edits into one PR**. Auto-compact backstop: a clone gets it from the kit's
`settings.json`; a plugin install doesn't (ships none) — set `/autocompact 200k` (cloud:
`CLAUDE_CODE_AUTO_COMPACT_WINDOW=200000`).

- **Load by task:** `INDEX.md` first, then only needed leaf docs. **Read slices** (`grep`,
  `Read` with offset+limit) and pipe big output to a count or scratch file.
- **Delegate by cost — reads and code.** The main model costs most: wide reads go to a
  subagent (keep its conclusion); a queue of ≥ 2 tickets goes to one background worker each
  (own worktree; pushes a branch, no PR; conflicts go back to it via `SendMessage`).
  **Set `model` on every `Agent` call** by size: Haiku to read/classify, Sonnet for a specified fix,
  Opus for judgment/security. The main model opens the draft PR, reviews it via `review-runner`'s ≤3K
  overview, not the diff, and owns every post, merge and flush. Brief: `docs/delegation.md`. Heartbeats:
  shell, no `/loop`.
- **Flush at every step.** When a ticket/PR/epic step lands: context doc (Session log + section), tick
  `.context/reference/priorities.md` where it exists; the `MEMORY.md` index if a memory note changed.
- **One scope per session.** Feedback+ticketing, a build/review round, or a release — flush and end
  at its boundary (a next-session prompt, not a carried prefix). Don't wake other sessions needlessly
  — coordinate through the registry and context doc.

## Repos

Environment-specific: `.context/reference/environment.md` § Repos and `.context/repos/README.md`.
