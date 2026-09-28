# Contributor reference

The full rules behind [`CONTRIBUTING.md`](../CONTRIBUTING.md), one section per topic. The kit ships to every
machine on its next `make claude_sync`, and a sync only fast-forwards, so whatever lands on `main` changes how
every session behaves the same day. That is why nothing reaches `main` except through an issue and a reviewed PR.

## Prerequisites

Stdlib Python only (no pip installs), plus `git` and `make` on the machine, `gh` logged in for the PR and ticket
skills. The floor is **Python 3.9**: `context-db/bin/kit_profile.py` — imported by every engine script — checks
`sys.version_info` before its first import and exits with one line (`ai-baton needs Python 3.9+ (found …)`) on
anything older, because the engine itself uses 3.9-only stdlib (`zoneinfo`, `Path.is_relative_to`,
`str.removeprefix`). `ci.yml`'s `python-floor` job runs `make test` and `verify-skill` under 3.9 on
`ubuntu-latest`, pinned by commit SHA like every other action (`docs/contributing.md` § Where CI runs); no macOS
leg yet — BSD-vs-GNU `date`/`sed`/`awk` differences need their own fix first, tracked separately. `README.md`'s
own prerequisites table gets the same minimum-versions line from a different PR; this is the floor's one other
statement, not a second source of truth for it.

## From issue to merge

Every change is traceable both ways: **issue → PR → squash commit → release-log line**, and back.

```
 issue #N ─────────► branch + PR ──────────► review ─────────────► squash-merge ─────► make claude_sync
 area:* label        body: "Closes #N"       checks green          GitHub closes #N     on every machine
                     title = conv. commit    head reviewed
                     pr-issue check (CI)     every thread answered
                                             + resolved
```

