# Session lifecycle — starters, naming, heartbeat, stats, archiving

Read on demand from `SKILL.md`; each section names the step that cites it.

## Successor starters

(Step 1.) The Ended table's starter line — "Register as the successor of `<name>`; your prompt is in `<path>`
§ Next session." — never carries the prompt's own words. Open `<path>` (relative to the workspace root, e.g.
`.context/sessions/<name>.md`) and read its `## Next session` section.

- Only the newest `MAX_ENDED` ended sessions get a starter; an older one (still in the fold) is named with a link
  to the same path but no starter — open the file the same way.
- A session that ended with nothing to hand over (no `## Next session`, or a "no successor" note) is not listed at
  all — there is nothing to pick up.
- A predecessor that ended more than `SESSION_ARCHIVE_DAYS` (7) days ago, or whose named successor has already
  registered, is no longer in the index — it was swept to `sessions/archive/<name>.md` (listed in
  `sessions/archive/INDEX.md`, prompt intact): the starter's `sessions/<name>.md` path then resolves under
  `sessions/archive/`.

## Naming

(Step 3.) `<lane>-<topic>[-n]`: lower-case kebab-case, at least two parts, at most 32 characters. The *lane* is the
repo or area (`kit`, the repo's short name), the *topic* what you own (`hardening`, a ticket number, a feature); a
successor on the same lane adds `-2`, `-3` (`kit-hardening`, `kit-216-changelog`, `<repo>-weekly-2`).
`session-register` refuses a new name outside it; a successor takes the name its predecessor's prompt proposes.

The name ends every PR body and PR comment on your own PRs, so it is public: no person, org or private project in
it. Enforced, not just asked: a NEW name is refused when it embeds one of this environment's own `tracker.repos`
(full slug or bare name)/domain names, or its `tracker.key_regex` shape (lower-case included, e.g. a lane like
`key-123-topic`); an existing name already on file is grandfathered. A generic placeholder still matches the
regex and is not exempt — `<lane>-lane-<n>` (e.g. `kit-lane-6`) is not a topic; the *topic* part must name what
you actually own.

The session file's body is writable directly (`Edit`) or through the ctx tools — `sessions/` is exempt from the
write deny, and the hook regenerates `SESSION_INDEX.md`. The session type is owned: a ctx write to your own file
must name your actor, or the store refuses it `NOT_OWNER`.

## Rename

(Step 3.) `make -C $BATON/context-db session-rename FROM=<old-name> TO=<new-name>` moves the file, rewrites its
frontmatter and title, and — only when your own backstop heartbeat was actually running for the old name —
restarts it under the new name with the same focus, in one step. Only rename **your own live session**: renaming
an ended or someone else's entry moves the file but starts no heartbeat (there is none of yours to restart), so do
that by hand if it still needs one. A loop another session started under the same name is left running.
`<new-name>` follows the same convention and is refused if another entry already has it.

## Backstop heartbeat

(Step 2.) `heartbeat.sh` finds the `claude` process that owns this session (the walk also accepts a bare version
string as the comm, the shape the desktop app's own binary reports) and detaches itself (`skills/_lib/portable.sh`'s
`detach` — the Bash tool kills its process group after ~10 min otherwise; `setsid` where the host has it, a
`python3 os.setsid()` re-exec on macOS/BSD, which ship none). It touches your row every 6h while that process is
alive and marks the row `ended` within a minute of the session dying, so the registry never lies about liveness and
no `/loop` wake-up re-bills the prefix for a heartbeat.

A second start for the SAME session is a no-op, also under another key: the same name and the same owning process
is one loop (pidfile `$TMPDIR/ai-baton-<uid>/heartbeat-<key>.pid`; log `…heartbeat-<key>.log` beside it, `<key>`
your `$CLAUDE_CODE_SESSION_ID` else your name — namespaced per user and per session, never a bare
`/tmp/heartbeat-<name>.*` shared by everyone on the host, nor one name's pidfile mistaken for another live
session's). Later focus changes still go through `session-touch … WORKING=` — the script's own first tick only
fills a still-blank `working_on`, never overwriting one you already registered.

## Zombie sweep

A session that ran `session-handoff` but whose claude process was never closed is a zombie: its row says `ended`, its
heartbeat loop keeps running (the loop only ends a row when the owner dies) and the live process pins the transcript's
mtime, so a "move transcripts idle > N days" cleanup matches nothing and a dedicated transcript volume fills (#655).
`zombie-sweep.sh` beside `heartbeat.sh` reads the mapping the heartbeat logs already hold — each log's last `start
name=<name> owner=<pid>` line. One process may own several logs (a handoff, then a new session registered in the same
pane; an earlier key), so the verdict is per owner, judged across every log that names it. A log whose heartbeat loop
is gone never vouches for a kill, yet an active row it names still keeps the owner `LIVE` (a loop can die under a
working session, after an earlier handoff in the same pane) while a missing row from it proves nothing (a name renamed
away, a log from before a reboot). One line per owner, every name its logs carry comma-joined and `row=` their
statuses in the same order: `ZOMBIE` (owner alive, a heartbeat loop alive, every row `ended` or archived, a dead-loop
log's missing one aside), `LIVE` (any row active, or this script's own session), `UNREGISTERED` (a live-loop name with
no row in `sessions/` or `sessions/archive/`, and none active), `STALE` (every loop is gone, so the logs may predate a
reboot and the pid belong to anyone), `REUSED` (the pid's comm is no longer claude-shaped), `DEAD` (owner gone; the
loop ends the row itself) or `UNMAPPED` (no start line), then a summary; exit 3 when it listed a zombie. Only `--kill`
sends SIGTERM, and only to `ZOMBIE` owners — never to an unmapped, stale, reused or unregistered pid (an unregistered
pane may be the user's own terminal), never to an owner with any active row, and never to this script's own ancestry;
the heartbeats then end the rows on their own, final stats written, within a minute. `kit-health` § 4 runs the list
mode and names `--kill` as the Fix; `session-handoff` step 13 is what keeps a session from becoming one.

## Stats

(§ 2b.) The `stats:` line is derived from the session transcript (`~/.claude/projects/*/<session-id>.jsonl`, found
via `$CLAUDE_CODE_SESSION_ID`; engine `$BATON/context-db/bin/session_stats.py`):

`<n> turns · <h>h · ctx peak/avg · cache-read · out · ~$ (list price) · compactions · tool calls ·
PRs referenced (gh pr create calls) · tickets referenced (created / comments / transitions) · sign
jobs · Slack drafts`

- Turns are **API requests** (deduped per `requestId`), not transcript lines — a multi-block reply is one turn.
  Spend is a list-price floor at `SESSION_STATS_PRICES` (default Opus-4-class `15,18.75,1.5,75` $/Mtok
  in/cache-write/cache-read/out) for the **TOTAL** — main session + every subagent transcript summed in, same
  figure the ledger and cost-report use; `session-stats`'s full block breaks the two back out. PRs/tickets are
  *referenced in tool inputs* (touched), not "owned".
- `make -C $BATON/context-db session-stats` prints the full block (window, prompts, token split, top tools,
  delegation, PR/ticket lists, hand-offs). `NOSTATS=1` skips the refresh; `SESSION_ID=<uuid>` derives stats for
  another session.
- On `session-end` the block is written under `## Session stats` in your session file and one row is appended to
  `.context/sessions/_ledger.md` (cross-session ledger; `_`-prefixed files are skipped by the index).
- The `split hint: …` suffix appears once the average prefix over the last `SESSION_STATS_SPLIT_WINDOW` turns
  (default 20) reaches `SESSION_STATS_SPLIT_THRESHOLD` tokens (default 150k) — never on the strength of turns from
  before the last auto-compact.

## End and archiving

(`SKILL.md` § 3.) The Ended table gets a starter that points at your `## Next session` prompt, never the
prompt's own words. An ended session **without** a real prompt (nothing on file, or a "no successor" note) does not
appear in the Ended table at all, and is archived out of the index after `SESSION_ARCHIVE_NOPROMPT_HOURS` (48,
never more than `ARCHIVE_DAYS`) — that covers both a clean end with nothing left to do and a crash (`heartbeat.sh`
ends a crashed session the same way, without a prompt). A session whose prompt names a successor (`Register as
<name>`) is archived as soon as that successor has registered (it registered after this session ended; a lane of
that name already live by then never counts, however often it touches): the starter's `sessions/<name>.md` path
then resolves under `sessions/archive/`, age aside.

A predecessor that died without a handoff (host restart: no `session-end`, so its row stays `active ⚠ STALE`) is
ended by its successor: `session.py register --successor-of <name>` marks an `active` row with no heartbeat for over
12 hours, whose owner process (the one its heartbeat log names, as `zombie-sweep.sh` reads it) is gone, `ended` with
`ended_reason: superseded by <successor>`, writes the stats block and ledger row `session-end` would have (when the row
has a `session_id` and a transcript), and prints one line saying so. The archive rule takes such a row at once, with
or without a `## Next session` prompt: the successor that superseded it consumed the hand-off. A row whose owner
process still runs (whatever the heartbeat age), a fresh heartbeat, a missing heartbeat, or a status other than
`active` stays as it is: the register prints a line saying why, and `session.py end` is the way out. `ended_reason`
is cleared if that lane registers again.

`session-register` / `session-touch` / `session-end` move an archived file back from `sessions/archive/` before
writing (a re-register reactivates it), so a resumed session can still add a hand-off later — a lane that continues
with follow-on work always leaves a prompt.

`session.py end`/`touch` always leave a `## Open PRs` heading behind (`none` when there is nothing to list); right
after a fresh register it may not exist yet. A duplicate heading is merged on the next touch, the last one winning.

## Fields

`session` (name) · `session_id` (auto: `$CLAUDE_CODE_SESSION_ID` at register — the key a hook matches on) · `ref`
(ListAgents ref — the stable id) · `status` (`active`|`idle`|`ended`) · `state` (`standby`|`retired`, optional; retired keeps `status: active`) · `expires` (standby only: last heartbeat + `swarm.standby_hours`) · `confirmed` (when `working_on` was last set; older than 12 h at a re-register marks it `unconfirmed: <old>`) · `epic` (the initiative's tracker key) ·
`repos` · `working_on` (one line) · `responsibilities` · `stats` (auto, § Stats) · `registered` (auto: when the
lane started — set by a register that creates or reactivates the entry, never by touch/end) · `heartbeat` (auto) ·
`updated` (auto). These are operational state, **not** knowledge — the registry is excluded from
`make index`/`make verify` and the doc DB count.

## Retire and `owns:`

`session.py retire [--dry-run]` (run by the zombie sweep's `--kill` mode; the list mode only previews it) sets
`state: retired` on every `standby` row past `expires` and prints `owns: <tickets, PRs>` for each, taken from the
swarm ledgers and `sessions/<name>/prs.tsv`, so the owner of the leftovers is visible. A row that ends by
supersession prints the same line. Rename and archive carry `sessions/<name>/` (the `prs.tsv`) with the row.

## Liveness and failover

**A successor orchestrator re-briefs.** For each worker in the `live assignments` block send one
`ASSIGN <ticket> rebrief=1` (SendMessage by the worker's ref) and wait for its ack. A worker whose registry
heartbeat is under 5 minutes old is live (re-brief once); an older heartbeat means it ended with no return:
`swarm.py resume` classifies its row (drive-to-merge when a PR is open, else respawn in place or reassign), no
re-brief. No registry row: `ListAgents` decides. Standby, retired and ended workers are skipped.
**A resume re-confirms.** Re-registering a live row without `WORKING=` whose `working_on` was not touched within
12 hours marks it `unconfirmed: <old>` and prints a line asking for `WORKING=`: restate the current work, or
`session-touch` it, before trusting it; never keep a stale `working_on` silently.
**One process per session id.** A register of the same session id under a new `ref` while the row's heartbeat is
under 5 minutes old prints `DUPLICATE REGISTER` naming both refs: the second process stands down (tell the
owner, do no work) and leaves the row to the first.
A parked session registers `STATE=standby`; the row carries `expires` (`swarm.standby_hours`, default 24 h from
the last heartbeat) and the zombie sweep's `--kill` mode retires it past that (`session.py retire`).
