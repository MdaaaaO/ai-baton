---
type: llm
weight: 1
---

The response treats this as filling in a missing env fact: running the aws discovery manifest's tool, verifying the candidate, and writing it back with provenance — env-init's job. It does not start the SSO device-code login flow, which assumes the profile name is already known.
