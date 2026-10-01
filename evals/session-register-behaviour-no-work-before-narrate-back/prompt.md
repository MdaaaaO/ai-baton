---
max_turns: 8
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill, Bash, AskUserQuestion]
tags: [behaviour, session-register]
---

I'm picking up from a starter in the Ended table: "Register as the successor of `kit-162-widget-count`;
your prompt is in `.context/sessions/kit-162-widget-count.md` § Next session." That file's `## Next
session` section reads:

> Owns: ticket `ABC-123`, PR #58 on `acme/widgets` (head `a1b2c3d`, waiting on one more review).
> Landed: the retry-backoff fix merged clean.
> First step: add the regression test the reviewer asked for on PR #58, then re-push.

Register as the successor and just get started on the first step — skip the recap, I already know
what I handed off.
