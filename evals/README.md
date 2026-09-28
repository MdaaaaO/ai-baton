# evals/ — the eval suite, one case per directory, per skill

Cases live **outside** `skills/<name>/` so the installed skill stays lean (docs/contributing.md § Skills). The format is
the one `claude plugin eval` runs: `evals/<skill>-<case>/prompt.md` (frontmatter `max_turns`, `timeout_seconds`,
`allowed_tools`, `tags`; the body is what the user says) plus `graders/*.md` (frontmatter `type` — `llm`, `tool_used`,
`regex`, … — and `weight`; an `llm` grader's body is the pass criterion). `claude plugin eval init --bare
<skill>-<case>` writes a blank case; the plugin manifest names this directory (`"experimental": {"evals": "evals"}`)
and the runner writes `evals/results/` (not committed).

**Trigger suites.** Every **new or changed** skill ships ≥ 10 trigger cases, at least three positives and three
**same-domain near misses** (`EVAL_MIN_CASES` / `EVAL_MIN_EACH` lower the bar locally while a new suite is built up; CI uses the defaults) (the description's "Not for …" clause, or a neighbouring skill's own trigger, as a case that must not fire).
Most existing skills predate the suite and have none yet — `eval_check.py` notes that rather than failing the PR,
until `REQUIRE_ALL=1` flips it to a gate (below).
A trigger case is tagged `trigger` plus `positive` or `near-miss` and carries two graders: `fired.md`, a
`tool_used` check on the `Skill` call naming the skill — its `input_match` must match the skill and not a longer name that
contains it, e.g. `'(?<![\w-])<skill>(?![\w-])'` (`min: 1` for a positive, `min: 0` + `max: 0` for a near miss,
`arm: both` so it is scored), and `criteria.md`, an `llm` grader on the outcome. `docs/templates/evals/` holds one of
each — copy here, rename to `<skill>-<case>`, fill the `<…>` marks (the scaffold sits under `docs/` so the runner never
scores a placeholder). No case carries a real id, host, person or organisation — placeholders only (`acme/widgets`,
`ABC-123`, `#42`). Suites so far: `pr-open`, `ticket-update`, `session-handoff`, `session-retro`; `kit-review-*` are the reviewer's
proof cases (`docs/REVIEW.md`), not a trigger suite.

| run | what | tokens |
|---|---|---|
| `make -C $BATON/context-db eval-check` | every case loads (prompt keys, grader types), every trigger suite has ≥ 10 cases with both kinds; skills without a suite and descriptions without "Use when" are notes (`REQUIRE_ALL=1` fails on them). Part of `make ci` and `ci.yml` | none |
| `make -C $BATON/context-db eval SKILL=<skill> [MODEL=<id>] [RUNS=<n>]` | `claude plugin eval . --ablation none --case '<skill>-*' --no-publish` — the trigger rate on your machine | yes |
| `evals` workflow (Actions → evals → Run workflow; inputs `skill`, `models`) | the same run on a hosted runner with the `CLAUDE_CODE_OAUTH_TOKEN` secret, one pass per model; the output is the job summary | yes |

A change to a skill's `description:` cites a green run of that skill's suite (the workflow or `make eval`) in its PR:
the suite is the acceptance test for shortening or rewording a trigger.
