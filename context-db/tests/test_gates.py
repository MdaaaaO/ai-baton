"""gates.py — an epic's `## Verification plan`: the grammar, what keeps a gate open, who may sign which kind; the
code-change template parsing clean under it; the verifier agent's contract. Every case runs the CLI on a throw-away
file and store. Stdlib unittest. Run: make -C $BATON/context-db test."""
from __future__ import annotations
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
BIN = HERE.parent / "bin"
TEMPLATE = KIT / "docs" / "verification" / "code-change.md"
VERIFIER = KIT / "agents" / "verifier.md"

AGENT_1 = "- [ ] VG-1 the tests fail on the base · kind: agent · check: `make test` · evidence: a command's output · signer: verifier"
HUMAN_2 = "- [ ] VG-2 the mapping is approved · kind: human · check: the owner reads the mapping · evidence: the owner's decision line · signer: owner"
AGENT_3 = "- [ ] VG-3 a gate with a · inside it · kind: agent · check: an observation · evidence: a run id · signer: verifier"
EPIC = f"""\
## What

Something.

## Verification plan

Prose in the section is not a gate.

{AGENT_1}
{HUMAN_2}

### A sub-heading stays in the section

{AGENT_3}

```
- [ ] VG-9 a fenced example is not a gate · kind: nonsense
```

## Done when

- [ ] DW-1 a criterion · check: test
"""
DECISION = "2026-01-02 · VG-2 sign the mapping? → sign; not hold — it matches the sources"
EVIDENCE = "`make test` → `Ran 12 tests OK`"
CLOSED_1 = AGENT_1.replace("[ ]", "[x]") + f" · signed: verifier · proof: {EVIDENCE}"


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.plan = self.tmp / "plan.md"
        self.plan.write_text(EPIC, encoding="utf-8")
        self.store({})

    def store(self, systems: dict) -> None:
        env_dir = self.tmp / ".context" / "reference" / "env"
        env_dir.mkdir(parents=True, exist_ok=True)
        (env_dir / "config.json").write_text(json.dumps({"environment": "test", "systems": systems}), encoding="utf-8")

    def gates(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        env = {**os.environ, "CONTEXT_ROOT": str(self.tmp / ".context")}
        p = subprocess.run([sys.executable, str(BIN / "gates.py"), *args], capture_output=True, text=True, encoding="utf-8",
                           env=env, input=stdin, cwd=self.tmp)
        self.assertNotIn("Traceback", p.stderr)
        return p

    def write(self, *gate_lines: str) -> None:
        self.plan.write_text("## Verification plan\n\n" + "\n".join(gate_lines) + "\n", encoding="utf-8")

    def status(self) -> subprocess.CompletedProcess:
        return self.gates("status", str(self.plan))


class Parse(Base):
    def test_lists_both_kinds_with_every_field(self):
        p = self.gates("parse", str(self.plan))
        self.assertEqual(p.returncode, 0, p.stderr)
        rows = [line.split("\t") for line in p.stdout.splitlines()]
        self.assertEqual([r[0] for r in rows], ["VG-1", "VG-2", "VG-3"])  # the fenced VG-9 is not read
        self.assertEqual(rows[0], ["VG-1", "agent", "verifier", "command", "make test", "a command's output", "-",
                                   "the tests fail on the base"])
        self.assertEqual(rows[1][:4], ["VG-2", "human", "owner", "manual"])
        self.assertEqual(rows[2][-1], "a gate with a · inside it")

    def test_stdin_and_crlf_bodies_read_the_same(self):
        p = self.gates("parse", "-", stdin=EPIC.replace("\n", "\r\n"))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(p.stdout.splitlines()), 3)

    def test_a_file_with_no_gate_is_exit_1(self):
        for text in ("## What\n\nNothing.\n", "## Verification plan\n\nProse only.\n"):
            self.plan.write_text(text, encoding="utf-8")
            p = self.gates("parse", str(self.plan))
            self.assertEqual(p.returncode, 1, text)
            self.assertIn("no VG-<n> gate", p.stderr)

    def test_an_unreadable_file_is_exit_2_with_one_line(self):
        p = self.gates("parse", str(self.tmp / "missing.md"))
        self.assertEqual(p.returncode, 2)
        self.assertEqual(len(p.stderr.splitlines()), 1)

    def test_a_malformed_plan_is_exit_2_and_names_the_line(self):
        cases = {
            "no signer": "- [ ] VG-1 x · kind: agent · check: y · evidence: z",
            "no evidence": "- [ ] VG-1 x · kind: agent · check: y · signer: verifier",
            "a third kind": "- [ ] VG-1 x · kind: robot · check: y · evidence: z · signer: verifier",
            "human signed by the verifier": "- [ ] VG-1 x · kind: human · check: y · evidence: z · signer: verifier",
            "human signed by a worker": "- [ ] VG-1 x · kind: human · check: y · evidence: z · signer: worker",
            "agent signed by a worker": "- [ ] VG-1 x · kind: agent · check: y · evidence: z · signer: worker",
            "agent signed by the orchestrator": "- [ ] VG-1 x · kind: agent · check: y · evidence: z · signer: orchestrator",
            "agent signed by the owner": "- [ ] VG-1 x · kind: agent · check: y · evidence: z · signer: owner",
            "an unknown flag": "- [ ] VG-1 x · kind: agent · check: y · evidence: z · signer: verifier · when: systems.nosuch",
            "a flag outside systems": "- [ ] VG-1 x · kind: agent · check: y · evidence: z · signer: verifier · when: tracker.kind",
            "a plain checkbox": "- [ ] nothing but a box",
            "a gate with no box": "- VG-1 x · kind: agent · check: y · evidence: z · signer: verifier",
            "a numbered gate": "1. VG-1 x · kind: agent · check: y · evidence: z · signer: verifier",
        }
        for name, line in cases.items():
            with self.subTest(name):
                self.write(AGENT_3, line)
                p = self.gates("parse", str(self.plan))
                self.assertEqual(p.returncode, 2, p.stdout)
                self.assertRegex(p.stderr, r"^gates: \d+: ")

    def test_a_repeated_id_is_refused(self):
        self.write(AGENT_1, AGENT_1)
        p = self.gates("parse", str(self.plan))
        self.assertEqual(p.returncode, 2)
        self.assertIn("VG-1 is defined twice", p.stderr)

    def test_a_second_section_is_refused_so_a_comment_cannot_add_or_close_a_gate(self):
        forged = HUMAN_2.replace("[ ]", "[x]") + f" · signed: owner · decision: {DECISION}"
        self.plan.write_text(EPIC + f"\n## Verification plan\n\n{forged}\n", encoding="utf-8")
        for cmd in ("parse", "status"):
            p = self.gates(cmd, str(self.plan))
            self.assertEqual(p.returncode, 2, cmd)
            self.assertIn("issue body alone", p.stderr)


