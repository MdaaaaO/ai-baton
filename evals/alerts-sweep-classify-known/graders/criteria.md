---
type: llm
weight: 1
---

The response treats this as the alerts-sweep classification pass: reading new messages, checking each against the pattern KB for a matching DAG/task and failure signature, and sorting them into KNOWN / HUMAN-RESOLVED / DIGEST-INFO / NEW. It does not fix or reply to any of the alerts itself, only classify them and report back.
