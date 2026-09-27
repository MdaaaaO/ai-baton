---
name: leaky-skill
description: Fixture skill that must FAIL the env-free validator: a leaked id, a missing script, a legacy key.
version: 1
metadata:
  updated: "2026-09-26"
  reviewed: "2026-09-26"
---

# leaky-skill

Post to channel C0AB12CD3EF (a Slack id that belongs in the env store), then run `scripts/missing.sh`.
Example commit: KEY-9876 fix the thing (a real-looking number behind the placeholder prefix).
