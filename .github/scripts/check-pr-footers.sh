#!/usr/bin/env bash
# A breaking change reaches main only as a `BREAKING CHANGE:` footer of the squash commit, and the squash carries
# the PR body's trailing footer block and nothing else (auto-merge.yml; squash-footers.jq is the one parser).
# The release tool reads that footer for the bump level and the release notes (docs/contributing.md § Releases).
#
#   PR_TITLE="..." PR_BODY="..." .github/scripts/check-pr-footers.sh   (CI: a step of the pr-title check)
#   .github/scripts/check-pr-footers.sh "<title>" <body-file>          (locally, before gh pr create)
#
# Fails when
#   - the title marks a breaking change (`feat(x)!: …`) and the footer block has no `BREAKING CHANGE:` line;
#   - the body has a `BREAKING CHANGE:` line outside the footer block — the squash would drop it.
# Whether § Machines' prose names a step every machine must take is the reviewer's call, not this script's.
# Exempt: release PRs ("chore(release): …") and dependabot — their bodies quote other projects' notes.
# Prints the footer block (what the squash commit will carry), so callers can reuse it.
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
  echo "pr-footers: exempt ($title)" >&2
  exit 0
fi

footers="$(printf '%s' "$body" | jq -Rjs -f "$(dirname "$0")/squash-footers.jq")"
breaking='^BREAKING[ -]CHANGE: '

# Lines of prose only: fenced code (closed by its own marker, ``` or ~~~) and HTML comments (the PR
# template's own hints) do not count.
prose="$(printf '%s\n' "$body" | tr -d '\r' | awk '
  match($0, /^[[:space:]]*(```|~~~)/) {
    mark = substr($0, RSTART + RLENGTH - 3, 3)
    if (fence == "") { fence = mark; next }
    if (mark == fence) { fence = ""; next }
  }
  fence != "" { next }
  {
    line = $0; out = ""
    while (line != "") {
      if (incomment) {
        j = index(line, "-->"); if (!j) { line = ""; break }
        line = substr(line, j + 3); incomment = 0
      } else {
        i = index(line, "<!--"); if (!i) { out = out line; break }
        out = out substr(line, 1, i - 1); line = substr(line, i + 4); incomment = 1
      }
    }
    print out
  }')"

in_body="$(grep -cE "$breaking" <<<"$prose" || true)"
in_footers="$(grep -cE "$breaking" <<<"$footers" || true)"
bad=0

if [[ "$title" =~ ^[a-z]+(\([^\)]*\))?!: ]] && (( in_footers == 0 )); then
  cat >&2 <<'MSG'
pr-footers: the title marks a breaking change (`!`) but the body ends in no `BREAKING CHANGE:` footer.
End the body with a blank line and then one line per step every machine must take:
  BREAKING CHANGE: <the step, e.g. run `kb.py migrate` on every machine>
MSG
  bad=1
fi
if (( in_body > in_footers )); then
  cat >&2 <<'MSG'
pr-footers: a `BREAKING CHANGE:` line sits outside the footer block, so the squash commit would drop it.
Move it to the very end of the body, after a blank line, with only footer lines (`Token: value`, `Refs #N`) below it.
MSG
  bad=1
fi
(( bad == 0 )) || exit 1

[[ -z "$footers" ]] || printf '%s\n' "$footers"
