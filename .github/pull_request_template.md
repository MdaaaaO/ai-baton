<!--
Title = the squash commit on main. Conventional Commits (docs/commit-style.md), lower-case subject,
no trailing period, ≤ 72 characters:

  feat(pr-open): derive the diagram set from the diff
  fix(sync): release the mkdir lock on hosts without flock
  docs(kit): new-environment migration steps
  ci(kit): add the pr-issue check

Types: feat fix docs chore refactor test ci build perf style revert
-->

Closes #
<!-- Every PR comes from an issue. "Closes #N" = this PR finishes it (GitHub closes it on merge).
     "Refs #N" = one step of a larger issue, which stays open. No issue yet? Create it first.
     The pr-issue check fails without one. -->

## What

## Why

## Units touched

<!-- Each skill/agent whose behaviour changes: `name vN → vN+1`. Wording-only edits bump nothing — say so. -->

## Machines

<!-- What a machine must do after `make claude_sync` — usually "nothing". A step every machine must take (a `kb.py migrate`,
     a `config-set`, a re-run of setup) also goes in the squash commit as a `BREAKING CHANGE: <step>` footer, so the
     generated release notes carry it. -->

## Verified how

- [ ] `make -C context-db ci` green locally (every gate CI runs; `ALLOW_SKIP=1` for a missing tool)
- [ ] one type label (`enhancement`/`bug`/`documentation`) and one `area:*` label applied — `wording` too, if wording-only
- [ ] versions + `updated` bumped for every changed skill/agent — or the `wording` label / `[skip-bump]`, and no bump needed
- [ ] evals touched (`evals/<skill>-<case>/`) when a skill's `description:` or trigger wording changed
- [ ] no environment-specific value (ids, orgs, channels, hosts, token prefixes) added to a core file
- [ ] capabilities affected (every machine, or the `systems.*` flags involved) named, and § Machines filled
- [ ] `/kit-health` run on at least one machine after the change
