# auto-approve.md — the trivial-PR auto-approve contract (`--auto`)

The one home of the trivial-PR auto-approve rules (owner's standing decision 2026-09-19 — `shadow` until
`auto_approve.shadow_review_due`). `pr-review` (`--auto`), `pr-scan` (the `A` flag) and `reference/runner.md` point
here; none of them restates it. It is the one sanctioned exception to `scope.md` "APPROVE is never inferred" beside
the clean-sheet APPROVE of the unattended path (`reference/runner.md` § `--unattended` 2a, a separate contract).

## Classes

Small **docs-only** PRs and dependency **patch** bumps (minor only for dev tooling, major never) may be approved on
the user's behalf without a walk — pr-review steps 3–5 are skipped. Not eligible: lock-only PRs, stacked PRs (base ≠
default branch), Actions bumps; `docs_exclude_globs` keeps model docs and `packages.txt` / `constraints.txt` out of
the docs class; `.github/**`, `.claude/**` and CODEOWNERS are hard excludes. dependabot/renovate PRs are kept in the
`pr-scan` queue **only** when eligible (`auto_approve.bot_authors`); other bots drop.

## Two layers

1. **Gate — deterministic, no model.** `pr-scan.sh` pre-filters on size, then runs
   `$BATON/skills/pr-review/scripts/trivial-check.py <repo> <pr>` (config block `auto_approve` in
   `.context/state/pr-review/config.json`). **Ownership first**: the user (`WORKSPACE_GITHUB_LOGIN`) is a requested
   reviewer, directly or via a team in the env store's `github.owner_teams`; a `src=sweep` row never runs the gate
   ("not in the user's review scope" — a sweep row is not a review request). Then class, globs, hard excludes, size
   caps, CI green, the env config's `github.review_bot` green where required and set, no human CHANGES_REQUESTED, 0
   unresolved threads. A gate that errors (`error: true`, exit 3 for a setup problem such as an unset login) counts
   as not eligible and as a sweep error.
2. **Review — the Sonnet `auto-runner`** (`$BATON/agents/auto-runner.md`, ≤ 15 turns), never the Opus
   review-runner: docs claims checked against the bundle at the head ref, bump release notes read for
   breaking/behaviour notes, lockfile consistency. Zero findings → `AUTO: approve`; anything else → `AUTO: fallback`.

## Spawn (main session)

One `auto-runner` per `A` row whose `src` is `direct` or `team`, in parallel when several:

```
Agent(subagent_type: "auto-runner", description: "auto-review <repo>#<pr>", prompt: "
MODE: auto · CLASS: <docs|patch-bump> · REPO: <owner/repo> · PR: <n> · HEAD: <sha>
Gate output: <the queue.json .auto object> — write it to $CTX/trivial.json first.
Return the AUTO:/CTX:/NEEDS lines only.")
```

## Modes (`auto_approve.mode`) and the ledger

| Mode | On `AUTO: approve` | Ledger status |
|---|---|---|
| `off` | the gate is not run; the row is an ordinary queue row | — |
| `shadow` | nothing posted; tell the user one line "would approve <repo>#<n> — <body>" | `shadow_approve` (the row drops from later sweeps on that head) |
| `live` | write `$CTX/request.json` `{"event":"APPROVE","body":<body>,"comments":[]}`, run `submit-review.sh preview … --auto`, then `submit … --confirm <digest> --auto` **without** an `AskUserQuestion`; tell the user one line with the review link | `auto_approved` (written by the script) |

`submit-review.sh --auto` re-runs `trivial-check.py --head <sha>` and refuses unless the gate still passes on that
exact head and `auto_approve.mode == live`. In `shadow`, append
`{"repo","pr","head","status":"shadow_approve"|"shadow_fallback","ts","reason"}` by piping the row to
`bash $BATON/skills/pr-review/scripts/ledger-append.sh "$ROOT/ledger.jsonl"` (exit 0 appended, 1 bad JSON, 3 lock
timeout, 4 write failure; see its header).

## Fallback

`AUTO: fallback` (any finding, any doubt) → tell the user one line "fallback: <reason>"; the PR stays in the queue as
an ordinary row and goes through the normal walk with its sheet (`shadow` ledgers `shadow_fallback`; such a row stays
an ordinary row). `NEEDS` lines are handled like any other runner's.

**Kill switch:** `auto_approve.mode: off`.
