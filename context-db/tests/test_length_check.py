"""length_check.py — the reading-time budget: prose-only word counting, per-surface defaults, the PR-only
per-repo override, and the never-blocks exit code. Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

BIN = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))

import kb  # noqa: E402
import kit_profile  # noqa: E402
import length_check as lc  # noqa: E402

TEST_REPO = "widget-forge/catalog"


def filler(n: int) -> str:
    """`n` made-up prose words, none of them shaped like a table, link, or fence marker."""
    return " ".join(f"widget{i}" for i in range(n))


class BlankStore(unittest.TestCase):
    """Points kit_profile/kb at a throw-away blank store so no test ever touches a real env store."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / ".context"
        env = self.root / "reference" / "env"
        self._saved = (kit_profile.ENV_DIR, kb.ENV)
        kit_profile.ENV_DIR = env
        kb.ENV = env
        kit_profile.env_config.cache_clear()
        kit_profile.load.cache_clear()
        kb.init_blank()
        self.env = {**os.environ, "CONTEXT_ROOT": str(self.root)}

    def tearDown(self):
        kit_profile.ENV_DIR, kb.ENV = self._saved
        kit_profile.env_config.cache_clear()
        kit_profile.load.cache_clear()
        self.tmp.cleanup()


class ProseOnlyCounting(unittest.TestCase):
    def test_fenced_block_is_dropped_whole(self):
        text = "one two three\n\n```\n" + filler(2000) + "\n```\n"
        self.assertEqual(lc.word_count(text), 3)

    def test_tilde_fence_is_dropped_too(self):
        text = "one two\n\n~~~\n" + filler(50) + "\n~~~\n"
        self.assertEqual(lc.word_count(text), 2)

    def test_table_rows_are_dropped(self):
        text = "one two\n| a | b |\n| --- | --- |\n| c widget | d widget |\nthree\n"
        self.assertEqual(lc.word_count(text), 3)

    def test_html_comment_is_dropped(self):
        text = "one two <!-- diagram-plan: where=x;what=y -->\nthree\n"
        self.assertEqual(lc.word_count(text), 3)

    def test_multiline_html_comment_is_dropped(self):
        text = "one\n<!--\n" + filler(40) + "\n-->\nthree\n"
        self.assertEqual(lc.word_count(text), 2)

    def test_markdown_link_target_dropped_text_kept(self):
        text = "see [the release](https://example.test/releases/9) notes\n"
        self.assertEqual(lc.word_count(text), 4)  # see / the release / notes — "the release" is one bracket's text

    def test_bare_url_is_dropped(self):
        text = "see https://example.test/releases/9 for details\n"
        self.assertEqual(lc.word_count(text), 3)  # see / for / details

    def test_autolink_is_dropped(self):
        text = "see <https://example.test/9> for details\n"
        self.assertEqual(lc.word_count(text), 3)

    def test_reference_style_link_definition_line_is_dropped(self):
        text = "one two\n[ref]: https://example.test/9 \"a title\"\nthree\n"
        self.assertEqual(lc.word_count(text), 3)


class FixturePair(unittest.TestCase):
    """The issue's own fixture: a PR body over budget, and the same body with the over-budget detail moved
    into a fenced block instead — the exact pair length_check must tell apart."""

    def test_pr_body_over_budget_prints_over(self):
        body = f"Changes\n{filler(1198)}\n"
        self.assertEqual(lc.render(lc.word_count(body), lc.DEFAULT_BUDGETS["pr"]), "LENGTH: over (1199 words, budget 1000)")

    def test_same_detail_moved_to_a_fenced_block_prints_ok(self):
        body = f"Changes\nsee the attached log below\n```\n{filler(1198)}\n```\n"
        self.assertEqual(lc.render(lc.word_count(body), lc.DEFAULT_BUDGETS["pr"]), "LENGTH: ok")


class PerSurfaceDefaults(unittest.TestCase):
    def test_ticket_budget_is_400(self):
        self.assertEqual(lc.DEFAULT_BUDGETS["ticket"], 400)
        self.assertEqual(lc.render(400, 400), "LENGTH: ok")
        self.assertEqual(lc.render(401, 400), "LENGTH: over (401 words, budget 400)")

    def test_pr_budget_is_1000(self):
        self.assertEqual(lc.DEFAULT_BUDGETS["pr"], 1000)
        self.assertEqual(lc.render(1000, 1000), "LENGTH: ok")
        self.assertEqual(lc.render(1001, 1000), "LENGTH: over (1001 words, budget 1000)")

    def test_handoff_budget_is_600(self):
        self.assertEqual(lc.DEFAULT_BUDGETS["handoff"], 600)
        self.assertEqual(lc.render(600, 600), "LENGTH: ok")
        self.assertEqual(lc.render(601, 600), "LENGTH: over (601 words, budget 600)")


class RepoOverride(BlankStore):
    def test_pr_words_override_replaces_the_default(self):
        kb.save_config({**kb.load_config(), "length": {"repos": {TEST_REPO: {"pr_words": 1800}}}})
        kit_profile.env_config.cache_clear()
        kit_profile.load.cache_clear()
        self.assertEqual(lc.budget_for("pr", TEST_REPO), 1800)

    def test_no_override_falls_back_to_the_default(self):
        self.assertEqual(lc.budget_for("pr", TEST_REPO), lc.DEFAULT_BUDGETS["pr"])

    def test_override_only_applies_to_the_pr_surface(self):
        kb.save_config({**kb.load_config(), "length": {"repos": {TEST_REPO: {"pr_words": 1800}}}})
        kit_profile.env_config.cache_clear()
        kit_profile.load.cache_clear()
        self.assertEqual(lc.budget_for("ticket", TEST_REPO), lc.DEFAULT_BUDGETS["ticket"])
        self.assertEqual(lc.budget_for("handoff", TEST_REPO), lc.DEFAULT_BUDGETS["handoff"])

    def test_no_repo_given_falls_back_even_with_an_override_on_file(self):
        kb.save_config({**kb.load_config(), "length": {"repos": {TEST_REPO: {"pr_words": 1800}}}})
        kit_profile.env_config.cache_clear()
        kit_profile.load.cache_clear()
        self.assertEqual(lc.budget_for("pr", ""), lc.DEFAULT_BUDGETS["pr"])


class ExitCodes(unittest.TestCase):
    def test_exit_0_when_ok(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("one two three\n")
            path = f.name
        try:
            self.assertEqual(lc.main([path, "--surface", "ticket"]), 0)
        finally:
            os.unlink(path)

    def test_exit_0_when_over_too(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(filler(500) + "\n")
            path = f.name
        try:
            self.assertEqual(lc.main([path, "--surface", "ticket"]), 0)
        finally:
            os.unlink(path)

    def test_exit_2_on_bad_surface(self):
        with self.assertRaises(SystemExit) as cm:
            lc.main(["-", "--surface", "nonsense"])
        self.assertEqual(cm.exception.code, 2)

    def test_exit_2_on_missing_surface(self):
        with self.assertRaises(SystemExit) as cm:
            lc.main(["-"])
        self.assertEqual(cm.exception.code, 2)

    def test_exit_2_on_unreadable_file(self):
        self.assertEqual(lc.main(["/nonexistent/path/does-not-exist.md", "--surface", "ticket"]), 2)

    def test_stdin_dash_is_read(self):
        with patch("sys.stdin") as stdin:
            stdin.read.return_value = "one two\n"
            self.assertEqual(lc.main(["-", "--surface", "handoff"]), 0)


if __name__ == "__main__":
    unittest.main()
