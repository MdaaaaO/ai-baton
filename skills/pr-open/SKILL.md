---
name: pr-open
description: "Checklist for opening a PR: sizing gate, body with the diagram set and a ## Verified section, public-text-check and length_check, labels, reviewers, pr-watch, tracker link, Slack review draft where enabled. Use when writing a PR body and right after `gh pr create`."
metadata:
  version: "37"
  updated: "2026-10-23"
  reviewed: "2026-10-23"
  facts: "slack.review-venue,slack.channel,github.review_bot,github.owner_teams,tracker.kind,tracker.url_template"
---

# pr-open — what "the PR is open" means here

A PR is not open until all of this is done, in this order. The Slack review draft (step 6) runs only where the
environment has Slack (`systems.slack`); everything else holds in every environment.

**Read the config first** (from the workspace root; `K=python3 $BATON/context-db/bin/kit_profile.py`):
`$K get tracker.kind`, `tracker.url_template`, `github.review_bot`, `github.owner_teams`,
`systems.signed_commits`, `labels.shared`, `labels.repos.<repo>` (may be absent), `systems.slack`,
`slack.repo_channels.<owner/repo>`, `commits.default` / `commits.repos.<owner/repo>` (§ Commit style). Never hardcode any of these values.

0. **Sizing, then branch & push.** When the PR opens against a ticket that was never sized (an umbrella/epic
   ticket that predates `ticket-open`, or one no `ticket-pickup` ran on), size it once before it is built — the
   same read/verify/decide/write-back as `ticket-pickup` §§ 1–4: `python3 $BATON/context-db/bin/sizing.py parse
   <ticket body + comments, oldest first>`. Exit 0 → honour the line as read. Exit 1/2 (no line, or malformed) →
   size it from `docs/delegation.md` § Sizing and post `sizing.py format <model> "<delegate|main session>"
   "<reason>"` as a ticket comment (`ticket-update`). `delegate` → a worker builds per `docs/delegation.md` § The
   worker brief and this session reviews the diff and opens the PR; `main session` → build here. No ticket → say
   so and continue.

   Then work in a worktree under `.worktrees/`. **Commit style first**: `python3
   $BATON/context-db/bin/commit_style.py resolve --dir <worktree>` names the convention (§ Commit style); every
   commit subject passes `commit_style.py check --dir <worktree> <msg-file>` before it is made or enqueued, and
   the PR title passes `commit_style.py title --dir <worktree> "<title>"`. If `systems.signed_commits` is true,
   commit + push go through `/sign-queue` — enqueue, tell the user in one line, create the PR once the branch is
   on the remote. Otherwise commit and `git push -u origin <branch>` directly.
1. **Create** with a body file (`gh pr create --body-file …`): Overview with the tracker link (`reference/links.md`),
   Changes, **Diagrams** (§ Diagrams), `## For the operator` (§ For the operator), `## Verified` (§ Verified — real command output, never a claim). Detail
   lives here, not in chat. Where `tracker.kind` is `github`, add `Closes #<n>` (or `Refs #<n>` for a partial
   step). The body's last line is the footer from the resolved-profile block in your SessionStart context —
   `session `<name>`` (`session-register` records the name); once a compaction drops that block, or it never
   printed, fall back to `python3 $BATON/context-db/bin/kit_profile.py footer`. Never an AI attribution line;
   every comment you post on this PR ends with it too. **Before `gh pr create`** (and before any later `gh pr comment`/`gh pr edit --body-file`
   on this PR), run `python3 $BATON/context-db/bin/kit_profile.py public-text-check <file> --repo <o>/<r>` —
   exit 0 → post; exit 1 → rewrite the hits generically and re-check; exit 3 → do not post (the env store could
   not be loaded; fix it or check the file by hand); a no-op when `<o>/<r>` is one of `tracker.repos` or not
   public. Also run `python3 $BATON/context-db/bin/length_check.py <file> --surface pr --repo <o>/<r>` — a
   warning (step 7), never a blocker; a long template overrides the budget via `length.repos.<o>/<r>.pr_words`.
2. **Reviewers**: `python3 $BATON/skills/pr-open/codeowners.py resolve <o>/<r> --pr <n>`: exit **0** → request exactly
   its `team:`/`user:` lines plus a non-empty `github.review_bot` (`gh api -X POST repos/<o>/<r>/pulls/<n>/requested_reviewers`);
   exit **3** → nothing to request; exit **2** is a failed read, never "nothing to request" — `reference/codeowners.md`.
