---
name: pr-scan
description: "Sonnet-forked sweep for the review queue: direct and CODEOWNERS team requests, then open PRs minus bots, drafts, stale, already-reviewed heads. Returns a <=8-row brief in 5 ordered State sections, or NO-OP; hand a row to `pr-review`. Use when sweeping the review queue, often armed with `/loop 2h /pr-scan`."
metadata:
  version: "20"
  updated: "2026-10-02"
  reviewed: "2026-10-02"
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
never writes it. The one write this skill causes (`--mark-only --run <dir>`, appending `surfaced` rows via
`ledger-append.sh`) happens outside the fork, in the main session's own step — triggered by the
self-executing `MARK` trailer this fork's own answer ends with, never by prose in this file reaching the
main session directly (`context: fork` means only this fork ever reads anything below this paragraph;
§ Steps step 3 and § Answer carry the trailer's exact shape).

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
   with the brief below, always ending in the self-executing `MARK` trailer (§ Answer): `context: fork`
   means the main session never reads this file, so the instruction to mark has to travel inside the
   fork's own returned text, not as prose here. Write the real `out=` dir from step 1's summary line into
   the trailer (never the literal placeholder) — that is the exact run this brief came from, not whatever
   `latest` happens to point at by the time the main session gets to it. The main session then runs the
   trailer verbatim as one shell command; a run dir marks at most once (a `.marked` stamp beside
   `queue.json` makes a second `--mark-only --run` on the same run a safe no-op), so it can run the trailer
   even when unsure whether a prior tick already did.

## Answer (exactly this shape — real markdown, NO code fence, so the terminal draws the table)

A brief always ends with one self-executing `MARK` trailer line — the only place this skill's one write
(`--mark-only --run <dir>`) gets triggered, since `context: fork` means the main session never reads
anything above this line. Write the real `out=` value from step 1's summary line into it (never the
literal `<out dir>` shown below), single-quoted — run dirs are `mktemp -d` output under `$BASE_OUT`, so
quoting is for shell-safety, not because the value is untrusted:
```
MARK — run: bash $BATON/skills/pr-scan/pr-scan.sh --mark-only --run '<out dir>'
```
(shown with a placeholder here only to name the shape; what you return has the real path.) `NO-OP` carries
no trailer — nothing was shown, so there is nothing to mark.

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
MARK — run: bash $BATON/skills/pr-scan/pr-scan.sh --mark-only --run '<out dir>'

Column rules (same meaning in every section): full list in `reference/answer-format.md`.

Rules: `Why now` is fact from the data, never an opinion on the PR's value. Author names follow the map
in the column rules. Every PR is a clickable link. Do not read PR bodies or diffs — that is `pr-review`'s
job. The `**Auto**` and `**AUTO-COMMENT**` lines repeat the gate's facts only — the decision belongs to the main session.

## Trivial-PR auto-approve (owner's standing decision, 2026-09-19 — `shadow` until `auto_approve.shadow_review_due`)

Rows flagged `A` are eligible for approval on the user's behalf without a walk: a deterministic gate
(`trivial-check.py`, outside this fork) filters on ownership, class/globs/excludes, size, CI + review-bot-green,
and threads; eligible rows then get a Sonnet `auto-runner` pass that returns `AUTO: approve` or `AUTO: fallback`.
Mode (`auto_approve.mode`: `off`/`shadow`/`live`) controls whether that posts. Kill switch: `mode: off`; the one
sanctioned exception to `scope.md` "APPROVE is never inferred". Full gate rules and the two layers: `reference/auto-paths.md` § Trivial-PR auto-approve.

## Unattended auto-COMMENT (owner-decision scope, opt-in — off by default)

Rows this fork flags `C` (config block `auto_comment` in `config.json`) are eligible for `pr-review`'s unattended
auto-COMMENT path — a deterministic, gh-call-free pre-filter computed inline in `pr-scan.sh`. This fork only
computes and reports eligibility in the `**AUTO-COMMENT**` trailer — it never spawns a runner or decides anything.
Full pre-filter rules: `reference/auto-paths.md` § Unattended auto-COMMENT; the full contract (what the main
session does with an eligible row, the `on_stop` policy, the ledger status): `pr-review/SKILL.md` § Unattended
auto-COMMENT path for direct review requests.

## Skip / tune (the user's call, main session executes)

- Skip a PR for its current head: append `{"repo":"<org>/x","pr":N,"head":"<sha>","status":"skipped","ts":"…"}` to the ledger.
- Change repos, freshness window, bot list, or row cap: edit `.context/state/pr-review/config.json`.
- Arm recurring: `/loop 2h /pr-scan` — the default cadence (the fork returns NO-OP on quiet ticks, so the
  main session spends nothing beyond the ~$0.1 Sonnet tick); widen the interval, e.g. `/loop 4h /pr-scan`,
  to halve that further when the queue is calm.
  Disarm by stopping the loop; the ledger keeps state across sessions. Never two sweeps at once — the
  script's `.scan.lock` refuses the second, and the ledger is shared state.
