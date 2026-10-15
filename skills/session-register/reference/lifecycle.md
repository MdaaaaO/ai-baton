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

`session-register` / `session-touch` / `session-end` move an archived file back from `sessions/archive/` before
writing (a re-register reactivates it), so a resumed session can still add a hand-off later — a lane that continues
with follow-on work always leaves a prompt.

`session.py end`/`touch` always leave a `## Open PRs` heading behind (`none` when there is nothing to list); right
after a fresh register it may not exist yet. A duplicate heading is merged on the next touch, the last one winning.

## Fields

`session` (name) · `session_id` (auto: `$CLAUDE_CODE_SESSION_ID` at register — the key a hook matches on) · `ref`
(ListAgents ref — the stable id) · `status` (`active`|`idle`|`ended`) · `epic` (the initiative's tracker key) ·
`repos` · `working_on` (one line) · `responsibilities` · `stats` (auto, § Stats) · `registered` (auto: when the
lane started — set by a register that creates or reactivates the entry, never by touch/end) · `heartbeat` (auto) ·
`updated` (auto). These are operational state, **not** knowledge — the registry is excluded from
`make index`/`make verify` and the doc DB count.
