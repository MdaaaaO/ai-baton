---
name: pr-event-brief
description: "Sonnet-forked triage of one pr-watch event — reads the PR's reviews, unresolved threads, checks and mergeability and returns a ≤10-line brief with exactly one recommended ACTION. Invoke for every BOT REVIEW / NEW comment / NEW review / CHECK NOT GREEN / HEAD MOVED(drift) line a pr-watch Monitor emits; the main session then performs the action."
metadata:
  version: "15"
  updated: "2026-10-01"
  reviewed: "2026-10-01"
  facts: "github.review_bot"
argument-hint: <owner/repo> <pr_number> "<event line>"
arguments: [repo, pr, event]
context: fork
agent: triage
model: sonnet
effort: low
background: false
---

# pr-event-brief — triage one PR event, return a brief

Repo `$repo`, PR `$pr`. Event line from the watcher: `$event`

Not for a `PR <n> LOOKUP FAILED: …` or `ERROR <repo> startup: …` line — that is a failed `gh` read, not a
PR event; there is nothing on the PR to triage. The main session handles it directly (a one-off recovers on
its own; `(still failing)` → stop the watch or fix auth), never forking this skill for it.

You are read-only. Gather the facts below with `gh api …` — run `eval "$(python3 $BATON/context-db/bin/kit_profile.py gh-env)"` once first
(the `github.sandbox_token_prefix` placeholder where a sandbox needs one, nothing where `gh` is logged in; skill `gh-cli`) (paginate every
listing with `--paginate` and `per_page=100`), then answer in the template. Do not post, resolve,
re-request or merge anything — the main session does that.

## Gather

1. `repos/$repo/pulls/$pr` → `head.sha`, `mergeable_state`, `draft`, `requested_reviewers[].login`.
2. Bot verdict — resolve the login exactly like the watcher does (`PR_WATCH_BOT_LOGIN` if set, else
   `kit_profile.py get github.review_bot`), **checking its exit status apart from its value** (a failed
   `kit_profile.py get` must never read the same as "no bot configured"), then pick one of three cases:
   - **No bot on this machine**: the resolved login is empty **and** `$event` is not a `BOT REVIEW …`
     line. Write `BOT: n/a` and skip straight to step 3 — most machines have no review bot; that is the
     default, not a missing fact, and `NEEDS github.review_bot` would be the wrong story here.
   - **Fork resolution failure**: the resolved login is empty **and** `$event` IS a `BOT REVIEW …` line —
     the event only exists because the watcher itself matched a bot login, so an empty value here means
     *this fork* could not resolve it (the env var was set in the watcher's shell only, or a failed
     `kit_profile.py get` read as empty), never "no bot on this machine". Write
     `BOT: unverified (event says: <the event's green/yellow/red>)` and carry on with steps 3–6. Never
     `NEEDS github.review_bot`: that line is reserved for a missing row (`triage` agent rules), and
     `/env-init` would leave the key empty and re-spawn the fork in a loop.
   - **Bot configured and resolved**: `repos/$repo/pulls/$pr/reviews` → the latest review by that login
     **whose `commit_id` equals the current head**: its `### Assessment:` line (🟢/🟡/🔴 + one clause). A
     review on an older head is *stale*; a body without `### Assessment:` is *not a review* (drafter died /
     feedback-ingest run).
   In every case: latest state per human reviewer (APPROVED / CHANGES_REQUESTED / COMMENTED).
3. Unresolved threads **and** `reviewDecision` in one GraphQL call (the MERGE rule below needs
   `reviewDecision`; no REST call above returns it):
   ```
   gh api graphql -f query='query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){pullRequest(number:$n){reviewDecision reviewThreads(first:100){pageInfo{hasNextPage} nodes{isResolved comments(first:1){nodes{databaseId author{__typename login} body}}}}}}}' -F o=<owner> -F r=<repo> -F n=$pr
   ```
   Thread count, and for up to 5 unresolved ones `<databaseId> by <login>: <≤12 words>`. When step 2 landed
   on "Bot configured and resolved" (a login to compare against), capture the author of each unresolved
   thread to check if any are bot-authored: a thread is bot-authored when its `author.login` matches that
   login either exactly or after adding/stripping a trailing `[bot]` (REST logins for an App bot are
   usually `name[bot]`; GraphQL `login` usually drops the suffix — check both spellings), **or** its
   `author.__typename` is `Bot`. When step 2 instead landed on "No bot on this machine" or "Fork resolution
   failure" (no login to compare against), this check simply does not apply — no thread counts as
   bot-authored, whatever its `__typename` says. `first:100` is one page (gh-cli's own pagination trap,
   here on a GraphQL cursor rather than a REST offset): if `pageInfo.hasNextPage` comes back true, the
   count is a floor, not a total — say `≥N threads (more exist, unread)` rather than reporting it as
   complete.
