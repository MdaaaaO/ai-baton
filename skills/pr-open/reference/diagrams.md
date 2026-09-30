# Diagrams — exit codes, the facet matrix, and the rules for every block

Loaded from `SKILL.md` § Diagrams. The contract every diagram surface follows (tickets, reviews, the epic doc) is
[`docs/diagrams.md`](../../../docs/diagrams.md); this file covers the PR body's plan only.

**Exit codes** (`diagram-plan.py`, also in `--help`): `0` success (a plan printed, or `--check` found the
marker current) · `1` a `gh`/`git` call failed · `2` bad usage (no changed files, or `--check` without
`--pr`) · `3` `--check` found drift, no marker, or a marker present but malformed. The facet-weighting
thresholds (a facet under 12 changed lines never earns a block; a secondary facet needs ≥ 25 % of the
dominant's weight) are defaults — override per run with `--trivial-lines` / `--secondary-share`.

**Facet → question matrix.** The one copy is the script: `python3 $BATON/skills/pr-open/diagram-plan.py --explain`
prints every facet with its globs (first match wins; the env-config overlay before the defaults) and the WHERE /
WHAT / RUNS answer each facet raises, `—` where a question is not raised, `only when …` where it is conditional.
Read it there when a plan surprises you; never copy it into this file (it drifted here before).

Intent overlays: **bugfix** → RUNS only (one sequence of the failing path, fixed step in a `rect`); **refactor** →
WHERE as before → after, no flow. Composition: the dominant facet (most changed lines) answers first; a
secondary facet fills a question the dominant does not raise only when it carries ≥ 25 % of the dominant's
weight; a facet under 12 changed lines never earns a block. Three or more substantive facets → the note says
"one PR = one concern": draw the dominant one and name the rest in prose, or split the PR. Repo path
conventions that the core globs cannot know (a pipeline under `assets/…`, DDL that is really event schema,
route modules that are UI) go into the env config — `diagrams.repos.<owner/repo>.facets.<facet>: [globs]` and
`.ignore: [globs]`, read by the script before its defaults — never into this file.

**Rules that hold for every block**
- **GitHub renders ```` ```mermaid ```` natively** on PR bodies and comments — no images, nothing to upload.
  Jira does **not**: a ticket gets the PR link, never the diagram source.
- **Validate before publishing.** A syntax error renders as a red box for every reviewer. Deps first, in a
  scratch dir — module resolution is CWD-relative, so the check must also **run** from that same dir (`cd`
  back to the repo root before the `node` call is the usual miss):
  ```
  D="$(python3 $BATON/context-db/bin/kit_profile.py scratch mm)"
  df -h "$D"    # low free space here is usually a build cache, not this install — clear the largest one
                # (e.g. a language toolchain's build cache) first, then retry
  cp $BATON/skills/pr-open/package.json $BATON/skills/pr-open/package-lock.json "$D"/
  ( cd "$D" && npm ci --no-audit --no-fund )   # pinned versions, not the day's latest — Dependabot bumps the lockfile
  S="$(cd "$BATON" && pwd)/skills/pr-open"    # $BATON (e.g. .claude) is relative to the workspace root —
  B="$(realpath <body.md>)"                   # resolve both before the cd, or the node call can't find them
  ( cd "$D" && node "$S/mermaid-check.mjs" "$B" )
  ```
  Prints `OK (<type>)` / `FAIL <error>` per block; handles CRLF-terminated fences; exits 1 on any failure, or
  on a file with zero mermaid blocks unless `--allow-none` is passed (a docs/config-only push has none on
  purpose); exits 2 with its own install hint when the deps above are not on the CWD yet. Or, if all that is
  impossible, re-read against these traps: a `;` inside sequence
  text **terminates the statement** (use `—`/`,` or parentheses); one message per line; quote node labels with
  `(`, `)`, `/`, `$`, `·`; `<br/>` for line breaks; edge labels `A -- text --> B` / `A -. text .-> B`.
- **Honest labels.** Tag nodes `(existing)` / `(this PR)` / `(follow-up <key>)`; highlight the changed nodes
  (`classDef new fill:#e6f4ea,stroke:#1e7e34` + `class a,b new`). A diagram that implies a handler, store or
  column exists when it does not is a false claim on a permanent record (`WORKSPACE.md` § Verification). A
  delta table's *before* column comes from the base branch (`git show <base>:<file>`, the schema yml, or the
  warehouse), not from memory.
- **Right-sized.** ≤ ~15 nodes / ≤ ~25 sequence steps per block, ≤ ~20 rows per table; more means the PR is
  probably too big. Ticket/PR ids inside Mermaid labels stay bare (labels cannot carry links) — link them in
  the prose.
- **Keep them current** — Procedure step 5 in `SKILL.md`; the marker is what makes drift detectable.
