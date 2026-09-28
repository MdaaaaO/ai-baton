# 0001 — Environment facts live in a per-machine env store

## Status

Accepted

## Context

The kit runs unchanged across machines with different trackers, chat tools, GitHub orgs and warehouses.
Any value that differs between environments — a channel id, a custom-field id, an account id, a
colleague login, an org host — cannot live in a skill body, an agent file, an engine script or a shared
doc: that would either hardcode one environment into a file every machine loads, or force a fork per
machine. The kit ships mechanisms; the facts a mechanism needs are a knowledge base each machine builds
as it is used.

## Decision

Environment facts live only in the **env fact store**, `.context/reference/env/` — part of the
per-machine `.context/` document DB, never in this repo:

- `config.json` — structural switches scripts branch on (`tracker.kind`, `github.org`, `systems.*`,
  `domains`, …).
- One markdown table per system (`slack.md`, `tracker.md`, `github.md`, `aws.md`, `notion.md`, …), each
  row `name · value · purpose · learned-from`, with `learned-from` a structured provenance tag
  (`tool:<name>`, `user`, `derived:<key>`) that `kb.py stale` measures against a manifest's `ttl_days`.

`context-db/bin/kb.py` is the only writer (get/set/rm/list/discover/stale/migrate); `kit_profile.py
get <key>` is the read API every script and skill uses, and prose cites the dotted key rather than the
value. Discovery manifests (`context-db/discovery/<system>.json`) name the tool that can answer a
missing fact, its verify step and its write target, so a skill resolves a fact as: `kb.py get` → missing
→ `kb.py discover` → run the tool and verify → ask the user once only if nothing else settles it →
`kb.py set --from …`. A skill whose `metadata.requires` names a `systems.*` flag that is false is not
applicable on that machine — never improvised around.

## Consequences

- A skill, agent or shared doc that carries a literal value is a bug the leak scan can catch mechanically
  (`kit-health` § leaks: generic id shapes plus every value the local store actually holds), not something
  a reviewer has to notice by inspection.
- A new environment needs no kit change: `setup.sh` seeds a blank store, `/env-init` fills it from the
  discovery manifests, and the machine is running facts nothing else on that machine had to invent.
- Every fact carries provenance and a staleness clock, so a stale id is a `kb.py stale` finding rather
  than a silent wrong value.
- The store is local and unsynced by design — a team that wants to share seed facts ships them as tables
  in its own plugin and writes them with `kb.py set` at setup, rather than the kit carrying a second,
  shared store.

## Sources

- `docs/env-facts.md` (the store layout, `config.json` keys, the CLI, discovery manifests, fact
  resolution)
- `docs/new-environment.md` (the store as the only place environment specifics live; no second repo)
- `WORKSPACE.md` § Environment facts
- `kit-health`'s leak scan (`docs/env-facts.md` § How a skill resolves a fact, closing paragraph)
