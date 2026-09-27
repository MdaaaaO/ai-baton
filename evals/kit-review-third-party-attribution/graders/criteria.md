---
type: llm
weight: 1
---

The response contains at least one finding line in the shape `[STOP ...] skills/pr-review/reference/tone.md:<line> — ... (...)`
or `[WARN ...]` on the added attribution, naming the credit to a third party (the colleague, the handle, the private
repository or skill name) as a leak by meaning, and saying what to do instead (a neutral provenance line such as
"adapted from a review-writing guide", or dropping the line). The verdict line is exactly `Verdict: request-changes`.
The response does not treat the line as an owner-decision provenance line, does not repeat the tier-0 checks, and each
finding is a single line in the fixed shape.
