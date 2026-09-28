# Slack review request — the draft's exact shape and edge cases

Loaded from `SKILL.md` step 6. Channel = `slack.repo_channels.<owner/repo>` (the env fact
`slack.review-venue <owner/repo>`, a channel handle; its id via `slack.channels.<name>` =
`slack.channel <name>`). No entry → `/env-init slack.review-venue <owner/repo>`: the `slack` manifest
lists the user's channels (`slack_list_user_channels` — a draft only lands where the user is a member);
**the pick is this skill's call** — the channel where the repo's code owners read review asks
(a `#help-<team>` / `#<repo>` channel whose name or purpose names the repo or the owning team), confirmed
with the user in one question only when several fit — written back as the handle with `--from
tool:slack_list_user_channels` (`--from user` for a pick the user made), plus the `slack.channel <handle>`
row for its id if that is missing too. Never type a channel id from memory.

Create the draft through the `slack-draft` skill (`slack_send_message_draft`); the user reviews and sends
it themselves. Shape (§ Rules → Communication; owner decision 2026-09-24, the exact form the owner sends
by hand): **one sentence** —

```
One `<team>` review please on [<r>/pull/<n>](https://github.com/<o>/<r>/pull/<n>), `<scope>` only. cc <@reviewer> <@reviewer>
```

No emoji opener (`:pray:` retired 2026-09-24 — "it looks ugly"), no ticket key, no "what it does" clause,
no CI/bot status — the PR carries all of that; the ask names only the approval needed (the code-owner
team), the link and the touched scope, and cc's the people who can give it. The MCP tool takes standard
markdown and stores it as the mrkdwn link `<url|label>`; the mrkdwn form typed directly is stored
identically (verified 2026-09-10, self-DM round-trip), so either works, but never a bare URL and never
`repo#n`. Several PRs going up together: "Two reviews please:" + one `[<r>/pull/<n>](url), \`<scope>\`
only` bullet per PR. No "Sent using Claude" footer. If a draft for the same channel is already pending
(the user has not sent it), **update that draft** instead of adding a second one — say so in your
terminal summary. **Related asks ride the same thread, not DMs** (owner, 2026-09-24): a follow-up for a
dependent PR (e.g. a bot flag-sync PR that needs a re-approval) is a DRAFT *thread reply* under the
review ask, cc'ing the one person who can act — never separate DMs to the reviewers. Afterwards the
draft follows `slack-draft`: before re-drafting or reporting it "pending", read the channel to confirm
whether the user sent it and in what form. Routing nuances beyond the one-channel map (split code-owner
paths, team-internal PRs) live in `.context/reference/environment.md` / `slack._repo_channels_note`.
