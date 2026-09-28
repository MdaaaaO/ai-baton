---
name: pr-open
description: "Checklist for opening a PR: body with the diagram set derived from the diff, labels in every repo, commit-style check of commits and title, reviewers plus the review bot where configured, pr-watch, tracker link, and the review request (a Slack DRAFT where enabled, never sent). Use when writing a PR body and right after `gh pr create`."
metadata:
  version: "10"
  updated: "2026-09-27"
  reviewed: "2026-09-25"
  facts: "slack.enabled,slack.review-venue,slack.channel,github.review_bot,github.owner_teams,github.signed_commits,tracker.kind,tracker.url_template"
---

# pr-open — what "the PR is open" means here

A PR is not open until all of this is done, in this order. Owner decision (2026-09-10): "on PR creation we
draft a message in slack for review in the correct help channel / team channel (draft!)" — that step runs
only where the environment has Slack (`systems.slack`); everything else holds in every environment.

**Read the config first** (from the workspace root; `K=python3 $BATON/context-db/bin/kit_profile.py`):
`$K get tracker.kind`, `tracker.url_template`, `github.review_bot`, `github.owner_teams`,
`github.signed_commits`, `labels.shared`, `labels.repos.<repo>` (may be absent), `slack.enabled`,
`slack.repo_channels.<owner/repo>`, `commits.default` / `commits.repos.<owner/repo>` (§ Commit style). Never hardcode any of these values.

0. **Branch & push**: work in a worktree under `.worktrees/`. **Commit style first**: `python3
   $BATON/context-db/bin/commit_style.py resolve --dir <worktree>` names the convention (Conventional Commits
   unless the repo overrides it — § Commit style); every commit subject passes `commit_style.py check --dir
   <worktree> <msg-file>` before it is made or enqueued, and the PR title passes `commit_style.py title --dir
   <worktree> "<title>"` — same shape as the (squash) commit subject. If `github.signed_commits` (=
   `systems.signed_commits`) is true, the commit + push go through `/sign-queue` — enqueue, tell the user in
   one line, create the PR once the branch is on the remote. Otherwise commit and `git push -u origin <branch>`
   directly.
