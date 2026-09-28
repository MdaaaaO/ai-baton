"""docs/layout.md's "## The kit's files" table names every path in the kit tree — a row that claims a
path in this repo (not one of the `../.context/...` rows, which are explicitly not in this repo) must
resolve on disk, so a directory that moved (the sign-queue scripts/state split being one instance) is
caught instead of drifting silently. Stdlib unittest.
Run: make -C $BATON/context-db test."""
from __future__ import annotations
import re
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
DOC = KIT / "docs" / "layout.md"
ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|")


def kit_file_rows(text: str) -> list[str]:
    """The left-column path of every row in the "## The kit's files" table, in order."""
    section = text.split("## The kit's files", 1)[1].split("\n## Notes", 1)[0]
    return [m.group(1) for line in section.splitlines() if (m := ROW.match(line))]


class LayoutDocMatchesTree(unittest.TestCase):
    def test_every_row_naming_a_path_in_this_repo_exists(self):
        text = DOC.read_text(encoding="utf-8")
        rows = kit_file_rows(text)
        self.assertGreater(len(rows), 5, "the table parsed suspiciously short — check the section markers")
        missing = []
        for row in rows:
            path = row.split()[0]  # e.g. "hooks/pre-push" out of a cell with no further backticks
            if path.startswith("../"):
                continue  # explicitly ".context/ content, not in this repo" per the doc's own note
            if not (KIT / path).exists():
                missing.append(path)
        self.assertEqual(missing, [], f"docs/layout.md names a path that does not exist in the kit tree: {missing}")

    def test_no_stale_top_level_sign_queue_row(self):
        """The runtime queue moved to `.context/state/sign-queue/`; the kit tree holds
        `skills/sign-queue/` scripts only, no top-level `sign-queue/` directory or table row."""
        text = DOC.read_text(encoding="utf-8")
        self.assertNotIn("sign-queue/` |", text,
                         "docs/layout.md still tables a top-level `sign-queue/` row — the kit ships no such directory")
        self.assertIn("state/sign-queue/", text, "docs/layout.md's workspace tree should name .context/state/sign-queue/")


if __name__ == "__main__":
    unittest.main()
