"""evidence_check.py — every Verified claim cites an evidence item (ticket-update § Comment grammar).
Stdlib unittest, no env store touched (a pure text checker). Run: make -C .claude/context-db test."""
from __future__ import annotations
import subprocess
import sys
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))

import evidence_check as ec  # noqa: E402


class NoVerifiedSection(unittest.TestCase):
    def test_no_verified_section_passes(self):
        text = "**Win** — shipped the widget\n**Next** — cut the follow-up ticket\n"
        self.assertEqual(ec.missing_evidence(text), [])
        self.assertEqual(ec.claim_lines(text), [])


class ThreeEvidenceKinds(unittest.TestCase):
    def test_command_and_output(self):
        text = "**Verified** — tests pass · `make -C context-db test` → `OK`\n"
        self.assertEqual(ec.missing_evidence(text), [])

    def test_url(self):
        text = "**Verified** — service is up · https://status.example.com/widgets\n"
        self.assertEqual(ec.missing_evidence(text), [])

    def test_pr_or_commit_reference(self):
        for evidence in ("#123", "acme/widgets#123", "a1b2c3d", "https://github.com/acme/widgets/pull/9"):
            text = f"**Verified** — shipped · {evidence}\n"
            self.assertEqual(ec.missing_evidence(text), [], evidence)


class ClaimWithoutEvidence(unittest.TestCase):
    def test_one_line_form_names_that_line_only(self):
        text = "**Win** — moved fast\n**Verified** — looks right to me\n**Next** — ship it\n"
        missing = ec.missing_evidence(text)
        self.assertEqual([n for n, _ in missing], [2])
        self.assertEqual(missing[0][1], "looks right to me")

    def test_bulleted_form_names_each_bare_claim(self):
        text = "**Verified**\n- widgets load · #42\n- checkout still feels slow\n- payments unaffected\n"
        missing = ec.missing_evidence(text)
        self.assertEqual([n for n, _ in missing], [3, 4])
        self.assertEqual([c for _, c in missing], ["checkout still feels slow", "payments unaffected"])


class MixedMultiClaim(unittest.TestCase):
    def test_lists_only_the_bare_claims(self):
        text = (
            "**Verified**\n"
            "- import job finishes · `make -C context-db test` → `OK`\n"
            "- dashboard renders, checked by eye\n"
            "- rollback still works · acme/widgets#77\n"
            "- nobody complained on the call\n"
        )
        missing = ec.missing_evidence(text)
        self.assertEqual([n for n, _ in missing], [3, 5])
        self.assertEqual([c for _, c in missing], ["dashboard renders, checked by eye", "nobody complained on the call"])

    def test_section_stops_at_the_next_field(self):
        text = "**Verified**\n- ok · #1\n**Out of scope** — the rest\n"
        self.assertEqual(ec.missing_evidence(text), [])
        self.assertEqual(len(ec.claim_lines(text)), 1)


class Head(unittest.TestCase):
    def test_strips_a_dangling_separator_and_caps_length(self):
        self.assertEqual(ec.head("a short claim"), "a short claim")
        long_claim = "x" * 100
        self.assertEqual(ec.head(long_claim), "x" * (ec.HEAD_LEN - 1) + "…")


class Cli(unittest.TestCase):
    def _run(self, *args, inp=None):
        r = subprocess.run([sys.executable, str(BIN / "evidence_check.py"), *args], input=inp, capture_output=True, text=True)
        return r.returncode, r.stdout.strip(), r.stderr.strip()

    def test_exit_0_no_verified_section(self):
        code, out, err = self._run("-", inp="**Win** — shipped\n")
        self.assertEqual(code, 0)
        self.assertIn("no Verified section", out)
        self.assertEqual(err, "")

    def test_exit_0_every_claim_cited(self):
        code, out, err = self._run("-", inp="**Verified** — shipped · #1\n")
        self.assertEqual(code, 0)
        self.assertIn("1 claim(s)", out)
        self.assertEqual(err, "")

    def test_exit_3_names_the_missing_claim_line(self):
        code, out, err = self._run("-", inp="**Verified** — looks right to me\n")
        self.assertEqual(code, 3)
        self.assertEqual(out, "")
        self.assertEqual(err, "evidence_check: 1: looks right to me")

    def test_reads_from_a_file(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("**Verified** — shipped · https://example.com/ok\n")
            path = f.name
        try:
            code, out, _ = self._run(path)
            self.assertEqual(code, 0)
            self.assertIn("1 claim(s)", out)
        finally:
            Path(path).unlink()


if __name__ == "__main__":
    unittest.main()
