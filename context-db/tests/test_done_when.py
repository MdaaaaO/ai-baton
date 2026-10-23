"""done_when.py — a ticket's `## Done when` block (stable DW-<n> ids with a `· check:`), which of them a PR body
covers, and the probe that runs the command checks. Pure text plus throwaway temp dirs; no env store touched.
Run: make -C .claude/context-db test."""
from __future__ import annotations
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))

import done_when as dw  # noqa: E402

TICKET = """\
## What

Something.

## Done when

- [ ] DW-1 the parser lists ids · check: unit tests
- [ ] DW-2 the gate refuses a gap · check: `make gate`
- [x] DW-3 a criterion with a · inside it · check: an observation on the board

<!-- Add one area: label. -->
"""

CMD = "`pytest -q` → `12 passed`"


def body(verified: str = "", limit: str | None = None) -> str:
    out = "## Summary\n\nThings.\n\n## Verified\n" + verified + "\n"
    if limit is not None:
        out += "\n## One honest limit\n" + limit + "\n"
    return out + "\nsession `kit-test`\n"


def run(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(BIN / "done_when.py"), *args], input=stdin, capture_output=True,
                          text=True)


class Parse(unittest.TestCase):
    def test_lists_every_id_in_file_order(self):
        crits, problems = dw.parse_ticket(TICKET)
        self.assertEqual(problems, [])
        self.assertEqual([c.id for c in crits], ["DW-1", "DW-2", "DW-3"])

    def test_splits_criterion_and_check_on_the_last_check_marker(self):
        crits, _ = dw.parse_ticket(TICKET)
        self.assertEqual(crits[2].text, "a criterion with a · inside it")
        self.assertEqual(crits[2].check, "an observation on the board")

    def test_only_a_whole_backticked_check_is_a_command(self):
        crits, _ = dw.parse_ticket(TICKET)
        self.assertIsNone(crits[0].command)
        self.assertEqual(crits[1].command, "make gate")
        self.assertIsNone(crits[2].command)

    def test_a_ticket_without_the_block_has_no_criteria(self):
        self.assertEqual(dw.parse_ticket("## What\n\n- [ ] DW-1 x · check: y\n"), ([], []))

    def test_a_legacy_checkbox_with_no_id_is_a_problem(self):
        _, problems = dw.parse_ticket("## Done when\n\n- [ ] DW-1 a · check: b\n- [ ] plain old line\n")
        self.assertEqual(len(problems), 1)
        self.assertIn("plain old line", problems[0])

    def test_an_id_without_a_check_is_a_problem(self):
        _, problems = dw.parse_ticket("## Done when\n\n- [ ] DW-1 criterion with no check\n")
        self.assertEqual(len(problems), 1)

    def test_a_repeated_id_is_a_problem(self):
        crits, problems = dw.parse_ticket("## Done when\n\n- [ ] DW-1 a · check: b\n- [ ] DW-1 c · check: d\n")
        self.assertEqual(len(crits), 1)
        self.assertIn("DW-1 is defined twice", problems[0])

    def test_reads_a_later_done_when_section_from_a_comment(self):
        text = TICKET + "\n---\n2026-10-23 update\n\n## Done when\n\n- [ ] DW-4 added later · check: unit tests\n"
        crits, problems = dw.parse_ticket(text)
        self.assertEqual(problems, [])
        self.assertEqual([c.id for c in crits][-1], "DW-4")

    def test_a_heading_inside_a_code_fence_is_not_a_section(self):
        text = "```\n## Done when\n- [ ] DW-1 a · check: b\n```\n"
        self.assertEqual(dw.parse_ticket(text), ([], []))