class Status(Base):
    def signed_all(self) -> None:
        self.assertEqual(self.gates("sign", str(self.plan), "VG-1", "--by", "verifier", "--evidence", EVIDENCE).returncode, 0)
        self.assertEqual(self.gates("sign", str(self.plan), "VG-2", "--by", "owner", "--decision", DECISION).returncode, 0)
        self.assertEqual(self.gates("sign", str(self.plan), "VG-3", "--by", "verifier", "--evidence", "run 42").returncode, 0)

    def test_exits_non_zero_while_any_gate_is_open_and_names_them(self):
        p = self.status()
        self.assertEqual(p.returncode, 3)
        self.assertEqual(p.stdout.splitlines()[-1], "GATES: open VG-1, VG-2, VG-3")
        self.gates("sign", str(self.plan), "VG-1", "--by", "verifier", "--evidence", EVIDENCE)
        self.gates("sign", str(self.plan), "VG-3", "--by", "verifier", "--evidence", EVIDENCE)
        p = self.status()
        self.assertEqual(p.returncode, 3)
        self.assertEqual(p.stdout.splitlines()[-1], "GATES: open VG-2")  # the one human gate still holds the close

    def test_exits_zero_only_when_every_gate_is_closed(self):
        self.signed_all()
        p = self.status()
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(p.stdout.splitlines()[-1], "GATES: closed")
        self.assertIn("VG-2\tclosed\thuman\towner\t", p.stdout)

    def test_no_plan_and_a_malformed_plan_are_also_non_zero(self):
        self.plan.write_text("## What\n\nNo plan.\n", encoding="utf-8")
        self.assertEqual(self.status().returncode, 1)
        self.write("- [ ] VG-1 no fields")
        self.assertEqual(self.status().returncode, 2)

    def test_a_box_ticked_by_hand_closes_nothing(self):
        self.write(HUMAN_2.replace("[ ]", "[x]"))
        p = self.status()
        self.assertEqual(p.returncode, 3)
        self.assertIn("ticked with no signature", p.stdout)

    def test_a_signature_on_an_unticked_box_is_open(self):
        self.write(HUMAN_2 + f" · signed: owner · decision: {DECISION}")
        p = self.status()
        self.assertEqual(p.returncode, 3)
        self.assertIn("unticked", p.stdout)

    def test_a_signature_typed_by_hand_needs_the_shape_sign_would_accept(self):
        ticked = HUMAN_2.replace("[ ]", "[x]")
        for tail in (f" · signed: worker · decision: {DECISION}",  # not the owner
                     " · signed: owner · decision: the owner said yes",  # a peer's report, not a decision line
                     " · signed: owner · proof: `make test` → `ok`",  # evidence cannot stand in for the owner
                     " · signed: owner · decision: 2026-01-02 · VG-7 sign it? → sign; not hold — another gate's answer",
                     " · signed: owner · decision: 2026-01-02 · VG-2 sign it? → hold; not sign — not yet"):
            with self.subTest(tail):
                self.write(ticked + tail)
                p = self.status()
                self.assertEqual(p.returncode, 3, p.stdout)
                self.assertEqual(p.stdout.splitlines()[-1], "GATES: open VG-2")

    def test_a_when_gate_on_a_machine_with_its_flags_off_stays_open_and_says_why(self):
        self.write(AGENT_1 + " · when: systems.dbt | systems.airflow")
        p = self.status()
        self.assertEqual(p.returncode, 3)
        self.assertIn("not applicable here: systems.dbt, systems.airflow false", p.stdout)
        self.store({"dbt": False, "airflow": True})
        p = self.status()
        self.assertEqual(p.returncode, 3)  # one flag on: an ordinary open gate
        self.assertNotIn("not applicable", p.stdout)


