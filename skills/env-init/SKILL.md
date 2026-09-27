---
name: env-init
description: Fill or refresh this machine's env fact store from the discovery manifests: run the named tool per missing or stale fact, verify, write back with provenance, and ask the user once for what no tool settles. Invoke on a new environment, when a skill stops with `NEEDS <system>.<kind> <name>`, with `--refresh` for stale rows, or with one fact.
metadata:
  version: "7"
  updated: "2026-09-27"
  reviewed: "2026-09-26"
user-invocable: true
---

# env-init — discover the environment, don't interrogate the user

The env fact store (`.context/reference/env/`, spec `$BATON/docs/env-facts.md`) is a knowledge base
that grows as the kit is used. Before this skill, every unset fact cost the user a question. Now the
discovery manifests (`$BATON/context-db/discovery/<system>.json`, plus a plugin's copies in the store's
`_discovery/`) say **which tool finds each fact, how to verify it and how long it stays fresh** — and
`kb.py` turns that into a plan. This skill executes the plans. It never guesses a value, never stores a
secret, and asks the user only for what no tool can settle — once, batched.

`K=python3 $BATON/context-db/bin/kb.py` below. Run everything from the workspace root.

## Modes

| Invocation | Scope |
|---|---|
| `/env-init` | every **applicable** manifest fact that is missing here (state `—` in `discover --all`) plus every **named** `metadata.facts` entry of an installed skill/agent that `kb.py get` cannot answer |
| `/env-init --refresh` | the rows `kb.py stale` lists (past their `ttl_days`; `user` rows are never stale) — re-run the tool, rewrite only what changed, re-date what did not |
| `/env-init <system>.<kind> [<name>]` or `/env-init <config.key>` | one fact — the path a skill takes when it stopped with `NEEDS …` |
| `/env-init --dry-run` | steps 0–2 only: print the plan table, touch nothing, ask nothing |

## Procedure

0. **Store health first.** `$K migrate --check` — exit 3 means renamed flags/kinds are pending: run
   `$K migrate`, say so in one line. Then `make -C $BATON/context-db kit-verify` must pass (a store
   with a broken `config.json` is fixed before it is filled).
1. **Take the inventory.** `$K discover --all` → one line per fact: key, target (`row`/`config`), tool,
   ttl, state (`set` / `N row(s)` / `—`). A system heading marked *not applicable* (its `requires` flag is
   false or its `when` does not hold) is skipped whole — never turned on from here; the user decides
   `systems.*` (`kb.py config-set`). Then the named needs: for every installed unit,
   ```sh
   python3 $BATON/context-db/bin/frontmatter.py facts
   ```
   (every unit's `metadata.facts` string — comma-separated entries, the same parser `kit-verify` uses — sorted,
   unique) → each `<system>.<kind> <name>` entry is a concrete row to have; `$K get <system>.<kind> <name>`
   decides missing vs present (exit 1 = missing; a stderr *stale* hint = candidate for `--refresh`).
   Kinds with an open name set (a channel, a person, a page — any `<system>.<kind>` whose names are not
   enumerable) are filled **only** for the names a unit declares — this skill does not enumerate the world.
2. **Plan.** `$K discover <key> [<name>]` per missing fact prints `call:` (tool + args, already rendered
   with `{name}` and the config values it depends on), `verify:`, `note:`, `ttl:`, `write:` (the exact
   `kb.py set … --from tool:<name>` / `config-set` line). Order the work by dependency: a `derive` fact
   or an arg like `{config.tracker.project}` needs its input first (`derive`/`derived:` facts come last),
   and by tool, so one MCP tool serves several facts in a row. Print the plan as a ≤ 20-row table
   `fact | tool | state`; with `--dry-run` stop here.
