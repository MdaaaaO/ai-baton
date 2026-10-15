# codeowners.py — exit codes and reading the output

`python3 $BATON/skills/pr-open/codeowners.py` reads the repo's CODEOWNERS from the branch GitHub evaluates
(the PR's base branch with `--pr`, the default branch with `--paths`). Used by `pr-open` step 2 (`resolve`) and
by `pr-watch` on a real `HEAD MOVED` (`check`). A failed read is never an answer: exit 2 is not "no owners"
and not "OK".

## `resolve <o>/<r> --pr <n>` (pr-open step 2)

| Exit | Meaning | Do |
|---|---|---|
| 0 | stdout is the owners of the changed paths, one `team:<slug>` / `user:<login>` per line | request exactly those, plus a non-empty `github.review_bot`: `gh api -X POST repos/<o>/<r>/pulls/<n>/requested_reviewers` with `-f 'reviewers[]=…'` / `-f 'team_reviewers[]=…'` |
| 3 | the repo is readable but no CODEOWNERS line owns a changed path, or there is no CODEOWNERS file | nothing to request from owners — say so (the bot is still requested when `github.review_bot` is set) |
| 2 | the read failed; stderr `FAIL …` says why (repo or PR not readable, a `gh` call failed) | never "nothing to request": retry once, then request by hand from the CODEOWNERS file you can see, or say the reviewer step is blocked |

`github.owner_teams` only names the approval you cannot give yourself; it is never the list to request. The PR
author is left out of the output (GitHub refuses a review request on it); an e-mail owner is skipped with a
stderr note.

## `check <o>/<r> --pr <n> [--ignore <github.review_bot>]` (pr-watch, head moves)

| Exit | Output | Do |
|---|---|---|
| 0 | `OWNERS OK …` | nothing |
| 3 | `OWNERS CHANGED +<owner> …` | report that one line and request the `+` owners as in step 2 above |
| 2 | stderr `FAIL …` | the read failed, never "OK": retry once, then check the CODEOWNERS file by hand or say the step is blocked |

- The check only asks for MISSING owners; it never asks to remove anyone. A reviewer the author requested by
  hand is one stderr `note:` only.
- A team counts as reviewed once one of its members reviewed (GitHub drops the team from the requests then).
  When the membership is not readable (403/404), a team that was ever requested counts once any review exists
  from a login other than the author and `--ignore`, with a stderr `note:`.