4. `repos/$repo/issues/$pr/comments` — human comments newer than the event, if any (`$WORKSPACE_GITHUB_LOGIN`
   = own, skip; if it is unset in this fork's shell — it is a `settings.local.json` value the watcher's
   shell has, not necessarily exported here — resolve it via `gh api user --jq .login` instead of assuming
   it is exported).
5. `repos/$repo/commits/<head.sha>/check-runs` → not-green completed runs by name (a check
   cancelled right after a draft toggle is normal; say so).
6. Read the `pr-watch` skill § "Rules this encodes" only if you need the merge gates.
7. **Only when `$event` is a real `HEAD MOVED to <sha>` line** (the watcher's own auto-sync merge head is
   tracked silently and emits no event at all, so `$event` is never that case — do not run the check or
   invent a verdict for it): run
   `python3 $BATON/skills/pr-open/diagram-plan.py --pr $repo $pr --check` and read its one stdout line —
   `OK <plan>` → `DIAGRAMS: OK`; `NO MARKER — …` → `DIAGRAMS: NO MARKER`; `DRIFT <old> → <new>` →
   `DIAGRAMS: DRIFT` (keep `<old> → <new>` for the `WHY` slot); `MALFORMED MARKER — <bad>; fresh plan:
   <new>` → `DIAGRAMS: DRIFT` too (`docs/diagrams.md` folds a malformed marker into drift — keep `<bad> →
   <new>` for `WHY`). The exit code is informational only; the drift, no-marker and malformed cases all
   exit 3 alike, so read the printed line, not the code.

## Answer (exactly this shape, ≤10 lines — the code block ONLY, no preamble, no summary after it)

```
PR $pr $repo · head <sha7> · <mergeable_state> · checks <green | N not green: names>
EVENT: <the event line, trimmed>
BOT: <n/a | 🟢/🟡/🔴 "<clause>" on <sha7> | none on this head | stale (on <sha7>) | run without Assessment | unverified (event says: …)>
THREADS: <k unresolved> — <id by login: gist> ; …
HUMANS: <login STATE, …> | none
DIAGRAMS: <OK | DRIFT | NO MARKER>
ACTION: <REPLY+RESOLVE | FIX+PUSH | RE-REQUEST-BOT | UPDATE-BRANCH | MERGE | WAIT(<who/what>) | INVESTIGATE-CHECK | REDRAW>
WHY: <one sentence, the single decisive fact>
```

`DIAGRAMS:` is the brief's one optional line: include it only for a real `HEAD MOVED to <sha>` event
(Gather step 7) and omit it entirely for every other event — the watcher's own auto-sync merge head is
never one of those: it is tracked silently and produces no event to fork this skill on in the first
place. `pr-watch` forks this skill for a `HEAD MOVED` line only once its own check already found `DRIFT`, so in practice
this slot normally reads `DIAGRAMS: DRIFT` with `ACTION: REDRAW`; Gather step 7 still runs the check
itself rather than trust the caller, so a `HEAD MOVED` passed in with an `OK` check (a direct call, not
`pr-watch`'s) gets `DIAGRAMS: OK` and the normal ACTION rules below apply instead.

The main session performs `ACTION` directly only when standing go already covers it; otherwise the
`ACTION` becomes the recommended option of the carousel question it asks the user (`docs/carousel.md`).

Pick one ACTION: `RE-REQUEST-BOT` when a bot is configured, the head has no Assessment, **and** no
unresolved thread is bot-authored (when a bot-authored thread is open, this rule does not fire — the
existing FIX+PUSH / REPLY+RESOLVE rules below decide instead, exactly as they would for a human thread);
`REPLY+RESOLVE` when unresolved threads are the only gate; `UPDATE-BRANCH` when `mergeable_state` is `behind`; `MERGE`
when `reviewDecision` APPROVED + zero unresolved threads + `clean`, **and** (bot configured: 🟢 on the
head) or (`BOT: n/a`: no bot condition at all); `WAIT(<login>)` when a
named human must act; `INVESTIGATE-CHECK` for a real red check; `FIX+PUSH` when the bot or a human
asked for a code change; `REDRAW` whenever `DIAGRAMS:` reads `DRIFT` on a real `HEAD MOVED` event —
this overrides every other rule above for that event (`docs/diagrams.md`'s contract), and `WHY` then
names the check's own `<old> → <new>` (or `<bad> → <new>` on a malformed marker) rather than a fresh
sentence. If a fact could not be fetched, write `unverified` in that slot. A `BOT: n/a`
slot means this machine has no review bot at all — never `RE-REQUEST-BOT`, the merge gate is CI + human
review only. An `unverified` BOT slot
on a `BOT REVIEW on <sha>` event whose `<sha>` (the watcher's short prefix) is a prefix of step 1's `head.sha` —
`startswith`, the way the watcher compares heads — means the bot *has* reviewed this head
(the event line carries its color): decide on the other gates and that color, never `RE-REQUEST-BOT`. If the
head moved after the event (`head.sha` does not start with `<sha>`), the event's color is *stale* — write `BOT: stale (on <sha7>)`
and the normal rules apply; a green from an older head never counts toward `MERGE`.
