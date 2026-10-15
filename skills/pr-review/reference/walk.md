# walk.md — the main session's walk, post and thread replies (steps 5–7)

Loaded from `SKILL.md` § 5 when the runner's sheet comes back. Everything here runs in the main session: the
user decides, the post carries their login. Read `$CTX/triage.json`, never the diff.

## 5. Triage — overview, then walk every finding with the user (in the terminal, not on GitHub)

Same shape as `notion-page-review` (docs/carousel.md): an overview first, then **one `AskUserQuestion` per batch
of 4 findings**, so the user approves, rewrites, deep-dives or drops each item before anything is posted. Numbers
are assigned once and never change — the user refers to "#3" later.

**5a. Overview** (plain text, before the first question):
```
REVIEW SHEET <repo>#<pr> @ <head7> · mode <mode> · <author> · <title> · +A/-D F files
bot: <assessment> · humans: <…> · checks: <…> · traps checked: <n> (<hits>)
[lenses: design, failure modes, verification[, security (<reasons>)][ · security gate failed: <reason>]]   ← only on `--deep`
[<System> enrichment: n/a in this environment]   ← one per skipped gate
| # | sev | class | file:line | finding (one line) |
…
RECOMMEND: COMMENT | REQUEST_CHANGES | APPROVE-if-user-agrees
```
The `bot:` field is the `github.review_bot` verdict (omit it when that key is empty). An environment gate whose
`systems.<x>` flag is false gets its one `<System> enrichment: n/a in this environment` line — never an improvised
substitute. Order: STOP → WARN → NIT, file order within a severity. PR and tracker keys as clickable links
(`tracker.url_template`); numbers in the table, not prose. The runner has already written the sheet to
`$CTX/triage.json` (`[{n, sev, class, path, line, side, finding, evidence, comment, decision:null}]`) — print the
overview from that file, never from the diff. The loop survives a compaction only if the decisions land in that
file after every batch. A PR a human already approved with green checks gets a lighter touch (nits → body, or
skip; ask the user).

**5b. The loop** — `AskUserQuestion`, 4 findings per call, sheet order:
- `header`: `"#<n> <sev>"` (≤ 12 chars; a revision becomes `"#<n> v2 <sev>"`).
- `question`: `file:line · <class>` line, blank line, `Finding:` one or two sentences with the evidence named
  (what was run / read to establish it), blank line, `Proposed inline comment:` the exact text in quotes, blank
  line, `Post this? (type a wording change or a question as Other)`.
- options, always these four, in this order: **Post (Recommended)** · **Deep dive** · **Body only** (drop the
  inline anchor, one softened line in the review body) · **Skip**. For a finding you recommend dropping yourself,
  put **Skip (Recommended)** first instead.
- **Deep dive** = the user wants more before deciding. Do the work now — run the SQL (`systems.datalake`), read
  the source at the tag, `git show origin/main:`, spawn an `opus` lens on that one question (`runner.md` § Deep
  dives during the walk) — then answer in the terminal (≤ 10 lines, evidence named) and **re-present the item as
  `vN` in the next batch** with its severity re-graded (up, down, or withdrawn). A deep dive can also be the
  user's question about the code itself; answer it the same way, the re-presented item then says what changed
  (nothing is fine).
- Any free-text answer = a rewrite or a redirect. Rewrite, re-present as `vN` in the next batch, never post an
  unseen rewrite. A redirect ("ask the author" — in Slack only where `systems.slack` / "open a ticket") leaves the
  loop and lands in `FF` (fast follows) at the end.
- After **each** batch: write the decisions into `$CTX/triage.json`, then ask the next batch in the same turn.
  Never post mid-loop — a GitHub review is one atomic POST (step 6).

**5c. Close the loop** — one last `AskUserQuestion`:
- `question`: the counts (posted / body-only / skipped / deep-dived), the **body draft** in full (summary line,
  body-only items, labels note, KB/FF pointers), and the recommended event with its reason per `scope.md`
  § Event recommendation.
- options: **COMMENT** · **REQUEST_CHANGES** · **APPROVE** · **Edit body** (types via Other). Flag the
  recommended one `(Recommended)`; APPROVE / REQUEST_CHANGES are never inferred (`scope.md`).
- `--post` (or "review and post") skips 5b/5c **only** for a COMMENT with no STOP finding; the overview is still
  printed.

## 6. Post exactly one review

Write `$CTX/request.json` (`{"event","body","comments":[{"path","line","side","body"}]}`; `line` is the line
number in the **new** file for `RIGHT`, old file for `LEFT`; it must be inside a hunk of `diff.patch` or GitHub
rejects the whole review — nothing half-posts). Then:
```sh
bash $BATON/skills/pr-review/scripts/submit-review.sh preview --repo <o/r> --pr <n> --head <full sha> --request $CTX/request.json
```
Show the preview (event, anchored comments, body, digest) — every item in it was approved in 5b/5c, so this is a
last look, not a second triage. One `AskUserQuestion`: **Submit (Recommended)** · **Abort**. On Submit:
```sh
bash $BATON/skills/pr-review/scripts/submit-review.sh submit … --confirm <digest>
```
The script refuses a closed/merged PR (set `PR_REVIEW_ALLOW_CLOSED=1` for a post-merge **COMMENT** with inline
anchors; never APPROVE/REQUEST_CHANGES post-merge — a PR can merge mid-walk), a moved head, a digest mismatch, a
path outside the diff, and a second submit of the same digest; it appends `reviewed` to the ledger and prints
`status: submitted · review <id> · url`. `uncertain` → check GitHub before any retry. `failed` → fix the request
(usually a line outside a hunk) and preview again. The body gets no footer (config `footer` is empty by owner
decision — attribution text only costs the reader) and the marker `<!-- pr-review:v1 head=… base=… by=<login> -->`
— that marker is how the next run knows the mode.

## 7. Thread replies (follow_up / replies_only)

`threads.json` lists every thread with `mine`, `last_author`, `id`. With `threads_complete: false` (the runner's
`NOTE:` says so) reply to nothing and resolve nothing. Otherwise draft replies per `review-writing.md` § Replies,
then run the **same loop as 5b**, 4 threads per `AskUserQuestion`:
- `header`: `"T<n> <who>"`; `question`: thread location + what the author said (quoted, ≤ 3 lines), blank line,
  `Proposed reply:` in quotes, blank line, `Reply?`.
- options: **Reply (Recommended)** · **Reply + resolve** (only offered on a thread we opened whose point is
  settled — otherwise omit it) · **Deep dive** · **Skip**. Free text = rewrite → `vN` next batch.
- Decisions land in `$CTX/replies.json` after every batch
  (`{"replies":[{"thread_id":"PRRT_…","body":"…","resolve":false}]}`); preview with `reply-threads.sh`, show the
  digest, submit on the user's go. The script refuses `resolve:true` on any thread we did not open
  (`dont-resolve-threads-pending-a-third-party`).
