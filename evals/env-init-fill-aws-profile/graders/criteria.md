---
type: llm
weight: 1
---

The response treats this as resolving the single named `aws.profile stage` fact: running the aws discovery manifest's tool, verifying the candidate, and writing it back with provenance rather than typing a guessed value. It does not skip verification or invent a plausible-looking profile name. The run starts in an empty scratch directory with no live AWS access, so judge the approach, not whether the fact actually resolved.
