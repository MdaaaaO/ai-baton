# Operator brief — what a landed change means for the person who runs it

A PR body tells a reviewer what the diff does. It does not tell the operator what to do tomorrow. Every landed
change therefore ends with a short block for the operator: four fixed lines in Simplified Technical English
(`output-styles/simplified-technical-english.md`). One person at least must understand each change before it ships,
and the block is where that understanding is written down.

The block is one text in three places. `pr-open` writes it as `## For the operator` in the PR body.
`ticket-close` copies it into the close comment. `session-handoff` copies it into the next-session prompt under
`### What changed, in plain words`. Nobody rewrites it on the way: the PR body is the source.

## The four lines

Write the lines in this order, each as `- <Label>: <text>`.

| Label | It answers | Starts with |
|---|---|---|
| `Can now:` | What can the operator do now that they could not do before? | A command verb: `Run`, `Set`, `Open` |
| `Do differently:` | What must the operator do in another way than before? | A command verb, or the old step and the new step |
| `Check with:` | What does the operator run or look at to see the change work? | A command in a code span, then what it prints |
| `Not covered:` | What does the change still not do? | The gap, named as a thing the operator can meet |

The operator is the person who runs, deploys or uses the result. For a skill change, that person is the one who
installs the kit. For a library, it is the person who calls it. It is never the reviewer.

## The writing rules

- At most 20 words after the label. A code span counts as one word.
- One verb in each sentence. Do not join two actions with `and` or a semicolon: make two sentences or drop the weaker one.
- Use the active voice. Say who does what: `Run`, `You can`, `The check warns`.
- Use the verb, not the noun made from it: `Run the check`, not `Perform an execution of the check`.
- Name things the operator can see: a command, a file, a message, a setting. Do not name functions or internals.
- Keep names, commands and paths exactly as they are.

## When the answer is nothing

Write `Nothing.` and, if it helps, a short reason in the same line. A line is never empty, `n/a` or left out:
a missing line looks the same as a line the author forgot.

```
- Can now: Nothing. This change fixes a wrong exit code.
- Do differently: Nothing. Every command works as before.
- Check with: `make -C context-db test` → `OK`.
- Not covered: Nothing.
```

A change with `Nothing.` on all four lines is allowed. It says the author asked the questions and the answer is
"no effect on the operator", for example a wording-only change.

## The near miss: restating the diff

The usual failure is a block that repeats the diff in four lines. It passes the length rule and helps nobody.

| Restates the diff | Written for the operator |
|---|---|
| Can now: Renamed `--out` to `--output` in `export.py`. | Can now: Run `export --output <dir>` to choose where the files go. |
| Do differently: Moved the retry helper into `retry.py`. | Do differently: Replace `--out` with `--output` in your scripts. The old flag stops working. |

Test each line: could the operator act on it without opening the diff? If the line names a rename, a move or a
refactor and no action, rewrite it.

## The check

`python3 $BATON/skills/pr-open/operator_brief.py check <body-file>` warns, and never blocks, on a missing
`## For the operator` section, a missing, empty or repeated line, a line over 20 words, and any extra line.
`pr-open` runs it with the other body checks on every push. `operator_brief.py extract` prints the four lines, so
`ticket-close` and `session-handoff` copy them without retyping.

## The cold read

The cold reader (`docs/cold-reader.md` § The operator block) reads the four lines alone, with no diff and no PR
body. If it cannot say what the operator can do, what to change or how to check, rewrite the block before
posting. A rewritten block needs no result line: the block holds the four lines and nothing else.
