"""notion-page-review's SKILL.md must say whether `notion-get-comments` includes resolved discussion
threads and, if not, whether a flag exists to ask for them — otherwise a review that must "walk every
outward comment" can silently skip a thread that was resolved before the fetch. Stdlib unittest, reads
only the tracked working tree. Run: make -C $BATON/context-db test."""
from __future__ import annotations
import re
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
SKILL = KIT / "skills" / "notion-page-review" / "SKILL.md"


def get_comments_paragraph(text: str) -> str:
    """The line introducing `notion-get-comments` plus its immediate continuation lines."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "notion-get-comments" in line:
            chunk = [line]
            j = i + 1
            while j < len(lines) and lines[j].startswith("   "):
                chunk.append(lines[j])
                j += 1
            return "\n".join(chunk)
    return ""


class CommentsFetchDocumentsResolvedThreads(unittest.TestCase):
    def test_says_whether_resolved_threads_are_included(self):
        text = SKILL.read_text(encoding="utf-8")
        para = get_comments_paragraph(text)
        self.assertTrue(para, "no `notion-get-comments` step found in SKILL.md")
        # The paragraph must call out open vs resolved explicitly, not just "resolved or not" —
        # that phrase describes a per-thread field without saying whether resolved threads ever arrive.
        mentions_open = re.search(r"\bopen\b", para, re.IGNORECASE)
        mentions_resolved_scope = re.search(r"resolved (thread|discussion|comment)", para, re.IGNORECASE)
        self.assertTrue(mentions_open, f"comments-fetch paragraph never says the fetch is open-only:\n{para}")
        self.assertTrue(mentions_resolved_scope, f"comments-fetch paragraph never scopes resolved threads:\n{para}")

    def test_says_there_is_no_flag_to_fetch_resolved_threads(self):
        text = SKILL.read_text(encoding="utf-8")
        para = get_comments_paragraph(text)
        # Plan asked to "document the default and the flag (or the absence of one)" — assert the
        # absence is spelled out, not left for the reader to assume.
        self.assertRegex(
            para,
            r"no (way to (retrieve|fetch|ask for)|flag)",
            f"comments-fetch paragraph doesn't say there's no flag/way to get resolved threads:\n{para}",
        )


if __name__ == "__main__":
    unittest.main()