1. **Create** with a body file (`gh pr create --body-file …`): Overview with the tracker link (§ Links),
   Changes, **Diagrams** (§ Diagrams below — the set `diagram-plan.py` derives from the diff, plus its marker),
   Rollout/Test plan. Detail lives here, not in chat. Where `tracker.kind` is `github`, add
   `Closes #<n>` (or `Refs #<n>` for a partial step) so the issue links itself. The body's last line is
   `python3 $BATON/context-db/bin/kit_profile.py footer` — the attribution with the session that wrote it
   (`session-register` records the name); every comment you post on this PR ends with it too.
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
      unlabelled and never wait for someone to name one (owner decision, 2026-09-18: "the pr-open skill should
      attempt to always apply reasonable tags following the repo or best practices"). Rules: (a) reuse the
      repo's own vocabulary first (read the label list *and* the labels on the last ~30 human PRs — `gh api
      'repos/<o>/<r>/pulls?state=all&per_page=40' -q '.[] | [.labels[].name]'`); a repo whose convention is
      area-only stays area-only; (b) if nothing fits, create a short kebab-case name with a one-line
      description via `gh api -X POST repos/<o>/<r>/labels -f name=… -f color=… -f description=…` — directly
      in repos the user's org/team owns; in another team's repo apply the closest existing name and propose
      the new one in the review request instead; (c) record the new name in the env config's `labels.repos.<repo>`
      so the next session reuses it; (d) say in the terminal summary which labels were applied and which
      were created.
   Dependabot's `dependencies` / `python` / `github_actions` are automatic — never add them by hand.
   (An environment's per-repo map belongs in its env config → `labels.repos`; the prose per repo in `.context/reference/environment.md`.)
4. **Watch**: arm `/pr-watch` on the head sha (`$BATON/skills/pr-watch/pr-watch.sh <o/r> <n> <head>`).
5. **Tracker**: comment the PR link on the ticket (`ticket-update`). Where `tracker.kind` is `jira` and the
   PR is the ticket's deliverable, move it to *In Review* (`tracker.transitions.in_review`). Where it is
   `github`, the `Closes #<n>` in the body is the link; add the issue's in-review label if the repo uses one.
6. **Slack review request — as a DRAFT, never sent — only when `slack.enabled` is true.** When it is false,
   skip the step and say so in one line ("no Slack in this environment — reviewer request on GitHub is the ask").
   Channel = `slack.repo_channels.<owner/repo>` (the env fact `slack.review-venue <owner/repo>`, a channel
   handle; its id via `slack.channels.<name>` = `slack.channel <name>`). No entry → `/env-init slack.review-venue
   <owner/repo>`: the `slack` manifest lists the user's channels (`slack_list_user_channels` — a draft only lands
   where the user is a member); **the pick is this skill's call** — the channel where the repo's code owners read
   review asks (a `#help-<team>` / `#<repo>` channel whose name or purpose names the repo or the owning team),
   confirmed with the user in one question only when several fit — written back as the handle with `--from
   tool:slack_list_user_channels` (`--from user` for a pick the user made), plus the `slack.channel <handle>` row
   for its id if that is missing too. Never type a channel id from memory. Create the draft through the
   `slack-draft` skill (`slack_send_message_draft`); the user
   reviews and sends it themselves. Shape (§ Rules → Communication; owner decision 2026-09-24, the exact
   form the owner sends by hand): **one sentence** —
   ```
   One `<team>` review please on [<r>/pull/<n>](https://github.com/<o>/<r>/pull/<n>), `<scope>` only. cc <@reviewer> <@reviewer>
   ```
   No emoji opener (`:pray:` retired 2026-09-24 — "it looks ugly"), no ticket key, no "what it does" clause,
   no CI/bot status — the PR carries all of that; the ask names only the approval needed (the code-owner
   team), the link and the touched scope, and cc's the people who can give it. The MCP tool takes standard
   markdown and stores it as the mrkdwn link `<url|label>`; the mrkdwn form typed directly is stored
   identically (verified 2026-09-10, self-DM round-trip), so either works, but never a bare URL and never
   `repo#n`. Several PRs going up together: "Two reviews please:" + one `[<r>/pull/<n>](url), \`<scope>\`
   only` bullet per PR. No "Sent using Claude" footer. If a draft for the same channel is already pending
   (the user has not sent it), **update that draft** instead of adding a second one — say so in your
   terminal summary. **Related asks ride the same thread, not DMs** (owner, 2026-09-24): a follow-up for a
   dependent PR (e.g. a bot flag-sync PR that needs a re-approval) is a DRAFT *thread reply* under the
   review ask, cc'ing the one person who can act — never separate DMs to the reviewers. Afterwards the
   draft follows `slack-draft`: before re-drafting or reporting it "pending", read the channel to confirm
   whether the user sent it and in what form. Routing nuances beyond the one-channel map (split code-owner
   paths, team-internal PRs) live in `.context/reference/environment.md` / `slack._repo_channels_note`.
