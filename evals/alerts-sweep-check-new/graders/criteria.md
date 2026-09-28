---
type: llm
weight: 1
---

The response treats this as sweeping the airflow-alerts channel from the last-swept timestamp forward and flagging NEW-classified messages against the pattern KB. It does not take action on any alert, only reads, classifies and reports. The run starts in an empty scratch directory with no live Slack access, so judge the approach: a NEEDS line or NO-OP is a fine outcome.
