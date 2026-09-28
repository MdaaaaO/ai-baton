# A new environment — the kit on another machine

An environment is everything about *where* the kit runs: which tracker, which chat, which GitHub org,
which systems exist, which extra `.context/` domains and doc templates, and the prose rules that only
make sense there. One machine = one environment. The kit (`WORKSPACE.md`, the engine, every
skill) never changes for a new environment; everything specific lives in `.context/` on that
machine — the **env fact store** `.context/reference/env/` (values, `kb.py`, see `env-facts.md`) and
**`.context/reference/environment.md`** (prose, imported by the root `CLAUDE.md`). There is no second
repo (§ History).

## Two paths

- **Quick start — a GitHub-only machine** (no tracker but GitHub issues, no chat, no warehouse): one command, no
  questions — `README.md` § Install. `setup.sh --personal` writes the store with every `systems.*` false, GitHub issues
  as the tracker, the clones under the workspace root as the tracked repos, the OS timezone and your `gh` identity
  (`context-db/bin/personal.py`, also `kb.py init --personal`). Facts a skill needs later arrive through discovery
  (`/env-init`, `NEEDS <fact>`), never through a form. Everything below is the full path; the quick start is the same
  machine with steps 2–4 done for you.
- **Full path — a machine with a tracker, chat or warehouse:** the install prompt below, then the checklist.
- **Plugin install** instead of a clone: [`plugin-setup.md`](plugin-setup.md), then the checklist from step 2.

## Install prompt — the full path (paste into Claude Code)

Nobody runs shell scripts by hand. The kit repo is public, so cloning it needs no access step; have `gh auth login`
(or an SSH key) working for your own team repos, start **Claude Code in your workspace root** (the directory
that holds your team repos, e.g. `~/Projects`) and paste this:

```
Install the Claude Code workspace kit into this directory (my workspace root).

1. Clone https://github.com/MdaaaaO/ai-baton.git as `.claude/` here. If a `.claude/`
   already exists and is not a clone of that repo, move it to `.claude.bak-<today>` first and
   tell me what it contained.
2. Read `.claude/README.md`, `.claude/WORKSPACE.md` and `.claude/docs/env-facts.md`. Then ask me,
   in one go: my name, GitHub login, IANA timezone, (optional) Slack self-DM id and Lattice DM id,
   and a short lowercase name for this environment (e.g. the company). Copy
   `.claude/settings.local.example.json` to `.claude/settings.local.json` and put the identity
   values into its `env` block (`WORKSPACE_*` keys). (On a plugin install skip the file: the same values
   are the plugin's `userConfig`, asked for by `/plugin configure ai-baton`.)
3. Run `sh .claude/setup.sh` from this directory — it creates a blank env fact store
   `.context/reference/env/`, seeds `.context/reference/environment.md`, `.context/`, the memory
   symlink and the workspace files — and show me what it created and what it left alone. Set the
   environment name (`kb.py config-set environment <name>`) and put my GitHub login into
   `.context/state/pr-review/config.json` (it seeds that file). Ask me for the structural switches
   (`tracker.kind`, `github.org`, which `systems.*` exist, `tz_default`, the `domains`) and set them
   with `kb.py config-set`; ids and names arrive later as skills ask for them (`kb.py set`).
4. Write my preamble at the top of the root `CLAUDE.md`: who I am and how to address me. Keep the
   two import lines `@.claude/WORKSPACE.md` and `@.context/reference/environment.md`. If I already
   had a root `CLAUDE.md` or `Makefile`, add the import lines / the `include .claude/workspace.mk`
   line to my files instead of replacing them. Fill `.context/reference/environment.md` with what
   is specific to this place (domains, conventions, rules, repo map) as we learn it.
5. Verify: `make -C .claude/context-db kit-verify index verify` passes, `make -n sign_list` resolves,
   `git -C .claude check-ignore settings.local.json` says it is ignored, and
   `~/.claude/projects/<slug>/memory` is a symlink to `.context/memory`.
6. Tell me to restart Claude Code: the root `CLAUDE.md` and its imports load at session start,
   so the conventions are live from the next session on. In that first session run `/kit-health`
   — it proves the wiring on this machine and stamps this environment as green — then `/env-init`
   to fill the env fact store from the discovery tools this machine has.
```

After the restart the first session should know the `.context/` DB commands and the skills table without being told
(`/memory` lists the root `CLAUDE.md` with `.claude/WORKSPACE.md` as its import). Register with `session-register` and
the workspace is yours. On a sandbox recreate (which wipes `~/.claude` but keeps the mounted workspace) just say "run
`.claude/setup.sh`" — it is idempotent and only re-creates the memory symlink and whatever is missing.

<details>
<summary>What the prompt does (the manual equivalent)</summary>

HTTPS, matching `README.md` § Install: a sandbox's credential proxy injects a token for HTTPS only, never for
SSH, so a clone command in these docs is always HTTPS. SSH is fine on your own machine, once, if you already
have a working SSH key for GitHub — never inside a sandbox session.

```
git clone https://github.com/MdaaaaO/ai-baton.git <workspace root>/.claude
cp .claude/settings.local.example.json .claude/settings.local.json
$EDITOR .claude/settings.local.json      # WORKSPACE_* identity keys (plugin install: /plugin configure ai-baton instead)
sh .claude/setup.sh                      # from the workspace root — env store, memory link, seeds
python3 .claude/context-db/bin/kb.py config-set environment acme    # then tracker.kind, github.org, systems.*, tz_default, domains
$EDITOR .context/state/pr-review/config.json   # set "login"
$EDITOR CLAUDE.md                        # the preamble: who you are
```

