---
name: pr-review
description: "Reviews another's PR as the user: snapshot, trap KB, Opus pass, walks findings, posts one review, replies in threads; a trivial PR auto-approves via Sonnet, an opt-in auto-COMMENT path skips the walk. Use when reviewing a PR pr-scan surfaces or the user names; never the user's own."
metadata:
  version: "44"
  updated: "2026-10-15"
  reviewed: "2026-10-15"
  facts: "systems.jira,systems.datalake,systems.slack,tracker.mcp_tools.search,datalake.mcp_tools.probe"
argument-hint: "<owner/repo> <pr> [--deep] [--post] [--local]"
---

# pr-review — review a PR, post it as the user, grow the repo KB

The user is the reviewer of record: everything posted carries their name. The model drafts, verifies and
proposes; **the user walks every finding before anything is posted** (step 5, the Post / Deep dive / Body only /
Skip rhythm of `notion-page-review`). `--post` (or "review and post") skips that gate for a COMMENT with no
STOP finding; APPROVE / REQUEST_CHANGES are always the user's explicit call.

References in `reference/`: `runner.md` (where each step runs, spawn, return), `walk.md` (steps 5–7 in
full), `scope.md` (classes, STOP/WARN/NIT, event policy, follow-up ledger), `review-writing.md`, `slop.md`,
`verify.md`, `stakeholder-impact.md`, `kb-writeback.md`, `auto-approve.md`. State: `.context/state/pr-review/`
(config, ledger, `.submitted/`; override with `PR_REVIEW_HOME`). GitHub facts (login, `github.org`,
`github.bots`) come from the env store via `kit_profile.py review-facts`, never from `config.json`.
**Environment gates:** a step whose `systems.<x>` flag is false (`systems on: …` in the resolved-profile block,
else `kit_profile.py get systems.<x>`) is skipped with one `n/a` line in the overview — never a substitute.

## 0. Coordinate *(main session)*

`grep -n "<repo>" .context/SESSION_INDEX.md` — another *active* session on this PR → agree ownership with one
`SendMessage` before posting. Parallel reads are fine; two reviews on one PR are not.

## 1–4. Snapshot, trap KB, review pass, verification *(`review-runner` child)*

Spawn `Agent(subagent_type: "review-runner")` — prompt, budget and return contract: `reference/runner.md`.
The steps themselves (bundle, mode, trap KB, review rules, tickets, review pass, `--deep` lenses, verification,
stakeholder impact) live only in `$BATON/agents/review-runner.md` § Contract. It writes `$CTX/triage.json` and
returns the overview plus `CTX:` / `NEEDS` / `NOTE:` lines. The main session never opens `$CTX/diff.patch`,
`files.json`, `reviews.json` or `threads.json` (that is a deep dive). `--local` (runner.md § `--local`) runs
steps 1–4 in-session, only in a session that exists for this one review.

## 5. Walk every finding with the user *(main session)*

Print the 5a overview from `$CTX/triage.json` (STOP → WARN → NIT), then one `AskUserQuestion` per 4 findings —
Post · Deep dive · Body only · Skip; free text = rewrite, re-presented as `vN`. Write the decisions into
`triage.json` after every batch; never post mid-loop. Close with one question on the event and the full body
draft. Template, options and rules: `reference/walk.md` § 5.

## 6. Post exactly one review *(main session)*

`$CTX/request.json` → `submit-review.sh preview` → one `AskUserQuestion` (**Submit** · **Abort**) →
`submit-review.sh submit … --confirm <digest>`. The script refuses a closed PR, a moved head, a digest mismatch,
a path outside the diff and a repeat submit; its `pr-review:v1` marker sets the next run's mode.
Details: `reference/walk.md` § 6.

## 7. Thread replies (follow_up / replies_only) *(main session)*

Same loop as 5 over `threads.json`, decisions in `$CTX/replies.json`, preview and submit via
`reply-threads.sh`; never resolve a thread we did not open. Details: `reference/walk.md` § 7.

## 8. Flush — the review is not done until the KB grew *(main session)*

1. **KB write-back** via `ctx_str_replace` / `ctx_insert` to `.context/pr-reviews/<repo>.md` (create with
   `make -C $BATON/context-db new TYPE=pr-review DOMAIN=pr-reviews SLUG=<repo> TITLE="<org>/<repo> — PR review
   knowledge"`), from `triage.json` `evidence` + the walk decisions. Bullet format, dedupe, 30KB archive move:
   `reference/kb-writeback.md`.
2. `## Session log` line in the same file: `- <date> — #<pr> <author> <title> — <event>, <n> inline, <hits>
   traps hit, KB +<n>` (newest first).
3. One evidence-linked bullet in `.context/self-assessment/inbox/YYYY-Wnn--pr-reviews.md` (PR link, author,
   notable findings, outcome). Items 1–3 write through the ctx tools (`ctx_str_replace` / `ctx_insert` /
   `ctx_create`); a direct `Write`/`Edit` of a `.context/` doc is denied.
4. A fast follow that needs a ticket → offer `ticket-open` (the backlog convention `environment.md` § Rules
   names, if any); never assign it.

## Auto-approve path for trivial PRs *(`auto-runner` + main session)*

Docs-only PRs and dependency patch bumps that pass `scripts/trivial-check.py` skip steps 3–5 (`pr-scan` flags
them `A`). The whole contract: `reference/auto-approve.md`.

## Unattended auto-COMMENT path for direct review requests *(`review-runner` + main session; opt-in)*

For PRs `pr-scan` flags `C` (`auto_comment` in `config.json`, `mode` off by default): steps 1–4 run unchanged,
only the walk and the Submit prompt are skipped, and only for a `COMMENT` with no STOP finding — or an `APPROVE`
on a clean sheet when `auto_comment.approve_on_clean` is `live` (`shadow` posts the COMMENT and ledgers the
verdict). Clean-sheet definition: `reference/runner.md` § `--unattended` 2a; spawn, `on_stop`, modes, logging,
kill switch: `reference/runner.md` § `--unattended`.

## Not in scope
Our own PRs (`pr-open`, `pr-watch`, `pr-event-brief`); merging (`pr-merge.sh` in `pr-watch`); re-requesting
the `github.review_bot`; review asks — Slack drafts only where `systems.slack` (`pr-open`); unattended
REQUEST_CHANGES.
