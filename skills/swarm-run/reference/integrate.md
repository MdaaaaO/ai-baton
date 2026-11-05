# integrate.md — steps 7 and 8 in detail

The rules are `docs/swarm.md` § Authority matrix (a routine merge, a protected path) and § Lifecycle (the verifier
works twice). This page holds the reads and the input lines the skill's steps point at.

## Code owners at PR open

- The rules: the first `CODEOWNERS` GitHub reads (`.github/`, the root, `docs/`), at the PR's base, read live:
  `gh api repos/<owner/repo>/contents/<path>?ref=<base> --jq .content | base64 -d`. The last matching pattern wins.
- The protection: `gh api repos/<owner/repo>/branches/<base>/protection --jq .required_pull_request_reviews`. Only a
  404 whose message is `Branch not protected` means no protection, and so no code-owner gate. A 403, a 404 with any
  other message, or any other failure leaves the gate unknown, and an unknown gate routes the merge to the owner.
- A path whose owners, minus the PR's author, are empty (or teams the author alone belongs to) has no reachable
  reviewer: report `needs owner of <path>` at PR open. A `READY` head on that PR does not count toward the merge gate
  until the owner answers.
- Approved is `gh pr view <pr> --json reviewDecision` reading `APPROVED`. One approving review under a code-owner
  rule is not it.

## The repo's own merge gate

`swarm.protected_paths` is the floor. Where the repo has its own auto-merge (a workflow that merges, or a ruleset with
merge rules), read it at the base and add every path it reserves for the owner. Two outcomes per PR:

| The PR touches | The repo's auto-merge | The skill |
|---|---|---|
| a reserved path | skips it | keeps it out of its own merge; the digest says `needs the owner's merge`, because the PR waits with no red check and no event |
| a `swarm.protected_paths` path the gate does not reserve | would merge it once ready | keeps the draft and stops at `NEEDS owner` before `gh pr ready`, because marking it ready arms the merge |
| neither | may merge it | marks it ready only once the overview, the miss check and the verifier's read are back |

## Review depth

`git diff --shortstat origin/<base>...<READY sha>` in a clone: insertions plus deletions of 20 or fewer → spawn the
`review-runner` with `model: sonnet`; otherwise its own model. A few-line fix needs no deep pass to find what its
`DW-` ids already prove. The miss check (`docs/miss-check.md`) runs on the body file before ready either way.

## The verifier's input

Spawn it fresh by name, in the background, every run (`docs/swarm.md` § Lifecycle). `model: sonnet` when every `DW-<n>`
check is a backticked command, `opus` otherwise. The prompt is one line, as `agents/verifier.md` § Input states it:

```
TICKET: <n> · REPO: <owner/repo> · PR: <n> · HEAD: <READY sha> · PHASE: read · KEY: <key> · CHECKOUT: <clone>
TICKET: <n> · REPO: <owner/repo> · PR: <n> · HEAD: <READY sha> · PHASE: merged · KEY: <key> · CHECKOUT: <clone>
TICKET: <epic> · REPO: <owner/repo> · PR: - · HEAD: <default-branch sha> · PHASE: gates · KEY: <key> · CHECKOUT: <clone> · PLAN: <file> · GATES: <VG-n, …> · CHILDREN: <ticket>:<pr>, …
```

`CHECKOUT` is never a worker's worktree. `phase=read` and `phase=merged` take no `GATES` or `PLAN`. A re-run of the gates
names only the gates still open. After the run, post the plan file back: `gh issue edit <epic> --body-file <file>`.

## The return, read

| Return | Step |
|---|---|
| `PASS <t> <head> phase=read` | opens the merge gate for that head at the next tick; a newer `READY` voids it |
| `FAIL <t> <head> phase=read` | `ASSIGN … rebrief=1` with the why in `note="…"` |
| `PASS <t> <sha> phase=merged` | the passes are in the ledger; `ticket-close`, then `status done` |
| `FAIL <t> <sha> phase=merged` | `status blocked --reason`; a new assignment on a new branch |
| `PASS`/`FAIL <epic> <sha> phase=gates` | post the plan file back; `gates.py status` decides the close |
