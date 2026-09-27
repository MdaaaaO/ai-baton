# Plugin setup — from `claude plugin install` to a green `/kit-health`

The plugin path, step by step. The clone path has its own walkthrough, [`new-environment.md`](new-environment.md);
[`packaging.md`](packaging.md) explains how the two differ and why. Everything here happens on one machine, in one
**workspace root**: the directory that holds your repos (e.g. `~/Projects`), never your home directory.

You need [Claude Code](https://claude.com/claude-code), `python3` and `make`. `gh` logged in is optional: with it,
step 3 fills your identity and your repos without a question.

## Steps

1. **Install the plugin** (any directory):

   ```sh
   claude plugin marketplace add MdaaaaO/ai-baton && claude plugin install ai-baton@ai-baton-kit
   ```

   The repository is its own marketplace (`ai-baton-kit`); the plugin is `ai-baton`. Claude Code keeps it in its
   plugin cache, whose path changes on every update — nothing of yours is ever stored there.

2. **Set your identity** (optional when `gh` is logged in). In a Claude Code session run `/plugin configure ai-baton`,
   or pass `--config key=value` to the install command. The keys are the plugin's `userConfig`:

   | key | what | |
   |---|---|---|
   | `user_name` | your name | required |
   | `github_login` | your GitHub login | required |
   | `tz` | your IANA zone, `Region/City` | default `UTC` |
   | `slack_self_dm`, `slack_lattice_dm` | your chat DM channel ids | optional, stored as `sensitive` |

   Without this step, step 3's `--personal` writes the same values (from `gh` and the OS zone) into
   `<root>/.claude/settings.local.json`. When both are set, the plugin option wins. Tokens go in neither place
   (`contributing.md` § Secrets).

3. **Scaffold the workspace** — from the workspace root:

   ```sh
   cd ~/Projects      # your workspace root: the directory that holds your repos
   sh "$(claude plugin list --json | python3 -c 'import json,sys; d=json.load(sys.stdin); d=d if isinstance(d,list) else [dict(e,id=k) for k,v in d.get("plugins",{}).items() for e in v]; print(next(p["installPath"] for p in d if p["id"] == "ai-baton@ai-baton-kit"))')/setup.sh" --personal
   ```

   The line asks Claude Code where the plugin root is and runs its `setup.sh`, which takes the current directory as
   the workspace root (`PROJECTS=/path` overrides it). It creates the env store `.context/reference/env/` (filled for a
   GitHub-only machine: GitHub issues as the tracker, your clones as the tracked repos, every `systems.*` false),
   `.context/` with its README and index, the memory symlink, `.context/state/pr-review/config.json`,
   `<root>/.claude/settings.local.json` and a root `CLAUDE.md` that imports `.context/reference/environment.md`. It
   writes no `Makefile` and no `@.claude/WORKSPACE.md` import. Existing files are reported, never changed; the command
   is safe to re-run. When the workspace root is a git repository, add `.claude/settings.local.json` to its
   `.gitignore` (the script warns until you do).

4. **Restart Claude Code in the workspace root.** Skills, agents and the plugin's SessionStart hook load at startup.
   The hook exports `BATON` (the plugin root), `CLAUDE_PROJECT_DIR` and your identity to every Bash command, and
   prints `WORKSPACE.md` into the session — only in a workspace that has an env store.

5. **Run `/kit-health`** (`/ai-baton:kit-health` if another plugin has a skill of that name). It checks the kit, the
   leaks, the env store and this machine's wiring, and stamps `.context/kit-health/HEALTH-<env>.md` once it is
   GREEN or AMBER. Its § 1 prints the install mode (`plugin`).

6. **A tracker, chat or warehouse?** The quick start leaves every `systems.*` flag false, so a skill that needs one
   stops with a "not applicable here" line. Name the environment and turn on the systems you have
   (`new-environment.md` § Checklist, steps 2–3, with `python3 $BATON/context-db/bin/kb.py config-set …`), then run
   `/env-init` to discover the facts.

## Install prompt (paste into Claude Code)

Start Claude Code in your workspace root and paste:

```
Install the ai-baton kit as a Claude Code plugin for this directory (my workspace root).

1. Run `claude plugin marketplace add MdaaaaO/ai-baton` and `claude plugin install ai-baton@ai-baton-kit`.
2. Find the plugin root: the `installPath` of `ai-baton@ai-baton-kit` in `claude plugin list --json`.
   Run `sh <plugin root>/setup.sh --personal` from this directory and show me what it created,
   what it left alone and every line it asks me to fix.
3. If `gh` was not logged in, ask me for my name, GitHub login and IANA timezone, and tell me to
   run `/plugin configure ai-baton` with them.
4. Tell me to restart Claude Code here and run `/kit-health` in the first session.
```

## What the plugin does not bring

A plugin installs skills, agents and hooks. The rest of the workspace kit is conventions around your directories:

| on a clone | on the plugin path |
|---|---|
| `.context/`, the env store, the root `CLAUDE.md` | `setup.sh` seeds them (step 3) |
| `@.claude/WORKSPACE.md` import | the SessionStart hook prints `WORKSPACE.md` instead |
| root `Makefile` with `include .claude/workspace.mk` (`make claude_sync`, `make sign*`, `make kit_release`) | none; the engine runs as `make -C $BATON/context-db …`, a release as `make -f $BATON/workspace.mk kit_release KIT_CHECKOUT=<kit clone>` |
| kit git hooks (`pre-push`, `commit-msg`) | none: they guard kit commits, which you make from a kit checkout (`CONTRIBUTING.md`) |
| `SessionEnd` hook that runs `sync.sh` | none: the plugin update is the sync (§ Updating) |

A file you edit under `$BATON` is lost on the next update; a kit change is a PR from a checkout of the repository.

## Updating

The plugin moves with each release, not with each merge:

```sh
claude plugin marketplace update ai-baton-kit && claude plugin update ai-baton@ai-baton-kit
```

Then restart Claude Code and run `/kit-health`: it re-stamps `HEALTH-<env>.md` with the new `kit_version`, and § 6
lists the units that changed. `/kit-health` also warns when a newer release is out and prints the command above.
`setup.sh` needs no re-run; the step 3 line with `--refresh-seeds` added shows how your seeded files differ from
the current templates.

## Switching from a clone

A machine that ran the clone keeps its `.context/` (env store, DB, memory) as it is. Remove the clone's wiring, or
it shadows the plugin: the SessionStart hook stays quiet while `<root>/.claude/WORKSPACE.md` exists.

1. Steps 1–2 above.
2. On a machine with `systems.signed_commits`, drain the sign-queue first (`make sign`): its jobs live in the clone.
3. From the workspace root, move the clone aside: `mv .claude .claude.bak && mkdir .claude`. Copy your identity
   file back when you have one (`cp .claude.bak/settings.local.json .claude/`), and anything else of yours that
   lived in `.claude/`.
4. Delete the line `@.claude/WORKSPACE.md` from the root `CLAUDE.md` and the line `include .claude/workspace.mk`
   from the root `Makefile` (delete the `Makefile` when that was all it held).
5. Re-run the step 3 line. It records `kit.install_mode` as `plugin` in the env store and names any import or
   include still left.
6. Restart and run `/kit-health`. § 1 warns while the recorded and detected install modes differ, naming a leftover
   `.claude/` clone, import or include; § 4 fails on an import or include whose target is gone. Delete
   `.claude.bak` once it is GREEN.
