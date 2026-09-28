"""README.md must link every doc page under docs/ at least once — a reader who only reads the README should
never find a docs/ page it doesn't know exists. `docs/templates/` is a scaffold seeded elsewhere (check_links.py
skips it the same way, `docs/authoring.md`) and carries no page of its own."""
from __future__ import annotations
import re
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
LINK = re.compile(r"\]\((docs/[\w.-]+\.md)\)")


class ReadmeDocsIndex(unittest.TestCase):
    def test_every_doc_page_is_linked_from_the_readme(self):
        readme = (KIT / "README.md").read_text(encoding="utf-8")
        linked = {m.group(1) for m in LINK.finditer(readme)}
        pages = {f"docs/{p.name}" for p in (KIT / "docs").glob("*.md")}
        missing = sorted(pages - linked)
        self.assertEqual(missing, [], f"docs/ pages the README never links: {missing}")


if __name__ == "__main__":
    unittest.main()
