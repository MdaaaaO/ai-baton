#!/usr/bin/env bash
# Every change comes from a ticket: the PR body must link the issue it came from.
#
#   PR_TITLE="..." PR_BODY="..." .github/scripts/check-pr-issue.sh   (CI: the pr-issue check)
#   .github/scripts/check-pr-issue.sh "<title>" <body-file>          (locally, before gh pr create)
#
# The owner's other repos carry a copy of this check (same rule, same parser); keep them in step.
#
# Accepted, anywhere in the body, one per line or several:
#   Closes #12      the PR finishes the issue — GitHub closes it on merge
#   Fixes #12       (Closes / Fixes / Resolves, any case)
#   Refs #12        the PR is a step of a larger issue — it stays open
#
# Exempt: release PRs ("chore(release): …") and dependabot — neither comes from a ticket.
# Prints the linked issues as "closes 12" / "refs 12" lines, so callers can reuse the parsing.
set -euo pipefail

if [[ $# -ge 2 ]]; then
  title="$1"
  body="$(cat "$2")"
else
  title="${PR_TITLE:-}"
  body="${PR_BODY:-}"
fi
author="${PR_AUTHOR:-}"

if [[ "$title" == "chore(release):"* || "$author" == "dependabot[bot]" ]]; then
  echo "pr-issue: exempt ($title)" >&2
  exit 0
fi

# Only prose counts. Stripped first: HTML comments (the PR template's own hint says "Closes #"),
# fenced code blocks and `inline code` — a PR that *describes* the syntax, or quotes a log,
# must not link (and later close, and move the card of) the issues in its examples. GitHub's
# own auto-close ignores code spans the same way.
# Bash and sed only (no perl), the same on GNU and BSD: the two multi-line spans go by parameter
# expansion — first opener to the first closer after it, as a non-greedy match would; one left
# unclosed stays — and inline code line by line with sed, since a code span never crosses a line.
strip_spans() {  # <text> <open> <close>
  local s="$1" out=""
  while [[ "$s" == *"$2"*"$3"* ]]; do
    out+="${s%%"$2"*}"
    s="${s#*"$2"}"
    s="${s#*"$3"}"
  done
  printf '%s' "$out$s"
}
clean="$(strip_spans "$body" '<!--' '-->')"
clean="$(strip_spans "$clean" '```' '```')"
clean="$(LC_ALL=C sed -E 's/`[^`]*`//g' <<<"$clean")"
# Keyword, optional colon, whitespace, #N — "Fixes #12" and "Fixes: #12" both close an issue on
# GitHub, so both must count here, or the check and GitHub would disagree about what a PR closes.
links="$(grep -oiE '\b(close[sd]?|fix(e[sd])?|resolve[sd]?|refs?):?[[:space:]]+#[0-9]+' <<<"$clean" \
  | sed -E 's/^[Rr][Ee][Ff][Ss]?:?[[:space:]]+#/refs /; s/^[A-Za-z]+:?[[:space:]]+#/closes /' | sort -u || true)"

if [[ -z "$links" ]]; then
  cat >&2 <<'MSG'
pr-issue: this PR links no issue.

Every change comes from a ticket. Add to the PR description:
  Closes #N     if this PR finishes issue N
  Refs #N       if it is one step of issue N
No ticket yet? Create it first (an area: label) — see CONTRIBUTING.md,
"From issue to merge".
MSG
  exit 1
fi

# A link has to point at a real issue: `Closes #9999` would pass a syntax check, merge, and only
# then fail — after the point where anything can be done about it (a review finding).
# Needs gh with a token: GH_TOKEN in CI, the normal login locally. Without either, say so
# and keep the syntax check rather than block work offline.
if command -v gh >/dev/null 2>&1 && { [[ -n "${GH_TOKEN:-}" ]] || gh auth status >/dev/null 2>&1; }; then
  repo="${GITHUB_REPOSITORY:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}"
  bad=0
  while read -r _ issue; do
    # Exit status first, output second: on a 404 gh still prints the error body through --jq
    # before failing, so "value || fallback" yields *both* and matches nothing below.
    if kind="$(gh api "repos/$repo/issues/$issue" --jq 'if .pull_request then "pr" else "issue" end' 2>/dev/null)"; then
      :
    else
      kind="missing"
    fi
    case "$kind" in
      issue) ;;
      pr)      echo "pr-issue: #$issue is a pull request, not an issue — link the ticket the work came from." >&2; bad=1 ;;
      missing) echo "pr-issue: #$issue does not exist in $repo." >&2; bad=1 ;;
      *)       echo "pr-issue: could not tell what #$issue is (got: $kind)." >&2; bad=1 ;;
    esac
  done <<<"$links"
  (( bad == 0 )) || exit 1
else
  echo "pr-issue: gh not available — linked issues not verified to exist" >&2
fi

echo "$links"
