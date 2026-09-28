# Labels — creating one the repo doesn't have yet

Loaded from `SKILL.md` step 3.4. If the repo has no name for an axis that applies, **create a
best-practice label** — never ship the PR unlabelled and never wait for someone to name one (owner
decision, 2026-09-18: "the pr-open skill should attempt to always apply reasonable tags following the
repo or best practices"). Rules:

(a) reuse the repo's own vocabulary first (read the label list *and* the labels on the last ~30 human
PRs — `gh api 'repos/<o>/<r>/pulls?state=all&per_page=40' -q '.[] | [.labels[].name]'`); a repo whose
convention is area-only stays area-only;

(b) if nothing fits, create a short kebab-case name with a one-line description via `gh api -X POST
repos/<o>/<r>/labels -f name=… -f color=… -f description=…` — directly in repos the user's org/team
owns; in another team's repo apply the closest existing name and propose the new one in the review
request instead;

(c) record the new name in the env config's `labels.repos.<repo>` so the next session reuses it;

(d) say in the terminal summary which labels were applied and which were created.

Dependabot's `dependencies` / `python` / `github_actions` are automatic — never add them by hand. (An
environment's per-repo map belongs in its env config → `labels.repos`; the prose per repo in
`.context/reference/environment.md`.)
