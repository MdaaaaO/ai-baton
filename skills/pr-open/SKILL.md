---
name: pr-open
description: "Checklist for opening a PR: body with the diagram set derived from the diff, labels in every repo, commit-style check of commits and title, reviewers plus the review bot where configured, pr-watch, tracker link, and the review request (a Slack DRAFT where enabled, never sent). Use when writing a PR body and right after `gh pr create`."
metadata:
  version: "31"
  updated: "2026-10-01"
  reviewed: "2026-09-25"
  facts: "slack.review-venue,slack.channel,github.review_bot,github.owner_teams,tracker.kind,tracker.url_template"
---

# pr-open — what "the PR is open" means here

A PR is not open until all of this is done, in this order. Owner decision (2026-09-10): "on PR creation we
draft a message in slack for review in the correct help channel / team channel (draft!)" — that step runs
only where the environment has Slack (`systems.slack`); everything else holds in every environment.

**Read the config first** (from the workspace root; `K=python3 $BATON/context-db/bin/kit_profile.py`):
`$K get tracker.kind`, `tracker.url_template`, `github.review_bot`, `github.owner_teams`,
`systems.signed_commits`, `labels.shared`, `labels.repos.<repo>` (may be absent), `systems.slack`,
`slack.repo_channels.<owner/repo>`, `commits.default` / `commits.repos.<owner/repo>` (§ Commit style). Never hardcode any of these values.

