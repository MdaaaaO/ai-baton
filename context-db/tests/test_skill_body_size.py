"""docs/authoring.md § 2 Body sets a 70-130 line target for a skill body (`kit_verify.py` warns past
BODY_WARN_LINES=130 and errors past BODY_MAX_LINES=300, unless the unit is named in BODY_LINES_ALLOW with a
reason) — reference detail (tables, worked scripts, incident histories) is
meant to move into `reference/*.md`, loaded on demand, never the always-loaded body. Seven skills drifted
well past the target (up to 263 body lines) with no reference/ material to show for it. This locks the
fix in: each of the seven now stays under a size comfortably between its old and its restructured line
count, AND has grown a non-empty reference/ directory it did not have (or had less of) before. Reads
files only — no git, no env store. Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
BIN = KIT / "context-db" / "bin"
sys.path.insert(0, str(BIN))

import frontmatter as fmt  # noqa: E402

# skill -> (max body lines allowed now, minimum reference/*.md files expected)
# The ceilings sit strictly between each skill's pre-restructure body length (all well past 130,
# up to 263) and its restructured length — loose enough that a later, legitimate small edit will
# not trip the test, tight enough that reverting the restructuring trips it immediately.
LIMITS = {
    "self-assessment": (220, 1),
    "pr-review": (230, 1),
    "pr-open": (180, 1),
    "pr-watch": (180, 1),
    "cost-report": (140, 1),
    "dbt-sqlfluff-fixes": (110, 1),
    "gh-cli": (105, 1),
}


def body_line_count(skill: str) -> int:
    text = (KIT / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
    parts = fmt.split(text)
    body = parts[1] if parts else text
    return body.count("\n") + (1 if body and not body.endswith("\n") else 0)


class SkillBodySize(unittest.TestCase):
    def test_seven_bodies_stay_under_their_ceiling(self):
        for skill, (max_lines, _) in LIMITS.items():
            with self.subTest(skill=skill):
                n = body_line_count(skill)
                self.assertLessEqual(
                    n, max_lines,
                    f"{skill}/SKILL.md body is {n} lines, over the {max_lines}-line regression ceiling — "
                    "move reference detail into skills/<name>/reference/*.md (docs/authoring.md § 2 Body)",
                )

    def test_seven_skills_grew_a_reference_directory(self):
        for skill, (_, min_files) in LIMITS.items():
            with self.subTest(skill=skill):
                ref_dir = KIT / "skills" / skill / "reference"
                self.assertTrue(ref_dir.is_dir(), f"skills/{skill}/reference/ is missing")
                md_files = [p for p in ref_dir.glob("*.md") if p.stat().st_size > 0]
                self.assertGreaterEqual(
                    len(md_files), min_files,
                    f"skills/{skill}/reference/ has no non-empty .md file — the moved-out detail should live here",
                )


if __name__ == "__main__":
    unittest.main()
