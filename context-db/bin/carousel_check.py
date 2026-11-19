#!/usr/bin/env python3
"""carousel_check.py — warns when a turn ends on a prose ask or offer instead of an `AskUserQuestion` carousel.

The rule (`WORKSPACE.md` § Rules → Communication, spec `docs/carousel.md`): every open decision goes in one
carousel, never in prose. The two forms that slip through are an optional offer at the end of a reply ("tell me
if you want X") and a "waits for you" list. This module decides, from a session transcript, whether the LAST
turn broke that, and is the `Stop` hook:

    carousel_check.py hook            # Stop-hook JSON on stdin; prints a `systemMessage` warning or nothing; exit 0
    carousel_check.py check <file>    # the text of a final reply in <file>: prints its ask kind (question, offer,
                                      # waits-list) or nothing; exit 0

The hook only warns — it never blocks the session and never asks the model to continue, so it cannot loop;
`stop_hook_active` set in the input makes it silent anyway. It is also silent when

  - the turn called `AskUserQuestion` (the carousel was used),
  - the reply ends with a numbered `Decisions` block (the headless form, `docs/carousel.md` § No tool), or
  - the reply asks nothing: only the END of the reply is read (its last non-empty lines, outside code), so a
    status report with a question mid-text, or a rhetorical question in a code block, never counts.

Three patterns, each anchored to the end of the reply, each a CANDIDATE the user judges — a warning, not a verdict:
  question    the last line ends with `?`
  offer       one of the last three lines holds an offer phrase ("tell me if", "let me know", "want me to", …)
  waits-list  a heading or label in the last 15 lines such as "Waits for you" or "Open decisions"
`kind(text)` is shared with `carousel_stats.py`, which counts the same thing over many transcripts. Stdlib only.
"""
from __future__ import annotations
import json
import os
import re
import sys

import transcripts  # same dir — the shared transcript reader

CAROUSEL_TOOL = "AskUserQuestion"
TAIL_BYTES = 4_000_000  # the hook reads only the end of a transcript: the last turn is always near it
LINES_OFFER, LINES_LIST = 3, 15

FENCE_RE = re.compile(r"^[ \t]*(```|~~~).*?^[ \t]*\1[ \t]*$", re.S | re.M)
UNCLOSED_FENCE_RE = re.compile(r"^[ \t]*(```|~~~).*\Z", re.S | re.M)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
TRAIL_RE = re.compile(r"[\s*_~\"')\]>.!]+$")
OFFER_RE = re.compile(
    r"\b(?:tell me (?:if|when|which|what)|let me know|say the word|just say|your call|up to you"
    r"|want me to|would you like|do you want|shall i|should i|if you(?:'d| would)? (?:want|like|prefer)"
    r"|needs? your (?:word|go|call|decision|ok|okay|approval|answer|say)|awaits? your"
    r"|(?:waiting (?:on|for)|waits? for) you)\b", re.I)
LIST_RE = re.compile(
    r"^[ \t>#*_-]*(?:waits? for you|waiting (?:on|for) you|needs your (?:word|go|call|decision|answer)"
    r"|open decisions?|decisions? for you|for you to decide)\b[ \t*_]*:?[ \t*_]*$", re.I)
DECISIONS_RE = re.compile(r"^[ \t#*_]*Decisions[ \t*_:]*$", re.M)
NUMBERED_RE = re.compile(r"^\s*1[.)]\s+\S")


def _lines(text: str) -> list[str]:
    """The non-empty lines of `text` outside code fences and inline code, in order."""
    t = FENCE_RE.sub("", text or "")
    t = UNCLOSED_FENCE_RE.sub("", t)
    t = INLINE_CODE_RE.sub("", t)
    return [ln.rstrip() for ln in t.splitlines() if ln.strip()]


def has_decisions_block(text: str) -> bool:
    """True when the reply ends with the headless numbered `Decisions` block."""
    lines = (text or "").splitlines()
    idx = [i for i, ln in enumerate(lines) if DECISIONS_RE.match(ln)]
    if not idx:
        return False
    rest = [ln for ln in lines[idx[-1] + 1:] if ln.strip()]
    return bool(rest) and bool(NUMBERED_RE.match(rest[0]))


