# Rules baked in

- **Weekdays only for rates.** A Saturday with $9 halves a per-day average. For the control row this
  filter runs in `ingest`, in Python (`date.isoweekday()`), not in SQL — the control query carries no
  weekday predicate for a warehouse to reject.
- **Cap work data at both the first and the last spend day.** A PR opened before the first or after the
  last billed day inflates $/PR in either direction.
- **Draft PRs are not opened work.** `work-prs` excludes a hit with `isDraft` true — it cannot be merged
  without leaving draft first, so it never double-counts later.
- **Exclude the changeover day** from before/after buckets (`X=<day>..<day>` in `cost.phases`); it is
  neither. Compare a **steady baseline** (post-onboarding weeks) rather than the whole history.
- **Overlapping phases are not compared** (P0 ⊃ P0b): the decomposition takes non-overlapping pairs only.
- **A phase name is never `prior-7d`/`rolling-7d` or a week label (`YYYY-Wnn`)** — the rolling pair `report`
  always builds and the keys of its week buckets; reusing one exits 2.
- **Cheap models are Sonnet/Haiku by name**; an org table's `model_family` column is not needed.
- **Per-epic billed cost does not exist.** The bill has no ticket dimension; the only per-epic view is the
  session ledger estimate (`.context/sessions/_ledger.md`), labelled as such, with its unlabelled share.
- **No naming, no ranking.** The report names no peer, no team member, no manager; the control is an
  aggregate. Self-analysis is the only purpose. This holds for the report, the doc and the chat reply.
- **Say what was skipped.** The Basis lists every factor that could not be computed (no control in private
  mode, tickets not supplied, estimate not bill). A number without its basis is not a finding.
- **Numbers are parsed strictly, never silently zeroed.** A row whose amount/date does not parse (not a
  finite number, not `YYYY-MM-DD`, not a `$`/thousands-separator form like `$1,234.50`) exits 2 naming the
  row (counted from 1) and column instead of counting as 0. `--since`/`--until` are checked the same way, in every subcommand
  that takes them.
- **A rate-limited `gh search` retries, bounded.** `work-prs`/`work-tickets` wait 10, 20, then 40 s
  (each wait announced on stderr) when `gh`'s failure says "rate limit" or reports HTTP 429
  (`COST_REPORT_RETRY_DELAY`, `docs/env-vars.md`) — a bare 403 (SAML enforcement, a scope error) is not
  retried; any other `gh` failure, or the retries running out, is a clear error naming the scope/repo
  and month — never a partial TSV.
