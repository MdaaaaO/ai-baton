"""Dangling cross-references and stale timing numbers that `check_links.py` cannot catch on its own
(its own `§` checker exempts a bare `CLAUDE.md`, since that file is the session's *assembled* root, never
the kit's own placeholder — so a `CLAUDE.md § <heading>` pointer that should say `WORKSPACE.md` slips
through silently). Stdlib unittest, reads only the tracked working tree. Run: make -C $BATON/context-db test."""
from __future__ import annotations
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
from tests import hermetic_env  # noqa: E402

CLAUDE_SECTION = re.compile(r"CLAUDE\.md`?\s*§")


def tracked(*patterns: str) -> list[Path]:
    with tempfile.TemporaryDirectory() as tmp:
        r = subprocess.run(["git", "-C", str(KIT), "ls-files", *patterns], capture_output=True, text=True,
                           check=True, env=hermetic_env(tmp, trust=KIT))
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


class PrWatchCadenceMatchesTheHarnessMonitorCap(unittest.TestCase):
    """The Monitor tool caps `timeout_ms` at 1800000 (30 min) — a larger value is silently capped. The skills
    used to prescribe 3600000 as "the maximum" and priced idle watching at one expiry per hour, while every
    watch really expired (and cost a wake-up) every 30 min. Every arming call and the cost prose must say 30 min."""

    FILES = (("skills", "pr-watch", "SKILL.md"), ("skills", "session-register", "SKILL.md"),
             ("skills", "pr-watch", "reference", "auto-sync.md"))

    def test_arming_calls_use_the_real_cap(self):
        for parts in self.FILES[:2]:
            with self.subTest(file="/".join(parts)):
                text = KIT.joinpath(*parts).read_text(encoding="utf-8")
                self.assertIn("timeout_ms: 1800000", text)
                self.assertNotIn("timeout_ms: 3600000", text)

    def test_no_prose_still_claims_a_60_min_monitor(self):
        for parts in self.FILES:
            with self.subTest(file="/".join(parts)):
                text = KIT.joinpath(*parts).read_text(encoding="utf-8")
                for stale in ("60-min Monitor", "one expiry per hour", "1 h multi-PR Monitor"):
                    self.assertNotIn(stale, text)


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
