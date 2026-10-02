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
   cd ~/Projects && claude    # your workspace root: the directory that holds your repos
   /kit-setup
   ```

   The `kit-setup` skill runs the plugin's own `setup.sh --personal` (the SessionStart hook exports the plugin root as
   `$BATON`), which takes the session's project directory as the workspace root (`PROJECTS=/path` overrides it). Without
   a session, the same from a shell: `sh <plugin root>/setup.sh --personal`, where the plugin root is the `installPath`
   of `ai-baton@ai-baton-kit` in `claude plugin list --json`. It creates the env store `.context/reference/env/` (filled for a
   GitHub-only machine: GitHub issues as the tracker, your clones as the tracked repos, every `systems.*` false),
   `.context/` with its README and index, the memory symlink, `.context/state/pr-review/config.json`,
   `<root>/.claude/settings.local.json` and a root `CLAUDE.md` that imports `.context/reference/environment.md`. It
   writes no `Makefile` and no `@.claude/WORKSPACE.md` import. When the pinned ctx-store is not already installed, it
   fetches it itself (`python3 $BATON/context-db/bin/ctx_adapter.py install`, a network git clone of the pinned tag,
   printing what it did) and then adopts `.context/` as a ctx store (`ctx_adapter.py adopt`), after which writes to
   `.context/` docs go through the plugin's `ctx` MCP tools; a failed fetch (offline) falls back to printing the two
   commands to run by hand, and setup still succeeds. `KIT_NO_CTX_FETCH=1` skips the fetch outright and always just
   prints the commands. Existing files are reported, never changed; the command
   is safe to re-run. When the workspace root is a git repository, add `.claude/settings.local.json` to its
   `.gitignore` (the script warns until you do).

4. **Restart Claude Code in the workspace root.** Skills, agents, the plugin's hooks and its `ctx` MCP server load at startup.
   The hook exports `BATON` (the plugin root), `CLAUDE_PROJECT_DIR` and your identity to every Bash command, and
   prints `WORKSPACE.md` into the session — only in a workspace that has an env store.

5. **Run `/kit-health`** (`/ai-baton:kit-health` if another plugin has a skill of that name). It checks the kit, the
   leaks, the env store and this machine's wiring, and stamps `.context/kit-health/HEALTH-<env>.md` once it is
   GREEN or AMBER. Its § 1 prints the install mode (`plugin`). A plugin ships no auto-compact backstop, so § 4
   warns until you set one: `/autocompact 200k` (`/kit-setup` offers it too).

6. **A tracker, chat or warehouse?** The quick start leaves every `systems.*` flag false, so a skill that needs one
   stops with a "not applicable here" line. Name the environment and turn on the systems you have
   (`new-environment.md` § Checklist, steps 2–3, with `python3 $BATON/context-db/bin/kb.py config-set …`), then run
   `/env-init` to discover the facts.

## Install prompt (paste into Claude Code)

Start Claude Code in your workspace root and paste:

```
Install the ai-baton kit as a Claude Code plugin for this directory (my workspace root).

1. Run `claude plugin marketplace add MdaaaaO/ai-baton` and `claude plugin install ai-baton@ai-baton-kit`.
2. Tell me to restart Claude Code here, then run `/kit-setup` and show me what it created,
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

The plugin moves with each release, not with each merge. [`SECURITY.md`](../SECURITY.md#verifying-a-release) §
Verifying a release has what the release manifest and its attestation check prove.

### Updating a plugin install safely

1. Run `/kit-health` before you update, not after. Its § 1 checks the plugin cache against the installed
   release's attested manifest, and — when a newer release is out — warns with the update command, the release
   notes, and the files the update would change (`manifest_check` / `pending_update_check`,
   `skills/kit-health/kit-health.py`).
2. Verify the release named in that warning yourself ([`SECURITY.md`](../SECURITY.md#verifying-a-release) §
   Verifying a release):

   ```sh
   gh release download <tag> --repo MdaaaaO/ai-baton --pattern manifest.txt
   gh attestation verify manifest.txt --repo MdaaaaO/ai-baton \
     --signer-workflow MdaaaaO/ai-baton/.github/workflows/release.yml
   ```

   A pass proves that release's manifest came from the release workflow. Whether the files the update
   installs match that manifest is what step 4 checks.
3. Update:

   ```sh
   claude plugin marketplace update ai-baton-kit && claude plugin update ai-baton@ai-baton-kit
   ```

   Then restart Claude Code.
4. Run `/kit-health` again: it re-stamps `HEALTH-<env>.md` with the new `kit_version`, checks the cache against
   the newly installed release's own attested manifest, and § 6 lists the units that changed.

`setup.sh` needs no re-run; the step 3 line with `--refresh-seeds` added shows how your seeded files differ from
the current templates. The exception: an update that moves the ctx-store pin (and the first update that brought
ctx-store in) needs `python3 $BATON/context-db/bin/ctx_adapter.py install && python3 $BATON/context-db/bin/ctx_adapter.py adopt`
once, or a `setup.sh` re-run. Until then the adapter finds no ctx at the new pin and the hooks do nothing;
`/kit-health` § 5 reports it. `adopt` runs `ctx init --upgrade` with the kit's store settings and type
schemas: a re-run after an update replaces a file that still holds what the last `init` wrote, and keeps and
reports one edited here (exit 5); `adopt --replace` takes the kit's copy of those too, local edits included.

## Switching from a clone

A machine that ran the clone keeps its `.context/` (env store, DB, memory) as it is. Remove the clone's wiring, or
it shadows the plugin: the SessionStart hook stays quiet while `<root>/.claude/WORKSPACE.md` exists.

1. Steps 1–2 above.
2. On a machine with `systems.signed_commits`, drain the sign-queue first (`make sign`): the jobs themselves live in
   `.context/state/sign-queue/` (untouched by the clone move below), but step 4 removes the Makefile's only path
   to them.
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
