#!/usr/bin/env python3
"""sizing.py — parse and format a ticket's Sizing line (docs/delegation.md § The Sizing line).

`ticket-open` writes the line into the opening comment when a ticket is created; `ticket-pickup` reads it back
when the ticket is picked up, verifies it still holds, and sizes a missing one. One line, anywhere in the body:

  **Sizing:** `<model>`, <delegate|main session>. <one-line reason>

`<model>` is one of MODELS (the table in `docs/delegation.md` § Sizing); the decision is exactly `delegate` or
`main session`; the reason is free text on the same line.

Usage:
  sizing.py parse   <file|->                              → prints "model\\tdecision\\treason"
                                                              exit 1 no Sizing line in the body, 2 malformed
  sizing.py check   <file|->                              same exit codes; prints "ok · <model>, <decision>"
  sizing.py format  <model> <delegate|main-session> "<reason>"   → the line text, exit 2 on a bad model/decision
  sizing.py models                                         → the accepted models, one per line
"""
from __future__ import annotations
import argparse
import re
import sys
from pathlib import Path

MODELS = ("haiku", "sonnet", "opus")
DECISIONS = ("delegate", "main session")
# A well-formed line, matched anywhere in the body (MULTILINE `$` stops at the newline, so the reason never
# spans lines). The model group is `[a-z]+` (not the MODELS alternation) so an unknown model is reported as
# "malformed", not silently treated as "missing" — the two failure modes callers must tell apart.
LINE = re.compile(
    r"^\*\*Sizing:\*\*\s*`(?P<model>[a-z]+)`,\s*(?P<decision>delegate|main session)\.\s*(?P<reason>\S.*?)\s*$",
    re.MULTILINE,
)


def find_raw(body: str) -> str | None:
    """The first raw `**Sizing:**` line in `body`, matched or not — lets a caller tell "missing" (no such line)
    from "present but malformed" (this line, `LINE` didn't match it)."""
    for ln in body.splitlines():
        if ln.strip().startswith("**Sizing:**"):
            return ln.strip()
    return None


def parse(body: str) -> tuple[str, str, str]:
    """(model, decision, reason) from `body`'s Sizing line. Raises ValueError("missing") when there is no
    `**Sizing:**` line at all, or ValueError(<the raw line>) when one exists but is not `LINE`-shaped (including
    a model outside MODELS)."""
    m = LINE.search(body)
    if m is not None and m.group("model") in MODELS:
        return m.group("model"), m.group("decision"), m.group("reason").strip()
    raw = find_raw(body)
    if raw is None:
        raise ValueError("missing")
    raise ValueError(raw)


def format_line(model: str, decision: str, reason: str) -> str:
    if model not in MODELS:
        raise ValueError(f"model must be one of {', '.join(MODELS)}, got {model!r}")
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {', '.join(DECISIONS)}, got {decision!r}")
    reason = reason.strip()
    if not reason:
        raise ValueError("reason must not be empty")
    return f"**Sizing:** `{model}`, {decision}. {reason}"


def _read(arg: str) -> str:
    return sys.stdin.read() if arg == "-" else Path(arg).read_text(encoding="utf-8", errors="replace")


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("parse", "check"):
        p = sub.add_parser(name)
        p.add_argument("file", help="path, or - for stdin")
    p_format = sub.add_parser("format")
    p_format.add_argument("model")
    p_format.add_argument("decision", help='"delegate" or "main session" — one shell argument, quote it')
    p_format.add_argument("reason")
    sub.add_parser("models")
    a = ap.parse_args(argv)

    if a.cmd == "models":
        print("\n".join(MODELS))
        return 0
    if a.cmd == "format":
        try:
            print(format_line(a.model, a.decision, a.reason))
            return 0
        except ValueError as e:
            print(f"sizing: {e}", file=sys.stderr)
            return 2

    body = _read(a.file)
    try:
        model, decision, reason = parse(body)
    except ValueError as e:
        if str(e) == "missing":
            print("sizing: no Sizing line in the body", file=sys.stderr)
            return 1
        print(f"sizing: malformed Sizing line: {e}", file=sys.stderr)
        return 2
    if a.cmd == "check":
        print(f"ok · {model}, {decision}")
    else:
        print(f"{model}\t{decision}\t{reason}")
    return 0


def main(argv: list[str]) -> int:
    try:
        return _main(argv)
    except OSError as e:
        print(f"sizing: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