7. **Tell the user** in one line: PR link + labels + where the ask went ("review-request draft is in
   #<channel>" or "reviewers requested: …").

## Diagrams — the right set for this PR's content, derived from the diff (owner decisions 2026-09-17 / 2026-09-25)

"When you create a PR description please also add event flow diagrams, and component / architecture diagrams"
(2026-09-17) — refined 2026-09-25: "not just all diagrams randomly added but rather the right set of diagrams
based on PR content — if pipeline then pipeline flow, if model changes the table before and after, if
architecture then components". A reviewer should see *where the change sits*, *what changes* and *what runs*
without reading the diff — and nothing that the PR does not raise.

**Three principles**
1. **One artifact per reviewer question, only for the questions the content raises.** WHERE does the change
   sit (placement), WHAT changes (before → after), what RUNS when (flow). At most three blocks; most PRs need
   one or two. A refactor raises WHERE + WHAT, never RUNS; a bugfix raises RUNS only; a docs/config/deps/test
   PR raises none.
2. **The form follows the content — Mermaid is not always the answer.** Graphs and flows are Mermaid
   (`flowchart`, `sequenceDiagram`, `stateDiagram-v2`); a *delta* (columns, fields, resources, jobs, seed
   windows) is a markdown **table** `x | before | after | note`. A column list as an `erDiagram` is worse than
   the table.
3. **The plan is deterministic and comes from the diff, not from habit.** Changed paths → facets → plan,
   computed by `diagram-plan.py`; the same facets that pick the labels. The model draws exactly what the plan
   asks for and stamps the plan marker into the body so a later push can be checked for drift.

**Procedure**
1. From the workspace root, with the branch pushed or the diff local:
   ```
   python3 $BATON/skills/pr-open/diagram-plan.py --repo <repo-dir> [--base origin/main] [--type bugfix|refactor|feature]
   python3 $BATON/skills/pr-open/diagram-plan.py --pr <owner/repo> <n>      # after create: files + type label from GitHub
   ```
   `--repo` takes the clone's **directory** (the worktree you are about to push): `git diff` runs there and
   the env-config overlay key is read from its `origin` — sessions run from the workspace root, so a bare
   `git diff` in the cwd would answer "not a git repo". An `owner/repo` slug is still accepted with `--files`.
   It prints the facets with their weight, the dominant one, the plan (`WHERE` / `WHAT` / `RUNS`, a `?`
   suffix = conditional on its "only when" clause), notes, and the `<!-- diagram-plan: … -->` marker. `--type`
   is the PR *intent* (paths cannot show it); with `--pr` it is read from the type label (`bug`/`hotfix` →
   bugfix, `tech-debt`/`refactor` → refactor, else feature).
2. Draw **exactly** the planned blocks under `## Diagrams`, in WHERE → WHAT → RUNS order, each with a one-line
   caption naming the question it answers. A conditional block (`RUNS?`) is drawn only when its clause holds;
   otherwise one line says why not ("no routing change — no flow diagram"). `SKIP` → one line under
   `## Diagrams` ("Docs-only, no diagram." / "Config-only, no diagram.") and no block.
3. Paste the marker as the last line of the `## Diagrams` section (HTML comment, invisible on GitHub).
4. Validate every Mermaid block (below), then create/update the PR.
5. **On every later push** run `diagram-plan.py --pr <o/r> <n> --check` — `OK` / `DRIFT <old> → <new>` (exit 3) /
   `NO MARKER`. On drift, redraw in the same turn as the code (a new route file, a dropped RPC, a model added
   to the PR). The `pr-watch` head-move event is the reminder.

**Facet → question matrix.** The one copy is the script: `python3 $BATON/skills/pr-open/diagram-plan.py --explain`
prints every facet with its globs (first match wins; the env-config overlay before the defaults) and the WHERE /
WHAT / RUNS answer each facet raises, `—` where a question is not raised, `only when …` where it is conditional.
Read it there when a plan surprises you; never copy it into this file (it drifted here before).

Intent overlays: **bugfix** → RUNS only (one sequence of the failing path, fixed step in a `rect`); **refactor** →
WHERE as before → after, no flow. Composition: the dominant facet (most changed lines) answers first; a
secondary facet fills a question the dominant does not raise only when it carries ≥ 25 % of the dominant's
weight; a facet under 12 changed lines never earns a block. Three or more substantive facets → the note says
"one PR = one concern": draw the dominant one and name the rest in prose, or split the PR. Repo path
conventions that the core globs cannot know (a pipeline under `assets/…`, DDL that is really event schema,
route modules that are UI) go into the env config — `diagrams.repos.<owner/repo>.facets.<facet>: [globs]` and
`.ignore: [globs]`, read by the script before its defaults — never into this file.

**Rules that hold for every block**
- **GitHub renders ```` ```mermaid ```` natively** on PR bodies and comments — no images, nothing to upload.
  Jira does **not**: a ticket gets the PR link, never the diagram source.
- **Validate before publishing.** A syntax error renders as a red box for every reviewer. Parse every block
  with the mermaid library: `node $BATON/skills/pr-open/mermaid-check.mjs <body.md>…` from a scratchpad dir
  after `npm i --no-audit --no-fund mermaid@11 jsdom dompurify` (prints `OK (<type>)` / `FAIL <error>` per block, exit
  non-zero on any failure) — or, if that is impossible, re-read against these traps: a `;` inside sequence
  text **terminates the statement** (use `—`/`,` or parentheses); one message per line; quote node labels with
  `(`, `)`, `/`, `$`, `·`; `<br/>` for line breaks; edge labels `A -- text --> B` / `A -. text .-> B`.
- **Honest labels.** Tag nodes `(existing)` / `(this PR)` / `(follow-up <key>)`; highlight the changed nodes
  (`classDef new fill:#e6f4ea,stroke:#1e7e34` + `class a,b new`). A diagram that implies a handler, store or
  column exists when it does not is a false claim on a permanent record (`WORKSPACE.md` § Verification). A
  delta table's *before* column comes from the base branch (`git show <base>:<file>`, the schema yml, or the
  warehouse), not from memory.
