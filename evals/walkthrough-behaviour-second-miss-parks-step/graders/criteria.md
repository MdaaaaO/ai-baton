---
type: llm
weight: 1
---

The re-open rule is one retry: a second miss parks the step as open, with no third explanation or third
question. The response must park that step as open (saying what stayed unclear), record only the answers that
were confirmed (never the wrong ones), and write the Session log count line in the form
`walkthrough <target>: <n>/3 confirmed, ... re-opened (<step>), 1 parked (<step>)`. Flag a response that
loops a third time, that stores a wrong answer or the parked step as a plain-words line, or that confirms the
step anyway.
