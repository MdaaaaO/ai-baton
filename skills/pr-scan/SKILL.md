---
name: pr-scan
description: Sonnet-forked sweep building the user's PR review queue: direct and CODEOWNERS team requests, then open PRs in the configured repos, minus bots, drafts, stale and already-reviewed heads. Returns a ≤8-row table (review state, threads, bot verdict, trivial PRs flagged `A` with an `AUTO:` line) or exactly NO-OP. Arm with `/loop 2h /pr-scan`; hand a row to `pr-review`.
metadata:
  version: "11"
  updated: "2026-09-27"
  reviewed: "2026-09-24"
  facts: "github.display_names"
argument-hint: "[--days N] [--limit N] [--repo owner/name]"
context: fork
agent: triage
model: sonnet
effort: low
---

# pr-scan — one pass over the user's review queue

Working directory: the workspace root. Config `.context/state/pr-review/config.json`; ledger
`.context/state/pr-review/ledger.jsonl` (the script appends to it — you never edit it by hand). You are
read-only on GitHub; the only side effect you cause is the script's `--mark` ledger append.

## Steps

1. Run the sweep (it takes 1–4 minutes; set the Bash timeout to 300000):
   ```sh
   bash $BATON/skills/pr-scan/pr-scan.sh --mark $ARGUMENTS
   ```
   It prints a ranked table and a `summary:` line, and exits **non-zero when any `gh` call failed after
   its retry** (`errors=<n>`; `retries=` counts calls that needed the second attempt, `failed=` the PRs
   dropped for it, `degraded=` rows kept with unknown review state — shown as `?` in `HUMANS`). If
   `errors=` is not 0, `head -20 <out>/errors.txt` (the `out=` run dir in the summary; `latest` is a
   symlink beside it) and mention the failure in the brief — a failed `gh` call is not an empty queue.
   Exit 3 = another sweep holds the lock; answer `NO-OP` and say so in one line.
2. Read the table. Column meanings: `PRIO` 1 = direct request to the user, 2 = follow-up on a PR they
   already reviewed (new head or author replied), 3 = team request with no human review yet,
   4 = swept PR with no human review, 5 = the rest. `*` = first time surfaced. `!` = over the deep
   threshold (600 lines) — a `pr-review --deep` candidate. `A` = passed the trivial-PR auto-approve gate
   (`.auto.eligible` in `queue.json`; the summary line carries `auto=<n> auto_mode=<mode>`). `HUMANS` = last state per human reviewer
   (`APP`, `CHA`, `COM`). `THREADS/BOT` = unresolved threads / review-bot (`github.review_bot`) Assessment on
   this head (`🟢`/`🟡`/`🔴`, `-` if none or no bot configured).
3. Decide **NO-OP vs brief**: if the summary says `new=0`, answer with the single word `NO-OP` — nothing
   else. `new` counts the **shown** rows that were not surfaced before on this head with this kind
   (`--mark` records exactly those), so a follow-up or an `A` row that was already reported is not new
   again; a new head, a new author reply, or a first-time `A` flag is. Otherwise answer with the brief below.

## Answer (exactly this shape — real markdown, NO code fence, so the terminal draws the table)

**PR QUEUE** · YYYY-MM-DD HH:MM UTC · 16 candidates · 10 new · dropped: 33 bots · 1 draft · 49 stale · 9 done · 9 approved · errors 0

| # | PR | Prio | Why now | Size | Humans · Thr · Bot | Author · Title |
|--:|----|------|---------|-----:|:--:|----|
| 1 | [<repo>#<n>](https://github.com/<org>/<repo>/pull/<n>) | 2 · follow-up | new head since your APPROVE | 592/2 | APP · 1 · – | <author> · Migrate the model descriptions to … |
| 2 | [<repo>#<n>](https://github.com/<org>/<repo>/pull/<n>) | 3 · team | via <team>, requested 09-17 | 159/3 | – · 0 · – | <author> · KEY-123: pin the local toolchain binary … |
| 3 | [<repo>#<n>](https://github.com/<org>/<repo>/pull/<n>) | 3 · team ‼ deep | via <team>, 1088 lines | 1088/13 | – · 1 · – | <author> · KEY-456: archive expired usage rows … |

…up to 8 rows, PRIO ascending, newest first within a prio…

**Auto** (shadow) · none
**Follow-up** · [<repo>#<n>](https://github.com/<org>/<repo>/pull/<n>) — you APPROVED `<sha-a>`, author pushed `<sha-b>`
**Next** · `/pr-review <org>/<repo> <pr>` for the row you pick · errors 0

Column rules:
- `#` — row number; `PR` — always a `[repo#n](url)` link, repo without the `<org>/` prefix.
- `Prio` — `<n> · <word>`: `1 · direct`, `2 · follow-up`, `3 · team`, `4 · sweep`, `5 · other`; append ` ‼ deep`
  for `!` (over the deep threshold) and ` ✓ auto` for `A` (passed the trivial gate). Drop the raw `*` marker — first-time
  rows are already counted in `new`.
- `Why now` — one short clause of fact (≤ 40 chars) that adds something the other columns do not: prio 1 → `requested MM-DD`;
  prio 2 → `new head since your APPROVE` / `author replied in N threads`; prio 3 → `via <team>` (one of the env config's `github.owner_teams`);
  prio 4/5 → `opened MM-DD`. Never write "no human review", "no review yet", "bot green" or a thread count here — the
  `Humans · Thr · Bot` column already carries those.
- `Size` — `lines/files` (no spaces).
- `Humans · Thr · Bot` — last state per human reviewer (`APP`/`CHA`/`COM`, comma-joined, `–` if none) · unresolved
  thread count · review-bot Assessment on this head (`🟢`/`🟡`/`🔴`, `–` if none).
- `Author · Title` — first name from the profile's `github.display_names` map (`python3 $BATON/context-db/bin/kit_profile.py get github.display_names`),
  otherwise the login verbatim (never a capitalised login) · title truncated to 45 characters with `…`; never wrap a cell.
- Header line: date **and** time as `YYYY-MM-DD HH:MM UTC` (from `date -u`), counts from the summary line; `dropped:` lists only
  non-zero buckets.
- Trailing lines: `**Auto**` repeats the gate facts only (`class`, `packages`, CI, threads from `queue.json .auto`) or `none`;
  `**Follow-up**` lists every PRIO-2 row with the old and new short SHAs / the thread count, or `none`; `**Next**` carries the
  errors count. Nothing before the header line and nothing after `**Next**`.

Rules: `Why now` is fact from the data, never an opinion on the PR's value. Author names follow the map
in the column rules. Every PR is a clickable link. Do not read PR bodies or diffs — that is `pr-review`'s
job. The `**Auto**` line repeats the gate's facts only — the decision belongs to the main session.

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

## Skip / tune (the user's call, main session executes)

- Skip a PR for its current head: append `{"repo":"<org>/x","pr":N,"head":"<sha>","status":"skipped","ts":"…"}` to the ledger.
- Change repos, freshness window, bot list, or row cap: edit `.context/state/pr-review/config.json`.
- Arm recurring: `/loop 2h /pr-scan` (the fork returns NO-OP on quiet ticks, so the main session
  spends nothing beyond the ~$0.1 Sonnet tick; `/loop 4h /pr-scan` halves that when the queue is calm).
  Disarm by stopping the loop; the ledger keeps state across sessions. Never two sweeps at once — the
  script's `.scan.lock` refuses the second, and the ledger is shared state.