0. **Sizing, then branch & push.** First, sizing: when the PR opens against an existing ticket that was never
   sized — an umbrella/epic ticket that predates `ticket-open`, or one no `ticket-pickup` ran on — the work
   still gets sized once before it is built, the same read/verify/decide/write-back as `ticket-pickup` §§ 1–4
   (reuse its adapter, don't duplicate it here): `python3 $BATON/context-db/bin/sizing.py parse <ticket body +
   comments, oldest first>`. Exit 0 → honour the line as read (§ 3 there). Exit 1/2 (no line, or malformed) →
   size it now from the table in `docs/delegation.md` § Sizing and post `sizing.py format <model>
   "<delegate|main session>" "<reason>"` as a ticket comment (`ticket-update`; "last Sizing line wins" already
   covers a later correction). `delegate` → the build goes to a worker per `docs/delegation.md` § The worker
   brief and this session only reviews the diff and opens the PR; `main session` → build here. No ticket in the
   arguments (a branch with nothing to size) → nothing to do, say so and continue.

   Then, branch & push: work in a worktree under `.worktrees/`. **Commit style first**: `python3
   $BATON/context-db/bin/commit_style.py resolve --dir <worktree>` names the convention (Conventional Commits
   unless the repo overrides it — § Commit style); every commit subject passes `commit_style.py check --dir
   <worktree> <msg-file>` before it is made or enqueued, and the PR title passes `commit_style.py title --dir
   <worktree> "<title>"` — same shape as the (squash) commit subject. If `systems.signed_commits` is true,
   the commit + push go through `/sign-queue` — enqueue, tell the user in
   one line, create the PR once the branch is on the remote. Otherwise commit and `git push -u origin <branch>`
   directly.
1. **Create** with a body file (`gh pr create --body-file …`): Overview with the tracker link (§ Links),
   Changes, **Diagrams** (§ Diagrams below — the set `diagram-plan.py` derives from the diff, plus its marker),
   `## Verified` (§ Verified below — real command output, never a claim; replaces the free-prose test plan).
   Detail lives here, not in chat. Where `tracker.kind` is `github`, add
   `Closes #<n>` (or `Refs #<n>` for a partial step) so the issue links itself. The body's last line is the
   footer from the resolved-profile block already in your SessionStart context — the session self-identifier,
   `session `<name>`` (`session-register` records the name); once a compaction drops that block, or it never
   printed, fall back to `python3 $BATON/context-db/bin/kit_profile.py footer`. Never an AI attribution line;
   every comment you post on this PR ends with it too. **Before `gh pr create`** (and before any later `gh pr comment`/`gh pr edit --body-file`
   on this PR), run `python3 $BATON/context-db/bin/kit_profile.py public-text-check <file> --repo <o>/<r>` on
   the body file — exit 0 → post; exit 1 → rewrite the hits generically (never post the file as is) and
   re-check; exit 3 → do not post — the env store could not be loaded, so the check did not run; fix the
   store or check the file by hand before posting; a no-op when `<o>/<r>` is one of `tracker.repos` or not public.
   Also run `python3 $BATON/context-db/bin/length_check.py <file> --surface pr --repo <o>/<r>` on the same
   file — a warning (step 7), never a blocker; a long template overrides the budget via
   `length.repos.<o>/<r>.pr_words`.
2. **Reviewers**: the code owner(s) who must approve (CODEOWNERS for the touched paths; the user's teams
   are `github.owner_teams`) — plus `github.review_bot` **only when it is non-empty**. Request via
   `gh api -X POST repos/<o>/<r>/pulls/<n>/requested_reviewers -f 'reviewers[]=…'` (teams:
   `-f 'team_reviewers[]=…'`). A solo repo with no CODEOWNERS and no bot: nothing to request — say so.
3. **Labels — always, in every repo, whether or not the repo asks for them** (owner decision, 2026-09-11 and
   2026-09-18: "applying tags should not be repo specific, we always want to apply tags and follow best
   practices, even if the repo itself doesn't require it"). A PR without labels is not open. Procedure:
   1. If `labels.repos.<repo>` exists in the env config, that is the repo's type/area/risk map — use it.
      Otherwise (or to confirm a name still exists) `gh label list -R <o>/<r> --limit 200 --json name -q
      '.[].name'` — the repo's existing names (an unknown name silently creates a bare label, so never guess).
   2. Classify the PR on three axes and pick a label for each that applies (under the conventional style the
      **type** follows from the title: `commit_style.py label "<title>"` → `enhancement` / `bug` / `documentation`,
      empty for the other types — then pick from the repo's own set as below):
      | axis | meaning | typical names |
      |---|---|---|
      | **type** (exactly one) | what kind of change | `feature` / `enhancement`, `bug`, `documentation`, `hotfix`, `tech-debt`/`refactor` |
      | **area** (one or more) | which component / language the reviewer must know | the repo's component or language labels (`python`, `javascript`, a module/app name, …) |
      | **risk / handling** (when it applies) | what the merge or deploy must respect | `data-migration`, `config-only`, `hotfix`, `breaking` |
      Spell the type labels exactly as `labels.shared` does, so ticket labels (`ticket-open` § Labels) and PR
      labels match.
   3. Apply with `gh api -X POST repos/<o>/<r>/issues/<n>/labels -f 'labels[]=…'` and re-read the PR to verify.
   4. If the repo has no name for an axis that applies, **create a best-practice label** — never ship the PR
      unlabelled and never wait for someone to name one. Reuse order, where to create it, and recording the
      new name for next time: `reference/labels.md`.
   Dependabot's `dependencies` / `python` / `github_actions` are automatic — never add them by hand.
4. **Watch**: arm `/pr-watch` on the head (`PR_WATCH_WORKTREE=<checkout> bash $BATON/skills/pr-watch/pr-watch.sh <o/r> <n> <head>`).
5. **Tracker**: comment the PR link on the ticket (`ticket-update`). Where `tracker.kind` is `jira` and the
   PR is the ticket's deliverable, move it to *In Review* (`tracker.transitions.in_review`). Where it is
   `github`, the `Closes #<n>` in the body is the link; add the issue's in-review label if the repo uses one.
6. **Slack review request — as a DRAFT, never sent — only when `systems.slack` is true.** When it is false,
   skip the step and say so in one line ("no Slack in this environment — reviewer request on GitHub is the ask").
   Channel = `slack.repo_channels.<owner/repo>` (the env fact `slack.review-venue <owner/repo>`); no entry →
   `/env-init slack.review-venue <owner/repo>` (**the pick is this skill's call** — never type a channel id
   from memory). Create the draft through the `slack-draft` skill (`slack_send_message_draft`); the user
   reviews and sends it themselves. **A draft already pending for the same channel gets updated, never
   duplicated.** The exact one-sentence shape, the no-emoji/no-ticket-key/no-CI-status rule, and
   thread-vs-DM routing for related asks: `reference/slack-review-request.md`.
7. **Tell the user** in one line: PR link + labels + where the ask went ("review-request draft is in
   #<channel>" or "reviewers requested: …") + the resolved Sizing line from step 0 when a ticket was sized
   ("sizing: `sonnet`, delegate") + step 1's `LENGTH:` line (`LENGTH: over (<n> words, budget <m>)` when
   over, the way `ticket-open` does) — a main-session build is then a visible choice, not a silent default.

## Diagrams — the right set for this PR's content, derived from the diff (owner decisions 2026-09-17 / 2026-09-25)

"When you create a PR description please also add event flow diagrams, and component / architecture diagrams"
(2026-09-17) — refined 2026-09-25: "not just all diagrams randomly added but rather the right set of diagrams
based on PR content — if pipeline then pipeline flow, if model changes the table before and after, if
architecture then components". A reviewer should see *where the change sits*, *what changes*, *what runs* and,
on a feature PR, *why this shape* — without reading the diff, and nothing the PR does not raise.

**Three principles**
1. **One artifact per reviewer question, only for the questions the content raises.** WHERE (placement), WHAT
   (before → after), RUNS (flow) — at most three blocks, most PRs need one or two — plus WHY (the option that
   lost) on a feature PR with a real alternative. A refactor raises WHERE + WHAT, never RUNS; a bugfix raises
   RUNS only; a docs/config/deps/test PR raises none; none of those three ever carries WHY.
2. **The form follows the content — Mermaid is not always the answer.** Graphs and flows are Mermaid
   (`flowchart`, `sequenceDiagram`, `stateDiagram-v2`); a *delta* (columns, fields, resources, jobs, seed
   windows, DDL grants) is a markdown **table** `x | before | after | note`; WHY is always a table too (≤ 4
   rows). A column list as an `erDiagram` is worse than the table.
3. **The plan is deterministic and comes from the diff, not from habit.** Changed paths → facets → plan,
   computed by `diagram-plan.py`; the same facets that pick the labels. The model draws exactly what the plan
   asks for, writes the WHY table and the Sketch reason by hand, and stamps the plan marker so a later push
   can be checked for drift.

**Procedure**
1. From the workspace root, with the branch pushed or the diff local, run `diagram-plan.py --repo <repo-dir>
   [--base origin/main] [--type bugfix|refactor|feature]` (or `--pr <owner/repo> <n>` after create, which also
   reads the type label). It prints the facets, the dominant one, the plan (`WHERE`/`WHAT`/`RUNS`, a `?`
   suffix = conditional), notes, and the `<!-- diagram-plan: … -->` marker — every flag and mode, and the facet
   → question matrix: `reference/diagrams.md`.
2. Draw **exactly** the planned blocks under `## Diagrams`, in WHERE → WHAT → RUNS order, each with a one-line
   caption naming the question it answers; a conditional block (`RUNS?`) only when its clause holds, else one
   line why not. Add the WHY table (`reference/diagrams.md` § WHY) on a feature PR with a real alternative.
   `SKIP` → one line under `## Diagrams` and no block. Drawing is delegated to a Sonnet worker with the brief
   in `docs/diagrams.md` § The drawing brief; the main session validates and pastes.
3. Directly under `## Diagrams`, paste the `Sketch:` line when the ticket carries a sketch marker for this
   repo — `diagram-plan.py --sketch <ticket-text>` computes `none` / `matches` / `differs (+a, −b)`
   (`reference/diagrams.md` § `--sketch`); add only the ` — <reason>` for `differs`, never edit the computed
   part. Then paste the plan marker as the section's last line (HTML comment, invisible on GitHub).
4. Validate every Mermaid block (`reference/diagrams.md` § Rules that hold for every block), then create/update the PR.
5. **On every later push** run `diagram-plan.py --pr <o/r> <n> --check` — `OK` / `DRIFT <old> → <new>` / `NO
   MARKER` / `MALFORMED MARKER <line>` (fix a hand-edited marker, don't just redraw), each of the last three
   exit 3; it also prints `LINT: ok` or one `LINT: warn <reason>` per finding (`reference/diagrams.md` §
   Lint) — warn only, the exit code stays the marker result. On drift, redraw in the same turn as the code,
   and re-run `--sketch` too when the ticket has a marker. Before creating or updating the PR, and again
   after any redraw this step triggers, run the cold-reader gate on the `## Diagrams` section
   (`docs/cold-reader.md`) when the plan is not `SKIP`. The `pr-watch` head-move event is the reminder — the
   same event is `verified.py check`'s STALE reminder too (§ Verified step 4), and the moment to re-run
   `length_check.py <file> --surface pr --repo <o>/<r>`: the push step now runs all three body checks
   (diagram marker, evidence/verified, length), none of which block.

Exit codes, the facet → question matrix (intent overlays / composition), the WHY table, `--sketch`, the lint,
the `ddl` facet, and the rules every block follows (native Mermaid rendering, validate-before-publishing,
honest labels, right-sizing, keeping them current): `reference/diagrams.md`.

## Verified — real command output, never a claim (owner decision, 2026-09-30)

`WORKSPACE.md` § Verification: done is an observation, not a claim. `## Verified` replaces step 1's
free-prose test plan.

1. Read `verify.repos.<owner/repo>.cmd` / `.timeout` (default 300). Declared → in the worktree, on the
   pushed head, `python3 $BATON/skills/pr-open/verified.py run --repo-dir <worktree> --cmd "<cmd>" --timeout
   <s>` prints the block to paste under `## Verified` as-is. A non-zero exit (or a timeout) → `gh pr create
   --draft`, keep the failing tail, say so in your one-line report — never hide or retry it.
2. No declared command → cite the commands you actually ran, one `` `cmd` → `output` `` line each
   (evidence_check.py's own shape); ran none → `Not verified locally: <reason>`. A section that only
   claims ("tests pass") is refused.
3. `## Not in this PR` and `## One honest limit` are optional, ≤ 3 lines each for the section itself — `## One honest limit` also carries every due `Assumed:` line (revisit: pr-open) still open on the session (`docs/assumptions.md`), which follow the limit and do not count toward those three lines (at most 4 — more means the session should have asked earlier), asked in the carousel before `gh pr create`.
4. Before `gh pr create`, and on every later push: `python3 $BATON/skills/pr-open/verified.py check <file>
   [--pr <o>/<r> <n>]` — refuses a bare claim, and with `--pr` prints `STALE <old> → <new>` (exit 3) when
   the recorded head has moved; rerun it in the same turn as the push. The pasted tail still passes
   `public-text-check` before posting (already step 1's rule). Grammar, worked examples, the three
   accepted shapes: `reference/verified.md`.

## Commit style — Conventional Commits by default, the repo may override

Owner decision, 2026-09-25: by default follow Conventional Commits unless the repo overrides it (core rule,
`WORKSPACE.md` § Rules → Workflow & scope; spec `$BATON/docs/commit-style.md`). The resolver
(`python3 $BATON/context-db/bin/commit_style.py`) checks the repo's own marker file first, then the env
config, then the kit default — never the repo's recent log. Type/scope grammar, the PR-title-is-the-squash-subject
rule, the gates that enforce it and how a repo overrides it: `reference/commit-style.md`.

## Links — every ticket reference is clickable, everywhere

Owner decision, 2026-09-10: every ticket/PR reference, on every surface this checklist touches, is a
clickable link, never a bare key or a bare URL (core rule, `WORKSPACE.md` § Rules → Communication) — the
per-surface rendering table: `reference/links.md`. Don't DM individuals for reviews (§ Rules → Don't
pester people); one ask in the right venue is the ask, a follow-up nudge is the user's decision.

## Not in scope
Merging, re-requesting the bot after pushes, thread handling — those are `/pr-watch` + the repo's
Coordination rules (`.context/repos/<repo>.md`). Signing mechanics are `/sign-queue` (only where
`systems.signed_commits` is true).
