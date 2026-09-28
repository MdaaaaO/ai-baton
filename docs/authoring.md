# Authoring checklist — a skill or agent that passes the gates

The contract `context-db/bin/kit_verify.py` enforces and `docs/contributing.md` § Skills / § Skill frontmatter explain,
as one list to tick before the PR. Scaffold: copy `docs/templates/skill/` to `skills/<name>/` (+
`docs/templates/evals/` cases to `evals/<name>-<case>/`); an agent is one file `agents/<name>.md`.

## 1. Frontmatter — every key `kit_verify.py` accepts, with its rule

Top level: only the Agent Skills spec keys and the allow-listed Claude Code keys; anything else fails. Kit
bookkeeping goes under `metadata:` as **double-quoted strings** (`migrate_frontmatter.py` moves old top-level keys).

| key | who | rule (`kit_verify.py`) |
|---|---|---|
| `name` | required | equals the skill's directory name / the agent's file stem |
| `description` | required | the trigger: `<what>. Use when <situations>. Not for <near misses>.` — ≤ 60 whitespace tokens (`DESC_MAX_WORDS`), ≤ 400 B (`DESC_MAX_BYTES`), all units ≤ 9,500 B together (`DESC_TOTAL_BYTES`, warns past 90%); no `(<env>) ` prefix. Warns (an error from the next release) when it names no trigger (`when`) or reads in the first person |
| `metadata.version` | required | an integer string, bumped on every behaviour change |
| `metadata.updated` | required | `YYYY-MM-DD` of that change (`review_gate.py`: on or after the base's when a file of the unit changed) |
| `metadata.reviewed` | required | `YYYY-MM-DD` the unit was last re-read; `kit-verify --stale N` warns past N days; always warns when older than the file's last committed edit (`--no-git` skips) |
| `metadata.requires` | capability tier | comma-separated `systems.*` flags from `kb.SYSTEMS` (`jira slack notion datalake airflow dbt aws_sso incident_io lattice signed_commits`) — the only gate a unit carries |
| `compatibility` | with `requires` | `"Designed for Claude Code; needs <flag>[, <flag>] (systems.*)"` — names every required flag, ≤ 500 chars |
| `metadata.facts` | when the body reads a fact | comma-separated `<system>.<kind>[ <name>]` or `<config.key>` entries, each covered by a manifest in `context-db/discovery/` (a fact without one fails); a `systems.<flag>` entry needs no manifest, only a real flag. Every `kit_profile.py get` / `kb.py get` in the body must be declared here, or `kit-verify` fails |
| `license` `allowed-tools` | spec | accepted; `license` is added once the repo has one |
| `user-invocable` | Claude Code | `true` offers `/name`; absent = auto-trigger only |
| `argument-hint` `arguments` | Claude Code | the `/name` argument line (`<owner/repo> <pr>`) and the named positionals the body reads as `$repo` |
| `context` `agent` `model` `effort` `background` | Claude Code, forked skills | `context: fork` runs the body in a subagent; `agent:` names the `agents/*.md` it runs under; `model:` / `effort:` pin the tier it is priced for; `background: false` when the main session waits for its result |
| `tools` `color` `omitClaudeMd` `maxTurns` | **agents only** | the tool roster, UI colour, dropping the always-on prefix for a cheap worker, the turn cap — on a skill they fail |
| `environments` `profiles` | retired | fail with a pointer to `metadata.requires` |

Also failed: a bare value with a ` #` in it (YAML cuts it there — quote it), a line the parser cannot read
(`key: value` at column 0, or two-space `sub: value` under `metadata:`).

## 2. Body — what `docs/contributing.md` § Skills asks of the text

- **Runs on any machine or stops with one line**: `<skill>: not applicable here — <why>` when a required flag is
  false (`kit_profile.py get systems.<flag>`). Never improvise a substitute.
- **Numbered imperative sections** (`## 1. Detect`, `## 2. Do`, `## 3. Report`); 70–130 lines as the target, 500 the
  cap (`BODY_MAX_LINES`). Rationale, history and worked scripts go to `reference.md` / `references/` (loaded on demand).
- **No environment value**: no id, host, org / team / channel name, person, ticket key, timezone literal — the
  body names the fact (`metadata.facts`) and reads it (`kb.py get`); a missing one is resolved as `docs/env-facts.md`
  § How a skill resolves a fact says, and a forked worker returns exactly `NEEDS <system>.<kind> <name>`.
  `leak_shapes.py` fails the generic shapes; `skills/kit-health/allow.txt` is the one allow-list (dated examples
  that must stay verbatim, never a value a skill reads). Use `<placeholder>` forms otherwise.
- **Cross-reference other skills by name** (`pr-watch`, `/pr-watch`), never by path — a `skills/<x>/SKILL.md`
  literal fails; a script of another skill that a step runs is cited by its path at that one point.
- **Own paths exist**: every `scripts/…` / `references/…` the body cites must be a file beside it.
- **The kit is `$BATON/`**, never `.claude/`: a plugin install has no kit there (`kit-verify` fails it; `docs/packaging.md` § Kit root).
- **Machine-only steps become a handoff artefact** the user runs (a queued job, a `commit.sh`), never a pasted
  one-liner or a silent substitute.
- **Capability tier ships a `README.md`** (≤ 30 lines): what it needs, what it does without it, one example.

## 3. Before the PR

1. `make -C $BATON/context-db verify-skill UNIT=skills/<name>` — the env-free validator (schema, caps, body,
   cited paths, leak shapes, manifests), then `make -C $BATON/context-db ci` (every gate CI runs; `ALLOW_SKIP=1`
   where shellcheck or the `claude` CLI is missing).
2. **Bump**: a behaviour change bumps `metadata.version` and sets `metadata.updated`; what a machine must do after the
   sync goes in the PR's § Machines (the release log is generated from the squash commit). Wording-only edits bump nothing and the PR says so (`wording`
   label or `[skip-bump]` in its title or body). `make -C $BATON/context-db review-gate BASE=origin/main` is the
   tier-0 gate CI runs: leak and PII shapes on every added line, and the bump per changed unit (`docs/REVIEW.md` § 1).
3. **Tests**: a script beside the skill or an engine change gets a stdlib `unittest` in `context-db/tests/`
   (`make -C $BATON/context-db test`); a fix adds the regression test; a fact-shaped literal a test needs is
   assembled at run time, never written out.
4. **Evals**: ≥ 10 trigger cases per skill, ≥ 3 positives and ≥ 3 same-domain near misses, in `evals/<name>-<case>/`
   (`prompt.md` + `graders/*.md`, the `claude plugin eval` format; `evals/results/` is ignored) — `evals/README.md`.
   `make -C $BATON/context-db eval-check` checks the suite without tokens (part of `make ci`); a changed
   `description:` cites a green `make -C $BATON/context-db eval SKILL=<name>` or `evals` workflow run in the PR.
5. A new native frontmatter key goes into `NATIVE_KEYS` / `AGENT_KEYS` in `kit_verify.py` **and** the table in
   `docs/contributing.md` § Skill frontmatter, with the reason in the PR.
6. Open the PR with the `pr-open` skill: Conventional Commit title, `Closes #N` / `Refs #N`, type + area labels;
   answer and resolve every review thread (`docs/REVIEW.md`).