class HiddenGates(Base):
    """A gate the walker cannot see would let `status` print `GATES: closed`, so what hides one is refused by its line."""

    def assertMalformed(self, lineno: int) -> None:
        for command in ("parse", "status"):
            p = self.gates(command, str(self.plan))
            self.assertEqual(p.returncode, 2, p.stdout)
            self.assertIn(f"gates: {lineno}: ", p.stderr)
            self.assertNotIn("GATES: closed", p.stdout)

    def test_a_plus_quoted_or_numbered_bullet_is_malformed_never_dropped(self):
        for line in ("+ " + HUMAN_2[2:],
                     "+ [x] " + HUMAN_2[6:],
                     "+ [ ] a plain checkbox",
                     "> " + HUMAN_2,
                     "> + VG-2 quoted",
                     "1. [ ] a numbered checkbox"):
            with self.subTest(line[:24]):
                self.write(CLOSED_1, line)
                self.assertMalformed(4)

    def test_a_fence_left_open_in_the_plan_is_malformed_and_names_its_line(self):
        for name, body in (("to the end of the file", f"{CLOSED_1}\n```\n{HUMAN_2}\n"),
                           ("a longer opener a shorter fence cannot close", f"{CLOSED_1}\n````\n```\n{HUMAN_2}\n```\n"),
                           ("over the next heading, closed below it", f"{CLOSED_1}\n```\n{HUMAN_2}\n\n## Done when\n\n```\n")):
            with self.subTest(name):
                self.plan.write_text(f"## Verification plan\n\n{body}", encoding="utf-8")
                self.assertMalformed(4)
                p = self.gates("sign", str(self.plan), "VG-1", "--by", "verifier", "--evidence", EVIDENCE)
                self.assertEqual(p.returncode, 2, p.stdout)

    def test_a_fence_that_closes_or_opens_below_the_plan_changes_nothing_and_an_open_one_above_finds_no_gate(self):
        self.plan.write_text(f"## Verification plan\n\n{CLOSED_1}\n\n```\nan example\n```\n", encoding="utf-8")
        self.assertEqual(self.status().returncode, 0)
        self.plan.write_text(f"## Verification plan\n\n{CLOSED_1}\n\n## Notes\n\n```\nleft open\n", encoding="utf-8")
        self.assertEqual(self.status().returncode, 0)
        self.plan.write_text(f"## Notes\n\n```\nleft open\n\n## Verification plan\n\n{CLOSED_1}\n", encoding="utf-8")
        self.assertEqual(self.status().returncode, 1)
        self.write(CLOSED_1, "```")
        self.assertMalformed(4)  # the one case here that needs the scan: without it this file reads as closed