- **Right-sized.** ≤ ~15 nodes / ≤ ~25 sequence steps per block, ≤ ~20 rows per table; more means the PR is
  probably too big. Ticket/PR ids inside Mermaid labels stay bare (labels cannot carry links) — link them in
  the prose.
- **Keep them current** — step 5 above; the marker is what makes drift detectable.

## Commit style — Conventional Commits by default, the repo may override

Owner decision, 2026-09-25: "by default we want to follow conventional commits if the repo doesn't override it"
(core rule, `WORKSPACE.md` § Rules → Workflow & scope; spec `$BATON/docs/commit-style.md`). The resolver is
`python3 $BATON/context-db/bin/commit_style.py` — repo marker (`<repo>/.claude/commit-style`: `conventional` |
`ticket-key` | `free`, or a commitlint config → conventional) → env config `commits.repos.<owner/repo>` →
`commits.default` → `conventional`. Never decide the style by reading the repo's recent log: a repo that drifted
is not a repo that overrode.

- **Conventional**: `<type>(<scope>)!: <description>` — types `feat fix docs chore refactor test ci build perf
  style revert`, lowercase scope (the component: `dbt`, `dag`, `<service>`, `kit`), `!` for a breaking change,
  lowercase imperative description, ≤ 72 chars, no trailing period. **The tracker key goes inside the
  description** (`feat(dbt): KEY-123 add the fact table`), never as the prefix — the key is text in the title
  (§ Links) and a link in the body.
- **PR title = the squash-commit subject.** Under squash merging the title becomes the commit on `main`, so it
  passes the same check (`commit_style.py title`). The type maps to the type label (step 3): `feat` →
  `enhancement`, `fix` → `bug`, `docs` → `documentation`; other types take no type label unless the repo has one.
- **Gates**: the kit repo's `hooks/commit-msg` (installed with `pre-push` via `core.hooksPath`), `sign-queue`'s
  `enqueue.sh` (refuses a message file that fails the check), and step 0 above for direct commits. Bypass only
  deliberately (`KIT_SKIP_COMMIT_STYLE=1` / `SIGN_QUEUE_SKIP_STYLE=1`) and say so.
- **Overrides are the repo's call, not the session's**: a repo that wants `ticket-key` (`KEY-123: description`)
  or `free` says so in its own marker file or in the env config's `commits.repos` (set by the user as one object — `kb.py
  config-set commits '{"default":"conventional","repos":{"<owner/repo>":"ticket-key"}}'` — a dotted key
  would break on a repo name with a dot). Never add a marker to someone else's repo.

## Links — every ticket reference is clickable, everywhere

Owner decision, 2026-09-10: "all tickets … whenever referenced should be clickable links" (core rule,
`WORKSPACE.md` § Rules → Communication). Applies to every surface this checklist touches — PR body, Slack
draft, tracker comment, and the terminal line to the user. Render every key with `tracker.url_template`
(`{key}` = the match of `tracker.key_regex`; for `tracker.kind: github`, `{key}` is the issue number and
`{repo}` its `owner/repo`):

| Surface | Ticket | PR |
|---|---|---|
| PR body / GitHub comment (markdown) | `jira`: `[<KEY>](<url_template>)` · `github`: `#<n>` (autolinks same-repo), `owner/repo#<n>` cross-repo | `#<n>` same-repo or `[<r>#<n>](url)` cross-repo |
| Slack draft (markdown → mrkdwn) | `[<KEY>](<url_template>)` | `[<r>/pull/<n>](https://github.com/<o>/<r>/pull/<n>)` |
| Jira comment (markdown) | `[<KEY>](<url_template>)` (bare keys autolink only in the same site — link anyway) | full URL, Jira autolinks it |
| Terminal message to the user | `[<KEY>](<url_template>)` / `[#<n>](<url_template>)` | `[#<n>](https://github.com/<o>/<r>/pull/<n>)` |

Never a bare key in prose on any of these, and never a bare URL (it unfurls and eats a line in Slack).
PR *titles* are the one exception (GitHub titles cannot carry links; keep the key there as text so
search finds it).

Don't DM individuals for reviews (§ Rules → Don't pester people); one ask in the right venue is the ask.
A follow-up nudge is the user's decision, not the session's.

## Not in scope
Merging, re-requesting the bot after pushes, thread handling — those are `/pr-watch` + the repo's
Coordination rules (`.context/repos/<repo>.md`). Signing mechanics are `/sign-queue` (only where
`github.signed_commits` is true).
