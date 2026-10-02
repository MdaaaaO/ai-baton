# ticket-update — adapter mechanics, full detail

Loaded from `SKILL.md` § Adapters. The full per-adapter text moved out of the body; `SKILL.md` keeps only
the summary a session needs on the common path.

## Adapter — Jira (tracker.kind = jira)

- **Comment** per Sync — core § 1 (`tracker.mcp_tools.comment`). @-mentions need an ADF comment (a mention
  inside a markdown body is plain text to the tracker, nobody is notified) — rich markdown body + a
  one-line ADF cc underneath.
- **Status** via `tracker.mcp_tools.transition`: In Progress → **In Review**
  (`tracker.transitions.in_review`, when the PR is up — `pr-open` does this) → Done (`ticket-close`).
  Check the transitions actually available first via `tracker.mcp_tools.transitions_list` (ids drift).
- **Pointer** = a `**Thread** <url>` / `**PR** <url>` line in the comment (Sync — core § 3) — the sanctioned
  form here too; it needs no write scope and always lands. Only when `tracker.write_api` is also true, mirror
  it as a real remote issue link: first list the links already there via `tracker.mcp_tools.remote_link`
  (read-only) and stop if this URL is one of them, even when the read tool is missing (then read the links
  via `GET` on the same endpoint); otherwise create it with `POST /rest/api/3/issue/{key}/remotelink` —
  cloud id from the env fact `tracker.setting cloud_id` — body `{"object": {"url": "<link>", "title":
  "<title>"}}`. Check, then create: never create without checking. `tracker.write_api` false or unset (the
  common case — a read-only roster exposes only `getJiraIssueRemoteIssueLinks`, no write tool and no API
  token) → skip the POST silently, the comment line already carries the pointer.

## Adapter — GitHub issues (tracker.kind = github)

- **Comment** per Sync — core § 1 (`--body-file`). Mentions are plain `@login`. Edit-the-last-comment =
  `gh issue comment <n> --edit-last` under the same gate.
- **Status = labels + milestone** (no workflow states): `gh issue edit <n> -R <repo>
  --add-label/--remove-label/--milestone` from the repo's own label set; an open PR with
  `Closes #<n>` in its body is the "In Review" signal — no extra state to set.
- **Pointer** = `#<pr>` or the thread URL in the comment; GitHub cross-links PRs/issues itself.
