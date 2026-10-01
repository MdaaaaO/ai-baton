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
| `PR_WATCH_RETRY_DELAY` | `2` (seconds) | delay between the 3 attempts `gh_retry` makes on a verdict-driving `gh` read before it gives up and reports `LOOKUP FAILED` (tests set it near 0) |
| `PR_WATCH_BACKOFF_MAX` | `86400` (seconds) | cap on the additive backoff interval that re-announces an unresolved `CHECK NOT GREEN` on the same settled head |
| `PR_WATCH_NOW` | unset | overrides "now" for the `CHECK NOT GREEN` backoff clock and the auto-sync cooldown timer (tests only) |

- **`CHECK NOT GREEN` is first announced at most once per head** (reset whenever the head moves) and only
  once the suite has settled — no check run still `queued`/`in_progress` — listing every failing check at
  that moment; see below for the additive backoff that re-announces it on an unchanged head. Before
  2026-09-22 it re-fired whenever the *set* of failing names changed, i.e. once per check that finished
  red — ~10 wake-ups for one cause. The rollup mixes check runs with legacy commit statuses (a required
  status context an external CI posts); both count toward pending and toward red — a status context
  carries no `status`/`conclusion` field of its own, only a `state`, which the watcher maps to the same
  tri-state a check run's `conclusion` uses.
- **A failed lookup is never read as red or green.** Every `gh` read a verdict depends on — PR info,
  behind-by, approvals (sync check), the check-status rollup — goes through `gh_retry` (3 attempts,
  `PR_WATCH_RETRY_DELAY` apart) first. A read still failing after retries prints one
  `PR N LOOKUP FAILED: <what> — <error>` line instead of deriving a `CHECK NOT GREEN` or BEHIND alarm
  from data that was never read, and the sync/CHECK logic for that call is skipped for the cycle (retried
  next cycle instead). Deduped per `<what>` per PR: the same failure persisting across cycles announces
  once; a changed error, or a recovery followed by a new failure, announces again.
- **Once announced, `CHECK NOT GREEN` on an unchanged settled head backs off additively** instead of
  firing once-ever (before this) or on every 120s window (the pre-2026-09-22 behaviour): re-announced
  after 1h, then 3h, then 5h … +2h each time, capped by `PR_WATCH_BACKOFF_MAX` (default 86400s — once a
  day). The first announcement for a head is still immediate. `PR_WATCH_KNOWN_RED`'s mute is unchanged by
  this: a muted head never starts the backoff clock, so it stays silent (stderr only, once) for as long
  as it is muted.
- **`PR_WATCH_KNOWN_RED=<extended-regex>`** — when the red is a known, external cause, pass a regex over
  the *failure annotations*. Before emitting, the watcher fetches `check-runs/<id>/annotations` for every
  failing check run; it suppresses the line (stderr note only) **only if** each failing run has at least
  one `failure` annotation and *all* of them match. A failing check with zero annotations is unexplained →
  the line is emitted. So the regex must cover the generic wrappers too, e.g.
  `PR_WATCH_KNOWN_RED='<package-name>|Process completed with exit code'` mutes a known missing-export
  failure while a PR failing for another reason still reports. Drop the variable once the cause is fixed.
- **Silent re-arm.** The per-PR state dir (`${TMPDIR:-/tmp}/pr-watch-<owner>-<repo>-<pr>/`) survives the
  process, so a watcher re-armed on a head it already reported emits nothing until something changes —
  before 2026-09-22 every re-arm replayed the `BOT REVIEW` line for an unchanged head (one wasted wake-up per
  PR per re-arm; a third of one night's burn in the incident behind the park rule in `SKILL.md`). A different
  head, a missing state dir, or `PR_WATCH_REPLAY=1` restores the replay — except when the state's head is the
  PR's live head: then the watcher already followed that move and the argument is only the arming command's
  original head, reused on the re-arm, so the state is kept (re-arming used to report the same move twice). The dir is shared by every session
  on the same machine: a successor taking over a PR inherits the silence and reads the current verdict from the
  predecessor's `## Open PRs` list instead.
