# What loads when — the three layers and their byte budgets

Every later turn re-reads the prefix (`WORKSPACE.md` § Cost & context hygiene): cached at ~0.1× input, but an edit
to an always-on file re-bills it in full for every open session. The kit therefore sorts every file into one of three
layers, and the always-on layer is the one lever the budgets guard. Numbers below were measured on 2026-09-27
against the kit at that date; re-measure as § How to measure says (`kit_verify.py --loading-table --write` rewrites
a marker only when it would otherwise fail the drift check — a count that differs, or a byte total outside the
5% tolerance; add `--force` to refresh every marker regardless, e.g. at release time). The `<!-- kit-verify:<key> -->`
spans are machine-checked — `kit-verify` fails the PR when one drifts from what `--loading-table` computes.

## Layer 1 — always on (every session, every turn)

Loaded by Claude Code at session start, before the first prompt:

| what | from | measured | cap (`context-db/bin/kit_verify.py`) |
|---|---|---|---|
| the root `CLAUDE.md` preamble | the user's file, seeded from `CLAUDE.example.md` | 480 B skeleton (the user's preamble adds to it) | none — keep it about the user |
| `@.claude/WORKSPACE.md` | the kit, imported by the root `CLAUDE.md` | 9,990 B | `ALWAYS_ON_BUDGET["WORKSPACE.md"]` = 10,000 B |
| `@.context/reference/environment.md` | the machine's prose, seeded from `environment-template/environment.md` | 2,039 B template | `ALWAYS_ON_BUDGET["environment-template/environment.md"]` = 2,200 B (the seeded copy grows on the machine and is not capped) |
| every skill's and agent's `name` + `description` | `skills/*/SKILL.md`, `agents/*.md` (<!-- kit-verify:units -->29<!-- /kit-verify:units --> units) | <!-- kit-verify:desc-bytes -->9,380 B<!-- /kit-verify:desc-bytes --> | `DESC_MAX_WORDS` 60 and `DESC_MAX_BYTES` 400 per unit, `DESC_TOTAL_BYTES` 9,500 across the kit (warns past 90%) |
| the auto-memory index `MEMORY.md` | `.context/memory/` (the harness memory dir, symlinked there by `setup.sh`) | per machine | none in the kit |

≈ 22.0 KB from the kit and templates before the user's preamble and memory index. What belongs here: rules that
must hold in every session (`WORKSPACE.md` § Rules), the skill trigger list, the environment's always-on rules
(`environment.md`), and each unit's trigger (`<what>. Use when <situations>. Not for <near misses>.`). What never
does: a procedure, an example exchange, a rationale, an environment value.

## Layer 2 — on invoke (when a skill or agent runs)

- **`SKILL.md` bodies** load when the skill is invoked (by trigger or `/name`): <!-- kit-verify:bodies -->26<!-- /kit-verify:bodies --> bodies,
  <!-- kit-verify:body-bytes -->232,064 B<!-- /kit-verify:body-bytes --> together, so the
  layer is roughly ten times the always-on one and is paid only by the session that uses it. Cap `BODY_MAX_LINES` = 500
  lines per body; target 70–130 (`docs/contributing.md` § Skills).
- **`reference.md` / `references/`** beside a skill load only when a step cites them — rationale, worked scripts,
  history go there, not into the body. `scripts/` are run, never read into context.
- **Agent bodies** (`agents/*.md`) load into the fork they run; `omitClaudeMd: true` keeps layer 1 out of a cheap
  worker's prefix, and a `context: fork` skill returns a brief so its reading never enters the main prefix.

## Layer 3 — on open (only when a session reads the file)

`README.md`, `CONTRIBUTING.md`, `docs/`, the engine sources, and every `.context/` leaf doc: the context DB is
"find first" — `INDEX.md` (then `SESSION_INDEX.md` for shared work), then only the leaf docs the task needs.
`make -C $BATON/context-db verify` warns when an active doc passes 30 KB, because a context doc is re-read whole
after a compaction. These files are carried by the plugin and the clone alike but cost nothing until opened.

## How to measure

```sh
wc -c .claude/WORKSPACE.md .context/reference/environment.md CLAUDE.md .context/memory/MEMORY.md   # layer 1 files
make -C $BATON/context-db kit-verify          # summary line ends "… descriptions N B"; fails past any cap
make -C $BATON/context-db verify-skill        # the same caps on a bare clone (kit_verify.py --no-env)
make -C $BATON/context-db kit-health          # § 1 runs kit-verify with this machine's store; § 5 smokes the engine
python3 $BATON/context-db/bin/session_stats.py --format block   # a session's cached-prefix tokens and peak context
```

A change to layer 1 must earn its bytes: `kit-verify` fails the PR at 10,001 B of `WORKSPACE.md` or 9,501 B of
descriptions. Move detail down a layer instead — into the skill body (on invoke) or a doc (on open).

## Templates never reach an existing machine

`setup.sh` seeds `CLAUDE.md` (from `CLAUDE.example.md`), `.context/reference/environment.md` (from
`environment-template/environment.md`), the root `Makefile`, `.context/README.md` (from
`context-db/context-README.template.md`), `.context/self-assessment/README.md`, `.claude/settings.local.json` and
`.context/state/pr-review/config.json` **only when the file is absent** ("seeded only if absent" in its output);
an existing file is reported and never touched, whatever the template now says. A kit PR that changes a template
therefore changes nothing on a machine that already ran `setup.sh`. To refresh by hand:

```sh
diff .context/reference/environment.md .claude/environment-template/environment.md   # carry the new sections over
diff CLAUDE.md .claude/CLAUDE.example.md                                            # keep the two import lines
diff .context/README.md .claude/context-db/context-README.template.md
```

`kit-health` § 4 checks only the wiring (the two `@` imports, the `include .claude/workspace.mk` line, the memory
symlink), not the templates' contents; such a PR's § Machines says "apply to a machine by …"
when a step is needed (a `BREAKING CHANGE:` footer when every machine must).
