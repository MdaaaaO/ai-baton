#!/usr/bin/env python3
"""gates.py — an epic's `## Verification plan` as checkable gates: who may close each one, and whether any is open.

The epic issue's BODY carries one `## Verification plan` section, one gate per line (the full format, for anyone
writing a plan or a new template: docs/verification/code-change.md § The gate format):

  - [ ] VG-<n> <gate> · kind: agent|human · check: <command | test | observation> · evidence: <what counts> · signer: verifier|owner[ · when: systems.<flag>[ | systems.<flag>…]]

An `agent` gate is signed by the verifier and closes on evidence; a `human` gate is signed by the owner and closes on
a decision line. The script reads and rewrites a local file and never touches the network: the orchestrator writes
the file from the issue body and posts it back. It runs no check either: the verifier does that.

Usage:
  gates.py parse <plan-file | ->
      One tab-separated line per gate: `VG-<n>`, kind, signer, `command` or `manual`, the check (a command is printed
      without its backticks), what counts as evidence, the `when:` flags or `-`, the gate.
  gates.py status <plan-file | ->
      One tab-separated line per gate: `VG-<n>`, `open` or `closed`, kind, signer, the gate, and why it is open where
      there is a reason to name (`ticked with no signature`, `not applicable here: …`, a refused signature). The last
      line is exactly `GATES: closed` or `GATES: open VG-<n>[, VG-<n>…]`. A box ticked by hand closes nothing: a gate
      is closed only by a ticked box and a signature that `sign` itself would accept. A gate whose `when:` flags are
      all false stays open: dropping it is the owner's re-plan delta, never an automatic skip.
  gates.py sign <plan-file> VG-<n> --by owner --decision <decision line>
      Closes a `human` gate. Any other `--by` is refused, so no worker, verifier or orchestrator signs one, and neither
      does a peer message: the decision line must be in the grammar of docs/carousel.md § After the answer,
        YYYY-MM-DD · <question> → <chosen>; not <rejected options> — <one clause why>
      with `VG-<n>` in its question and a `<chosen>` that starts with `sign`, so one answer closes one gate and a
      `hold` closes none.
  gates.py sign <plan-file> VG-<n> --by verifier --evidence <item>
      Closes an `agent` gate. `<item>` is one of evidence_check.py's shapes (`` `cmd` → `output` ``, a URL, a #<n> or
      commit reference) or `run <id>`. Refused while the gate's `when:` flags are all false on this machine.
      Signing again with the same record changes nothing; a different one is refused.

The file is the issue body alone, never its comments, which are anyone's text: a file with more than one
`## Verification plan` section is refused, so a concatenated comment can neither add a gate nor close one.

Exit codes: 0 ok (`status`: every gate closed; `sign`: recorded); 1 no `VG-<n>` gate in the file, or `sign` names a gate
the file does not hold; 2 bad usage, an unreadable file, or a malformed plan (a gate line that breaks the grammar, a
checkbox or `VG-<n>` bullet that is not a gate, a repeated id, a signer the gate's kind does not take, an unknown
flag, a second section); 3 `status`: a gate is open, or `sign`: the signature is refused, with the file untouched. Any
exit but 0 from `status` means the epic may not close. Stdlib only.
"""
from __future__ import annotations
import argparse
import datetime
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import done_when as dw  # noqa: E402 — the one section walker and the checkbox and command shapes
import evidence_check as ec  # noqa: E402 — the one place the evidence-shape rules live
import fsutil  # noqa: E402 — the `<file | ->` reader, the atomic write and the lock
import kb  # noqa: E402 — SYSTEMS, the known capability flags
import kit_profile  # noqa: E402 — reads a `when:` flag

