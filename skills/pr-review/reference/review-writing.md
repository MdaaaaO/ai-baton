# review-writing.md — how the posted text reads

Adapted from a colleague's `engineering-writing/pull-requests.md` plus this workspace's rules.

## Inline comments
- One finding per comment, anchored to the line where the reader must look. Lead with the concrete risk and the evidence, then the fix: "`created_at` here is naive UTC; `CONVERT_TIMEZONE` shifts it +7h (checked: the column type is TIMESTAMP_NTZ). Drop the conversion or cast first."
- Optional feedback starts with `nit:` or `suggestion:`. Questions end with a question mark and say what answer would close them.
- Suggest code with a ```suggestion block only when the replacement is exact and ≤10 lines.
- Write to the change, not the author. No "you forgot"; "this misses …".
- Never include local `.context/` paths, session names, or SSM values. Link the tracker key (`[KEY-123](<tracker.url_template>)`) or the PR/commit instead.

## Body
Shape (omit empty sections):
```
<one-paragraph verdict: what the PR does, whether it does it, the one thing that matters most>

**Findings** (only when there are STOP/WARN items not fully covered inline)
1. …

**Checked and fine** — 1–3 lines of what was verified and *which axis* (source vs completeness vs logic; a bare ✅ gets quoted back as blanket correctness).

**Fast follows** — FF1 … (out-of-diff, with the ticket to open)

**Not checked** — what this review did not cover (e.g. "not executed against prod; grants not verified").
```
The snapshot marker (an HTML comment, invisible when rendered) is appended by `submit-review.sh`; do not add it yourself. **No footer, no AI-attribution line of any kind** — owner decision (2026-09-19): it only wastes the reader’s attention and the author’s bot tokens. The review reads as the user’s own.

## Replies in threads (follow-up mode)
- First person, short, answer first. Never open with a SHA or "Addressed in".
- Acknowledge a fix in one line ("Confirmed on `abc1234`, thanks."). When we were wrong, say so: "You're right — I misread the grain; withdrawing this."
- On a contested design call, state the recommendation and offer to settle it synchronously; do not restate the whole argument.
- Resolve only threads we opened whose point is settled (`resolve:true` is refused for other people's threads by the script). Never resolve a thread waiting on a named third party.

## Chat / tracker mentions of the review
- A review-request message stays 1–3 lines: link + one clause + who must act.
- A review is never announced in stakeholder channels.