class OneLine(Base):
    def test_a_unicode_line_break_is_refused_and_leaves_a_plan_status_can_read(self):
        for brk in ("\u2028", "\u2029", "\x85", "\x0b", "\x0c"):
            for args in (("VG-2", "--by", "owner", "--decision", f"{DECISION}{brk}tail"),
                         ("VG-1", "--by", "verifier", "--evidence", f"{EVIDENCE}{brk}tail")):
                with self.subTest(brk=repr(brk), gate=args[0]):
                    before = self.plan.read_bytes()
                    p = self.gates("sign", str(self.plan), *args)
                    self.assertEqual(p.returncode, 3, p.stdout)
                    self.assertIn("one line", p.stderr)
                    self.assertEqual(self.plan.read_bytes(), before)
                    self.assertEqual(self.status().returncode, 3)


class SignHuman(Base):
    def assertRefused(self, *args: str, why: str = "") -> None:
        before = self.plan.read_bytes()
        p = self.gates("sign", str(self.plan), "VG-2", *args)
        self.assertEqual(p.returncode, 3, p.stdout)
        self.assertIn("refused", p.stderr)
        self.assertIn(why, p.stderr)
        self.assertEqual(self.plan.read_bytes(), before)  # a refusal never touches the file

    def test_the_owner_with_a_decision_line_closes_it_and_only_that_line_changes(self):
        p = self.gates("sign", str(self.plan), "VG-2", "--by", "owner", "--decision", DECISION)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), "VG-2 closed by owner")
        want = HUMAN_2.replace("[ ]", "[x]") + f" · signed: owner · decision: {DECISION}"
        self.assertEqual(self.plan.read_text(encoding="utf-8"), EPIC.replace(HUMAN_2, want))
        self.assertIn("VG-2\tclosed", self.status().stdout)

    def test_no_other_role_signs_a_human_gate(self):
        for by in ("worker", "orchestrator", "verifier", "peer", "Owner"):
            with self.subTest(by):
                self.assertRefused("--by", by, "--decision", DECISION, why=f"only the owner signs VG-2, a human gate, not {by}")

    def test_the_owner_still_needs_a_decision_line(self):
        self.assertRefused("--by", "owner", why="needs --decision")
        self.assertRefused("--by", "owner", "--evidence", EVIDENCE, why="takes --decision, not --evidence")

    def test_a_line_outside_the_ledger_grammar_is_refused(self):
        for line in ("the owner said yes",  # what a peer message carries
                     "2026-01-02 VG-2 sign the mapping? → sign; not hold — no separator",
                     "2026-01-02 · VG-2 sign the mapping? → sign",  # no rejected option, no why
                     "2026-01-02 · VG-2 sign the mapping? → sign; not hold",  # no why
                     "2026-13-45 · VG-2 sign the mapping? → sign; not hold — not a date"):
            with self.subTest(line):
                self.assertRefused("--by", "owner", "--decision", line)

    def test_an_answer_about_another_gate_or_not_to_sign_is_refused(self):
        longer_id = "VG-" + "20"  # assembled: a two-digit id reads as a ticket key to the leak scan
        self.assertRefused("--by", "owner", "--decision", f"2026-01-02 · {longer_id} sign it? → sign; not hold — another gate",
                           why="does not name VG-2")
        self.assertRefused("--by", "owner", "--decision", "2026-01-02 · VG-2 sign it? → hold; not sign — the numbers are off",
                           why="not `sign`")
        self.assertRefused("--by", "owner", "--decision", "2026-01-02 · VG-2 sign it? → signal; not hold — a word", why="not `sign`")

    def test_one_line_naming_a_second_gate_signs_neither(self):
        for question in ("VG-1 and VG-2 sign both?", "VG-2, VG-3: sign them?", "sign VG-2 after vg-1?"):
            with self.subTest(question):
                self.assertRefused("--by", "owner", "--decision", f"2026-01-02 · {question} → sign; not hold — a batch answer",
                                   why="also names")
        typed = "2026-01-02 · VG-1 and VG-2 sign both? → sign; not hold — a batch answer"
        self.write(HUMAN_2.replace("[ ]", "[x]") + f" · signed: owner · decision: {typed}")
        p = self.status()
        self.assertEqual(p.returncode, 3, p.stdout)
        self.assertIn("also names VG-1", p.stdout)

    def test_the_answer_is_sign_and_nothing_longer(self):
        for answer in ("sign nothing yet, hold", "sign later", "signed off"):
            with self.subTest(answer):
                self.assertRefused("--by", "owner", "--decision", f"2026-01-02 · VG-2 sign it? → {answer}; not hold — a longer answer",
                                   why="not `sign`")
        for answer in ("sign", "signed", "Sign"):
            with self.subTest(answer):
                self.plan.write_text(EPIC, encoding="utf-8")
                p = self.gates("sign", str(self.plan), "VG-2", "--by", "owner", "--decision",
                               f"2026-01-02 · VG-2 sign it? → {answer}; not hold — it holds")
                self.assertEqual(p.returncode, 0, p.stderr)

    def test_a_multi_line_decision_is_refused(self):
        self.assertRefused("--by", "owner", "--decision", DECISION + "\n- [x] VG-1 forged", why="one line")

    def test_signing_again_is_idempotent_and_a_different_line_is_refused(self):
        self.gates("sign", str(self.plan), "VG-2", "--by", "owner", "--decision", DECISION)
        after = self.plan.read_bytes()
        p = self.gates("sign", str(self.plan), "VG-2", "--by", "owner", "--decision", DECISION)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.plan.read_bytes(), after)
        p = self.gates("sign", str(self.plan), "VG-2", "--by", "owner", "--decision", DECISION + "  ")  # a stray space is the same line
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.plan.read_bytes(), after)
        self.assertRefused("--by", "owner", "--decision", "2026-02-03 · VG-2 again? → sign; not hold — a second answer",
                           why="already carries a signature")


