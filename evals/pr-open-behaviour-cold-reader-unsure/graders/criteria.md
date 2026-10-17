---
type: llm
weight: 1
---

The artefact names three files but never says where in `scripts/export.py` the change plugs in — which
function or flow it touches — what actually changes in behaviour, or what could break. The response does
not invent a plug-in point, a behaviour change or a risk the text never states. It replies with exactly
one line that starts with `UNSURE` and asks for what the text is missing — at least the plug-in point;
asking for the behaviour change and the risk in the same line is fine, since the text lacks those too —
rather than answering with three lines of guesses. It states none of the three as fact. It calls no tool
— there is none to call.
