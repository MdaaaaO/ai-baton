"""Dangling cross-references and stale timing numbers that `check_links.py` cannot catch on its own
(its own `§` checker exempts a bare `CLAUDE.md`, since that file is the session's *assembled* root, never
the kit's own placeholder — so a `CLAUDE.md § <heading>` pointer that should say `WORKSPACE.md` slips
through silently). Stdlib unittest, reads only the tracked working tree. Run: make -C $BATON/context-db test."""
from __future__ import annotations
import re
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
CLAUDE_SECTION = re.compile(r"CLAUDE\.md`?\s*§")


def tracked(*patterns: str) -> list[Path]:
    r = subprocess.run(["git", "-C", str(KIT), "ls-files", *patterns], capture_output=True, text=True, check=True)
    return [KIT / p for p in r.stdout.splitlines() if p]


class NoStaleClaudeMdSectionRefs(unittest.TestCase):
    def test_no_tracked_file_still_points_a_section_at_claude_md(self):
        """The always-on rules moved out of the kit's own `CLAUDE.md` placeholder into `WORKSPACE.md`;
        a body still citing `CLAUDE.md § <heading>` points at a file with no such heading."""
        hits = []
        for f in tracked("*.md", "*.sh"):
            for n, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if CLAUDE_SECTION.search(line):
                    hits.append(f"{f.relative_to(KIT)}:{n}")
        self.assertEqual(hits, [], f"still cites a section of CLAUDE.md (should be WORKSPACE.md): {hits}")


class PrWatchCadenceMatchesItsOwnMonitorCap(unittest.TestCase):
    def test_no_stale_30_min_wording_next_to_a_3600000ms_cap(self):
        """`timeout_ms: 3600000` (60 min, "the maximum") is the skill's own arming value; prose
        elsewhere in the same file must not still call that cap "30-min" — the actual cadence before
        the 2026-09-22 multi-PR consolidation, left behind when the value was raised."""
        text = (KIT / "skills" / "pr-watch" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("timeout_ms: 3600000", text, "fixture assumption broke — the skill no longer arms at 3600000ms")
        self.assertNotIn("30-min Monitor cap", text)
        self.assertNotIn("30-min Monitors", text)


class SessionSpendWordingIsOneDefinition(unittest.TestCase):
    """One definition of "session spend" (main + subagents), stated once and repeated verbatim: the
    registry row is the TOTAL, same figure the block breaks out — not "main session only"."""

    def test_session_handoff_does_not_contradict_its_own_step_8(self):
        text = (KIT / "skills" / "session-handoff" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("TOTAL of main session", text, "fixture assumption broke — step 8's TOTAL wording moved")
        self.assertNotIn("subagents excluded", text)

    def test_session_register_2b_does_not_say_main_session_only(self):
        text = (KIT / "skills" / "session-register" / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("main session only", text)


if __name__ == "__main__":
    unittest.main()