class SignAgent(Base):
    def test_the_verifier_closes_it_on_each_evidence_shape(self):
        for item in (EVIDENCE, "https://example.com/run/1", "#12", "a1b2c3d", "run 1234567", "run nightly-42"):
            with self.subTest(item):
                self.plan.write_text(EPIC, encoding="utf-8")
                p = self.gates("sign", str(self.plan), "VG-1", "--by", "verifier", "--evidence", item)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertIn(f"· signed: verifier · proof: {item}\n", self.plan.read_text(encoding="utf-8"))
                self.assertIn("VG-1\tclosed", self.status().stdout)

    def test_bare_evidence_is_refused(self):
        for item in ("it passed", "1500000", "run", "defaced"):
            with self.subTest(item):
                p = self.gates("sign", str(self.plan), "VG-1", "--by", "verifier", "--evidence", item)
                self.assertEqual(p.returncode, 3, p.stdout)
                self.assertIn("bare", p.stderr)

    def test_a_run_id_is_the_whole_item(self):
        for item in ("could not run v2", "run v2 failed", "did not run 1234"):
            with self.subTest(item):
                p = self.gates("sign", str(self.plan), "VG-1", "--by", "verifier", "--evidence", item)
                self.assertEqual(p.returncode, 3, p.stdout)
                self.assertIn("bare", p.stderr)
        self.write(AGENT_1.replace("[ ]", "[x]") + " · signed: verifier · proof: could not run v2")
        self.assertEqual(self.status().returncode, 3)

    def test_no_other_role_signs_an_agent_gate_and_a_decision_does_not_stand_in(self):
        before = self.plan.read_bytes()
        for by in ("worker", "orchestrator", "owner"):
            with self.subTest(by):
                p = self.gates("sign", str(self.plan), "VG-1", "--by", by, "--evidence", EVIDENCE)
                self.assertEqual(p.returncode, 3, p.stdout)
        p = self.gates("sign", str(self.plan), "VG-1", "--by", "verifier", "--decision", DECISION)
        self.assertEqual(p.returncode, 3, p.stdout)
        p = self.gates("sign", str(self.plan), "VG-1", "--by", "verifier")
        self.assertEqual(p.returncode, 3, p.stdout)
        self.assertEqual(self.plan.read_bytes(), before)

    def test_a_when_gate_signs_only_where_a_flag_is_on(self):
        self.write(AGENT_1 + " · when: systems.dbt")
        p = self.gates("sign", str(self.plan), "VG-1", "--by", "verifier", "--evidence", EVIDENCE)
        self.assertEqual(p.returncode, 3, p.stdout)
        self.assertIn("not applicable here", p.stderr)
        self.store({"dbt": True})
        p = self.gates("sign", str(self.plan), "VG-1", "--by", "verifier", "--evidence", EVIDENCE)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.status().returncode, 0)

    def test_a_when_gate_once_closed_stays_closed_where_the_flag_is_off(self):
        self.store({"dbt": True})
        self.write(AGENT_1 + " · when: systems.dbt")
        self.gates("sign", str(self.plan), "VG-1", "--by", "verifier", "--evidence", EVIDENCE)
        self.store({"dbt": False})
        self.assertEqual(self.status().returncode, 0)

    def test_an_absent_gate_is_exit_1_and_stdin_is_a_usage_error(self):
        self.assertEqual(self.gates("sign", str(self.plan), "VG-7", "--by", "verifier", "--evidence", EVIDENCE).returncode, 1)
        self.assertEqual(self.gates("sign", "-", "VG-1", "--by", "verifier", "--evidence", EVIDENCE).returncode, 2)
        self.assertEqual(self.gates("sign", str(self.tmp / "missing.md"), "VG-1", "--by", "verifier", "--evidence", EVIDENCE).returncode, 2)


