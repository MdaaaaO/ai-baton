# kb-writeback.md — the format and rules behind step 8 item 1

Add only **verified, reusable** facts to `.context/pr-reviews/<repo>.md` — a trap to check on future
PRs, a mechanic confirmed against the wheel/spec/data, a consumer map, a convention the repo docs do
not state. Never store the PR narrative here (that is the session log) and never anything the repo's
own docs already say.

Format: `- **<one-line rule>** — <how to check / why>. Verified on #<pr> (<date>).`

- Put traps under a `## ` or `### ` heading that contains "traps" (case-insensitive) — `## Repo-specific traps`
  (what `make new TYPE=pr-review` writes), `## Known traps`, `## Traps`. `fetch-context.sh` builds the runner's
  `kb-traps.md` from those headings plus any `## ` section whose title shares a word with a touched path. A KB with
  no such heading is passed whole, headed by `TRAPS: no traps section, whole KB passed`; rename the section rather
  than rely on the fallback.
- Grep for an existing entry first and extend it rather than duplicate; a trap that fired again gets
  its "seen on" list extended, which is the signal it deserves a repo fix.
- If the file passes 30KB, move narrative sections to `.context/archive/pr-reviews-<repo>-log.md`.
- Source every fact from `$CTX/triage.json` (`evidence`) and the walk decisions — the main session
  does not re-read the diff; a fact that needs re-verification goes to a one-question child.