`setup.sh` is idempotent: run it without a settings file and it creates one from the example; it
creates a blank env store and seeds `.context/reference/environment.md` and the root `CLAUDE.md`
from the kit templates when they are missing. It also seeds a root `Makefile` (just
`include .claude/workspace.mk`) and `.context/README.md`; existing files are reported and never
edited — if you already have a root `CLAUDE.md` or `Makefile`, add the two import lines and the
`include` line yourself (`setup.sh` names the missing one).
</details>

## Checklist

1. **Install the kit** (the prompt above) and run `sh .claude/setup.sh` — it creates a blank store
   (`kb.py init --blank`) and seeds `.context/reference/environment.md`, the root `CLAUDE.md` (with both
   imports) and `Makefile` when they are missing. (`--personal` fills the store for a GitHub-only machine
   instead — § Two paths.)
2. **Name it:** `kb.py config-set environment <name>` (lowercase slug — the company, the client, `home`).
   `kit_profile.py name` must print it; `kit-health` warns while it is empty.
3. **`config.json` switches** — `.claude/environment-template/config.json` lists every key; set them
   with `kb.py config-set <dotted.key> <json-or-string>`:
   - `tracker`: `kind` = `jira` | `github` | `none` (the kinds the `ticket-*` skills have adapters for);
     `key_regex` with exactly one capture group; `url_template`; Jira adds site/project/sprint
     fields/transitions/`mcp_tools`, GitHub adds `repos` + `close_reasons`.
   - `github`: `org`, `review_bot` (empty = none), `bots`, `signed_commits`, `owner_teams`,
     `display_names` (login → first name, used by `pr-scan`), `sandbox_token_prefix`.
   - `slack`: `enabled` + `domain`; disabled = the Slack steps of every skill are skipped.
   - `labels` (per-repo label names for `pr-open`), `diagrams` (per-repo diagram overlays),
     `self_assessment` (`sources`, `report`, `scope`, `ledger`, `ledger_url`, `report_url`, `sections` — the
     report block's headings, empty = the kit's own four), `systems` (the capability flags — `jira`,
     `slack`, `notion`, `datalake`, `airflow`, `dbt`, `aws_sso`, `incident_io`, `lattice`, `signed_commits`;
     a skill with `metadata.requires: "x"` is "not applicable" wherever `systems.x` is false), `tz_default`.
   - `domains`: the extra `.context/` domain folders (core domains need no entry).
4. **Facts** (ids, logins, hosts): run **`/env-init`** once the `systems.*` flags are set — it takes the
   inventory (`kb.py discover --all`), runs the discovery tool each manifest names for every applicable
   missing fact, verifies, writes back with provenance, and asks you once, batched, for what no tool can
   settle. Anything it leaves unset arrives later the same way as skills need it (`kb.py get` →
   `kb.py discover` → tool → ask once → `kb.py set`). Nothing to pre-fill by hand.
5. **`_templates/`** — optional per-type doc scaffolds in the store (`epic.md`, `oncall.md`, …); the
   engine falls back to `context-db/_templates/`.
6. **`.context/reference/environment.md`** — fill the skeleton: environment paragraph, domains, the
   capabilities this machine turns on (`systems.*`), how the skills behave here,
   conventions, the always-on rules that are yours, the repo map. Cite config keys, not values; it is
   a normal `.context/` reference doc (`make -C .claude/context-db index verify` covers it).
7. **Per-user review config:** `.context/state/pr-review/config.json` — `owner` = your `github.org`,
   `sweep_repos` inside it, `login` = you.
8. **Verify:** `make -C .claude/context-db kit-verify` (env store complete), then `/kit-health` in a
   session — it lists the skills that are not applicable here (a `requires:` flag you set false —
   `kb.py migrate --off` prints the same list at any time, both from `kb.unmet_units`), the
   leaks, and the machine wiring; fix until GREEN/AMBER, then it stamps
   `.context/kit-health/HEALTH-<name>.md`.
9. **Skills that need an adapter.** `ticket-open/update/close` adapt to `tracker.kind` — the enum is
   `kit_profile.TRACKER_KINDS` (`jira` | `github` | `none`; `kit-verify` rejects any other value); a
   third tracker means adding its kind there together with a new `## Adapter — <kind>` section in those
   three skills. A skill
   whose `requires:` names a capability this machine lacks is inert here; a skill for a system the kit
   does not cover yet is a new kit skill gated on a new `systems.*` flag (added to `kb.py` `SYSTEMS`,
   reaching every store via `kb.py migrate`) — never a skill bound to one environment. Any kit-side change
   goes out as a PR from a worktree branch — the kit's `main` is PR-only and `sync.sh` only
   fast-forwards `.claude/`.

## History

Until 2026-09-25 an environment's config and prose lived in a second private repo cloned at
`.claude/profiles/<name>/`. That layer was retired the next day in favour of the env store and
`.context/reference/environment.md`; the migration command (`kb.py import <dir>`) and every other code path
that knew about it were removed on 2026-09-26, once every machine had moved. `kit-health` keeps one
line: a leftover `.claude/profiles/` directory is a warning to delete it. A row a migration wrote still
carries `import:<name> <date>` as its provenance and reads like any dated row.

## What must never move into the environment doc

The engine, the session registry, the cost/flush rules, the versioning/kit-health discipline —
anything that is true for every environment. If you find yourself copying a core rule into
`environment.md`, it either belongs in `WORKSPACE.md` (every environment) or it is an environment rule
and should be stated only once, there.
