#!/usr/bin/env bash
# The push-to-main backstop's decision logic. .github/workflows/main-guard.yml checks out the pushed
# commit and calls this script:
#
#   GH_TOKEN=... REPO=<o>/<r> SHA=<sha> ACTOR=<login> FORCED=<true|false> .github/scripts/main-guard.sh
#
# Exit 0 — the commit is the squash of a merged PR (or a retry found it) — or exit 1 after opening or
# commenting on a tracking issue for a direct push or a forced push.
#
# MAIN_GUARD_ATTEMPTS (default 6) and MAIN_GUARD_PAUSE_SECONDS (default 10) are overridable so a test can
# drive the retry loop with a stub `gh` on PATH and zero pause; the defaults stay well inside the job's
# 5-minute timeout.
set -euo pipefail

: "${GH_TOKEN:?}" "${REPO:?}" "${SHA:?}" "${ACTOR:?}"
FORCED="${FORCED:-false}"
attempts="${MAIN_GUARD_ATTEMPTS:-6}"
pause="${MAIN_GUARD_PAUSE_SECONDS:-10}"

# "commits/<sha>/pulls" lists the PRs GitHub associates with this commit. A squash merge makes the pushed
# commit both a PR's merge_commit_sha and merged_at non-null; a direct push (or a merge/rebase merge, which
# CONTRIBUTING says is disabled) has no such match. `--jq` is gh's own built-in filter (no standalone jq
# dependency on the runner); env.SHA reads the step's env rather than string-interpolating $SHA into the
# filter. The call's exit status is checked on its own, separately from the value it prints — a failing
# call always exits non-zero here, never silently read as "no match".
#
# GitHub's commit-to-PR association can lag a squash merge by a few seconds, so a run that starts right
# after the merge can see a false "no match" that the same lookup would answer correctly moments later. A
# forced push is flagged regardless of the match, so it gets exactly one lookup; any other miss is retried,
# paced `pause` seconds apart, before it is treated as a real direct push.
matched=0
attempt=1
while true; do
  if ! matched="$(gh api "repos/$REPO/commits/$SHA/pulls" --jq '[.[] | select(.merged_at != null and .merge_commit_sha == env.SHA)] | length')"; then
    echo "::error::gh api repos/$REPO/commits/$SHA/pulls failed" >&2
    exit 1
  fi
  if [[ "$matched" -gt 0 ]]; then
    break
  fi
  if [[ "$FORCED" == "true" || "$attempt" -ge "$attempts" ]]; then
    break
  fi
  attempt=$((attempt + 1))
  sleep "$pause"
done

# A force-push that rewinds `main` to an EARLIER squash commit still matches a merged PR, yet it is
# the one push shape that makes every machine's fast-forward-only sync.sh refuse to pull — so a
# forced push is flagged regardless of the match.
if [[ "$matched" -gt 0 && "$FORCED" != "true" ]]; then
  echo "main-guard: $SHA is the squash of a merged PR — ok"
  exit 0
fi

short_sha="${SHA:0:7}"
if [[ "$FORCED" == "true" ]]; then
  title="main-guard: force-push $short_sha to main"
  what="Commit \`__SHA__\` was **force-pushed** to \`main\` (pushed by @__ACTOR__); every machine's fast-forward-only \`sync.sh\` refuses to pull wherever it had already synced past this commit (those machines now hold commits that are not on \`main\`) until \`main\` is restored or each such machine runs \`git reset --hard origin/main\`."
else
  title="main-guard: direct push $short_sha to main"
  what="Commit \`__SHA__\` landed on \`main\` without going through a squash-merged PR (pushed by @__ACTOR__)."
fi
# A $'...' literal — its backticks are inert, only \n is special, so the placeholders below are filled
# by plain bash string replacement, not shell expansion.
body_template=$'__WHAT__\n\nEvery kit change is meant to travel issue \xe2\x86\x92 branch \xe2\x86\x92 PR \xe2\x86\x92 squash-merge (docs/contributing.md \xc2\xa7 From issue\nto merge). `hooks/pre-push` is the local guard against this; it is bypassed by\n`KIT_ALLOW_MAIN_PUSH=1`, `git push --no-verify`, a fresh clone before `setup.sh`/`sync.sh`\ninstalled `core.hooksPath`, another machine, or the GitHub UI \xe2\x80\x94 the `main` ruleset on the repo lets\nrepo admins bypass it, so this workflow is the server-side backstop that notices after the fact.\n\nSee CONTRIBUTING.md \xc2\xa7 From issue to merge.'
body="${body_template//__WHAT__/$what}"
body="${body//__SHA__/$SHA}"
body="${body//__ACTOR__/$ACTOR}"

# Dedup: a re-run of this job for the same commit must not open a second issue. The issues API
# (exact title match in --jq via env.TITLE; no label filter, so the label-less fallback below is
# found too) is read directly — the search index lags a fresh issue by seconds to minutes.
if ! existing_number="$(TITLE="$title" gh issue list --repo "$REPO" --state open --limit 200 --json number,title --jq '[.[] | select(.title == env.TITLE)][0].number // empty')"; then
  echo "::error::gh issue list failed while checking for an existing main-guard issue" >&2
  exit 1
fi

if [[ -n "$existing_number" ]]; then
  gh issue comment "$existing_number" --repo "$REPO" --body "$body"
else
  # `area:ci` and `bug` are the repo's own labels (docs/contributing.md § Labels); should one be missing,
  # the issue is still opened without labels — the record matters more than its labels.
  gh issue create --repo "$REPO" --title "$title" --body "$body" --label area:ci --label bug \
    || gh issue create --repo "$REPO" --title "$title" --body "$body"
fi

echo "::error::$title"
exit 1
