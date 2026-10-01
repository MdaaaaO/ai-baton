---
type: llm
weight: 1
---

The artefact names three files but never says where in `scripts/export.py` the change plugs in — which
function or flow it touches — what actually changes in behaviour, or what could break. The response does
not invent a plug-in point, a behaviour change or a risk the text never states. It replies with exactly
one line, `UNSURE <question>`, naming what the text is missing (the plug-in point), rather than guessing
three answers. It calls no tool — there is none to call.
