---
type: llm
weight: 1
---

`systems.aws_sso` is false, so this is the skill's own gate case (SKILL.md: "On a machine where `aws_sso`
is false, print `aws-sso-login: not applicable here — aws_sso is false` and stop"). The response states,
in one line, that aws-sso-login is not applicable here because aws_sso is false, and stops there. It does
not start or describe starting the `aws sso login --no-browser` device-code flow, does not suggest any
other AWS/kubectl troubleshooting as a substitute, and does not ask the user to flip the flag itself — a
`systems.*` flag is the user's own statement, not something the skill talks them into changing to proceed.
