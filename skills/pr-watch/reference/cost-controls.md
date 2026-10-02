# Cost controls (2026-09-22)

The shell polling is free; what costs is every line emitted and every Monitor expiry (~$0.30 each at a
~120k prefix). Three knobs, on top of the one-process-per-session form in `SKILL.md`:

| Variable | Default | Effect |
|---|---|---|
| `PR_WATCH_SELF` | `$WORKSPACE_GITHUB_LOGIN` (else `gh api user`) | events by that login are dropped as your own |
| `PR_WATCH_SYNC` | `1` | `0` disables the auto `update-branch` (reference/auto-sync.md) |
| `PR_WATCH_SYNC_COOLDOWN` | `3600` | minimum seconds between two syncs of the same PR |
| `PR_WATCH_KNOWN_RED` | unset | extended regex; mutes a `CHECK NOT GREEN` whose failure annotations all match a cause you already know about |
| `PR_WATCH_REPLAY` | unset | `1` re-emits the current bot verdict / `CHECK NOT GREEN` on start; by default a re-arm on a head the state dir already knows is silent about what it already reported |
| `PR_WATCH_RETRY_DELAY` | `2` (seconds) | delay between the 3 attempts a `gh` read gets before it is reported as `LOOKUP FAILED` (tests set it near 0) |
| `PR_WATCH_BACKOFF_MAX` | `86400` (seconds) | cap on the interval between two repeats of the same alarm — an unresolved `CHECK NOT GREEN` on an unchanged head, a `LOOKUP FAILED` that lasts |
| `PR_WATCH_NOW` | unset | overrides "now" for the backoff clock and the auto-sync cooldown timer (tests only) |

- **`CHECK NOT GREEN` is first announced at most once per head** (reset whenever the head moves) and only
  once the suite has settled — no check run still `queued`/`in_progress` — listing every failing check at
  that moment; see below for the backoff that repeats it on an unchanged head. Before
  2026-09-22 it re-fired whenever the *set* of failing names changed, i.e. once per check that finished
  red — ~10 wake-ups for one cause. The rollup mixes check runs with legacy commit statuses (a required
  status context an external CI posts); both count toward pending and toward red — a status context
  carries no `status`/`conclusion` field of its own, only a `state`, which the watcher maps to the same
  tri-state a check run's `conclusion` uses.
- **A failed lookup is never read as red or green.** Every `gh` read an event depends on — PR info,
  behind-by, the reviews (read once per cycle for the sync's approval count, the bot verdict and the
  review listing), the check-status rollup, and the two comment listings (review comments, issue
  comments) — gets 3 attempts, `PR_WATCH_RETRY_DELAY` apart. A read still failing after
  that prints one `PR N LOOKUP FAILED: <what> — <error>` line instead of deriving a `CHECK NOT GREEN`,
  a BEHIND alarm or "nothing new" from data that was never read; the step that needed it is skipped for
  the cycle and tried again on the next. One line per `<what>` per PR: a changed error, or a recovery
  followed by a new failure, announces again; the same failure lasting announces again as
  `… (still failing)` on the backoff below, so an outage is neither one line per cycle nor silent for good.
- **A repeated alarm backs off additively.** A `CHECK NOT GREEN` on an unchanged head, and a
  `LOOKUP FAILED` that lasts, repeat after 1h, then 3h, then 5h … +2h each time, capped by
  `PR_WATCH_BACKOFF_MAX` (default 86400s — once a day; a value below 3600 shortens the first window
  too); the first announcement is immediate. Before
  this a red head was announced once and never again, and before 2026-09-22 on every 120s window.
  A due `CHECK NOT GREEN` reads the checks again before it repeats: it lists what is red at that
  moment, is dropped (a stderr note) when the checks went green, and waits while a re-run is still in
  progress. `PR_WATCH_KNOWN_RED`'s mute is unchanged: a muted head never starts the clock, so it stays
  silent (stderr only, once) for as long as it is muted.
- **`PR_WATCH_KNOWN_RED=<extended-regex>`** — when the red is a known, external cause, pass a regex over
  the *failure annotations*. Before emitting, the watcher fetches `check-runs/<id>/annotations` for every
  failing check run; it suppresses the line (stderr note only) **only if** each failing run has at least
  one `failure` annotation and *all* of them match. A failing check with zero annotations is unexplained →
  the line is emitted. So the regex must cover the generic wrappers too, e.g.
  `PR_WATCH_KNOWN_RED='<package-name>|Process completed with exit code'` mutes a known missing-export
  failure while a PR failing for another reason still reports.
  **A mute that actually suppressed a line on a PR expires on its own**: once that PR's checks are next
  green and settled — the checks it suppressed among them, so a rollup that holds only the first green
  entries after a push does not count — the watcher writes an expiry record (the regex it belongs to) and
  logs one stderr note naming the PR and head — from then on a red on that PR is reported even if its
  annotations still match. Until then the watcher reads that PR's check rollup once per cycle (one extra
  `gh pr view`, no annotation lookup, no repeated line) so it can notice the green. A mute that never suppressed anything there is untouched by a green. The record lives in the
  state dir and belongs to the PR, not to a head: a re-arm keeps it, on the same head or on a new one. A
  changed regex mutes again from scratch, and `PR_WATCH_REPLAY=1` clears it. Dropping the variable
  once the cause is fixed is still the clean end — the expiry just covers the case where it is left set.
- **Silent re-arm.** The per-PR state dir (`${TMPDIR:-/tmp}/pr-watch-<owner>-<repo>-<pr>/`) survives the
  process, so a watcher re-armed on a head it already reported emits nothing until something changes —
  before 2026-09-22 every re-arm replayed the `BOT REVIEW` line for an unchanged head (one wasted wake-up per
  PR per re-arm; a third of one night's burn in the incident behind the park rule in `SKILL.md`). A different
  head, a missing state dir, or `PR_WATCH_REPLAY=1` restores the replay — except when the state's head is the
  PR's live head: then the watcher already followed that move and the argument is only the arming command's
  original head, reused on the re-arm, so the state is kept (re-arming used to report the same move twice). The dir is shared by every session
  on the same machine: a successor taking over a PR inherits the silence and reads the current verdict from the
  predecessor's `## Open PRs` list instead.