3. **Labels — always, in every repo, whether or not the repo asks for them.** A PR without labels is not open.
   1. If `labels.repos.<repo>` exists in the env config, that is the repo's type/area/risk map — use it.
      Otherwise (or to confirm a name still exists) `gh label list -R <o>/<r> --limit 200 --json name -q
      '.[].name'` — never guess a name (an unknown one silently creates a bare label).
   2. Pick exactly one **type**, one or more **area**, and a **risk/handling** label when one applies
      (`reference/labels.md` § The three axes). Under the conventional style `commit_style.py label "<title>"`
      gives the type (`enhancement` / `bug` / `documentation`, empty for the others); spell type labels as
      `labels.shared` does.
   3. Apply with `gh api -X POST repos/<o>/<r>/issues/<n>/labels -f 'labels[]=…'` and re-read the PR to verify.
   4. No name for an axis that applies → **create a best-practice label**, never ship unlabelled
      (`reference/labels.md` § Creating one). Dependabot's automatic labels are never added by hand.
4. **Watch**: arm `/pr-watch` on the head (`PR_WATCH_WORKTREE=<checkout> bash $BATON/skills/pr-watch/pr-watch.sh <o/r> <n> <head>`).
5. **Tracker**: comment the PR link on the ticket (`ticket-update`). Where `tracker.kind` is `jira` and the
   PR is the ticket's deliverable, move it to *In Review* (`tracker.transitions.in_review`). Where it is
   `github`, the `Closes #<n>` in the body is the link; add the issue's in-review label if the repo uses one.
6. **Slack review request — as a DRAFT, never sent — only when `systems.slack` is true.** When it is false,
   skip the step and say so in one line ("no Slack in this environment — reviewer request on GitHub is the ask").
   Channel = `slack.repo_channels.<owner/repo>` (the env fact `slack.review-venue <owner/repo>`); no entry →
   `/env-init slack.review-venue <owner/repo>` (never type a channel id from memory). Create the draft through
   `slack-draft`; the user reviews and sends it. **A draft already pending for the same channel gets updated,
   never duplicated.** Shape and routing: `reference/slack-review-request.md`.
