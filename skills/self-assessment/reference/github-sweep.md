# GitHub sweep — the three sweeps plus issues, in full

Loaded from step 3 of `SKILL.md` when `github` is in `self_assessment.sources`. Prefix every `gh`
call with `github.sandbox_token_prefix` when set. Scope: org-wide `--owner=<github.org>`; when
`tracker.kind == github`, restrict to the repos in `tracker.repos` (one call per repo) and also
sweep issues opened/closed/commented there (`gh search issues`) — they are the tracker.
**`gh search prs --json` does not expose `mergedAt`** (its JSON fields are `assignees, author,
authorAssociation, body, closedAt, commentsCount, createdAt, id, isDraft, isLocked, isPullRequest,
labels, number, repository, state, title, updatedAt, url` — verify with `gh search prs --help` /
`gh pr list --help` before trusting this on a new `gh` version); use `gh pr list` per repo instead,
which does.

- **shipped** (per repo in `tracker.repos`, tracker-restricted scope):
  `gh pr list -R <owner>/<repo> --state merged --search "merged:<Mon>..<Sun>" --author
  $WORKSPACE_GITHUB_LOGIN --json number,title,mergedAt,url --limit 100`. Org-wide scope (no
  `tracker.repos` restriction) has no per-repo `-R` to hang off, so fall back to `gh search prs
  --author=$WORKSPACE_GITHUB_LOGIN --owner=<github.org> --merged --merged-at <Mon>..<Sun> --json
  repository,number,title,state,createdAt,closedAt,url --limit 100` — `--merged` +
  `--merged-at` are input filters `gh search prs` does support; `closedAt` stands in for
  `mergedAt` (equal for a merged PR in practice).
- **reviews given:** `gh search prs --reviewed-by=$WORKSPACE_GITHUB_LOGIN --owner=<github.org>
  --updated ">=<Mon>" --created "<=<Sun>" --json repository,number,title,url,updatedAt --limit 500` (add `--repo <r>`
  per repo instead of `--owner` in tracker-restricted scope) — `--updated` is a **pre-filter
  only** (the PR's *last* update, not when the review was given): a `--merged-at`/closed-date
  bound here would drop a review given in-window on a PR still open or merged the following
  week, so the update bound is one-sided (`>=<Mon>`); `--created "<=<Sun>"` is the lossless upper bound (an
  item created after Sunday cannot carry an in-window review or comment) that stops the net widening as
  the week ages — a range or back-fill run would otherwise hit the cap. Then per PR
  pull `/reviews` **and** `/comments` with pagination (30/page hides verdicts) and **keep only
  reviews whose own `submitted_at` falls inside `<Mon>..<Sun>`** — that per-review filter is the
  actual window, not the search query. The author sweep alone silently drops the whole
  *Collaboration & reviews* section.
- **collab comments:** `gh search prs --commenter=$WORKSPACE_GITHUB_LOGIN --owner=<github.org>
  --updated ">=<Mon>" --created "<=<Sun>" --json repository,number,title,url,updatedAt --limit 500` (swap `--owner`
  for `--repo <r>` per repo in tracker-restricted scope) — again a pre-filter, since a PR touched
  after Sunday would otherwise be dropped by a two-sided `--updated <Mon>..<Sun>` bound; then pull
  each PR's `/comments` and **keep only comments whose own `created_at` falls inside
  `<Mon>..<Sun>`** (a PR can carry comments from many weeks). Same shape for issue comments:
  `gh search issues --commenter=$WORKSPACE_GITHUB_LOGIN --owner=<github.org> --updated ">=<Mon>" --created "<=<Sun>"
  --json repository,number,title,url,updatedAt --limit 500`, then per-issue `/comments` filtered
  the same way.
- **issues opened/closed/commented** (tracker-restricted scope): `gh search issues
  --author=$WORKSPACE_GITHUB_LOGIN --repo <r> --updated ">=<Mon>" --created "<=<Sun>" --json
  repository,number,title,url,state,createdAt,closedAt --limit 500` per repo — pre-filter only;
  **keep only issues whose `createdAt` (opened) or `closedAt` (closed) falls inside
  `<Mon>..<Sun>`**. The "commented" leg is the `gh search issues --commenter` sweep above — run it with
  `--repo <r>` per repo in tracker-restricted scope, exactly like the PR sweep — so an issue opened weeks
  earlier and commented on in-window is caught there, not here. The one-sided sweeps carry `--limit 500` (`gh search` caps at 1000): a result
  count equal to the limit means the sweep was truncated — raise it or split by repo, never
  report from a capped list.
- Filter every result to the window: `gh pr list`'s per-repo merged-PR query is already
  window-scoped by `--search "merged:…"` and needs no further filtering; every `--updated
  ">=<Mon>"` sweep above is a pre-filter only — the real window comes from the client-side
  per-item/per-comment/per-review timestamp check described with each sweep, never from the
  search query alone.
