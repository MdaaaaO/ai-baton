# session-register — naming, rename, actor, stats, startup and archive detail

Loaded from `SKILL.md`. Each section below is the full text moved out of the body step it backs.

## Naming convention (step 3)

`NAME` follows the convention **`<lane>-<topic>[-n]`**: lower-case kebab-case, at least two parts, at most 32
characters. The *lane* is the repo or area (`kit`, the repo's short name), the *topic* what you own
(`hardening`, a ticket number, a feature); a successor on the same lane adds `-2`, `-3` (`kit-hardening`,
`kit-216-changelog`, `<repo>-weekly-2`). `session-register` refuses a new name outside it; a successor takes
the name its predecessor's prompt proposes. The name ends every PR body and PR comment you post on your own
PRs (the `footer:` line of the resolved-profile block), so it is public: no person, org or private project in
it — enforced, not just asked: a NEW name is refused when it embeds one of this environment's own
`tracker.repos` (full slug or bare name)/domain names, or its `tracker.key_regex` shape (lower-case included,
e.g. a lane like `key-123-topic`); an existing name already on file is grandfathered. A generic placeholder
still matches the regex and is not exempt — `<lane>-lane-<n>` (e.g. `kit-lane-6`) is not a topic; the *topic*
part must name what you actually own.

## Rename (step 3)

Rename a session already registered under a generic or wrong name instead of leaving it and re-registering
fresh — this moves the file, rewrites its frontmatter and title, and, only when your own backstop heartbeat
was actually running for the old name, restarts it under the new name with the same focus in one step. Only
rename **your own live session** — renaming an ended or someone else's entry moves the file but starts no
heartbeat (there is none of yours to restart), so do that by hand if it still needs one. A loop another
session started under the same name is left running:
```sh
make -C $BATON/context-db session-rename FROM=<old-name> TO=<new-name>
```
`<new-name>` follows the same convention and is refused if another entry already has it.

## Actor naming on ctx writes

**Name every ctx write as yourself (since v0.6.0, #393).** The ctx MCP server writes as one shared process
identity unless a call says otherwise, so a ctx tool call that omits `actor` on a doc your session owns (your
own `sessions/<name>.md`, once registered) is refused `NOT_OWNER`. Pass `actor: <name>` (the name you
registered under) on **every** `ctx_*` write tool call (`ctx_create`, `ctx_str_replace`, `ctx_insert`,
`ctx_delete`, `ctx_rename`, `ctx_log`, `ctx_fm`, `ctx_new`, `ctx_move`, `ctx_maintain`, `ctx_migrate`) — not
only the ones that touch your session file: the store only ever sees the actor a call names, so a write that
leaves it out is an unnamed write, never "this session" by default. The Bash fallback (`ctx_adapter.py ctx
<verb> …`, used when the MCP tools are absent) needs no such flag: it reads your registered name itself
(`kit_profile.py session-name`) and sets `CTX_ACTOR` from it when the environment does not already have one.

## Startup — paste-started successor detail (step 1)

Only the newest `MAX_ENDED` ended sessions get a starter; an older one (still in the fold) is named with a
link to the same path but no starter — open the file the same way. A session that ended with nothing to hand
over (no `## Next session`, or a "no successor" note) is not listed at all — there is nothing to pick up. A
predecessor that ended more than `SESSION_ARCHIVE_DAYS` (7) days ago is no longer in the index — it was swept
to `sessions/archive/<name>.md` (listed in `sessions/archive/INDEX.md`, prompt intact).

## Narrate-back — the headless fallback (step 1)

No tool available (a headless or scheduled run): print the five lines, log them, and proceed only with the
steps the handoff prompt marks standing go; the rest waits in a `Decisions` block.

## Compaction re-grounding

The same hook re-grounds you after a compaction too: its `compact` matcher opens with one line naming your
session file and, when one resolves, its context doc, then your session brief — `session`/`epic`/`working_on`/
`responsibilities` first, `## Open PRs`/`## Open decisions`/`## Assumptions`/`## Owns`/`## Worktrees` ahead of
whatever else you wrote — and the context doc's *Remaining work* head, so a compaction does not cost you
either.

## Backstop heartbeat — mechanism detail (step 2)

It finds the `claude` process that owns this session (the walk also accepts a bare version string as the
comm, the shape the desktop app's own binary reports), detaches itself (`skills/_lib/portable.sh`'s `detach`
— the Bash tool kills its process group after ~10 min otherwise; `setsid` where the host has it, a `python3
os.setsid()` re-exec on macOS/BSD, which ship none), touches your row every 6h while that process is alive,
and marks the row `ended` within a minute of the session dying. So the registry never lies about liveness
*and* no `/loop` wake-up re-bills the prefix for a heartbeat (the old `/loop 8h` backstop cost one
full-prefix turn per tick — retired 2026-09-18). A second start for the SAME session is a no-op, also under
another key: the same name and the same owning process is one loop (pidfile
`$TMPDIR/ai-baton-<uid>/heartbeat-<key>.pid`; log `…heartbeat-<key>.log` beside it, `<key>` your
`$CLAUDE_CODE_SESSION_ID` else your name — namespaced per user and per session, never a bare
`/tmp/heartbeat-<name>.*` shared by everyone on the host, nor one name's pidfile mistaken for another live
session's). Later focus changes still go through `session-touch … WORKING=` — the script's own first tick
only fills a still-blank `working_on`, never overwriting one you already registered.

## Stats fields (step 2b)

- Turns are **API requests** (deduped per `requestId`), not transcript lines — a multi-block reply is one
  turn. Spend is a list-price floor at `SESSION_STATS_PRICES` (default Opus-4-class `15,18.75,1.5,75`
  $/Mtok in/cache-write/cache-read/out) for the **TOTAL** — main session + every subagent transcript summed
  in, same figure the ledger and cost-report use; `session-stats`'s full block breaks the two back out.
  PRs/tickets are *referenced in tool inputs* (touched), not "owned".
- `make -C $BATON/context-db session-stats` prints the full block (window, prompts, token split, top tools,
  delegation, PR/ticket lists, hand-offs) — what `session-handoff` pastes into the wind-down entry.

## Re-arm (step 4)

`session.py end`/`touch` always leave a `## Open PRs` heading behind (`none` when there is nothing to
list) — a predecessor file missing the heading entirely predates that fix and is not the same as "no
PRs": treat it as unknown and check `gh pr list --author @me` before assuming there is nothing to
re-arm. The 30-min Monitor cap, one-Monitor-per-repo rule and the park-after-two-quiet-windows /
park-at-once-on-signoff rules are owner decisions 2026-09-22; registration is not complete until the
watches are up — a PR whose review lands unwatched is the failure this step prevents (owner decision,
2026-09-18).

## Archive timing (step 3, on end)

An ended session **without** a real prompt (nothing on file, or a "no successor" note) does not appear in
the Ended table at all, and is archived out of the index entirely after `SESSION_ARCHIVE_NOPROMPT_HOURS`
(48, never more than `ARCHIVE_DAYS`) — that covers both a clean end with nothing left to do and a crash
(`heartbeat.sh` ends a crashed session the same way, without a prompt); `session-register` / `session-touch`
/ `session-end` move an archived file back from `sessions/archive/` before writing (a re-register
reactivates it), so a resumed session can still add a hand-off later — a lane that continues with follow-on
work always leaves a prompt.
