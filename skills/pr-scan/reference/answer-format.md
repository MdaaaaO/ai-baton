# pr-scan — Answer column rules

Loaded from `SKILL.md` § Answer. Same meaning in every section.

- `#` — row number, counting across sections (not restarting per section); `PR` — always a `[repo#n](url)`
  link, repo without the `<org>/` prefix.
- `Prio` — `<n> · <word>`: `1 · direct`, `2 · follow-up`, `3 · team`, `4 · sweep`, `5 · other`, `6 · reviewed`; append ` ‼ deep`
  for `!` (over the deep threshold) and ` ✓ auto` for `A` (passed the trivial gate). Drop the raw `*` marker — first-time
  rows are already counted in `new`.
- `Why now` — one short clause of fact (≤ 40 chars) that adds something the other columns do not: prio 1 → `requested MM-DD`;
  prio 2 → `new head since your APPROVE` / `author replied in N threads`; prio 3 → `via <team>` (one of the env config's `github.owner_teams`);
  prio 4/5 → `opened MM-DD`; prio 6 → `your review on this head`. Never write "no human review", "no review yet", "bot green" or a thread count here — the
  `Humans · Thr · Bot` column already carries those.
- `Size` — `lines/files` (no spaces).
- `Humans · Thr · Bot` — last state per human reviewer (`APP`/`CHA`/`COM`, comma-joined, `–` if none) · unresolved
  thread count · review-bot Assessment on this head (`🟢`/`🟡`/`🔴`, `–` if none).
- `Author · Title` — first name from the profile's `github.display_names` map (`python3 $BATON/context-db/bin/kit_profile.py get github.display_names`),
  otherwise the login verbatim (never a capitalised login) · title truncated to 45 characters with `…`; never wrap a cell.
- `State` — `.state_label` verbatim (e.g. `handled (review #123456)`, `needs you (STOP)`, `new (auto)`, `follow-up (manual)`, `watching (you approved)`).
- Header line: date **and** time as `YYYY-MM-DD HH:MM UTC` (from `date -u`), counts from the summary line; `dropped:` lists only
  non-zero buckets.
- Trailing lines: `**Auto**` repeats the gate facts only (`class`, `packages`, CI, threads from `queue.json .auto`) or `none`;
  `**AUTO-COMMENT**` is `(<auto_comment_mode>) · ` then every eligible row as a `[repo#n](url)` link (comma-joined) or `none` —
  read `.auto_comment.eligible` off `queue.json`, never recompute the gate here; `**Next**` carries the errors count. Nothing
  before the header line; the `MARK` trailer (above) is the only thing after `**Next**`.
