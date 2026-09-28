"""sizing.py — parsing and formatting a ticket's Sizing line (docs/delegation.md § The Sizing line). Stdlib
unittest, no env store touched (a pure text parser). Run: make -C .claude/context-db test."""
from __future__ import annotations
import subprocess
import sys
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))

import sizing  # noqa: E402


class Parse(unittest.TestCase):
    def test_parses_the_line_anywhere_in_the_body(self):
        body = (
            "**Goal** — ship the widget\n\n"
            "**Plan** — one step\n\n"
            "**Sizing:** `sonnet`, delegate. Specified: one skill edit, one new small skill, docs.\n"
        )
        self.assertEqual(
            sizing.parse(body),
            ("sonnet", "delegate", "Specified: one skill edit, one new small skill, docs."),
        )

    def test_accepts_every_model_and_both_decisions(self):
        for model in sizing.MODELS:
            for decision in sizing.DECISIONS:
                line = f"**Sizing:** `{model}`, {decision}. a reason"
                self.assertEqual(sizing.parse(line), (model, decision, "a reason"))

    def test_missing_line_raises_missing(self):
        with self.assertRaises(ValueError) as cm:
            sizing.parse("no sizing line in this body at all")
        self.assertEqual(str(cm.exception), "missing")

    def test_unknown_model_raises_the_raw_line_not_missing(self):
        with self.assertRaises(ValueError) as cm:
            sizing.parse("**Sizing:** `medium`, delegate. a reason")
        self.assertNotEqual(str(cm.exception), "missing")
        self.assertIn("`medium`", str(cm.exception))

    def test_malformed_shape_raises_the_raw_line(self):
        for bad in (
            "**Sizing:** sonnet, delegate. no backticks",
            "**Sizing:** `sonnet` delegate. missing comma",
            "**Sizing:** `sonnet`, sometimes. bad decision",
            "**Sizing:** `sonnet`, delegate.",  # no reason at all
        ):
            with self.assertRaises(ValueError) as cm:
                sizing.parse(bad)
            self.assertNotEqual(str(cm.exception), "missing", bad)

    def test_reason_stops_at_the_line_end(self):
        body = "**Sizing:** `haiku`, main session. short reason\nnext paragraph, unrelated\n"
        self.assertEqual(sizing.parse(body), ("haiku", "main session", "short reason"))


class Format(unittest.TestCase):
    def test_round_trips_through_parse(self):
        line = sizing.format_line("opus", "main session", "design choice the issue leaves open")
        self.assertEqual(
            sizing.parse(line),
            ("opus", "main session", "design choice the issue leaves open"),
        )

    def test_rejects_bad_model_decision_or_empty_reason(self):
        with self.assertRaises(ValueError):
            sizing.format_line("medium", "delegate", "x")
        with self.assertRaises(ValueError):
            sizing.format_line("sonnet", "in progress", "x")
        with self.assertRaises(ValueError):
            sizing.format_line("sonnet", "delegate", "   ")


class Cli(unittest.TestCase):
    def _run(self, *args, inp=None):
        r = subprocess.run([sys.executable, str(BIN / "sizing.py"), *args], input=inp, capture_output=True, text=True)
        return r.returncode, r.stdout.strip(), r.stderr.strip()

    def test_models_lists_the_accepted_models(self):
        code, out, _ = self._run("models")
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines(), list(sizing.MODELS))

    def test_parse_exit_codes(self):
        code, out, _ = self._run("parse", "-", inp="**Sizing:** `sonnet`, delegate. a reason\n")
        self.assertEqual((code, out), (0, "sonnet\tdelegate\ta reason"))
        code, _, err = self._run("parse", "-", inp="nothing here\n")
        self.assertEqual(code, 1)
        self.assertIn("no Sizing line", err)
        code, _, err = self._run("parse", "-", inp="**Sizing:** `weird`, delegate. a reason\n")
        self.assertEqual(code, 2)
        self.assertIn("malformed", err)

    def test_format_exit_codes(self):
        code, out, _ = self._run("format", "sonnet", "delegate", "a reason")
        self.assertEqual((code, out), (0, "**Sizing:** `sonnet`, delegate. a reason"))
        code, _, err = self._run("format", "medium", "delegate", "a reason")
        self.assertEqual(code, 2)
        self.assertIn("model must be one of", err)


if __name__ == "__main__":
    unittest.main()
