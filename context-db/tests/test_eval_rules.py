"""gen_eval_rules.py: every kit-review eval prompt carries docs/REVIEW.md § 2–6 verbatim between the markers, and
`--check` reports a drifted prompt. Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
BIN = HERE.parent / "bin"
sys.path.insert(0, str(BIN))
import gen_eval_rules as g  # noqa: E402


class EvalRules(unittest.TestCase):
    def test_prompts_are_in_sync_with_the_rules(self):
        r = subprocess.run([sys.executable, str(BIN / "gen_eval_rules.py"), "--check"], capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "eval prompts carry the current rules"), r.stdout + r.stderr)
        cases = sorted(KIT.glob("evals/kit-review-*/prompt.md"))
        self.assertGreaterEqual(len(cases), 4)
        block = g.rules_block()
        self.assertIn("## 2.", block); self.assertIn("## 6.", block); self.assertNotIn("## 1.", block)  # CI's section stays out
        for p in cases:
            self.assertIn(block, p.read_text(encoding="utf-8"), p)

    def test_apply_replaces_or_appends(self):
        self.assertEqual(g.apply("head\n" + g.BEGIN + "\nold\n" + g.END + "\ntail\n", "NEW"), "head\nNEW\ntail\n")
        self.assertEqual(g.apply("head\n", "NEW"), "head\n\nNEW\n")


if __name__ == "__main__":
    unittest.main()
