# pr-scan — auto-approve and auto-COMMENT detail

Loaded from `SKILL.md`. Each section below is the full text moved out of the body section it backs.

## Trivial-PR auto-approve (owner's standing decision, 2026-09-19 — `shadow` until `auto_approve.shadow_review_due`)

Small docs-only PRs and dependency **patch** bumps (minor only for dev tooling, major never) may be
approved on the user's behalf without a walk. Two layers, both outside this fork:

1. **Gate — deterministic, no model.** `pr-scan.sh` pre-filters on size and runs
   `$BATON/skills/pr-review/scripts/trivial-check.py <repo> <pr>` (config block `auto_approve` in
   `config.json`: **ownership first** — the user must be a requested reviewer, directly or via a team in `owner_teams`
   (the env config's `github.owner_teams`); `src=sweep` rows never run the gate (a sweep row is not a review request); then classes, globs, hard excludes `.github/**`/`.claude/**`/CODEOWNERS, size caps, CI +
   review-bot-green requirement per repo, no human CHANGES_REQUESTED, 0 unresolved threads).
   dependabot/renovate PRs are kept **only** when eligible; lock-only PRs, stacked PRs (base ≠ default branch)
   and Actions bumps are not eligible; `docs_exclude_globs` keeps dbt model docs and `packages.txt`/`constraints.txt`
   out of the docs class. A gate that errors (`error: true`) counts as not eligible and as a sweep error.
2. **Review — Sonnet `auto-runner`.** The main session spawns `auto-runner` per `A` row
   (`pr-review/reference/runner.md` § `--auto`, ≤ 15 turns): docs claims checked against the bundle at the head
   ref, bump release notes read for breaking/behaviour notes, lockfile consistency. Zero findings → `AUTO: approve`;
   anything else → `AUTO: fallback` and the PR goes through the normal queue with its sheet (`shadow_fallback`
   rows stay ordinary rows).

Modes (`auto_approve.mode`): `off` · `shadow` (runner runs, ledger gets `shadow_approve` / `shadow_fallback`,
the main session tells the user one line "would approve …", nothing is posted) · `live` (the main session posts
`APPROVE` via `submit-review.sh --auto` with the runner's body — the script re-runs the gate on the exact head and
refuses unless mode is `live` — then tells the user one line; ledger `auto_approved`).
Kill switch: set `mode` to `off`. This is the one sanctioned exception to `scope.md` "APPROVE is never inferred".

## Unattended auto-COMMENT (owner-decision scope, opt-in — off by default)

Rows this fork flags `C` (config block `auto_comment` in `config.json`: `mode` off/shadow/live, `prios` — direct
requests (`[1]`) by default, `max_per_tick`) are eligible for `pr-review`'s unattended auto-COMMENT path — a
deterministic, gh-call-free pre-filter computed inline in `pr-scan.sh` alongside each row (prio in `prios`, `kind`
not `follow_up`, author not a bot, capped at `max_per_tick` rows). A direct re-request on a PR already reviewed
(`kind=re_review`) is excluded by default — the prior review's prio alone does not carry a re-request onto the
trailer; `auto_comment.include_re_review: true` opts it back in. This fork only computes and reports eligibility
in the `**AUTO-COMMENT**` trailer — it never spawns a runner or decides anything; the full contract (what the main
session does with an eligible row, the `on_stop` policy, the ledger status) is `pr-review/SKILL.md` § Unattended
auto-COMMENT path for direct review requests.
