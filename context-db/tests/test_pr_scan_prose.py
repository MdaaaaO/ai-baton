"""Text-shape regressions for three pr-scan prose/comment fixes that had drifted from the code:

1. `SKILL.md` hardcoded "the deep threshold (600 lines)" even though the threshold is `deep_lines` in
   `config.json` (600 is only its default) — a user who changes `deep_lines` got a brief that still
   quoted the old number back at them.
2. `--limit N` caps how many candidates get *enriched* (3 `gh` calls each), not how many rows are shown
   or counted new (`max_rows` does that) — the old `argument-hint` and prose read as if `--limit` capped
   the table itself.
3. `pr-scan.sh`'s comment at the `trivial-check.py` call site said "exit 1 = gh failure" as if that were
   the only non-zero exit, but the script also documents (and prints the same JSON shape for) exit 3 —
   a config/setup problem.

No network, no subprocess — these just read the files pr-scan.sh and SKILL.md back. Stdlib unittest.
Run: make -C .claude/context-db test."""
from __future__ import annotations
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
SKILL = (KIT / "skills" / "pr-scan" / "SKILL.md").read_text()
SCRIPT = (KIT / "skills" / "pr-scan" / "pr-scan.sh").read_text()


class DeepLinesProseMatchesConfig(unittest.TestCase):
    def test_skill_md_does_not_hardcode_600_lines(self):
        self.assertNotIn("600 lines", SKILL)

    def test_the_deep_threshold_sentence_itself_cites_deep_lines(self):
        # deep_lines is already documented elsewhere in SKILL.md (the config section) — that alone
        # doesn't prove the "over the deep threshold" sentence was fixed, so check that exact sentence
        # (word-wrapped across two lines in the source, hence the single-space join)
        joined = " ".join(ln.strip() for ln in SKILL.splitlines())
        idx = joined.find("over the deep threshold")
        self.assertNotEqual(idx, -1, "no 'over the deep threshold' sentence found in SKILL.md")
        self.assertIn("deep_lines", joined[idx:idx + 120])


class LimitSemanticsProseMatchesTheCode(unittest.TestCase):
    def test_skill_md_argument_hint_distinguishes_limit_from_max_rows(self):
        self.assertIn("max_rows", SKILL)
        # the frontmatter argument-hint line itself must not read as if --limit caps rows shown
        hint_line = next(ln for ln in SKILL.splitlines() if ln.strip().startswith("argument-hint:"))
        self.assertIn("enrich", hint_line.lower())

    def test_skill_md_says_limit_caps_enrichment_not_rows(self):
        self.assertIn("enrich", SKILL.lower())

    def test_pr_scan_sh_header_comment_explains_limit_vs_max_rows(self):
        # `max_rows` and `enrich` both already appear elsewhere in the script (config read, the
        # unrelated "3. enrich" section comment) — that alone proves nothing. The new comment sits
        # right after the usage line (line 2) and must tie --limit to both terms itself.
        lines = SCRIPT.splitlines()
        usage_idx = next(i for i, ln in enumerate(lines) if ln.startswith("# pr-scan.sh ["))
        nearby = "\n".join(lines[usage_idx:usage_idx + 4]).lower()
        self.assertIn("--limit", nearby)
        self.assertIn("max_rows", nearby)
        self.assertIn("enrich", nearby)


class TrivialCheckExitCodeCommentNamesBothFailureExits(unittest.TestCase):
    def test_pr_scan_sh_comment_at_the_trivial_check_call_site_mentions_exit_3(self):
        line = next(ln for ln in SCRIPT.splitlines() if '"$TRIVIAL"' in ln and "exit" in ln.lower())
        self.assertIn("exit 1", line)
        self.assertIn("exit 3", line)


if __name__ == "__main__":
    unittest.main()
