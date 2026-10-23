"""docs/vision.md § The four layers: the attribution line, a cross-walk row for every skill and gate, the nine
glossary terms, the README pitch and the cold-reader page that no longer calls the pr-open gate planned."""
from __future__ import annotations
import re
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
GATES = ("cold reader", "evidence_check", "length_check", "verified.py", "review_gate", "kit-verify",
         "diagram-plan", "public-text-check", "done_when", "sketch", "assumption register", "ledger", "carousel")
TERMS = ("Spec", "Verifier", "Environment", "Understanding", "Goal interview", "Miss check", "Operator brief",
         "Walkthrough", "Knowledge lint")
STATUS = re.compile(r"^(existing|shipped \(`\d+\.\d+\.\d+`\)|planned \(#\d+\))$")


def section(text: str, heading: str) -> str:
    if f"## {heading}" not in text:
        return ""
    start = text.index(f"## {heading}")
    rest = text[start + 3:]
    nxt = re.search(r"^## ", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


class VisionLayers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        vision = (KIT / "docs" / "vision.md").read_text(encoding="utf-8")
        cls.layers = section(vision, "The four layers")
        cls.rows = [[c.strip() for c in l.strip().strip("|").split("|")]
                    for l in cls.layers.splitlines() if l.startswith("|") and not l.startswith("|---")][1:]

    def test_attribution_line(self):
        t = " ".join(self.layers.split())
        self.assertIn("Austin Marchese's organisation of Andrej Karpathy's talks", t)
        self.assertIn("Karpathy did not name it as a taxonomy", t)
        for quote in ("people have to be in charge of the spec and plan", "can automate what you can verify",
                      "understanding is still the bottleneck because you cannot be a good director if you do not understand",
                      "Sequoia Ascent talk, Apr 2026", "raw sources, wiki and schema", "ingest, query and lint"):
            self.assertIn(quote, t)

    def test_table_shape(self):
        self.assertGreater(len(self.rows), 40)
        for row in self.rows:
            self.assertEqual(len(row), 5, row)
            self.assertIn(row[1], {"spec", "verifier", "environment", "understanding"}, row)
            self.assertRegex(row[4], STATUS, row)

    def test_every_skill_has_a_row(self):
        mechanisms = " ".join(r[0] for r in self.rows)
        skills = sorted(p.name for p in (KIT / "skills").iterdir() if (p / "SKILL.md").is_file())
        self.assertGreater(len(skills), 20)
        missing = [s for s in skills if f"`{s}`" not in mechanisms]
        self.assertEqual(missing, [], f"skills with no cross-walk row: {missing}")

    def test_every_gate_has_a_row(self):
        mechanisms = " ".join(r[0] for r in self.rows).lower()
        missing = [g for g in GATES if g.lower() not in mechanisms]
        self.assertEqual(missing, [], f"gates with no cross-walk row: {missing}")

    def test_children_status(self):
        by_issue = {r[3]: r[4] for r in self.rows}
        for issue in ("#620", "#621", "#622", "#623", "#625", "#626"):
            self.assertEqual(by_issue[issue], "shipped (`0.13.0`)", issue)

    def test_glossary_has_the_nine_terms(self):
        glossary = (KIT / "docs" / "glossary.md").read_text(encoding="utf-8")
        for term in TERMS:
            self.assertRegex(glossary, rf"(?m)^- \*\*{term}\*\* ", term)

    def test_readme_names_the_four_layers(self):
        readme = (KIT / "README.md").read_text(encoding="utf-8")
        pitch = readme.split("## ", 1)[0]
        self.assertRegex(pitch, r"four layers: spec, verifier, environment and understanding")
        vision = (KIT / "docs" / "vision.md").read_text(encoding="utf-8")
        self.assertIn("Layered practice", section(vision, "Landscape"))

    def test_cold_reader_no_longer_plans_pr_open(self):
        text = " ".join((KIT / "docs" / "cold-reader.md").read_text(encoding="utf-8").split())
        self.assertNotIn("step is planned", text)
        self.assertNotIn("(planned", text)
        self.assertIn("`pr-open` has run it on `## Diagrams` since #386", text)


if __name__ == "__main__":
    unittest.main()