class Template(Base):
    def rows(self) -> list[list[str]]:
        p = self.gates("parse", str(TEMPLATE))
        self.assertEqual(p.returncode, 0, p.stderr)
        return [line.split("\t") for line in p.stdout.splitlines()]

    def test_parses_clean_with_at_least_four_gates_and_nothing_signed(self):
        rows = self.rows()
        self.assertGreaterEqual(len(rows), 4)
        self.assertEqual({r[1] for r in rows}, {"agent"})
        p = self.gates("status", str(TEMPLATE))
        self.assertEqual(p.returncode, 3)  # a template is all open: nothing in it closes an epic
        self.assertNotIn("signed:", TEMPLATE.read_text(encoding="utf-8").split("\n## Verification plan\n", 1)[1])

    def test_the_default_gates_cover_what_the_template_promises(self):
        default = [r for r in self.rows() if r[6] == "-"]
        self.assertEqual(len(default), 4)
        text = " ".join(" ".join(r).lower() for r in default)
        for word in ("fail on the default branch", "approved review", "green ci", "dw-<n>", "miss check", "operator brief"):
            self.assertIn(word, text)

    def test_the_pipeline_block_holds_both_gates_with_their_flags(self):
        flagged = {r[-1]: r[6] for r in self.rows() if r[6] != "-"}
        prod_run = next(flags for gate, flags in flagged.items() if "prod run" in gate)
        manifest = next(flags for gate, flags in flagged.items() if "manifest" in gate)
        self.assertEqual(prod_run, "systems.dbt | systems.airflow")
        self.assertEqual(manifest, "systems.dbt")

    def test_the_page_states_the_format_a_new_template_builds_on(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("## The gate format", text)
        for field in ("`kind`", "`check`", "`evidence`", "`signer`", "`when`", "`signed`", "--decision", "--evidence",
                      "docs/verification/<name>.md"):
            self.assertIn(field, text)

    def test_both_closing_rows_state_their_refusals(self):
        lines = TEMPLATE.read_text(encoding="utf-8").splitlines()
        human = next(line for line in lines if line.startswith("| `human` | `gates.py sign"))
        agent = next(line for line in lines if line.startswith("| `agent` | `gates.py sign"))
        self.assertIn("whose answer is `sign`", human)
        self.assertIn("names this `VG-<n>` and no other", human)
        self.assertIn("exactly `run <id>`", agent)
        for row in (human, agent):
            self.assertIn("where the gate has a `when`, one of its flags is on", row)

    def test_the_page_says_gates_close_in_their_own_run_and_what_hides_one(self):
        text = " ".join(TEMPLATE.read_text(encoding="utf-8").split())
        lead = text.split(" ## Verification plan ", 1)[1].split("- [ ] VG-1", 1)[0]
        for phrase in ("the verifier's gate run (`phase=gates`)", "after every child's `phase=merged` has recorded its passes",
                       "one line naming the children it covers", "A gate stands alone"):
            self.assertIn(phrase, lead)
        self.assertIn("a `+` or quoted bullet", text)
        self.assertIn("A code fence still open at the next `## ` heading", text)

    def test_the_retired_word_stays_out_of_this_tickets_page_and_code(self):
        word = "pro" + "file"
        for path in (TEMPLATE, VERIFIER, BIN / "gates.py"):
            text = path.read_text(encoding="utf-8").lower().replace("kit_" + word, "")  # the module's name, not the layer
            self.assertNotIn(word, text, path.name)


class DecisionGrammar(unittest.TestCase):
    def test_the_signer_rule_reads_the_grammar_the_carousel_page_fixes(self):
        page = (KIT / "docs" / "carousel.md").read_text(encoding="utf-8")
        self.assertIn("YYYY-MM-DD · <question> → <chosen>; not <rejected options> — <one clause why>", page)
        source = (BIN / "gates.py").read_text(encoding="utf-8")
        self.assertIn("· (?P<question>\\S.*?) → (?P<chosen>\\S.*?); not (?P<rejected>\\S.*?) — ", source)


class Verifier(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = VERIFIER.read_text(encoding="utf-8")
        cls.front = cls.text.split("---", 2)[1]
        cls.body = cls.text.split("---", 2)[2]

    def section(self, head: str) -> str:
        return self.body.split(f"## {head}", 1)[1].split("\n## ", 1)[0]

    def test_passes_the_environment_free_validator(self):
        p = subprocess.run([sys.executable, str(BIN / "kit_verify.py"), "--no-env", "agents/verifier.md"], capture_output=True,
                           text=True, encoding="utf-8", cwd=KIT, env={**os.environ, "CONTEXT_ROOT": "/nonexistent/.context"})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_cannot_edit_by_tool_or_spawn_and_runs_on_opus_by_default(self):
        self.assertIn("model: opus", self.front)
        for tool in ("Edit", "Write", "NotebookEdit", "Agent", "SendMessage"):
            self.assertRegex(self.front, rf"disallowedTools:.*\b{tool}\b")

    def test_the_return_names_the_head_and_the_phase(self):
        section = self.section("6. Return")
        for line in ("PASS <ticket> <head> phase=<read|merged|gates> <ids> · <evidence>",
                     "FAIL <ticket> <head> phase=<read|merged|gates> <ids> · <why>"):
            self.assertIn(line, section)

    def test_a_pass_is_recorded_only_at_phase_merged(self):
        merged = self.section("4. phase=merged")
        self.assertIn("swarm.py pass <key> <ticket> DW-<n> --evidence", merged)
        self.assertIn("--by verifier", merged)
        for head in ("Input", "1. Read at the source", "2. The two checks", "3. phase=read", "5. phase=gates", "6. Return"):
            self.assertNotIn("swarm.py pass <", self.section(head), head)  # the only place the call is spelled
        self.assertIn("Write nothing", self.section("3. phase=read"))

    def test_a_gate_is_signed_only_in_the_gate_run(self):
        self.assertIn("gates.py sign <plan> VG-<n> --by verifier --evidence", self.section("5. phase=gates"))
        for head in ("Input", "1. Read at the source", "2. The two checks", "3. phase=read", "4. phase=merged", "6. Return"):
            self.assertNotIn("gates.py sign <", self.section(head), head)
        self.assertIn("call `gates.py sign` anywhere but `phase=gates`", self.section("Never"))

    def test_only_the_gate_run_takes_gates_plan_and_children(self):
        ticket, epic = self.section("Input").split("```")[1].strip().splitlines()
        self.assertIn("PHASE: read|merged", ticket)
        for field in ("GATES:", "PLAN:", "CHILDREN:"):
            self.assertNotIn(field, ticket)
        for field in ("TICKET: <epic n>", "PR: -", "HEAD: <default-branch sha>", "PHASE: gates", "PLAN: <file>",
                      "GATES: <VG-n, …>", "CHILDREN: <ticket>:<pr>, …"):
            self.assertIn(field, epic)

    def test_the_gate_run_holds_the_rules_it_was_split_out_for(self):
        section = " ".join(self.section("5. phase=gates").split())
        for rule in ("after every child's `phase=merged` has recorded its passes", "falls due days later", "once per child",
                     "filled from that child", "names the children it covers", "A gate stands alone", "the next run names only",
                     "A `human` gate is a `FAIL`", "`not applicable here` is not run"):
            self.assertIn(rule, section)
        self.assertIn("An epic gets the gate run", " ".join(self.body.split()))

    def test_the_probe_runs_in_the_background_and_a_cut_call_is_no_failure(self):
        section = " ".join(self.section("2. The two checks").split())
        for phrase in ("`run_in_background: true`", "completion notice", "A call the tool cut short is not a failing check: re-run it"):
            self.assertIn(phrase, section)

    def test_the_description_names_the_gate_run_within_its_old_size(self):
        value = next(line for line in self.front.splitlines() if line.startswith("description:")).split('"')[1]
        self.assertIn("phase=gates", value)
        self.assertLessEqual(len(value.encode()), 287)  # the kit-wide description budget has no room for more

    def test_a_ticket_gets_two_runs_from_a_fresh_agent(self):
        self.assertIn("two runs", self.body)
        self.assertIn("transcript", self.body)
        self.assertIn("fresh", self.body)

    def test_it_never_signs_a_human_gate_or_takes_a_verified_line_as_evidence(self):
        never = self.section("Never")
        self.assertIn("human gate", never)
        self.assertIn("`## Verified`", never)


class GateRunProof(Base):
    def test_the_agents_example_proof_signs_and_closes_a_gate(self):
        section = VERIFIER.read_text(encoding="utf-8").split("## 5. phase=gates", 1)[1]
        item = re.search(r"`` (`done_when\.py coverage`.*?) ``", section)[1]
        p = self.gates("sign", str(self.plan), "VG-1", "--by", "verifier", "--evidence", item)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("VG-1\tclosed", self.status().stdout)


if __name__ == "__main__":
    unittest.main()
