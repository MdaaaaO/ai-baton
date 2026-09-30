# slack-draft — message formatting

What the chat connector's draft tool renders and what it does not, so a draft reads as intended once the user
sends it. Loaded on demand; SKILL.md § 1 points here.

- **Bold** is `**text**` in the draft tool — for the one thing the reader must not miss, never whole sentences.
- **Links** are `<url|label>`; a bare URL unfurls into a preview card the reader did not ask for, so label
  every link.
- **Code blocks** (triple backticks) do not wrap on mobile: keep every line ≤ 55 characters or the reader scrolls
  sideways. Single backticks for identifiers, keys and paths inline.
- **No headings, no tables.** `#` renders literally and a Markdown table renders as pipes; use one short line
  per point, `-` bullets, and a blank line between groups.
- **Mentions** use the connector's user reference, never a typed `@name` (plain text, notifies nobody). A user
  or channel id is an env fact — read it with `kb.py get slack.<kind> <name>`, never paste it into the transcript.
- **Length.** A request stays 1–3 lines: link + one clause + who must act; a status update ≤ 8 lines with the
  ask first. Detail goes in a thread reply, not the top-level message.
- **Threads.** A reply carries `thread_ts`; the draft then lives in the thread's reply box, not the channel
  composer (SKILL.md § 1).
- **Draft tool parameter.** The draft tool's message parameter is `message`, not `text`.
