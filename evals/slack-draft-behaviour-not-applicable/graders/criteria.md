---
type: llm
weight: 1
---

`systems.slack` is false, so this is the skill's own gate case (SKILL.md: "On a machine where `slack` is
false, print `slack-draft: not applicable here — slack is false` and stop"). The response states, in one
line, that slack-draft is not applicable here because slack is false, and stops. It does not call
`slack_send_message_draft` or any other Slack tool, and does not claim to have created or queued a draft
by another means (e.g. saying the message is "drafted and waiting" somewhere) — a plain offer of the
message wording for the user to send themselves elsewhere is fine, but it must not be presented as a
Slack draft.
