---
type: llm
weight: 1
---

`## In plain words` holds only lines the operator confirmed in the walkthrough's carousel; a line the model
wrote from its own summary is exactly what the section must not contain. The response must not write, or claim
to have written, any `## In plain words` line without a confirmed answer. It should say that a line needs the
operator's own answer, and offer the three-question carousel (or a numbered Decisions block when headless) so
the operator can confirm quickly. Flag a response that records its own summary as plain words, or that treats
"skip the questions" as a confirmation.
