# Links — every ticket reference is clickable, everywhere

Owner decision, 2026-09-10: "all tickets … whenever referenced should be clickable links" (core rule,
`WORKSPACE.md` § Rules → Communication). Applies to every surface this checklist touches — PR body, Slack
draft, tracker comment, and the terminal line to the user. Render every key with `tracker.url_template`
(`{key}` = the match of `tracker.key_regex`; for `tracker.kind: github`, `{key}` is the issue number and
`{repo}` its `owner/repo`):

| Surface | Ticket | PR |
|---|---|---|
| PR body / GitHub comment (markdown) | `jira`: `[<KEY>](<url_template>)` · `github`: `#<n>` (autolinks same-repo), `owner/repo#<n>` cross-repo | `#<n>` same-repo or `[<r>#<n>](url)` cross-repo |
| Slack draft (markdown → mrkdwn) | `[<KEY>](<url_template>)` | `[<r>/pull/<n>](https://github.com/<o>/<r>/pull/<n>)` |
| Jira comment (markdown) | `[<KEY>](<url_template>)` (bare keys autolink only in the same site — link anyway) | full URL, Jira autolinks it |
| Terminal message to the user | `[<KEY>](<url_template>)` / `[#<n>](<url_template>)` | `[#<n>](https://github.com/<o>/<r>/pull/<n>)` |

Never a bare key in prose on any of these, and never a bare URL (it unfurls and eats a line in Slack).
PR *titles* are the one exception (GitHub titles cannot carry links; keep the key there as text so
search finds it).

Don't DM individuals for reviews (§ Rules → Don't pester people); one ask in the right venue is the ask.
A follow-up nudge is the user's decision, not the session's.
