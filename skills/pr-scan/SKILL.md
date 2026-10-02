---
name: pr-scan
description: "Sonnet-forked sweep for the review queue: direct and CODEOWNERS team requests, then open PRs in configured repos minus bots, drafts, stale, already-reviewed heads. Returns a ≤8-row brief in 5 ordered sections by per-row State (Needs you, New, Handled this tick, Follow-up, Watching) or exactly NO-OP. Arm with `/loop 2h /pr-scan`; hand a row to `pr-review`."
metadata:
  version: "19"
  updated: "2026-10-02"
  reviewed: "2026-09-27"
  facts: "github.display_names"
argument-hint: "[--days N] [--limit N (enrich cap, not row count — max_rows caps rows)] [--repo owner/name]"
context: fork
agent: triage
model: sonnet
effort: low
---

# pr-scan — one pass over the user's review queue

Working directory: the workspace root. Config `.context/state/pr-review/config.json`; ledger
`.context/state/pr-review/ledger.jsonl`. You are read-only on GitHub **and** on the ledger — this fork
never writes it. The one write this skill causes (`--mark-only`, appending `surfaced` rows via
`ledger-append.sh`) happens outside the fork, in the main session's own step after it reads your brief
(§ Steps, step 3).

## Steps

1. Run the sweep in the foreground (wall time ~3–4 minutes; set the Bash timeout to 300000). This step
   runs inside the forked triage worker itself, and steps 2–3 below need the table, the `summary:` line
   and the exit code the script produces on completion — backgrounding it here would return before that
   output exists. ("Run under Monitor or in the background, never foreground" is the right advice only
   for a main session invoking `pr-scan.sh` directly, outside this fork; nothing in this skill currently
   does that.)
   ```sh
   bash $BATON/skills/pr-scan/pr-scan.sh $ARGUMENTS
   ```
   It prints a ranked table and a `summary:` line, and exits **non-zero when any `gh` call failed after
   its retry** (`errors=<n>`; `retries=` counts calls that needed the second attempt, `failed=` the PRs
   dropped for it, `degraded=` rows kept with unknown review state — shown as `?` in `HUMANS`). If
   `errors=` is not 0, `head -20 <out>/errors.txt` (the `out=` run dir in the summary; `latest` is a
   symlink beside it) and mention the failure in the brief — a failed `gh` call is not an empty queue.
   Exit 3 = another sweep holds the lock; answer `NO-OP` and say so in one line.
   `--limit N` caps how many candidates get *enriched* (the 3 per-candidate `gh` calls) — it is not a cap
   on rows shown or counted as new; `max_rows` in `config.json` is what trims the table (`.[:$m]` in
   `queue.json`'s own prio order).
2. Read the table. Column meanings: `PRIO` 1 = direct request to the user, 2 = follow-up on a PR they
   already reviewed (new head or author replied), 3 = team request with no human review yet,
   4 = swept PR with no human review, 5 = the rest, 6 = the user's own review already sits on this head and
   nothing waits on them (`kind=done`: kept while their APPROVE / REQUEST_CHANGES stands on an open PR, and
   once for a review the kit posted; it never counts as new). `*` = first time surfaced. `!` = over the deep
   threshold (`deep_lines` in `config.json`, 600 by default) — a `pr-review --deep` candidate. `A` = passed the trivial-PR auto-approve gate
   (`.auto.eligible` in `queue.json`; the summary line carries `auto=<n> auto_mode=<mode>`). `C` = eligible
   for the unattended auto-COMMENT path (`.auto_comment.eligible` in `queue.json`; the summary line carries
   `auto_comment=<n> auto_comment_mode=<mode>`; § Unattended auto-COMMENT below — off by default). `HUMANS` = last state per human reviewer
   (`APP`, `CHA`, `COM`). `THREADS/BOT` = unresolved threads / review-bot (`github.review_bot`) Assessment on
   this head (`🟢`/`🟡`/`🔴`, `-` if none or no bot configured). `STATE` = `.state_label` from `queue.json`
   (`row-state.py`, no `gh` call) — the row's state from its kind, the user's own review on this head and the
   ledger, and the section it renders in below.
3. Decide **NO-OP vs brief**: if the summary says `new=0`, answer with the single word `NO-OP` — nothing
   else. `new` counts the **shown** rows that were not surfaced before on this head with this kind
   (`--mark-only` records exactly those), so a follow-up or an `A` row that was already reported is not new
   again; a new head, a new author reply, or a first-time `A` flag is. A prio-6 row is never new — the
   user's own review is no reason for a brief; it only rides along when something else is. Otherwise answer
   with the brief below. Once this fork returns, the main session — never this fork — runs
   `bash $BATON/skills/pr-scan/pr-scan.sh --mark-only` on the same run (the `out=` dir from step 1, via its
   `latest` symlink) to record the shown rows as surfaced; a second `--mark-only` on the same run is a safe
   no-op (a `.marked` stamp beside `queue.json` guards it), so the main session can call it even when unsure
   whether it already did.

## Answer (exactly this shape — real markdown, NO code fence, so the terminal draws the table)

**PR QUEUE** · YYYY-MM-DD HH:MM UTC · 16 candidates · 10 new · dropped: 33 bots · 1 draft · 49 stale · 9 done · 9 approved · errors 0

Five sections, always in this order — **Needs you** · **New — not started** · **Handled this tick** ·
**Follow-up — manual** · **Watching** — each `.state`/`.section` off `queue.json` groups rows into (never
recompute the grouping here). Each section is its own table, same columns as before plus a trailing
`State` column; an empty section prints `none` instead of an empty table. The ≤8-row cap (`max_rows`) is
on the **total** rows shown across all five sections, `.[:$m]` in `queue.json`'s own prio order — section
order groups what's shown, it never re-ranks which rows make the cut.

What each section holds: **Needs you** — a STOP hold, or the author replied in a thread the user opened
(whatever verdict they left). **New — not started** — never reviewed, plus re-requests the auto path covers.
**Handled this tick** — a review the kit posted on this head since the last brief (shown once, then dropped as
done). **Follow-up — manual** — reviewed on an older head; nothing acts unless asked. **Watching** — the
user's APPROVE or REQUEST_CHANGES stands on this head and the PR is still open: the next move is the author's.

**Needs you**
| # | PR | Prio | Why now | Size | Humans · Thr · Bot | Author · Title | State |
|--:|----|------|---------|-----:|:--:|----|----|
| 1 | [<repo>#<n>](https://github.com/<org>/<repo>/pull/<n>) | 2 · follow-up | author replied in 1 thread | 592/2 | APP · 1 · – | <author> · Migrate the model descriptions to … | needs you (author replied) |

…or `none`…

**New — not started**
| # | PR | Prio | Why now | Size | Humans · Thr · Bot | Author · Title | State |
|--:|----|------|---------|-----:|:--:|----|----|
| 2 | [<repo>#<n>](https://github.com/<org>/<repo>/pull/<n>) | 3 · team | via <team>, requested 09-17 | 159/3 | – · 0 · – | <author> · KEY-123: pin the local toolchain binary … | new (manual) |

…or `none`…

**Handled this tick** — or `none`
**Follow-up — manual** — or `none`
**Watching** — or `none`

**Auto** (shadow) · none
**AUTO-COMMENT** (off) · none
**Next** · `/pr-review <org>/<repo> <pr>` for the row you pick · errors 0

Column rules (same meaning in every section):
- `#` — row number, counting across sections (not restarting per section); `PR` — always a `[repo#n](url)`
  link, repo without the `<org>/` prefix.
- `Prio` — `<n> · <word>`: `1 · direct`, `2 · follow-up`, `3 · team`, `4 · sweep`, `5 · other`, `6 · reviewed`; append ` ‼ deep`
  for `!` (over the deep threshold) and ` ✓ auto` for `A` (passed the trivial gate). Drop the raw `*` marker — first-time
  rows are already counted in `new`.
- `Why now` — one short clause of fact (≤ 40 chars) that adds something the other columns do not: prio 1 → `requested MM-DD`;
  prio 2 → `new head since your APPROVE` / `author replied in N threads`; prio 3 → `via <team>` (one of the env config's `github.owner_teams`);
  prio 4/5 → `opened MM-DD`; prio 6 → `your review on this head`. Never write "no human review", "no review yet", "bot green" or a thread count here — the
  `Humans · Thr · Bot` column already carries those.
- `Size` — `lines/files` (no spaces).
- `Humans · Thr · Bot` — last state per human reviewer (`APP`/`CHA`/`COM`, comma-joined, `–` if none) · unresolved
  thread count · review-bot Assessment on this head (`🟢`/`🟡`/`🔴`, `–` if none).
- `Author · Title` — first name from the profile's `github.display_names` map (`python3 $BATON/context-db/bin/kit_profile.py get github.display_names`),
  otherwise the login verbatim (never a capitalised login) · title truncated to 45 characters with `…`; never wrap a cell.
- `State` — `.state_label` verbatim (e.g. `handled (review #123456)`, `needs you (STOP)`, `new (auto)`, `follow-up (manual)`, `watching (you approved)`).
- Header line: date **and** time as `YYYY-MM-DD HH:MM UTC` (from `date -u`), counts from the summary line; `dropped:` lists only
  non-zero buckets.
- Trailing lines: `**Auto**` repeats the gate facts only (`class`, `packages`, CI, threads from `queue.json .auto`) or `none`;
  `**AUTO-COMMENT**` is `(<auto_comment_mode>) · ` then every eligible row as a `[repo#n](url)` link (comma-joined) or `none` —
  read `.auto_comment.eligible` off `queue.json`, never recompute the gate here; `**Next**` carries the errors count. Nothing
  before the header line and nothing after `**Next**`.

Rules: `Why now` is fact from the data, never an opinion on the PR's value. Author names follow the map
in the column rules. Every PR is a clickable link. Do not read PR bodies or diffs — that is `pr-review`'s
job. The `**Auto**` and `**AUTO-COMMENT**` lines repeat the gate's facts only — the decision belongs to the main session.

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

## Skip / tune (the user's call, main session executes)

- Skip a PR for its current head: append `{"repo":"<org>/x","pr":N,"head":"<sha>","status":"skipped","ts":"…"}` to the ledger.
- Change repos, freshness window, bot list, or row cap: edit `.context/state/pr-review/config.json`.
- Arm recurring: `/loop 2h /pr-scan` — the default cadence (the fork returns NO-OP on quiet ticks, so the
  main session spends nothing beyond the ~$0.1 Sonnet tick); widen the interval, e.g. `/loop 4h /pr-scan`,
  to halve that further when the queue is calm.
  Disarm by stopping the loop; the ledger keeps state across sessions. Never two sweeps at once — the
  script's `.scan.lock` refuses the second, and the ledger is shared state.
