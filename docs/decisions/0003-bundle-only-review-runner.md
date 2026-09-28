# 0003 — The pr-review runner works from a bundle only

> Reconstructed from the implementation (skills/pr-review, agents/review-runner.md); the owner has not
> yet confirmed the rationale.

## Status

Proposed — the behaviour is implemented; the rationale waits on the owner's confirmation

## Context

`pr-review` steps 1–4 (snapshot, repo trap KB, review pass, verification) run in a spawned
`review-runner` subagent (`Agent(subagent_type: "review-runner")`, Opus, ≤ 60 turns,
`agents/review-runner.md`), not in the main session. `fetch-context.sh` first writes a bundle to a
context directory (`$CTX`): `manifest.json`, `bundle.json`, `diff.patch` and per-file `diffs/<path>.patch`,
`head/<path>` and `base/<path>` blobs, `kb-traps.md`, `threads.json`, `review_comments.json`,
`issue_comments.json`. The runner is told to read only that bundle — `SKILL.md` § Contract lists exactly
four live reads allowed beyond it (a repo's own `CLAUDE.md`/`AGENTS.md` rules when the PR didn't touch
that file, a base blob for a path the PR didn't touch, one named file in an external repo for a
convention claim, and a repo-wide `ref()`/exposures search) — never `git show`, CI-log polling, or a `gh
api` call the bundle already answers. The trivial-PR path (`agents/auto-runner.md`) follows the same
shape: it also runs from the bundle `fetch-context.sh` writes and never re-fetches what it already has.

The runner's `disallowedTools` excludes `Edit` and `NotebookEdit`; `Write` remains, but only to files
under `$CTX` (`triage.json`, `traps.json`, `ff.json`). Its instructions state explicitly: never post a
review, comment, reply, reaction or ticket comment; never edit the KB, the ledger or any repo file.
`auto-runner` is told the same: it never posts and never talks to the user. Both return one fixed, short
block and nothing else — `review-runner`'s caps at roughly 3K tokens (a summary table plus `RECOMMEND` /
`CTX:` / `NEEDS` / `IMPACT:` / `NOTE:` lines); `auto-runner` returns an `AUTO:` verdict line and a `CTX:`
path. Posting happens later, only in the main session's step 5 walk, requiring the user to approve each
finding before anything goes out under their name (`SKILL.md`: "the user is the reviewer of record").

## Decision

The review runner (and the trivial-PR auto-runner) is scoped to read the bundle a fetch step produces,
with a short, named list of live-read exceptions, and to write findings only to files under its own
`$CTX` directory — never to post a review, comment or reply, and never to ask the user anything. It
returns a small fixed-shape summary; the diff, per-file patches and thread contents it read stay under
`$CTX` and are never included in what it returns to the caller.

## Consequences

- Because the runner's return is capped to a short summary rather than the files it read, the diff and
  thread contents it processed do not enter the calling session's context — a session that reviews many
  PRs in sequence does not accumulate their diffs.
- Because the runner has no tool call that posts, comments, replies, or edits repo/ticket state, and
  `Edit`/`NotebookEdit` are disallowed, no review output can reach GitHub (or the tracker) without first
  passing through the main session's user-approval walk — the approval step is the only path from a
  finding to something posted.
- Because live reads are limited to four named, narrow cases, the runner's outside-the-bundle activity
  stays bounded and auditable rather than open-ended, even when the bundle is missing something.
- A consequence of never asking: any missing input the runner cannot resolve from the bundle or the
  four exceptions is reported back (`NEEDS …`) rather than solved by prompting the user mid-run, so the
  runner completes in one pass or hands back cleanly.

## Sources

- `skills/pr-review/SKILL.md` § "Contract: bundle vs. live reads" and § "Where each step runs (cost
  model, 2026-09-19)"
- `agents/review-runner.md` ("Do exactly this" steps 1–6, "Write only under `$CTX`", "Return exactly this
  and nothing else")
- `agents/auto-runner.md` (the trivial-PR path: bundle-only reads, `$CTX`-only writes, `AUTO:`-only
  return, "never posts")
