# Layout — what lives where

The kit is one directory, `$BATON`: `<workspace root>/.claude/` on a clone, Claude Code's plugin cache on a plugin
install (`docs/packaging.md` — both paths are supported). Everything personal and everything environment-specific
lives in the workspace's `.context/`, never in this repo. How the pieces call each other: `docs/architecture.md`.

**Content root.** The engine finds `.context/` at `CONTEXT_ROOT` when that variable is set, else beside the kit
(`.claude/`'s sibling — one level further up from a kit worktree such as `.worktrees/kit_<topic>/`), else
`CLAUDE_PROJECT_DIR`, else — a plugin install only — the nearest `.context/reference/env/config.json` above the
current directory. Full algorithm: `kit_profile.py`'s `context_root()` — the engine's only resolver: `kb.py`,
`session.py`, `gen_index.py`, `gen_sessions.py`, `verify.py`, `new.sh` (`kit_profile.py context`) and the Makefile's
`CONTEXT` all ask it and keep no fallback of their own. The test suite never uses it: `make test` and the test package
run on a throw-away store under the temp dir (`docs/contributing.md` § Testing). Every other mention of the default
(`context-db/Makefile`'s header, `docs/engine-cli.md`) links back to this paragraph rather than restating it.

> **Plugin install:** the tree below is the clone's. On a plugin install the kit is not in `<workspace root>/.claude/`
> (that directory holds only workspace settings such as your `settings.local.json`), the root `CLAUDE.md` has no `@.claude/WORKSPACE.md` line
> (the SessionStart hook injects `WORKSPACE.md`) and the root `Makefile` has no `workspace.mk` include; `.context/`,
> the repos and the rest of the workspace are the same.

## The workspace

```
<workspace root>/            # the dir Claude sessions run in (e.g. ~/Projects)
├── .claude/                 # ← this repo (shared kit) on a clone; only settings.local.json on a plugin install
├── .context/                # your knowledge base + personal state (NOT in this repo)
│   ├── README.md            #   the DB spec, seeded from context-db/context-README.template.md
│   ├── reference/env/       #   the env fact store: config.json + slack.md/tracker.md/github.md/… + _templates/ (kb.py)
│   ├── reference/environment.md  # this environment's prose: domains, capabilities, conventions, rules, repo map
│   ├── memory/              #   harness auto-memory (symlinked from ~/.claude/projects/<slug>/memory)
│   ├── state/pr-review/     #   pr-scan / pr-review config.json, ledger.jsonl, .submitted/
│   └── state/sign-queue/    #   the sign-queue's own runtime jobs + logs (skills/sign-queue/ ships the scripts only)
├── CLAUDE.md                # your preamble + `@.claude/WORKSPACE.md` (clone only) + `@.context/reference/environment.md` — NOT in this repo
├── Makefile                 # your targets + `include .claude/workspace.mk` (clone only; NOT in this repo)
└── <repo-a>/, <repo-b>/, …   # the repos this environment works on
```

## The kit's files

| Path | What it is |
|---|---|
| `WORKSPACE.md` | **The environment-agnostic body of the workspace `CLAUDE.md`** (the *core* layer, ≤ 10 KB). Loads into every session by import (clone) or hook (plugin) — details below. |
| `../.context/reference/env/` | **The env fact store** (not in this repo — `.context/` content): structural switches plus one markdown table per system, managed by `kb.py` — details below. |
| `../.context/reference/environment.md` | **This environment's prose** (not in this repo — `.context/` content, imported by the root `CLAUDE.md` as `@.context/reference/environment.md`): the domains, the capabilities this machine turns on (`systems.*`), conventions, always-on rules, and the repo map. Seeded by `setup.sh` from `environment-template/environment.md`. |
| `environment-template/` | Skeleton of a new environment: `config.json` (the env store's structural switches — every top-level key the store knows; `kb.py config` prints the live ones, `docs/env-facts.md` § The store explains them) and `environment.md` (the prose doc); `setup.sh` seeds both when they are missing and never again (`sh $BATON/setup.sh --refresh-seeds` shows what a later template change would add). See `docs/new-environment.md`. |
| `CLAUDE.md` | The kit's own memory file (≤ 600 B, `kit-verify` caps it): what this directory is, where the contributor and review rules live. On a clone Claude Code loads `.claude/CLAUDE.md` into a workspace session next to `WORKSPACE.md` (a plugin install has no such file, so it loads nowhere), and the review action restores it from the base branch in CI. |
| `CLAUDE.example.md` | Skeleton of the root `CLAUDE.md` (preamble + the two imports + personal additions); `setup.sh` seeds it when none exists. |
| `workspace.mk` | Shared Make targets for the root `Makefile` of a clone (`include .claude/workspace.mk`; a plugin install has no include): `sign*` (drain the sign queue on the host), `claude_sync` (fast-forward the kit), `kit_release` / `kit_release_dry` (a kit release PR, docs/contributing.md § Releases), `ctx_index` / `ctx_verify` / `ctx_find`. |
| `settings.json` | Project-level harness settings on a clone (compact window, subagent model, `BATON=.claude`, the `SessionEnd` sync hook, the ctx-store adapter's `SessionStart` brief, `PreToolUse` deny and `PostToolUse` validate/heartbeat/catalog hooks, and `enabledMcpjsonServers` approving the `ctx` server `setup.sh` adds to the workspace `.mcp.json`). Shared; not shipped by the plugin, whose `hooks/hooks.json` SessionStart hook exports `BATON` and injects `WORKSPACE.md` instead, and carries the same ctx-store hooks (`docs/troubleshooting.md` § 1). |
| `settings.local.example.json` | Template for the ignored `settings.local.json` (clone path): your identity as `env` (`WORKSPACE_USER`, `WORKSPACE_GITHUB_LOGIN`, `WORKSPACE_TZ`, `WORKSPACE_SLACK_SELF_DM`, `WORKSPACE_SLACK_LATTICE_DM`). On a plugin install the same five are `userConfig` options (`/plugin configure ai-baton`), which win over the file. |
| `setup.sh` | Idempotent first-run / post-recreate setup, safe to re-run — details below. |
| `sync.sh` | **PR-only for the kit:** installs the `hooks/` guards (`pre-push`, `commit-msg`), fast-forwards `main` to `origin/main` (never commits or pushes kit files; a dirty or ahead `.claude/` is an `error` in `.sync-status`, nothing destructive). Takes no arguments; nothing else is synced. See `docs/sync.md`. |
| `.claude-plugin/` | The kit **as a Claude Code plugin** (`plugin.json`, and `marketplace.json` making the repo its own marketplace): `claude plugin marketplace add <owner>/ai-baton && claude plugin install ai-baton@ai-baton-kit`. `plugin.json` `version` == `VERSION` (`kit-verify` checks; a release bumps both). Decision, layout map and what the plugin path cannot install: `docs/packaging.md`. |
| `hooks/pre-push` | Versioned git hook (`core.hooksPath`, set by `setup.sh` / `sync.sh`) that refuses any push to the kit's `main` — branch pushes pass; `KIT_ALLOW_MAIN_PUSH=1` is the deliberate one-off override. Defence in depth beside the repo's `main` ruleset (which admins bypass): it refuses before the push leaves the machine. |
| `hooks/commit-msg` | Versioned git hook (same `core.hooksPath`) that refuses a kit commit whose subject fails the repo's commit style — Conventional Commits by default (`context-db/bin/commit_style.py`; spec `docs/commit-style.md`). `KIT_SKIP_COMMIT_STYLE=1` is the deliberate one-off override. Other repos may point their `core.hooksPath` at this dir; the `sign-queue` enqueue and the `pr-open` checklist run the same check there. |
| `sync-check.sh` | Non-fatal: warns on stderr when the last sync errored, never finished (a stale `pending`) or has not reached origin for 3 days (`offline`), the kit is off `main`, ahead of `origin/main` (commits that will never be pushed), behind it (a PR merged), dirty, or missing the pre-push guard — and whenever it cannot tell: not a git checkout (outside a plugin install or a git-less `.claude/` copy), no `origin` remote, no `origin/main` ref, or a failed comparison (silence means in step). Runs at `session-register`; `make -C $BATON/context-db sync-check`. |
| `skills/` | Skills, one dir per skill (`SKILL.md` + scripts). Auto-discovered each session. |
| `evals/` | The eval suite `claude plugin eval` runs (`plugin.json` `experimental.evals`): one case per directory, `<skill>-<case>/` trigger suites (positives + near misses) and the reviewer's `kit-review-*` cases. Not loaded by a session; `evals/results/` is ignored. How to run and what CI checks: `evals/README.md`. |
| `agents/` | Subagent definitions, one file per agent — the forked workers skills run under; where they sit between skills, engine and context DB: `docs/architecture.md`. |
| `context-db/` | The engine (Makefile, `bin/`, `_templates/`) behind the `.context/` document DB, plus `context-README.template.md` (the DB spec, seeded into `../.context/README.md`) and `ctx-store/` (the store settings and type schemas `ctx_adapter.py adopt` writes into `../.context/` once). `make -C $BATON/context-db help` lists every target. Content lives in `../.context/`. CLI reference: `docs/engine-cli.md`. |
| `pr-review/` | Tooling README + `config.example.json` for `pr-scan` / `pr-review`; the live state is in `../.context/state/pr-review/`. |
| `docs/` | Kit-level docs, one file per topic — details below. |

## Notes

The four fattest cells above, unabridged:

- **`WORKSPACE.md`** — layout, the `.context/` DB, env facts, session registry, the skill trigger list, always-on
  rules, cost & context hygiene. `kit-verify` caps it at 10 KB (it loads into every session's prefix —
  `docs/loading.md`). It loads into every session by import or hook: on a clone the root `CLAUDE.md` imports it
  (`@.claude/WORKSPACE.md`) and it updates with `make claude_sync`; on a plugin install the SessionStart hook
  injects it (`kit_profile.py workspace-rules`) and it updates with `claude plugin update`. No names, logins,
  memory pointers — those stay in the preamble / `settings.local.json` — and nothing that differs between
  environments, which lives in the env store (values) and `.context/reference/environment.md` (prose).
- **`../.context/reference/env/`** — `config.json` with the structural switches scripts read (`tracker.kind`,
  `github.org`, `systems.*`, …) and one markdown table per system (`slack.md`, `tracker.md`, `github.md`, `aws.md`,
  `notion.md`) holding the ids and names a skill needs at run time — each row `name · value · purpose ·
  learned-from`. Managed by `context-db/bin/kb.py` (also `make -C $BATON/context-db kb ARGS="…"`), read by
  `kit_profile.py get <key>` — every subcommand and flag, including the scratch-directory helper: `docs/engine-cli.md`.
  Skills resolve a fact as `kb.py get` → discovery tool → ask once → `kb.py set`. `_templates/<type>.md` in the
  store override the engine's context-doc templates. Spec: `docs/env-facts.md`.
- **`setup.sh`** — Claude runs it from the install prompt (`README.md` § Install), you can re-run it any time. It
  creates `../.context/memory` + `state/` and symlinks the harness memory dir (an existing dir with notes is
  migrated with a verified copy; `--force` repoints a link that points elsewhere). It seeds the personal files
  (`settings.local.json`, the pr-review config) and a blank env fact store (`kb.py init --blank`; `--personal`
  fills it for a GitHub-only machine without a question — `README.md` § Install). It seeds the workspace files
  when absent — `.context/reference/environment.md`, the root `CLAUDE.md` and `Makefile`, `.context/README.md`,
  the self-assessment charter — and never overwrites one; `--refresh-seeds` prints the diff between each seeded
  copy and its template. It removes the kit's `__pycache__` dirs and empty leftover directories, and reports
  which `CLAUDE.md` import is missing.
- **`docs/`** — `delegation.md` (what to hand a subagent — reads and code — and which model, by size),
  `env-facts.md` (the env fact store — schema, `kb.py`, how a skill resolves a fact), `new-environment.md`
  (checklist for a new environment), `commit-style.md` (the commit-subject styles and how a repo's is resolved),
  `REVIEW.md` (the review rules — what CI decides, the four lenses, grading, the one-line finding shape; the CI
  reviewer reads it from the base branch), `packaging.md` (plugin + clone), `loading.md` (what loads when — the
  three layers, the byte budgets, how to measure, why a template change never reaches an existing machine),
  `architecture.md` (the one diagram: session → `CLAUDE.md` imports → skills / agents → engine → context DB ←
  hooks / sync), `authoring.md` (the checklist: every frontmatter key with its rule, body rules, bump, tests,
  evals, review gate), `engine-cli.md` (the engine's CLI reference, generated from `make help` + every tool's
  `--help` by `make -C $BATON/context-db engine-cli-doc`), `glossary.md` (the kit's terms), `contributing.md`
  (the full contributor reference — issue → PR → review → release, one section per topic), `CHANGELOG.md` (one
  line per skill/agent version bump). Every `SKILL.md`/agent carries `version`/`updated`/`reviewed` (+ optional
  `requires`, `facts`) as strings under `metadata:` — the Claude Code profile of the Agent Skills spec,
  `docs/contributing.md` § Skill frontmatter; `make -C $BATON/context-db kit-verify [STALE=90]` enforces it and checks
  this machine's env store is complete. Writing a skill: `docs/authoring.md` (the checklist), `docs/contributing.md`
  § Skills (the two tiers, the `NEEDS` return, layout, evals) and the scaffold `docs/templates/skill/` +
  `docs/templates/evals/`.