class Coverage(unittest.TestCase):
    def setUp(self):
        self.crits, _ = dw.parse_ticket(TICKET)

    def gap(self, text: str):
        return dw.coverage(self.crits, text)

    def test_every_id_verified_with_evidence_is_covered(self):
        b = body(f"- DW-1 · {CMD}\n- DW-2 · https://example.com/run/1\n- DW-3 · #42 merged")
        self.assertEqual(self.gap(b), ([], [], []))

    def test_an_unverified_id_is_a_gap(self):
        b = body(f"- DW-1 · {CMD}")
        self.assertEqual(self.gap(b)[0], ["DW-2", "DW-3"])

    def test_a_pr_that_verifies_a_non_criterion_leaves_the_real_one_a_gap(self):
        b = body(f"- the linter is clean · {CMD}\n- DW-1 · {CMD}\n- DW-3 · {CMD}")
        self.assertEqual(self.gap(b)[0], ["DW-2"])

    def test_a_bare_claim_does_not_cover(self):
        b = body(f"- DW-1 · it works\n- DW-2 · {CMD}\n- DW-3 · {CMD}")
        self.assertEqual(self.gap(b)[0], ["DW-1"])

    def test_one_line_may_name_several_ids(self):
        b = body(f"- DW-1, DW-2 · {CMD}\nDW-3 · {CMD}")
        self.assertEqual(self.gap(b), ([], [], []))

    def test_a_gap_answered_in_the_limit_section_counts_as_covered_with_a_limit(self):
        b = body(f"- DW-1 · {CMD}\n- DW-3 · {CMD}", limit="DW-2 · only checkable at the first real close")
        self.assertEqual(self.gap(b), ([], ["DW-2"], []))

    def test_a_limit_line_without_a_reason_does_not_cover(self):
        b = body(f"- DW-1 · {CMD}\n- DW-3 · {CMD}", limit="DW-2 ·")
        self.assertEqual(self.gap(b)[0], ["DW-2"])

    def test_a_limit_that_only_mentions_the_id_in_prose_does_not_cover(self):
        b = body(f"- DW-1 · {CMD}\n- DW-3 · {CMD}", limit="Not checked: DW-2 needs a live run.")
        self.assertEqual(self.gap(b)[0], ["DW-2"])

    def test_an_id_in_a_verified_line_that_lacks_evidence_is_not_rescued_by_the_limit_section_of_another_id(self):
        b = body(f"- DW-1 · {CMD}", limit="DW-3 · later")
        self.assertEqual(self.gap(b), (["DW-2"], ["DW-3"], []))

    def test_ids_inside_a_fenced_verified_tail_are_ignored(self):
        b = body("Command: `make ci`\nExit: `0`\n\n```\nDW-1 · `x` → `y`\nDW-2 · `x` → `y`\nDW-3 · `x` → `y`\n```")
        self.assertEqual(self.gap(b)[0], ["DW-1", "DW-2", "DW-3"])

    def test_an_id_the_ticket_never_defined_is_reported_and_changes_nothing(self):
        b = body(f"- DW-1 · {CMD}\n- DW-2 · {CMD}\n- DW-3 · {CMD}\n- DW-9 · {CMD}")
        self.assertEqual(self.gap(b), ([], [], ["DW-9"]))

    def test_a_body_with_no_verified_section_leaves_every_id_a_gap(self):
        self.assertEqual(self.gap("## Summary\n\nnothing\n\nsession `x`\n")[0], ["DW-1", "DW-2", "DW-3"])

    def test_the_section_ends_at_the_footer_and_at_the_next_heading(self):
        b = f"## Verified\n- DW-1 · {CMD}\n\n## Not in this PR\n- DW-2 · {CMD}\n\nsession `x`\n"
        self.assertEqual(self.gap(b)[0], ["DW-2", "DW-3"])