7. **Tell the user** in one line: PR link + labels + where the ask went ("review-request draft is in
   #<channel>" or "reviewers requested: …") + the resolved Sizing line from step 0 when a ticket was sized
   ("sizing: `sonnet`, delegate") + step 1's `LENGTH:` line (`LENGTH: over (<n> words, budget <m>)` when over)
   + the `DONE WHEN:` line of § Verified step 5 when the ticket has ids.

## Diagrams — the right set for this PR's content, derived from the diff

A reviewer should see *where the change sits*, *what changes*, *what runs* and, on a feature PR, *why this shape*
— without reading the diff, and nothing the PR does not raise. One artifact per question the content raises, in
the form the content needs (Mermaid for graphs and flows, a table for a delta or WHY), planned by the script from
the diff, never by habit (`reference/diagrams.md` § Why this shape).

1. With the branch pushed or the diff local, run `python3 $BATON/skills/pr-open/diagram-plan.py --repo <repo-dir>
   [--base origin/main] [--type bugfix|refactor|feature]` (or `--pr <owner/repo> <n>` after create). It prints the
   facets, the plan (`WHERE`/`WHAT`/`RUNS`, `?` = conditional), notes, and the `<!-- diagram-plan: … -->` marker.
2. Draw **exactly** the planned blocks under `## Diagrams`, WHERE → WHAT → RUNS, each with a one-line caption
   naming its question; a conditional block only when its clause holds, else one line why not. Add the WHY table
   (`reference/diagrams.md` § WHY) on a feature PR with a real alternative. `SKIP` → one line, no block. Drawing
   is delegated to a Sonnet worker with `docs/diagrams.md` § The drawing brief; the main session validates and pastes.
3. Directly under `## Diagrams`, paste the `Sketch:` line when the ticket carries a sketch marker for this repo
   (`diagram-plan.py --sketch <ticket-text>`, `reference/diagrams.md` § `--sketch`; add only the ` — <reason>`
   for `differs`). Paste the plan marker as the section's last line.
4. Validate every Mermaid block (`reference/diagrams.md` § Rules that hold for every block), then create/update the PR.
5. **On every later push** run `diagram-plan.py --pr <o/r> <n> --check` — `OK` / `DRIFT <old> → <new>` / `NO
   MARKER` / `MALFORMED MARKER <line>` (fix a hand-edited marker, don't just redraw), the last three exit 3; its
   `LINT:` lines only warn (`reference/diagrams.md` § Lint). On drift, redraw in the same turn as the code (and
   re-run `--sketch` when the ticket has a marker). Before creating or updating the PR, and after any redraw, run
   the cold-reader gate on `## Diagrams` (`docs/cold-reader.md`) unless the plan is `SKIP`. The `pr-watch`
   head-move event is the reminder for all four body checks (diagram marker, `operator_brief.py check`,
   `verified.py check`, `length_check.py`), none of which block.

Exit codes, the facet → question matrix, the `ddl` facet and the block rules: `reference/diagrams.md`.

## For the operator — what the change means for whoever runs it

Between `## Diagrams` and `## Verified`, write `## For the operator`: the four lines of `docs/operator-brief.md` (`Can now:` ·
`Do differently:` · `Check with:` · `Not covered:`; ≤ 20 words each; `Nothing.` when nothing applies), from the diff and `## Done when`.
Before `gh pr create` and on every push run `python3 $BATON/skills/pr-open/operator_brief.py check <file>` (warns, exit 0, never blocks); then the
cold-reader gate on the four lines alone (`docs/cold-reader.md` § The operator block) unless all say `Nothing.`; a miss rewrites the block first.

## Verified — real command output, never a claim

`WORKSPACE.md` § Verification: done is an observation, not a claim.

1. Read `verify.repos.<owner/repo>.cmd` / `.timeout` (default 300). Declared → in the worktree, on the
   pushed head, `python3 $BATON/skills/pr-open/verified.py run --repo-dir <worktree> --cmd "<cmd>" --timeout
   <s>` prints the block to paste under `## Verified` as-is. A non-zero exit (or a timeout) → `gh pr create
   --draft`, keep the failing tail, say so in your one-line report — never hide or retry it.
2. No declared command → cite the commands you actually ran, one `` `cmd` → `output` `` line each; ran none →
   `Not verified locally: <reason>`. A section that only claims ("tests pass") is refused.
3. `## Not in this PR` and `## One honest limit` are optional, ≤ 3 lines each — `## One honest limit` also carries every due `Assumed:` line still open on the session whose tag is `(revisit: pr-open)` (`docs/assumptions.md` § Checkpoints — `ticket-update` and `session-end` lines stay where they are), each verbatim in that doc's grammar, `Assumed: <one line> (revisit: pr-open)` — the tag is part of the line, not commentary — outside those three lines (at most 4), asked in the carousel before `gh pr create`.
4. Before `gh pr create`, and on every later push: `python3 $BATON/skills/pr-open/verified.py check <file>
   [--pr <o>/<r> <n>]` — refuses a bare claim; with `--pr` prints `STALE <old> → <new>` (exit 3) when the
   recorded head moved; rerun it in the same turn as the push. Shapes and examples: `reference/verified.md`.
5. **Done when, beside step 4.** A ticket whose `## Done when` carries `DW-<n>` ids: one `## Verified` line per id the PR proves,
   `DW-<n> · <evidence>`, then `python3 $BATON/context-db/bin/done_when.py coverage --ticket <ticket-file> --body <file>` with every
   `verified.py check`. `DONE WHEN: covered` (exit 0) → go on; `gap DW-n[, …]` (exit 3) → prove the id, or answer it in `## One honest
   limit` as `DW-<n> · <why it is not verified>`, then re-run; exit 1 → no ids, skip. Step 7 reports the line (`reference/verified.md`).

## Commit style, links, scope

- **Commit style**: Conventional Commits unless the repo overrides it (`WORKSPACE.md` § Rules → Workflow & scope);
  the resolver checks the repo's marker, then the env config, then the kit default — never the recent log.
  Grammar and gates: `reference/commit-style.md`.
- **Links**: every ticket/PR reference on every surface this checklist touches is a clickable link, never a bare
  key or URL (`reference/links.md`). No review DMs to individuals; a follow-up nudge is the user's decision.
- **Not in scope**: merging, re-requesting the bot, thread handling (`/pr-watch` + the repo's Coordination rules
  in `.context/repos/<repo>.md`); signing mechanics (`/sign-queue`, only where `systems.signed_commits` is true).
