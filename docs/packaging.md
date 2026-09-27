# Packaging — a plugin at the root, the clone path kept (#118)

**Decision (2026-09-26): both.** The repository root is a Claude Code plugin — `.claude-plugin/plugin.json` +
`.claude-plugin/marketplace.json`, so the kit is marketplace-installable, `claude plugin validate` / `eval` run
against it, and `userConfig` can carry identity (#116) — **and** the clone-into-`.claude/` path stays, because the
things that make it a *workspace* kit are conventions a plugin cannot install: the `.context/` DB beside the
workspace, the root `CLAUDE.md` imports, the root `Makefile` include, the git hooks and the fast-forward sync.

**Both install paths are supported**, and every page that says how the kit loads, where it lives or how it updates
covers both: the kit is `$BATON` (§ Kit root), `WORKSPACE.md` arrives by import (clone) or hook (plugin) — § Installing
— and each path has its own update flow (§ Updating).

## Layout map

| in the repo | plugin path (`claude plugin install`) | clone path (`git clone … .claude`) |
|---|---|---|
| `skills/<name>/` | plugin skills, invoked as `/ai-baton:<name>` (or `/<name>` when unambiguous) | discovered from `.claude/skills/` |
| `agents/*.md` | plugin agents | discovered from `.claude/agents/` |
| `evals/` (arrives with #115; cases per #98) | the default eval dir — `claude plugin eval .`; no manifest key until cases exist | same files, same command |
| `context-db/` (engine, discovery manifests, templates) | ships inside the plugin root; scripts find the kit from their own location | `.claude/context-db/` |
| `hooks/` (`pre-push`, `commit-msg`) | **git** hooks, not Claude Code hooks — not a plugin component | installed by `setup.sh` / `sync.sh` (`core.hooksPath`) |
| `settings.json` (`SessionEnd` → `sync.sh`) | not shipped: the plugin update **is** the sync | the kit's project settings |
| `setup.sh`, `sync.sh`, `workspace.mk`, `CLAUDE.example.md`, `environment-template/` | not plugin components; run from a clone | the workspace bootstrap |
| `.context/` (DB, env store, memory, state) | **never in the plugin** — project data, found through `CLAUDE_PROJECT_DIR/.context` | beside `.claude/` |
| `README.md`, `WORKSPACE.md`, `docs/`, `CONTRIBUTING.md` | carried, not loaded | the same |

**Env store location: one place on both paths.** `.context/reference/env/` next to the workspace — it is *project*
data (one machine = one environment = one workspace root), not plugin data, so `${CLAUDE_PLUGIN_DATA}` is not used
for it. `kit_profile.context_root()` resolves `CONTEXT_ROOT`, then the `.context/` beside the kit (clone path, also
from a worktree), then `CLAUDE_PROJECT_DIR/.context` (plugin path; the SessionStart hook exports it for Bash), then —
on a plugin install only, when neither variable is set — the nearest `.context/reference/env/config.json` above the
current directory, stopping below `$HOME` (#3).

**Nothing stateful under the plugin root.** `${CLAUDE_PLUGIN_ROOT}` changes on every update; state is `.context/`
(or `${CLAUDE_PLUGIN_DATA}` for plugin-private caches, none today). Skills cross-reference each other by name
(docs/contributing.md § Skills).

## Installing

- **Clone path** (the kit as a git checkout in `<workspace root>/.claude/`, which `setup.sh` wires up in one go):
  `README.md` § Install — one command for a GitHub-only machine, the prompt in `docs/new-environment.md` for a
  corporate one. Choose it to contribute to the kit or to follow `main` merge by merge.
- **Plugin path** (the skills and agents, from the repository as its own marketplace):
  ```sh
  claude plugin marketplace add <owner>/ai-baton          # the repo is the marketplace
  claude plugin install ai-baton@ai-baton-kit    # the plugin entry it lists
  ```
  Then, **from the workspace root**, run `sh <plugin root>/setup.sh` once to scaffold `.context/`, the root `CLAUDE.md`
  and `Makefile` — the conventions the plugin cannot install. Run from a plugin install (§ Install mode) the script takes `CLAUDE_PROJECT_DIR`, else the current directory, as the workspace root, and seeds the
  ignored `settings.local.json` in `<root>/.claude/` (the directory Claude Code reads project settings from) — nothing
  is written below the plugin cache; `PROJECTS=/path` overrides. Skill and agent bodies reach the kit as `$BATON/…` (§ Kit root below), so the engine-backed skills work from
  a plugin install too.

  **Always-on rules on the plugin path (#3).** A plugin cannot ship a CLAUDE.md, and a plugin install puts no kit
  file in `<root>/.claude/`. So the SessionStart hook prints `WORKSPACE.md` (`kit_profile.py workspace-rules`), and
  Claude Code adds hook stdout to the session's context on startup, resume, `/clear` and compaction. It prints only
  when `CLAUDE_PROJECT_DIR` is, or sits below, a dir holding an env store (the plugin is per user, so other projects
  get nothing) that has no `.claude/WORKSPACE.md` of its own (a clone imports that, so nothing loads twice). On this path `setup.sh` seeds a
  `CLAUDE.md` without the `@.claude/WORKSPACE.md` import and no `Makefile` include (`workspace.mk` drives a clone).
  A leftover import or include is flagged by `setup.sh` and is an error in `kit-health` § 4 (it checks the targets,
  not just the lines). The engine finds `.context/` through the `CLAUDE_PROJECT_DIR` the same hook exports (the
  Bash tool isn't handed it), else by walking up from the current directory.

## Install mode (#34)

One rule, `kit_profile.install_mode()` (`kit_profile.py install-mode` for `setup.sh`), names how the kit runs; every
mode-dependent step follows it, and `kit_profile.MODES` is the one table of what differs:

| mode | what it is | workspace root (`setup.sh`) | WORKSPACE.md | root Makefile | update to a new release |
|---|---|---|---|---|---|
| `clone` | the workspace's `.claude/` — a git checkout, or a copy without git (sync.sh skips it) | the parent of `.claude/` | `@.claude/WORKSPACE.md` import | `include .claude/workspace.mk` | `make claude_sync` |
| `plugin` | no git checkout, `.claude-plugin/plugin.json` present — Claude Code's plugin cache | `CLAUDE_PROJECT_DIR`, else the cwd | the SessionStart hook | no include | `claude plugin marketplace update … && claude plugin update …` |
| `dev-checkout` | any other kit copy — a git checkout not named `.claude` (a `.worktrees/kit_<topic>` worktree, a `claude --plugin-dir` checkout) | `CLAUDE_PROJECT_DIR`, else the cwd | the SessionStart hook | no include | `git -C <checkout> pull --ff-only` on its own branch |

`setup.sh` records the mode in the env store as `kit.install_mode`; a dev checkout run beside the workspace's
`.claude/` clone leaves `clone` recorded (the clone is the installed kit). `kit-health` § 1 prints the mode and
warns when it is not recorded or differs from the recorded one — a machine that switched from a clone to a plugin
and still has the `.claude/` clone, the import or the include; re-running `setup.sh` records the new mode. The
`HEALTH-<env>.md` stamp records `install_mode`. Hints `setup.sh` prints name the kit as `.claude` on a clone and by
its own path otherwise.

## Updating

| | clone | plugin |
|---|---|---|
| moves with | every merge to `main` | every release — Claude Code caches a plugin by its `version`, which only a release bumps (§ Version discipline) |
| how | `make claude_sync` (or `sh .claude/sync.sh`), and the `SessionEnd` hook in `settings.json` runs it in the background; a fast-forward only (`docs/sync.md`) | `claude plugin marketplace update ai-baton-kit` (re-read the marketplace), then `claude plugin update ai-baton@ai-baton-kit`, then restart Claude Code — skills, agents and the SessionStart hook load at startup |
| learning there is one | `sync-check.sh` at `session-register` warns when the kit is behind `origin/main` | the repository's GitHub Releases (the `release` workflow publishes one per tag); `/kit-health` warns when a newer release is out and prints the commands above |
| afterwards | `/kit-health` when the PR says an environment needs a step | `/kit-health`: it re-stamps `HEALTH-<env>.md` with the new `kit_version`, and § 6 lists the units changed since the last stamp |

A hand edit under the plugin cache is lost on the next update (`kit-health` § 1 warns about one); a kit change is a
PR from a checkout on either path (`CONTRIBUTING.md`).

## Kit root — `$BATON` (#3)

Unit bodies and `WORKSPACE.md` never spell the kit's location: they write `$BATON/context-db/bin/…`,
`$BATON/skills/<name>/…`. On a clone `settings.json` sets `BATON=.claude` (relative to the workspace root, where
sessions run); on a plugin install the SessionStart hook exports `BATON=${CLAUDE_PLUGIN_ROOT}` via `$CLAUDE_ENV_FILE`
(`kit_profile.py session-env`). `kit-verify` fails a unit or `WORKSPACE.md` that writes a kit path as `.claude/…`;
the workspace's own `.claude/` (settings.local.json, the sign-queue state), `~/.claude/` and a repo's
`<repo>/.claude/commit-style` are not kit paths. Scripts find the kit relative to themselves, never through `$BATON`.

## Identity (#116)

The user's own values — name, GitHub login, timezone, optional chat DM ids — are the one input the kit needs per
person. Two sources, one reader:

| path | source | how it reaches a script |
|---|---|---|
| plugin | `plugin.json` `userConfig` (`user_name`, `github_login`, `tz`, `slack_self_dm`, `slack_lattice_dm`), asked for by `/plugin configure ai-baton` or `claude plugin install --config key=value`; the chat ids are `sensitive` (masked, secure storage) | Claude Code hands them to hooks as `CLAUDE_PLUGIN_OPTION_<KEY>`; the `hooks/hooks.json` SessionStart hook runs `kit_profile.py session-env`, which appends `export WORKSPACE_*=…` (and `CLAUDE_PROJECT_DIR`, which the Bash tool is not handed, #3) to `$CLAUDE_ENV_FILE`, so every later Bash command sees them — from the *next* session on: a `/plugin configure` mid-session reaches Bash after a restart |
| clone | the `env` block of the ignored `.claude/settings.local.json` (`settings.local.example.json`) | merged into every Bash, hook and subagent environment by Claude Code |

`kit_profile.identity(var)` reads the option first, then the `WORKSPACE_*` variable, so a value typed into `/config`
wins over a stale file; `kit_profile.py identity-source <var>` says which source answered, never the value.
Scripts and skill bodies keep reading `$WORKSPACE_*`. `kit-verify` fails when `userConfig` and
`kit_profile.IDENTITY_KEYS` drift or the hook is missing; `kit-health` scans the option values for leaks like the
file's, and § 4 names the source of each key. Secrets belong in neither place (docs/contributing.md § Secrets); environment
facts (channel ids, tracker site, org) stay in the env store, which keeps discovery, TTL and provenance —
`userConfig` never replaces it.

## Version discipline

`plugin.json` `version` == `VERSION` == the latest `CHANGELOG.md` release section. `kit-verify` fails when the two
files disagree (every machine, no `claude` CLI needed); `.conventional-release.toml` lists both in `version-files`,
so a release PR bumps them together. `make -C .claude/context-db plugin-validate` (part of `make ci` and
`ci.yml`) runs `claude plugin validate --strict` on the manifests, the skills and the agents where the CLI exists.

## Name

The project is **ai-baton** (styled bAIton on the front page): the plugin `ai-baton`, the marketplace `ai-baton-kit`,
the repository `ai-baton` (#100). The name carries no "Claude" — the kit is *for* Claude Code, not by Anthropic —
and nothing reserved or impersonating is used. A future rename is a one-line change here plus a `renames` entry in
`marketplace.json`.

## Open items (follow-ups, not this decision)

- `install.sh` for agents without a marketplace (symlink `skills/` into `~/.claude/skills`) — the clone path already
  serves them (`~/.claude/skills` shadows `.claude/skills`, so a symlink there is a choice, not a need).
- Skipped on purpose (amendment on #118): a second agent's manifest pair, a toolchain submodule, a package tap —
  nothing here ships a binary.
