# scope.md — what a finding is, how hard to push, which event to recommend

## Classify every candidate finding

| Axis | Question | Typical evidence |
|---|---|---|
| Correctness | Does the change do what the PR says, for all the rows/paths it touches? | SQL run against the datalake, `git show origin/main:<path>` for the baseline, a traced code path |
| Data integrity | Grain, duplicates, NULL semantics, timezone, backfill/idempotency, write-audit-publish routing | a `GROUP BY` distribution, the model's `unique_key`, the runner's exclusion list |
| Interface / contract | Column renames, proto/schema changes, DAG ids, consumers downstream | the schema/contract definitions, dbt `ref()` graph, BI/consumer names |
| Operability | Alerts, retries, timeouts, secrets handling, runbook, rollback | DAG args, `on_failure_callback`, SSM paths (never print values) |
| Security | PII exposure, grants wider than asked, tokens in argv/logs | the grant statement, `ps`-visible flags |
| Verification gap | Claim in the PR body that nothing in the diff or CI proves | absence of a test, no run link, "tested locally" |
| Slop | AI residue that will confuse the next reader (see `slop.md`) | the diff itself |
| Stakeholder impact | Who reads this model/column downstream, and what changes for them that the PR body does not say? | `$CTX/impact.json`: the models' dependency and exposure graph (`ref()`s in a dbt repo), BI workbooks (the BI tool's MCP search), chat-alert/export workspaces, plus a SQL count of the rows/values that flip |

Rate each finding **STOP / WARN / NIT**:
- **STOP** — wrong output, data loss, security exposure, a contract break for a known consumer. Blocks merge; needs evidence a reader can re-run.
- **WARN** — likely defect or missing verification that the author should answer before merge; phrased as a question when the evidence is circumstantial. An **undisclosed value change** for a known consumer (a column a BI workbook / alert / export reads changes value or goes NULL and the body does not say so) is at least WARN; STOP when the consumer is customer- or exec-facing and the change is material.
- **NIT** — style, naming, wording, a nicer idiom. Prefix the posted comment `nit:` or `suggestion:`; never blocks.

## Proportionality

- Post at most what the PR can absorb: a 20-line fix gets ≤3 inline comments; if you have more, merge them into the body.
- A finding outside the diff (a pre-existing bug the PR merely touches) goes in the body as a **fast follow**, labelled `FF1`, `FF2`…, with the ticket you'd open — not as an inline blocker. Offer to open the backlog ticket (the environment's convention for architecture findings, `environment.md` § Rules); never auto-assign it.
- Repeat findings once. The second instance of the same pattern gets "same as above" or nothing.
- No finding without evidence. "Might be wrong" is a question, not a finding — ask it as one.
- Refuted candidates you investigated are worth one line in the body ("Checked: X is fine because Y") when the author or another reviewer is likely to wonder; otherwise drop them.

## Event recommendation

| Situation | Recommend | Who decides |
|---|---|---|
| Any STOP survives triage | `REQUEST_CHANGES` | the user |
| Only WARN/NIT, or questions | `COMMENT` (default) | the user |
| Nothing blocking, the user read it and is satisfied | `APPROVE` | the user — never inferred |
| Already approved by a human and we only add nits | `COMMENT`, or skip entirely | the user |
| Trivial PR (docs-only / dependency patch bump) **the user was asked to review** (direct request or a team in `auto_approve.owner_teams`; never a repo-sweep row) that passed `trivial-check.py` and the Sonnet `auto-runner` pass with zero findings | `APPROVE` on the user's behalf without a walk (mode `live`); mode `shadow` only logs "would approve" | owner's standing decision 2026-09-19 (shadow until the review date in `auto_approve.shadow_review_due`) — the one exception to "never inferred" |
| Direct review request (`pr-scan` `C`, prio in `auto_comment.prios`) reviewed in full by `review-runner` (no shortcut on steps 1–4) with no STOP finding | `COMMENT` posted on the user's behalf without a walk (mode `live`); mode `shadow` only logs "would comment" | opt-in config, off by default (`auto_comment.mode`) — COMMENT only, never APPROVE/REQUEST_CHANGES; `pr-review/SKILL.md` § Unattended auto-COMMENT path |

The skill never chooses APPROVE or REQUEST_CHANGES on its own; the recommendation is one line in the triage sheet.

## Follow-up mode (author pushed after our review)

Build the ledger before reading the new diff: for each finding of our previous review — **Addressed** (fix visible in the delta or in the author's reply, say where), **Outstanding** (unchanged, restate briefly, do not re-argue), **Superseded** (the code it referred to is gone), **Reversed** (we were wrong — say so plainly in the thread). Review only the delta between our marker head and the new head for new findings. Reply inside the existing threads (`reply-threads.sh`), post a new review only if the delta has new findings or the user wants to change the verdict.