| step | what happens | who / what does it |
|---|---|---|
| **1. Issue** | An issue exists *before* the branch: what, why, which environments, one `area:*` label. Found something else mid-work? New issue, not a bigger PR. | `gh issue create` (template *Kit change*) |
| **2. Branch** | A worktree off `origin/main` of your kit checkout (`.claude` on a clone install, a plain clone of the repo on a plugin install — [`CONTRIBUTING.md`](../CONTRIBUTING.md#development-setup) § Development setup). The kit checkout itself stays on `main`, clean. | you · `hooks/pre-push` refuses any push to `main` |
| **3. PR** | Before opening it: `git fetch origin main && make -C context-db ci` in the worktree (every gate CI runs — the review gate on the branch, the validator, a blank-store kit-verify, tests, parse checks, shellcheck, install smoke; a missing tool fails unless `ALLOW_SKIP=1`; one unit: `make -C context-db verify-skill UNIT=skills/<name>`). Title = a Conventional Commit (it becomes the squash subject). Body starts with `Closes #N` (finishes the issue) or `Refs #N` (one step of it; the issue stays open). Labels: a type label + the area label. | `pr-open` skill · CI checks **`pr-title`** and **`pr-issue`** (`.github/scripts/check-pr-issue.sh`) |
| **4. Checks** | `kit-verify --no-env` on the bare checkout (the env-free validator), then `kit-verify` (frontmatter schema — § Skill frontmatter — and manifests) against a blank env store, the engine's unittest suite (`make -C context-db test`), the plugin manifests through `claude plugin validate --strict` where the CLI exists (`make plugin-validate`), `py_compile`, `bash -n`. Then **`install-smoke`** replays README.md's clone and plugin install blocks on a fresh runner, from this checkout, ending in kit-health (`make -C context-db install-smoke` locally, #98). | CI checks **`kit-verify`**, **`install-smoke (clone)`**, **`install-smoke (plugin)`** |
| **5. Review** | Claude reviews the PR when it opens and again on every push (only what changed since its last review). See *Code review*. | `claude-review` workflow |
| **6. Merge** | Squash only. `auto-merge.yml` merges as soon as the six checks (`kit-verify`, `pr-title`, `pr-issue`, `claude-review`, both `install-smoke` legs) are green, the review verdict on the head is `approve` and every thread is resolved; a PR that misses a gate waits for the owner. GitHub closes every `Closes` issue. | `auto-merge` workflow · owner as fallback |
| **7. Sync** | `make claude_sync` on each machine; then `/kit-health` there if the PR says an environment needs a step. | owner, per machine |

Exempt from step 1/3's issue link: dependabot PRs and release PRs (title `chore(release): …`).

## Commits and PR titles

Conventional Commits — the full rule is [`docs/commit-style.md`](commit-style.md) where it exists,
otherwise: `<type>(<scope>): <description>`, types `feat fix docs chore refactor test ci build perf style
revert`, lower-case description, no trailing period, ≤ 72 characters. The scope is the unit or area
(`pr-open`, `kb`, `sync`, `kit`). Commits on a branch can be anything — they are squashed away.

## Versioning

A skill or agent whose **behaviour** changes bumps `metadata.version` and sets `metadata.updated`. What changed is the
PR's squash commit, which the generated release log (root `CHANGELOG.md`) lists; what a machine must do after the sync
is the PR template's § Machines, plus a `BREAKING CHANGE:` footer when every machine must act. Wording-only edits bump nothing. `kit-verify`
enforces the fields (§ Skill frontmatter); the reviewer checks the bump. A `description:` is a trigger, not the procedure: ≤ 60 words and
≤ 400 B per unit, ≤ 9,500 B across the kit (`kit-verify` counts whitespace-separated tokens, so a `/` or
`—` standing alone counts as a word) — it loads into every session whether or not the unit runs.

## Skills — the contract, on one screen

A skill is `skills/<name>/SKILL.md` (+ files beside it); an agent is one file `agents/<name>.md`. Two tiers, one floor.

**Floor tier — every skill.** `name` + `description` and a body that **runs on any machine or stops with one
line** (`<skill>: not applicable here — <why>`). The description is the trigger, not the procedure:
`<what>. Use when <the situations>. Not for <the near misses>.` — near-miss exclusions are part of it, and every
**new or changed** skill ships ≥ 10 trigger cases with negatives from the same domain (`evals/<skill>-<case>/`,
`claude plugin eval`). Most existing skills predate the suite and have none yet: `eval_check.py` notes a skill
without one rather than failing the PR, until `--require-all` flips that to a gate (§ Where CI runs). Nothing in a
body belongs to one machine, person or workplace: no ids, hosts, org / team / channel names, no people, no
anecdotes — `kit-verify --no-env` rejects fact-shaped literals, the reviewer judges meaning. A step only a
specific machine can perform (sign a commit, reach a host) becomes a **handoff artefact the user runs** — the
sign-queue writes a job, a commit skill writes `commit.sh` — never a pasted one-liner, never a silent
substitute. Cross-reference another skill **by name** (`pr-watch`, `/pr-watch`), never by path: installed paths
differ per host and a plugin root moves on every update, so `kit-verify` rejects a `skills/<x>/SKILL.md` literal
in a body. A script or reference file of another skill that a step actually runs is cited by its path at that
one point of use.

**Capability tier — a skill that needs a system.** Declare the gate: `metadata.requires: "slack"` (comma-separated
`systems.*` flags, the list is `kb.SYSTEMS`) plus `compatibility: "Designed for Claude Code; needs slack
(systems.*)"`. A false flag means *not applicable here*: one line, stop — `requires` is the only gate, the kit
never names an environment. The facts the body reads (a channel id, a field id, an account) are declared as
`metadata.facts: "slack.channel eng-help,tracker.kind"` and read at run time from the env store (`kb.py get`),
never written into the body; a missing one is resolved as `docs/env-facts.md` § How a skill resolves a fact
says, and a **forked worker returns exactly `NEEDS <system>.<kind> <name>`** for the main session to resolve
(`env-init`). A fact no manifest covers gets its discovery manifest entry first (`context-db/discovery/README.md`:
which tool finds it, how to verify, how long it stays fresh) — `kit-verify` rejects a `facts` entry without one.
A new system is a new `systems.*` flag in `kb.SYSTEMS` (every store gets it through `kb.py migrate`), never a
skill bound to one environment. Every `requires` skill carries a `README.md` (≤ 30 lines): what it needs, what it
does on a machine without it, one example exchange.

**Layout.** `SKILL.md` — the procedure as numbered imperative sections (`## 1. Detect …`, `## 2. Do …`), 70–130
lines as the target (`kit-verify` warns past it), 300 the hard cap for every unit — a unit that genuinely needs
more than 130 is named in `BODY_LINES_ALLOW` (kit_verify.py) with a reason, which silences only the warning; `reference.md` / `references/` — rationale, worked scripts,
history, loaded on demand; `scripts/` — stdlib Python or POSIX shell; `README.md` — capability tier. Evals live
**outside** the skill in `evals/<skill>-<case>/` (`prompt.md` + `graders/*.md`, the format `claude plugin eval` runs and
`claude plugin eval init --bare` scaffolds; `evals/results/` is the runner's output and is not committed), so the
installed skill stays lean. `make -C $BATON/context-db eval-check` is the token-free gate on every PR (each suite
≥ 10 cases, both kinds, every case loads); the token-spending run is `make … eval SKILL=<name>` or the manual `evals`
workflow (`evals/README.md`). `docs/templates/skill/` (SKILL.md + README.md) and `docs/templates/evals/` are the
scaffold: copy both, rename, fill the `<…>` marks. They live under `docs/`, not under `skills/` or `evals/`, because
Claude Code loads every `skills/*/SKILL.md` and `claude plugin eval` runs every case under `evals/`.

**Frontmatter.** The Agent Skills spec keys plus the allow-listed Claude Code keys, each with its reason —
§ Skill frontmatter; a new native key needs a PR justification.

**Before the PR.** `make -C $BATON/context-db verify-skill UNIT=skills/<name>` — the env-free validator (schema,
description caps, body cap, cited paths exist, no fact-shaped literal, no cross-skill path, manifests cover the
facts) — then `make -C $BATON/context-db ci`. A behaviour change bumps `metadata.version` and `metadata.updated`
(§ Versioning); the authoring checklist is `docs/authoring.md`; what loads when, `docs/loading.md`.

**One rule for contributors:** you must understand what you submit, whoever or whatever wrote it.

## Skill frontmatter

Every `skills/<name>/SKILL.md` and `agents/<name>.md` follows the **Claude Code profile** of the
[Agent Skills spec](https://agentskills.io/specification): at the top level only the spec's keys plus an explicit
allow-list of Claude Code native keys, and everything the kit itself tracks under `metadata:` as **strings**
(the spec's `metadata` is string → string). `kit-verify` fails any other top-level key; the packaging path a
public kit must pass (`claude plugin validate`, the spec's own tooling) errors on unknown keys, and
`.github/workflows/ci.yml` runs `kit-verify` on every PR.

```yaml
---
name: pr-open                      # = the directory name (agents: the file stem)
description: …                     # the trigger (§ Versioning caps it); "<what>. Use when <triggers>. Not for <near misses>."
compatibility: "Designed for Claude Code; needs slack (systems.*)"   # required with metadata.requires; names every flag
metadata:
  version: "6"                     # integer, bumped on every behaviour change
  updated: "2026-09-26"            # YYYY-MM-DD of that change
  reviewed: "2026-09-26"           # YYYY-MM-DD the unit was last re-read (`kit-verify --stale N` warns)
  requires: "slack"                # comma-separated `systems.*` flags — the ONLY gate a unit carries
  facts: "slack.channel eng-help,tracker.kind"   # comma-separated env facts; a space separates kind and name
user-invocable: true
---
```

`context-db/bin/frontmatter.py` is the one parser (skills, agents, `kb.py migrate --off`, `kit-health`,
`env-init`); `context-db/bin/migrate_frontmatter.py [--dry-run|--check]` moves the five bookkeeping keys of a
pre-2026-09-26 unit under `metadata:` and adds `compatibility` (idempotent).

| key | kind | why the kit allows it at the top level |
|---|---|---|
| `name` `description` `license` `compatibility` `metadata` `allowed-tools` | spec | the Agent Skills spec's own fields |
| `model` | Claude Code | the forked skills and the agents pin the model tier they are priced for (Sonnet triage, Opus review) |
| `effort` | Claude Code | the reasoning budget of a forked skill or agent (`low` for sweeps, `medium` for reviews) |
| `context` | Claude Code | `fork` runs a skill in a subagent so its output never enters the main prefix (alerts-sweep, pr-scan, pr-event-brief) |
| `agent` | Claude Code | which `agents/*.md` definition a forked skill runs under (`triage`) |
| `user-invocable` | Claude Code | whether the skill is offered as a `/slash` command or only auto-triggers |
| `argument-hint` | Claude Code | the argument line shown for a `/slash` skill (`<owner/repo> <pr>`) |
| `arguments` | Claude Code | named positional arguments a skill's body refers to |
| `background` | Claude Code | whether a forked skill may run in the background (pr-event-brief must not: the main session waits for its ACTION) |
| `tools` `disallowedTools` `color` `omitClaudeMd` `maxTurns` | Claude Code, **agents only** | the agent's tool roster (or, where the roster must include MCP connectors whose ids differ per install, the tools it must not have — #94), its colour in the UI, dropping the CLAUDE.md prefix for cheap workers, and its turn cap |

**The env-free validator.** `make -C $BATON/context-db verify-skill [UNIT=skills/<name>]` (= `kit_verify.py
--no-env`) runs everything that needs no environment — the schema above, `name` = directory, the description caps,
`metadata.requires` ∈ the capability flags, every `metadata.facts` entry has a discovery manifest, no bare scalar
that YAML would cut at a `#` comment, no frontmatter key repeated at one level, no numbered list that repeats a
step number or a step's text, the body ≤ 300 lines for every unit (a warn past 130, which BODY_LINES_ALLOW silences for a named unit),
every `scripts/…` / `references/…` path the body cites exists, no `skills/<x>/SKILL.md` path literal
(§ Skills: cross-reference by name), and no fact-shaped literal (Slack id,
custom-field id, account id, ticket key, org host, tz literal — `context-db/bin/leak_shapes.py`) — and prints
the checks it skipped (the env-store ones). Both scanners read the one allow-list `skills/kit-health/allow.txt`
(`<path>:<match>` regexes for a dated example that must stay verbatim, never a value a skill reads); on a PR, CI's review gate runs the base branch's `review_gate.py` with the base branch's allow-list, so a new allow line takes effect from the next PR on, once a human merged it (#129). It passes on a bare `git clone` with no `.context/`, so it is the
pre-PR step for a contributor and the first step of `ci.yml`. `make -C $BATON/context-db ci` runs every gate the CI job runs (§ Where CI runs).

A new native key is added to `NATIVE_KEYS` / `AGENT_KEYS` in `context-db/bin/kit_verify.py` **and** to this table,
with the reason in the PR — never to a unit alone. The repository is MIT-licensed (`LICENSE`, linked from
`README.md` § License); `license: MIT` on a unit is accepted but not required (a spec-strict reader requires it
to match the repo licence exactly, so set it only when you mean that reader to see it).

## Secrets

Three kinds of value, three homes — never a fourth:

- **Environment facts** (tracker site, org, channel ids, field ids, flags): the env store `.context/reference/env/`
  via `kb.py`, with discovery, TTL and provenance. Never `userConfig`, never `settings.local.json`.
- **Identity** (your name, GitHub login, timezone, your own DM ids): plugin `userConfig` (`/plugin configure`, chat
  ids `sensitive`) on a plugin install, the `env` of the ignored `.claude/settings.local.json` on a clone;
  `kit_profile.identity()` reads both (`docs/packaging.md` § Identity).
- **Tokens and passwords**: neither. A server or script reads them from the shell — `.mcp.json` takes `${VAR}`
  expansion, a script reads `os.environ` — and the user sets the variable in their login shell or a secret
  manager. A `userConfig` entry is never a token, `sensitive: true` or not; a settings file is never a token.
  `kit-health` § 2 scans the kit for the shapes; the leak scan covers identity values from both sources.

A script that starts reading a new environment variable, or a new `config.json` key through `kit_profile.py
get`, updates `docs/env-vars.md` (a row: name, default, reader, purpose, scope) and, for a config key, the
shape in `environment-template/config.json` or a discovery manifest. `context-db/tests/test_env_vars.py`
enforces both — an undocumented read or a stale row fails CI.

## Releases

The kit is released with [conventional-release](https://github.com/MdaaaaO/conventional-release) — no
package, just a version. A release is a `chore(release): X.Y.Z` PR that adds the version's section to the
root [`CHANGELOG.md`](../CHANGELOG.md) (generated by the release job — it exists from the first release on;
standard-version format, one line per squash subject since the last tag) and bumps [`VERSION`](../VERSION) and `.claude-plugin/plugin.json` together (`kit-verify` fails when they
disagree — `docs/packaging.md`); squash-merge it **with the title unchanged** and the `release` workflow
tags that commit `vX.Y.Z` and publishes a GitHub Release with the section as notes.

- `make kit_release_dry` (workspace root) — the next version and its section; writes nothing.
- `make kit_release [LEVEL=minor]` — cuts `release/vX.Y.Z` in a throwaway worktree off `origin/main`
  (the kit checkout itself stays on its branch), pushes it and opens the PR with the `release` label.
- The kit checkout is the workspace's `.claude/` clone. A plugin install has no clone (the plugin cache is not
  a git checkout), so point `KIT_CHECKOUT` at a clone of the kit repo and include the file by path:
  `make -f $BATON/workspace.mk kit_release_dry KIT_CHECKOUT=<kit clone>` (`$BATON` — the kit root — `docs/glossary.md`). The level is
  inferred from the subjects (`feat` → minor, `!` / `BREAKING CHANGE:` → major, else patch) unless given.
- Release PRs need no issue (`pr-issue` exempts them) and get no Claude review.
- `v0.0.0` marks the history before Conventional Commits; the first release lists what came after it.
- **A failed release job:** re-run it. Each step does only what is missing — a tag already on the commit is
  kept, a Release is created only when there is none — so a re-run finishes a half-done release and is a
  no-op on a finished one. The job runs only for a `chore(release):` commit; every other push skips it.
- **A failed `make kit_release`:** once the branch is cut, a failure keeps the branch and its worktree (the
  release commit may exist only there) and prints the push and `gh pr create` commands that finish the run.
- `sync.sh` logs the version a machine is at (`kit: main at 1a2b3c4 (v0.3.0+2)`), and the kit-health
  stamp records it as `kit_version`. A sync between the merge and the tag job shows the release
  commit against the previous tag (the 0.2.0 commit as `v0.1.0+5`); the next sync shows `v0.2.0`. That is expected, not a bug.

One changelog: the root `CHANGELOG.md`, the **release log**, generated from the squash commits (one line per PR). A unit's
version is its `metadata.version`; a step a machine must take is a `BREAKING CHANGE:` footer, which the release notes carry
(§ Versioning). The hand-written per-unit changelog (`docs/CHANGELOG.md`) is retired; its history is in git.

## Labels

| axis | labels |
|---|---|
| type | `enhancement` (feat) · `bug` (fix) · `documentation` (docs) |
| area — exactly one | `area:skills` (skills/, agents/ — includes `skills/sign-queue/`) · `area:engine` (context-db/, pr-review/) · `area:sync` (sync.sh, setup.sh, hooks/, workspace.mk, settings) · `area:docs` (README, WORKSPACE.md, docs/) · `area:ci` (.github/) |
| wording | `wording` — a wording-only PR; `ci.yml`'s `SKIP_BUMP` treats it (or `[skip-bump]` in the title or body) as skipping the version-bump check |
| follow-up | `review-followup` — a review finding deferred out of a PR |
| release | `release` — the `chore(release): X.Y.Z` PR, on its own (`make kit_release` applies it) |

## Where CI runs

What `ci.yml` checks — `make -C $BATON/context-db ci` runs the same gates (the dash selection is its `parse` target, shared
with CI), and a gate whose tool is missing fails there unless `ALLOW_SKIP=1`; only the token-spending evals are CI-only: the env-free validator, kit-verify
against a blank store, the unittest suite, the plugin manifests, `py_compile`, `bash -n`, `dash -n` for every
`#!/bin/sh` script (setup.sh, sync.sh and sync-check.sh are `#!/bin/sh`: they run under `sh`), `shellcheck -S warning`, the relative Markdown links
(`check_links.py`), the tier-0 review gate (`review_gate.py`), the eval suite's static check (`eval_check.py`, no
tokens), the README install smoke (`install_smoke.py`, clone and plugin) and `kit-health --ci` (the leak scan on the blank store, no writes). Third-party actions are pinned by commit
SHA and bumped by Dependabot. `evals.yml` — `claude plugin eval` on the `CLAUDE_CODE_OAUTH_TOKEN` secret — runs only
by hand (`workflow_dispatch`, inputs `skill` and `models`), never on a pull request, so no PR's code meets the token.

Every workflow runs on GitHub-hosted `ubuntu-latest` — free for a public repository, and GitHub's hardening guide
says a self-hosted runner "should almost never be used for public repositories". A job triggered by a pull request
runs the PR's code, which can come from anyone: a fork's PR runs with no secrets (GitHub's rule), so `ci` and
`pr-title` still check it; the reviewer needs its token and does not run for a fork — the owner reviews outside PRs
by hand. Workflow runs from outside contributors wait for the owner's approval (repository setting), and
`.github/CODEOWNERS` puts every change to `.github/workflows/` under the owner's review. `release` and `main-guard`
(push to main) and `auto-merge` (`workflow_run`, always the copy on main) check out no pull request.
`tests/test_ci_hygiene.py` fails when any workflow names a self-hosted runner. Each hosted job is a fresh VM, so
parallel reviews never race on one shared Claude Code install.

## Testing

The engine has a stdlib `unittest` suite in `context-db/tests/` — `make -C $BATON/context-db test` (discovery, < 10 s,
no install) — and CI runs it on every PR. `make test T=test_kb` runs one file (`tests/test_kb.py`) instead of the full
discovery — useful while iterating on one module. The suite always runs on a **throw-away env store**, never this
machine's `.context/`: `make test` builds a blank one under the temp dir as CI does (`kb.py init --blank`, environment
`ci`), whatever `CONTEXT` or `CONTEXT_ROOT` says and wherever it runs from — a kit worktree resolves the workspace's
live store (`docs/layout.md` § Content root). The test package enforces it: a bare `python3 -m unittest discover -s
context-db/tests -t .` without `CONTEXT_ROOT` gets its own blank store (removed at exit), and a `CONTEXT_ROOT` outside
the temp dir, the store beside the kit, or a store naming an environment other than `ci` stops the run before any
test loads. It covers `kb.py`
(the store: set/get/rm, renamed kinds, provenance and stale rows, config, migrate), `kit_verify.py` (frontmatter
schema, env-store checks, `--stale`, `--no-env`), `gen_index.py` / `verify.py` / `new.sh` on a throw-away
`CONTEXT_ROOT`, `gen_sessions.py`, `commit_style.py`, `eval_check.py` (on a throw-away kit), `session_stats.py` + `transcripts.py` on a synthetic
transcript, `frontmatter.py` + `migrate_frontmatter.py`, and the pure functions of `pr-review/scripts/trivial-check.py`
and `pr-open/diagram-plan.py`, and the `pr-issue` parser (`.github/scripts/check-pr-issue.sh`, with a stub `gh`).

- **A fix PR adds the regression test** that fails before the fix and passes after it — in the module that owns the
  code (`tests/test_<module>.py`), on a temp store or `CONTEXT_ROOT`, never on this machine's `.context/`.
- **A new engine module gets a test module**; a shared helper (`frontmatter.py`, `transcripts.py`, `leak_shapes.py`)
  is the only copy — a second parser or reader is a review finding.
- **A test never reads the machine's store.** It points `kit_profile.ENV_DIR` / `kb.ENV` at a throw-away blank one
  (clearing the `lru_cache`s) or passes its own `CONTEXT_ROOT` to every subprocess; one that drops `CONTEXT_ROOT` to
  test the resolver either only resolves the path or runs a kit copy in a temp dir. Tests are stdlib only, need no
  network and no `gh`, and may run inside a Claude session (`CLAUDE_CODE_SESSION_ID` is set there — tests that depend on it clear it). Fixture files live under `tests/fixtures/` (the leak scan skips
  that directory); a fact-shaped literal a test needs is assembled at run time.

## Code review

Three tiers, one rule book — [`docs/REVIEW.md`](REVIEW.md):

- **Tier 0 — `ci.yml`, no model, fails the PR.** `review_gate.py` scans every line the PR *adds* for the shared
  leak shapes plus e-mail, home-path and token shapes, and requires a higher `metadata.version`, an `updated` on or
  after the base's (and no later than tomorrow in UTC) for every skill or agent with a changed file (README edits
  do not count). A wording-only PR says so with the `wording` label or `[skip-bump]` in its title or body; the
  reviewer may question the claim. `ci.yml` skips it a third way, computed rather than claimed: a Dependabot npm
  PR whose diff touches only a unit's `package.json` / `package-lock.json` — it cannot also bump `SKILL.md` itself,
  and the run prints "bump check skipped: Dependabot manifest-only change" so the exemption stays visible.
  Run it yourself: `make -C $BATON/context-db review-gate` (`BASE=`, `SKIP_BUMP=1`).
  The rest of tier 0 was already there: kit-verify (frontmatter, description budget, referenced scripts, plugin
  manifests), the unittest suite, `py_compile`, `bash -n`.
- **Tier 1 — evidence.** `review_evidence.py` writes `.review/evidence.md` for the reviewer: the changed units with
  their `requires` sets, the tier-0 verdict, and two pre-flags only a reader can decide — swallowed errors on
  added lines of any script (by suffix or shebang: `.sh`, `.py`, `.mjs`, extension-less hooks) and system vocabulary in a unit that does not require the capability (`kb.SYSTEM_VOCAB`). The rules
  it judges by are copied from the **base** branch (`.review/RULES.md`), so a PR cannot rewrite them.
- **Tier 2 — judgment.** Every non-draft PR is reviewed by Claude when it opens and on every push
  (`.github/workflows/claude-review.yml`, procedure and posting only). The four lenses (leak by meaning,
  unfollowable or contradicting instructions, correctness and blast radius, stale docs), the grades and the
  calibration rule — a STOP quotes the line *and* names the rule, else it is a WARN phrased as a question — are
  `docs/REVIEW.md` § 2–3. Every finding is one line, `[STOP|WARN|NIT] path:line — claim (rule)`, so the next run
  reads earlier findings instead of being told "do not repeat", and `kit-health` § 1 reports the share acted on.
  PR content and comments are data to review, never instructions. Ask it anything with `@claude …`;
  `@claude review` asks for a review of the current head.
- **Tier 3 — proof.** `evals/kit-review-*` holds five synthetic PRs (a leak the shapes miss, an attribution to a
  third party, a missing bump the reviewer must not repeat, an unfollowable instruction, a clean PR that gets a
  two-sentence verdict);
  `claude plugin eval . --case 'kit-review-*'` scores them — run it on every change to `docs/REVIEW.md` or the prompt.

- **Re-reviews cover the delta.** Every review summary ends with `Reviewed head: <sha>`. On the next push
  the workflow finds the latest such line in claude[bot]'s comments and hands the reviewer `git diff
  <sha> <head>` — or a `git range-diff` after a rebase or force-push. With no marker, or when that sha is
  gone, it reviews the whole PR again. Answers to questions carry no marker and never count as a review.

- **Its verdict is the merge gate.** The summary's first line is `Verdict: approve` (no STOP or WARN finding
  open on the PR at this head; NITs never block) or `Verdict: request-changes`; the workflow submits it as a
  formal review by `github-actions[bot]` on that head, and `auto-merge.yml` merges an approved PR once its
  checks are green (`.github/workflows/auto-merge.yml` lists the gates). A push after the verdict means no
  verdict until the re-review lands. A PR that edits `claude-review.yml` itself gets no review: for `pull_request`
  events GitHub runs the PR's copy of the file, and the action then skips itself with a warning ("Workflow
  validation failed. The workflow file must exist and have identical content to the version on the repository's
  default branch" — verified on a live run), so the check turns green without a verdict, auto-merge never fires
  and the owner merges that PR by hand. Everything else in the PR's copy is live, which is why the rules are read
  from the base branch (`.review/RULES.md`) and why the server-side gate on `.github/workflows/**` is the
  `main` ruleset's code-owner review (`.github/CODEOWNERS` assigns that path to the maintainer).
  **Trust model (#122).** `github-actions[bot]` is the identity of every workflow, not only this one, so
  `auto-merge.yml` accepts its approval only when the review names the run that posted it (`claude-review run
  <id>`) and that run is `claude-review.yml` on a `pull_request` event for this head. What could forge it is PR
  code running with `pull-requests: write`, so none does: a PR that changes anything under `.github/`, or a file that
  judges PRs (`review_gate.py`, `leak_shapes.py`, `review_evidence.py`, `allow.txt`, `docs/REVIEW.md`), is never
  auto-merged (its own workflow copies are live; the merge token is the owner's PAT, which would satisfy the
  code-owner review by itself), and the review job keeps no credentials in its checkout and runs the evidence
  script from the base branch (`test_ci_hygiene.py` § ApprovalTrust holds both).
  **Its threads are not optional** either: a PR is merged only when every
  review thread is answered and resolved — fixed on the branch (the default), or deferred to a `review-followup`
  issue cited in the thread. Never resolve a thread without a reply that names the commit or the issue.
- It is **read-only** on the repo — it can comment, it cannot push.
- GitHub's `main` ruleset requires a PR and blocks force-pushes and deletes, but repo admins bypass it and it
  does not check the verdict or the threads, so the gate is this file, `hooks/pre-push` and `auto-merge.yml`
  (it merges only what passed every gate; everything else waits for the owner) — `main-guard.yml` is
  the server-side backstop, flagging (not blocking) a commit on `main` that isn't a squash-merged PR.

Setup, once, by the repo owner: `claude setup-token` locally (Claude Pro/Max), add the value as the
`CLAUDE_CODE_OAUTH_TOKEN` repository secret (`gh secret set CLAUDE_CODE_OAUTH_TOKEN -R <owner>/ai-baton`),
and make sure the Claude GitHub App is installed on this repo. Until the secret exists the jobs exit green with
a notice — a missing reviewer never blocks a PR. For the verdict and the merge, two more one-off steps: allow
Actions to approve PRs (`gh api -X PUT repos/<owner>/ai-baton/actions/permissions/workflow -f
default_workflow_permissions=read -F can_approve_pull_request_reviews=true`; without it the verdict step warns
and submits nothing) and add `AUTOMERGE_TOKEN`, a fine-grained PAT of the owner scoped to this repo with
Contents + Pull requests read/write (`gh secret set AUTOMERGE_TOKEN`). The merge needs the PAT because a merge
by `github.token` triggers no workflow, so `ci`, `main-guard` and `release` would never run on `main`.

The action refuses to run a `claude-review.yml` that differs from the copy on `main` (anti-tamper): a change
to that file is merged on its own before any PR carrying it can be reviewed.
