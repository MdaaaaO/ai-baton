---
name: kit-setup
description: "Scaffold this workspace for the kit from inside a session: runs the kit's setup.sh --personal here, reports the result, hands over to /kit-health. Use once, right after plugin install."
metadata:
  version: "3"
  updated: "2026-10-01"
  reviewed: "2026-09-27"
user-invocable: true
---

# kit-setup — the workspace is scaffolded and the user knows the next step

The plugin's install path moves on every update, so a shell cannot name `setup.sh` without looking it up. A session
can: the SessionStart hook exports the kit root as `$BATON` (and `.claude` on a clone), so this skill runs the
setup script in place (#97). `setup.sh` never overwrites a file and is safe to re-run.

## 1. Check

- The session was started in the workspace root, the directory that holds the repos (for example `~/Projects`),
  not in the home directory or inside one repo: `echo "${CLAUDE_PROJECT_DIR:-$PWD}"`. If it looks wrong, stop and
  tell the user to start Claude Code there.
- `$BATON` is set (`test -n "$BATON" && test -f "$BATON/setup.sh"`). If not, the plugin hook has not run in this
  session: tell the user to restart Claude Code in the workspace root and stop.

## 2. Run

```sh
sh "$BATON/setup.sh" --personal
```

`--personal` fills the env store for a GitHub-only machine without a question: identity from `gh`, the tracked repos
from the clones under the workspace root, the zone from the OS, every `systems.*` flag false. For a machine with a
tracker, chat or warehouse, run it without `--personal` and continue with `env-init` (`docs/new-environment.md`).

## 3. Report

- What `setup.sh` created, what it left alone, and every line it asks the user to act on, in its own words.
- If `gh` was not logged in: ask for name, GitHub login and IANA zone, and point to `/plugin configure ai-baton`.
- The next step, always: restart Claude Code in the workspace root (the hook loads the always-on rules only once the
  env store exists), then run `/kit-health` in the first session.

## 4. Offer the auto-compact backstop (only if it is not already in effect)

On a clone install the backstop is already live: the kit's own `settings.json` is
`<workspace>/.claude/settings.json`, and its `autoCompactWindow` backstops every session here — nothing to
offer. Only on a plugin install (the plugin ships no `settings.json`, so nothing is set) ask, in one line,
whether to set it now (`/autocompact 200k`, which writes the user's own `autoCompactWindow` setting in
`~/.claude/settings.json`) — a cloud session sets `CLAUDE_CODE_AUTO_COMPACT_WINDOW` instead, which this
skill cannot export for them. Never write either one unasked; `kit-health` § 4 keeps warning while it is
unset, so declining here is not a dead end.

## Related

- `kit-health` — the audit and stamp that follow this.
- `env-init` — facts beyond the GitHub-only defaults.
