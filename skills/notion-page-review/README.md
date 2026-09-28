# notion-page-review

Reviews a Notion page tree — the page, its sub-pages, every comment thread — against the user's position and
posts only the comments the user approves, one by one, as thread replies or anchored inline comments.

**Needs:** `systems.notion` (a Notion MCP connector in the session). Slack redirects inside the loop happen only
where `systems.slack` is true, read from the env config, never inferred from the loaded tools.

**Without it:** `notion-page-review: not applicable here — notion is false`, one line, stop.

**Example:**

> **user:** go through the "<page>" page and its comments with me
>
> **claude:** ingests the tree, proposes comments per section in batches of four (Comment / Update wording /
> Skip), posts the approved ones, and ends with the list of what was posted, skipped and redirected.