PLAN_HEAD = "## Verification plan"
SIGNER = {"agent": "verifier", "human": "owner"}  # the one role each kind takes; `parse` refuses any other
# The fields are matched in order and each stops at the first ` · <next field>:`, so a gate may hold ` · ` and text in
# a signature's tail can never read as an earlier field. A signature is the last thing on the line.
GATE_RE = re.compile(
    r"^\s*[-*]\s*\[(?P<box>[ xX])\]\s+VG-(?P<n>\d+)\s+(?P<gate>\S.*?)"
    r"\s+·\s+kind:\s*(?P<kind>\S+)"
    r"\s+·\s+check:\s*(?P<check>\S.*?)"
    r"\s+·\s+evidence:\s*(?P<evidence>\S.*?)"
    r"\s+·\s+signer:\s*(?P<signer>\S+)"
    r"(?:\s+·\s+when:\s*(?P<when>\S.*?))?"
    r"(?:\s+·\s+signed:\s*(?P<signed>\S+)\s+·\s+(?P<field>decision|proof):\s*(?P<record>\S.*?))?"
    r"\s*$"
)
# What a reader would take for a gate although GATE_RE may refuse it: never skipped, or a typo would drop a gate.
LOOSE_GATE_RE = re.compile(r"^\s*(?:(?:[-*]|\d+[.)])\s+)?(?:\[[ xX]\]\s*)?VG-\d+\b")
FLAG_RE = re.compile(r"^systems\.(?P<flag>\w+)$")
# docs/carousel.md § After the answer; a test pins this shape to that page's grammar line.
DECISION_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2}) · (?P<question>\S.*?) → (?P<chosen>\S.*?); not (?P<rejected>\S.*?) — \S.*$")
CHOSEN_SIGN_RE = re.compile(r"^sign(?:ed)?\b", re.IGNORECASE)
RUN_ID_RE = re.compile(r"\brun\s+\S*\d")  # a pipeline or CI run, the one evidence shape evidence_check.py has no use for


class Gate:
    __slots__ = ("id", "lineno", "ticked", "text", "kind", "check", "command", "evidence", "signer", "when", "signed",
                 "field", "record")

    def __init__(self, lineno: int, m: re.Match):
        self.id = f"VG-{int(m['n'])}"
        self.lineno = lineno
        self.ticked = m["box"] != " "
        self.text = m["gate"]
        self.kind = m["kind"]
        self.check = m["check"]
        cmd = dw.COMMAND_RE.match(m["check"])
        self.command = cmd.group("cmd").strip() if cmd else None
        self.evidence = m["evidence"]
        self.signer = m["signer"]
        self.when = [f.strip() for f in m["when"].split("|")] if m["when"] else []
        self.signed = m["signed"]
        self.field = m["field"]
        self.record = m["record"]


# ── parse ────────────────────────────────────────────────────────────────────────────────────
def field_problem(g: Gate) -> str | None:
    """Why a gate's own fields cannot stand, or None. The signature is judged later: it makes a gate open, not malformed."""
    if g.kind not in SIGNER:
        return f"{g.id}: kind must be agent or human, not {g.kind}"
    if g.signer != SIGNER[g.kind]:
        return f"{g.id}: a {g.kind} gate's signer is {SIGNER[g.kind]}, not {g.signer}"
    for f in g.when:
        m = FLAG_RE.match(f)
        if m is None or m["flag"] not in kb.SYSTEMS:
            return f"{g.id}: when: names `{f}`, not a known `systems.<flag>`"
    return None