3. **Run the tools.** Per plan line, by tool kind:
   - **MCP tool** (the name in the plan's `call:` line — the manifests hold the concrete names, this skill
     never does): call it with the rendered args. If the tool is not in this session's roster, that system's facts go
     to the user round (step 4) with the note *tool unavailable here* — never substitute another tool.
   - **`cli`**: run the rendered shell command exactly as the `run:` line prints it (every filled value is
     already shell-quoted; `argv:` is the same command as a list, for a caller without a shell), with the `github.sandbox_token_prefix`
     placeholder applied where one is set (`eval "$(python3 $BATON/context-db/bin/kit_profile.py gh-env)"`).
     Exit status is checked separately from the output; an error is a failed lookup, not an empty answer.
   - **`roster`**: the fact is "does this session have tool X" (a `<system>.enabled` switch) — answer from
     the tool list, no call. **`settings`**: read the named `WORKSPACE_*` variable from the shell environment
     (the plugin's `/config` options on a plugin install, `.claude/settings.local.json` on a clone); never
     print anything else from either source.
   - **`derive`**: compute from the facts named in `args` exactly as the `note:` says (e.g. `key_regex`
     from the tracker project); provenance `derived:<source-key>`.
   - **`user`**: no tool exists (a path the user chose, the environment's name) → step 4.
   **Verify every answer as the `verify:` clause says** before writing (the id resolves back to the same
   name, the field is on the create screen — whatever the manifest states, nothing more). A candidate that fails
   verify is dropped, not stored. Exactly one verified candidate → write it. Several → the manifest's
   `note:` heuristic (exact-name match first, …); still ambiguous → step 4 with the candidates.
4. **One user round.** Collect everything left — `user` facts, ambiguous picks, unavailable tools — into
   **one** `AskUserQuestion` call, ≤ 4 questions and never one question per fact: when more facts are open,
   group them by system (one question per system, its candidates as the options). What still does not fit
   in that one call is *left unset* and named in the report — the next `/env-init` picks it up. Each
   question lists the verified candidates as options with the `purpose` from the manifest; a `user` fact
   gets a free-text prompt. Nothing the user declines is asked again in this session — note it as *left
   unset* in the report.
5. **Write back** with the plan's `write:` line verbatim: `--from tool:<tool>` for a tool answer,
   `--from user` for the user's pick, `--from derived:<key>` for a derivation; `--purpose` from the
   manifest unless the user gave a better clause. Under `--refresh`, a re-run whose value is unchanged
   is still written (same value, new date) so `stale` stops reporting it; a changed value is written and
   named in the report — a drifted id is worth the user's eye. Never `kb.py rm` from here.
6. **Close.** `$K stale` (should be empty after `--refresh`), `make -C $BATON/context-db kit-verify`,
   then the report to the user: a ≤ 15-line table `fact | before → after | source` plus one line per fact
   left unset and why. Then `make -C $BATON/context-db session-touch NAME=<name>` (§ Cost & context hygiene). Nothing here changes a skill, the
   kit, or `.context/reference/environment.md` — prose stays the user's.

## Rules

- **Never guess, never infer an id from a similar one, never widen the scope** to "while I am here":
  a fact enters the store only through a verified tool answer, a derivation the manifest defines, or the
  user's word.
- **Secrets never enter the store or the transcript** — tokens, session names, SSM values, anything from
  `settings.local.json` or the plugin options other than the `WORKSPACE_*` identity values. A manifest never asks for one; if a
  tool answer contains one (a connection string with a password), store the non-secret part only.
- **One question round per invocation.** A skill that would ask twice is misusing this skill.
- **Not applicable stays not applicable.** A false `systems.*` flag is the user's statement about this
  machine; this skill fills facts *within* the systems that are on.
- **The forked-worker contract**: a `triage` / `review-runner` / `auto-runner` fork that hits a missing
  fact does not run this skill — it returns exactly `NEEDS <system>.<kind> <name>` and the main session
  runs `/env-init <system>.<kind> <name>` (the user round happens in the main session, where the user is).
- **Migration of a store from the free-text era** (`learned-from` cells like `slack_search_channels 2026-09-24`):
  nothing to do — `stale` reads the last date in the cell; an undated cell shows up under `--refresh` and
  is re-verified and re-dated like any other stale row.

## What this skill is not

It does not create `.context/reference/environment.md` prose, does not set `systems.*` flags, does not
install MCP servers or CLIs, and does not run `kit-health` (which calls `kb.py stale` monthly and hands
the result here). The manifests are the vocabulary: a fact no manifest covers is a kit change
(`context-db/discovery/README.md` — add the entry, then `kit-verify`), not a question to the user.
