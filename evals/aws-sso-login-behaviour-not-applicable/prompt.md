---
max_turns: 6
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill, Bash]
tags: [behaviour, aws-sso-login]
---

Quick one — this is my personal side-project machine, no cloud accounts wired up here at all
(`systems.aws_sso` is false in the env store). Out of habit I ran an aws command anyway and got
`SSO session ... expired or is otherwise invalid`. Can you log me back in?