class Cli(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.dir = Path(self.td.name)
        self.ticket = self.dir / "ticket.md"
        self.ticket.write_text(TICKET, encoding="utf-8")

    def body_file(self, text: str) -> str:
        p = self.dir / "body.md"
        p.write_text(text, encoding="utf-8")
        return str(p)

    def test_parse_prints_id_kind_check_and_criterion_tab_separated(self):
        p = run("parse", str(self.ticket))
        self.assertEqual(p.returncode, 0, p.stderr)
        rows = [r.split("\t") for r in p.stdout.splitlines()]
        self.assertEqual([r[0] for r in rows], ["DW-1", "DW-2", "DW-3"])
        self.assertEqual(rows[1], ["DW-2", "command", "make gate", "the gate refuses a gap"])
        self.assertEqual(rows[0][1], "manual")

    def test_parse_reads_stdin(self):
        p = run("parse", "-", stdin=TICKET)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(p.stdout.splitlines()), 3)

    def test_parse_exits_1_on_a_ticket_with_no_ids(self):
        p = run("parse", "-", stdin="## What\n\nlegacy\n")
        self.assertEqual((p.returncode, p.stdout), (1, ""))

    def test_parse_exits_2_on_a_malformed_block(self):
        p = run("parse", "-", stdin="## Done when\n\n- [ ] DW-1 a · check: b\n- [ ] legacy\n")
        self.assertEqual(p.returncode, 2)
        self.assertIn("legacy", p.stderr)

    def test_parse_exits_2_on_an_unreadable_file(self):
        self.assertEqual(run("parse", str(self.dir / "missing.md")).returncode, 2)

    def test_coverage_covered_prints_exactly_one_line_and_exits_0(self):
        b = self.body_file(body(f"- DW-1 · {CMD}\n- DW-2 · {CMD}\n- DW-3 · {CMD}"))
        p = run("coverage", "--ticket", str(self.ticket), "--body", b)
        self.assertEqual((p.returncode, p.stdout), (0, "DONE WHEN: covered\n"))

    def test_coverage_gap_names_the_ids_and_exits_3(self):
        b = self.body_file(body(f"- DW-2 · {CMD}"))
        p = run("coverage", "--ticket", str(self.ticket), "--body", b)
        self.assertEqual((p.returncode, p.stdout), (3, "DONE WHEN: gap DW-1, DW-3\n"))

    def test_coverage_with_a_limit_line_is_covered_and_says_so_on_stderr(self):
        b = self.body_file(body(f"- DW-1 · {CMD}\n- DW-2 · {CMD}", limit="DW-3 · needs the first real close"))
        p = run("coverage", "--ticket", str(self.ticket), "--body", b)
        self.assertEqual((p.returncode, p.stdout), (0, "DONE WHEN: covered\n"))
        self.assertIn("DW-3", p.stderr)

    def test_coverage_on_a_ticket_with_no_ids_exits_1_and_prints_nothing(self):
        b = self.body_file(body(f"- DW-1 · {CMD}"))
        p = run("coverage", "--ticket", "-", "--body", b, stdin="## What\n\nlegacy\n")
        self.assertEqual((p.returncode, p.stdout), (1, ""))

    def test_coverage_reads_the_body_from_stdin(self):
        p = run("coverage", "--ticket", str(self.ticket), "--body", "-", stdin=body(f"- DW-1 · {CMD}"))
        self.assertEqual(p.returncode, 3)
        self.assertEqual(p.stdout, "DONE WHEN: gap DW-2, DW-3\n")


class Probe(unittest.TestCase):
    """The pickup check: a fixture ticket and a fixture checkout, one passing, one failing, one slow, two manual."""

    def test_reports_passing_failing_timeout_and_manual(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "marker").write_text("x", encoding="utf-8")
            ticket = (
                "## Done when\n\n"
                "- [ ] DW-1 marker exists · check: `test -f marker`\n"
                "- [ ] DW-2 other file exists · check: `test -f other`\n"
                "- [ ] DW-3 unit tests cover it · check: unit tests\n"
                "- [ ] DW-4 slow command · check: `sleep 5`\n"
            )
            p = run("probe", "--ticket", "-", "--repo-dir", str(repo), "--timeout", "1", stdin=ticket)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(p.stdout.splitlines(),
                             ["DW-1 passing", "DW-2 failing (exit 1)", "DW-3 manual", "DW-4 timeout"])

    def test_runs_in_the_given_directory(self):
        with tempfile.TemporaryDirectory() as td:
            ticket = "## Done when\n\n- [ ] DW-1 cwd · check: `test \"$(basename \"$PWD\")\" = \"" + Path(td).name + "\"`\n"
            p = run("probe", "--ticket", "-", "--repo-dir", td, stdin=ticket)
            self.assertEqual(p.stdout.strip(), "DW-1 passing")


if __name__ == "__main__":
    unittest.main()
