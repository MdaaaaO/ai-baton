# slack-draft

The lifecycle of every Slack message a session writes on the user's behalf: created as a **draft** the user
edits and sends, recorded at once, and read back from the channel before any follow-up or status claim.

**Needs:** `systems.slack` (a Slack MCP connector able to create drafts). The venues it drafts into are env
facts of the calling skill (`slack.channel …`, `slack.review-venue …`), never values in this skill.

**Without it:** `slack-draft: not applicable here — slack is false`, one line, stop — nothing is
drafted and no Slack step of any other skill runs.

**Example:**

> **user:** ask the channel for a review of my PR
>
> **claude:** creates the draft, records its id and venue, and later — before saying "already asked" — reads the
> channel to see whether the user sent it, edited it or replied.