def parse_plan(text: str) -> tuple[list[Gate], list[str]]:
    """(the gates in file order, the problems). No section, or a section with no gate line, is no gates and no problems."""
    secs = dw.sections(text, PLAN_HEAD)
    if len(secs) > 1:
        return [], [f"{len(secs)} `{PLAN_HEAD}` sections: the file is the issue body alone, never its comments"]
    gates: list[Gate] = []
    problems: list[str] = []
    seen: set[str] = set()
    for lineno, line in secs[0] if secs else []:
        if not (dw.BOX_RE.match(line) or LOOSE_GATE_RE.match(line)):
            continue
        m = GATE_RE.match(line)
        if m is None:
            problems.append(f"{lineno}: not `- [ ] VG-<n> <gate> · kind: … · check: … · evidence: … · signer: …`: {line.strip()[:80]}")
            continue
        g = Gate(lineno, m)
        why = field_problem(g)
        if why:
            problems.append(f"{lineno}: {why}")
        elif g.id in seen:
            problems.append(f"{lineno}: {g.id} is defined twice")
        else:
            seen.add(g.id)
            gates.append(g)
    return gates, problems


# ── who may close a gate ─────────────────────────────────────────────────────────────────────
def decision_problem(g: Gate, line: str) -> str | None:
    """Why `line` cannot sign `g`, or None."""
    m = DECISION_RE.match(line)
    if m is None:
        return "not a decision line: `YYYY-MM-DD · <question> → <chosen>; not <rejected options> — <one clause why>`"
    try:
        datetime.date.fromisoformat(m["date"])
    except ValueError:
        return f"the decision line's date {m['date']} is not a date"
    if not re.search(rf"\b{g.id}\b", m["question"]):
        return f"the decision line's question does not name {g.id}: one answer closes one gate"
    if not CHOSEN_SIGN_RE.match(m["chosen"]):
        return f"the decision line's answer is `{m['chosen']}`, not `sign`"
    return None


def refusal(g: Gate, by: str, decision: str | None, evidence: str | None) -> str | None:
    """Why this signature cannot close `g`, or None. `sign` asks before it writes, `status` asks again of what the
    file holds, so a line typed by hand needs the same shape a real signature has."""
    if by != g.signer:
        return f"only the {g.signer} signs {g.id}, a {g.kind} gate, not {by}"
    value = decision if g.kind == "human" else evidence
    other = evidence if g.kind == "human" else decision
    want, unwanted = ("--decision", "--evidence") if g.kind == "human" else ("--evidence", "--decision")
    if other is not None:
        return f"a {g.kind} gate takes {want}, not {unwanted}"
    if value is None:
        return f"a {g.kind} gate needs {want}"
    if "\n" in value or "\r" in value:
        return f"{want} must be one line"
    if g.kind == "human":
        return decision_problem(g, value)
    if not (ec.has_evidence(f"-{ec.CLAIM_SEP}{value}") or RUN_ID_RE.search(value)):
        return "the evidence is bare: `cmd` → `output`, a URL, a #<n> or commit reference, or `run <id>`"
    return None


def flags_off(g: Gate) -> list[str]:
    """The gate's `when:` flags when all of them are false here, else [] (no `when:` applies everywhere). A flag this
    machine has no answer for counts as false: the gate then stays open, which is the safe side."""
    return [] if not g.when or any(kit_profile.get(f) is True for f in g.when) else list(g.when)


def state(g: Gate) -> tuple[str, str]:
    """(`open` or `closed`, why it is open where there is something to name)."""
    if g.signed is None:
        if g.ticked:
            return "open", "ticked with no signature"
        off = flags_off(g)
        return "open", f"not applicable here: {', '.join(off)} false; the owner can drop it in a re-plan delta" if off else ""
    if not g.ticked:
        return "open", "a signature on an unticked box"
    why = refusal(g, g.signed, g.record if g.field == "decision" else None, g.record if g.field == "proof" else None)
    return ("open", why) if why else ("closed", "")


# ── CLI ──────────────────────────────────────────────────────────────────────────────────────
def load(text: str) -> tuple[list[Gate] | None, int]:
    """(gates, 0), or (None, the exit code) after printing why: 1 no gate, 2 a malformed plan."""
    gates, problems = parse_plan(text)
    if problems:
        for p in problems:
            print(f"gates: {p}", file=sys.stderr)
        return None, 2
    if not gates:
        print(f"gates: no VG-<n> gate under `{PLAN_HEAD}`", file=sys.stderr)
        return None, 1
    return gates, 0


