"""operator_brief.py — pr-open's `## For the operator` block (docs/operator-brief.md): `check` warns and never
blocks on a missing section, a missing / empty / repeated / overlong / extra line; `extract` prints the four lines
for ticket-close and session-handoff. Also locks the wiring: the three skills, the doc, the README index and the
near-miss eval case all name the block. Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
SKILL = KIT / "skills" / "pr-open"
sys.path.insert(0, str(SKILL))

import operator_brief as ob  # noqa: E402

GOOD = """## Diagrams

drawn

## For the operator

- Can now: Run `export --output <dir>` to choose where the files go.
- Do differently: Replace `--out` with `--output` in your scripts. The old flag stops working.
- Check with: `pytest tests/test_export.py` → `3 passed`.
- Not covered: Nothing.

## Verified

- `pytest -q` → `3 passed`

session `kit-18`
"""


def run(mode: str, body: str):
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "body.md"
        f.write_text(body, encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = ob.main(["operator_brief.py", mode, str(f)])
    return rc, out.getvalue(), err.getvalue()


class Check(unittest.TestCase):
    def test_a_good_block_is_ok(self):
        rc, out, err = run("check", GOOD)
        self.assertEqual((rc, out.strip(), err), (0, "operator: ok", ""))

    def test_a_missing_section_warns_and_does_not_block(self):
        rc, out, err = run("check", GOOD.replace("## For the operator", "## Something else"))
        self.assertEqual(rc, 0)
        self.assertIn("MISSING — no `## For the operator` section", err)
        self.assertNotIn("ok", out)

    def test_a_line_over_20_words_warns(self):
        long_line = " ".join(["word"] * 21)
        rc, _, err = run("check", GOOD.replace("Nothing.", long_line))
        self.assertEqual(rc, 0)
        self.assertIn("LONG `Not covered:` (21 words, max 20)", err)

    def test_exactly_20_words_passes_and_a_code_span_counts_as_one_word(self):
        body = GOOD.replace("Nothing.", " ".join(["word"] * 20))
        self.assertEqual(run("check", body)[2], "")
        span = "`make -C context-db test JOBS=3 and more words inside`"
        self.assertEqual(ob.words(span), 1)
        self.assertEqual(ob.words(f"Run {span} now"), 3)

    def test_a_missing_line_warns_by_label(self):
        body = GOOD.replace("- Check with: `pytest tests/test_export.py` → `3 passed`.\n", "")
        _, _, err = run("check", body)
        self.assertIn("MISSING LINE `Check with:`", err)
        self.assertEqual(err.count("MISSING LINE"), 1)

    def test_an_empty_or_placeholder_line_warns_and_nothing_does_not(self):
        for text in ("", "n/a", "N/A", "none", "TBD", "-"):
            with self.subTest(text=text):
                _, _, err = run("check", GOOD.replace("Nothing.", text))
                self.assertIn("EMPTY `Not covered:`", err)
        for text in ("Nothing.", "Nothing. The check is read-only."):
            with self.subTest(text=text):
                self.assertEqual(run("check", GOOD.replace("Nothing.", text))[2], "")

    def test_a_repeated_line_and_an_extra_line_warn(self):
        body = GOOD.replace("- Not covered: Nothing.\n", "- Not covered: Nothing.\n- Not covered: Nothing.\n- Renamed X to Y.\n")
        _, _, err = run("check", body)
        self.assertIn("DUPLICATE `Not covered:`", err)
        self.assertIn("EXTRA line, not one of the four (- Renamed X to Y.)", err)

    def test_labels_are_case_insensitive_and_the_bullet_is_optional(self):
        body = GOOD.replace("- Can now:", "can now:").replace("- Check with:", "* CHECK WITH:")
        self.assertEqual(run("check", body)[2], "")

    def test_the_section_ends_at_the_footer_when_it_is_last(self):
        body = GOOD.split("## Verified")[0] + "\nsession `kit-18`\n"
        self.assertEqual(run("check", body)[2], "")

    def test_an_unreadable_file_is_exit_2(self):
        err = io.StringIO()
        with redirect_stderr(err):
            rc = ob.main(["operator_brief.py", "check", "/nonexistent/body.md"])
        self.assertEqual(rc, 2)

    def test_a_non_numeric_pr_number_is_exit_2_not_a_traceback(self):
        err = io.StringIO()
        with redirect_stderr(err):
            rc = ob.main(["operator_brief.py", "extract", "--pr", "o/r", "abc"])
        self.assertEqual(rc, 2)
        self.assertIn("operator:", err.getvalue())


class Extract(unittest.TestCase):
    def test_prints_the_four_lines_in_order_and_nothing_else(self):
        rc, out, _ = run("extract", GOOD)
        self.assertEqual(rc, 0)
        lines = out.splitlines()
        self.assertEqual([ln.split(":")[0] for ln in lines],
                         ["- Can now", "- Do differently", "- Check with", "- Not covered"])
        self.assertNotIn("Verified", out)

    def test_normalises_the_bullet(self):
        _, out, _ = run("extract", GOOD.replace("- Can now:", "can now:"))
        self.assertTrue(out.startswith("- Can now: Run `export"))

    def test_no_section_is_exit_1_and_prints_nothing(self):
        rc, out, err = run("extract", "## Verified\n- a → b\n")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("no `## For the operator` section", err)

    def test_the_cli_runs_from_a_shell(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "b.md"
            f.write_text(GOOD, encoding="utf-8")
            p = subprocess.run([sys.executable, str(SKILL / "operator_brief.py"), "check", str(f)],
                               capture_output=True, text=True)
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "operator: ok"))


class Wiring(unittest.TestCase):
    def read(self, rel: str) -> str:
        return (KIT / rel).read_text(encoding="utf-8")

    def test_the_doc_names_the_four_labels_and_the_rules(self):
        doc = self.read("docs/operator-brief.md")
        for label in ob.LABELS:
            self.assertIn(f"`{label}:`", doc)
        for needle in ("20 words", "Nothing.", "Simplified Technical English", "restates the diff"):
            self.assertIn(needle, doc.replace("Restates the diff", "restates the diff"))

    def test_pr_open_writes_the_block_between_diagrams_and_verified(self):
        text = self.read("skills/pr-open/SKILL.md")
        self.assertLess(text.index("## Diagrams —"), text.index("## For the operator —"))
        self.assertLess(text.index("## For the operator —"), text.index("## Verified —"))
        self.assertIn("operator_brief.py check", text)
        self.assertIn("docs/cold-reader.md", text.split("## For the operator —")[1].split("## Verified —")[0])

    def test_ticket_close_and_session_handoff_copy_the_block(self):
        close = self.read("skills/ticket-close/SKILL.md")
        self.assertIn("**For the operator**", close)
        self.assertIn("operator_brief.py extract", close)
        handoff = self.read("skills/session-handoff/SKILL.md")
        self.assertIn("### What changed, in plain words", handoff)
        self.assertIn("operator_brief.py extract", handoff)

    def test_a_failed_read_is_never_copied_as_no_block(self):
        # exit 2 (gh failed) must not read as exit 1 (no section) in either copy step
        self.assertIn("Exit 2 → the read failed", self.read("skills/ticket-close/SKILL.md"))
        self.assertIn("exit 2 (the read failed) is reported", self.read("skills/session-handoff/SKILL.md"))

    def test_the_cold_reader_has_an_operator_block_brief(self):
        self.assertIn("## The operator block", self.read("docs/cold-reader.md"))

    def test_the_doc_is_indexed_and_the_near_miss_eval_exists(self):
        self.assertIn("docs/operator-brief.md", self.read("README.md"))
        case = KIT / "evals" / "pr-open-behaviour-operator-brief-restates-diff"
        self.assertTrue((case / "prompt.md").is_file())
        self.assertIn("restate the diff", (case / "graders" / "criteria.md").read_text(encoding="utf-8"))
        self.assertIn(case.name, self.read("evals/README.md"))


if __name__ == "__main__":
    unittest.main()
