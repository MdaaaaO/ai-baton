---
type: llm
weight: 1
---

The response treats this as running the AWS SSO device-code login for the profile behind stage before any further AWS command runs, ending with the device URL and code handed to the user. It does not attempt the stage aws command first and only fall back to login on failure. The run starts in an empty scratch directory with no live AWS access, so judge the approach, not whether the login actually completed.