def kind(text: str) -> str:
    """'waits-list', 'offer', 'question' — what the end of this final reply asks the user in prose — or ''."""
    if not text or has_decisions_block(text):
        return ""
    lines = _lines(text)
    if not lines:
        return ""
    if any(LIST_RE.match(ln) for ln in lines[-LINES_LIST:]):
        return "waits-list"
    if any(OFFER_RE.search(ln) for ln in lines[-LINES_OFFER:]):
        return "offer"
    if TRAIL_RE.sub("", lines[-1]).endswith("?") or lines[-1].rstrip().endswith("?"):
        return "question"
    return ""


def _blocks(rec: dict) -> list[dict]:
    c = (rec.get("message") or {}).get("content")
    if isinstance(c, str):
        return [{"type": "text", "text": c}]
    return [b for b in (c or []) if isinstance(b, dict)]


def is_user_turn(rec: dict) -> bool:
    """A real user message: it starts a turn. Tool results, meta lines and the compaction summary do not."""
    if rec.get("type") != "user" or rec.get("isMeta") or rec.get("isCompactSummary") or rec.get("isSidechain"):
        return False
    return any(b.get("type") != "tool_result" for b in _blocks(rec))


def turns(recs):
    """Walk a transcript's records; yield one dict per turn: `carousel` (calls of AskUserQuestion), `final` (the text
    of the reply the turn ended on, '' when it ended on a tool call or no text) and `after_compact` (the turn began
    after a compaction boundary)."""
    cur = None
    compacted = False
    for rec in recs:
        if rec.get("type") == "system" and rec.get("subtype") == "compact_boundary":
            compacted = True
        elif is_user_turn(rec):
            if cur is not None:
                yield cur
            cur = {"carousel": 0, "final": "", "after_compact": compacted}
        elif rec.get("type") == "assistant" and cur is not None and not rec.get("isSidechain"):
            for b in _blocks(rec):
                if b.get("type") == "tool_use":
                    cur["final"] = ""
                    if b.get("name") == CAROUSEL_TOOL:
                        cur["carousel"] += 1
                elif b.get("type") == "text" and (b.get("text") or "").strip():
                    cur["final"] = b["text"]
    if cur is not None:
        yield cur


def _tail_records(path: str):
    """The records of the last TAIL_BYTES of a transcript (a cut first line is dropped)."""
    with open(path, "rb") as fh:
        size = fh.seek(0, os.SEEK_END)
        fh.seek(max(0, size - TAIL_BYTES))
        data = fh.read()
    lines = data.decode("utf-8", errors="replace").splitlines()
    if size > TAIL_BYTES:
        lines = lines[1:]
    for line in lines:
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if isinstance(o, dict):
            yield o


def warn(payload: dict) -> str:
    """The warning for one Stop-hook input, or ''."""
    if payload.get("stop_hook_active"):
        return ""
    path = payload.get("transcript_path")
    last = None
    if isinstance(path, str) and path:
        for last in turns(_tail_records(path)):
            pass
    if last is None or last["carousel"]:
        return ""
    text = payload.get("last_assistant_message")
    k = kind(text if isinstance(text, str) and text.strip() else last["final"])
    if not k:
        return ""
    return (f"carousel check: this reply ends on a prose ask ({k}) and the turn made no {CAROUSEL_TOOL} call. "
            "An open decision goes in one carousel, not in prose (WORKSPACE.md, Rules, Communication).")


def main(argv: list[str]) -> int:
    if argv[:1] == ["hook"]:
        try:
            raw = sys.stdin.read() if sys.stdin is not None and not sys.stdin.isatty() else ""
            msg = warn(json.loads(raw or "{}"))
        except Exception:  # noqa: BLE001 — a hook never fails the session: no output, exit 0
            return 0
        if msg:
            sys.stdout.write(json.dumps({"systemMessage": msg}) + "\n")
        return 0
    if len(argv) == 2 and argv[0] == "check":
        with open(argv[1], encoding="utf-8", errors="replace") as fh:
            k = kind(fh.read())
        if k:
            print(k)
        return 0
    sys.stderr.write("usage: carousel_check.py hook | check <file>\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