def cmd_parse(gates: list[Gate]) -> int:
    for g in gates:
        print("\t".join((g.id, g.kind, g.signer, "command" if g.command else "manual", g.command or g.check, g.evidence,
                         " | ".join(g.when) or "-", g.text)))
    return 0


def cmd_status(gates: list[Gate]) -> int:
    still_open: list[str] = []
    for g in gates:
        st, why = state(g)
        if st == "open":
            still_open.append(g.id)
        print("\t".join(x for x in (g.id, st, g.kind, g.signer, g.text, why) if x))
    if still_open:
        print(f"GATES: open {', '.join(still_open)}")
        return 3
    print("GATES: closed")
    return 0


def cmd_sign(a: argparse.Namespace) -> int:
    path = Path(a.file)
    if a.file == "-" or not path.is_file():
        print(f"gates: sign rewrites its file, and {a.file} is not one", file=sys.stderr)
        return 2
    with fsutil.locked(a.file):
        text = fsutil.read_arg(a.file)
        gates, rc = load(text)
        if gates is None:
            return rc
        g = next((x for x in gates if x.id == a.gate), None)
        if g is None:
            print(f"gates: no {a.gate} in {a.file}", file=sys.stderr)
            return 1
        decision = a.decision.strip() if a.decision is not None else None
        evidence = a.evidence.strip() if a.evidence is not None else None
        why = refusal(g, a.by, decision, evidence)
        field, value = ("decision", decision) if g.kind == "human" else ("proof", evidence)
        if why is None and g.signed is not None:
            if g.ticked and g.signed == a.by and g.record == value:
                print(f"{g.id} already closed by {g.signed}")
                return 0
            why = f"{g.id} already carries a signature by {g.signed}; a new one starts from the owner's re-plan delta"
        off = flags_off(g) if why is None else []
        if off:
            why = f"{g.id} is not applicable here: {', '.join(off)} false; the owner can drop it in a re-plan delta"
        if why:
            print(f"gates: refused: {why}", file=sys.stderr)
            return 3
        lines = text.splitlines(keepends=True)
        line = lines[g.lineno - 1]
        body = line.rstrip("\r\n")
        ticked = re.sub(r"\[[ xX]\]", "[x]", body.rstrip(), count=1)
        lines[g.lineno - 1] = f"{ticked} · signed: {a.by} · {field}: {value}{line[len(body):]}"
        fsutil.atomic_write(a.file, "".join(lines))
    print(f"{g.id} closed by {a.by}")
    return 0


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_parse = sub.add_parser("parse", help="list the plan's gates")
    p_parse.add_argument("file", help="the issue body, a template, or - for stdin")
    p_status = sub.add_parser("status", help="which gates are open; exit 3 while any is")
    p_status.add_argument("file", help="the issue body, or - for stdin")
    p_sign = sub.add_parser("sign", help="close one gate in the file")
    p_sign.add_argument("file", help="the issue body as a file: sign rewrites it")
    p_sign.add_argument("gate", help="VG-<n>")
    p_sign.add_argument("--by", required=True, help="the role signing: owner on a human gate, verifier on an agent gate")
    p_sign.add_argument("--decision", help="human gate: the owner's decision line, docs/carousel.md § After the answer")
    p_sign.add_argument("--evidence", help="agent gate: `cmd` → `output`, a URL, a #<n> or commit reference, or run <id>")
    a = ap.parse_args(argv)

    if a.cmd == "sign":
        return cmd_sign(a)
    gates, rc = load(fsutil.read_arg(a.file))
    if gates is None:
        return rc
    return cmd_parse(gates) if a.cmd == "parse" else cmd_status(gates)


def main(argv: list[str]) -> int:
    try:
        return _main(argv)
    except OSError as e:
        print(f"gates: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
