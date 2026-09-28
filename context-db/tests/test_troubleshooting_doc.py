"""docs/troubleshooting.md exists, is linked from README.md (the general rule is
test_readme_docs_index.py; this file checks the page's own content), and states the specific facts a user
needs — the hooks' silent-failure paths (cross-checked against hooks/hooks.json's real strings, so the doc
cannot drift from the script), `/kit-health` as the first command, the five most common RED findings and
their fix, and an uninstall procedure covering both install modes. Stdlib unittest.
Run: make -C $BATON/context-db test."""
from __future__ import annotations
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
DOC = KIT / "docs" / "troubleshooting.md"


class TroubleshootingDocExists(unittest.TestCase):
    def setUp(self):
        self.assertTrue(DOC.is_file(), "docs/troubleshooting.md is missing")
        self.text = DOC.read_text(encoding="utf-8")

    def test_linked_from_the_readme(self):
        readme = (KIT / "README.md").read_text(encoding="utf-8")
        self.assertIn("docs/troubleshooting.md", readme)


class HookFailureSurfaceMatchesTheRealHook(unittest.TestCase):
    """Cross-checked against hooks/hooks.json's actual strings, not just asserted in isolation."""

    def setUp(self):
        self.text = DOC.read_text(encoding="utf-8")
        self.hooks_json = (KIT / "hooks" / "hooks.json").read_text(encoding="utf-8")

    def test_names_the_scratch_dir_lookup(self):
        self.assertIn("kit_profile.py scratch", self.text)
        self.assertIn("hooks.log", self.text)
        # the doc's command must be the same tool the hook itself shells out to
        self.assertIn("kit_profile.py", self.hooks_json)

    def test_quotes_the_real_session_env_failure_line(self):
        self.assertIn("session-env failed writing to", self.text)
        self.assertIn("session-env failed writing to", self.hooks_json)

    def test_quotes_the_real_workspace_rules_failure_line(self):
        self.assertIn("could not load WORKSPACE.md", self.text)
        self.assertIn("could not load WORKSPACE.md", self.hooks_json)

    def test_sessionend_hook_points_to_sync_md_not_reinvented(self):
        # docs/sync.md already documents .sync-status / sync.log in full — this page should point there,
        # not carry a second, divergent description of the same mechanism
        self.assertIn("sync.md", self.text)
        self.assertIn(".sync-status", self.text)


class KitHealthIsTheFirstStep(unittest.TestCase):
    def test_kit_health_is_named_as_the_first_command(self):
        text = DOC.read_text(encoding="utf-8")
        self.assertIn("/kit-health", text)
        self.assertIn("first command", text)


class RedFindingsTable(unittest.TestCase):
    """The five findings quoted must be real `ERR` strings from kit-health.py, not paraphrases that could
    drift from what a user actually sees."""

    def setUp(self):
        self.text = DOC.read_text(encoding="utf-8")
        self.script = (KIT / "skills" / "kit-health" / "kit-health.py").read_text(encoding="utf-8")

    def test_five_red_findings_are_real_strings_from_kit_health(self):
        quoted = [
            "env store missing",
            "no identity from any source",
            "root CLAUDE.md missing",
            "kit-verify failed",
        ]
        text_flat = self.text.replace("`", "")
        script_flat = self.script.replace("`", "")
        for phrase in quoted:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text_flat)
                self.assertIn(phrase, script_flat)

    def test_names_the_fix_commands(self):
        for cmd in ("kb.py init --blank", "setup.sh", "verify-skill"):
            self.assertIn(cmd, self.text)


class UninstallProcedure(unittest.TestCase):
    def setUp(self):
        self.text = DOC.read_text(encoding="utf-8")
        self.setup_sh = (KIT / "setup.sh").read_text(encoding="utf-8")

    def test_covers_both_install_modes(self):
        self.assertIn("Clone", self.text)
        self.assertIn("Plugin", self.text)

    def test_names_the_real_plugin_cli_commands(self):
        self.assertIn("claude plugin uninstall ai-baton@ai-baton-kit", self.text)
        self.assertIn("claude plugin marketplace remove ai-baton-kit", self.text)

    def test_names_every_artefact_setup_sh_creates(self):
        # each of these is a real path/marker written by setup.sh (checked directly against its source below,
        # so a future setup.sh change that adds/removes an artefact is caught here too)
        for marker in ("settings.local.json", ".claude/workspace.mk", "@.claude/WORKSPACE.md",
                       "@.context/reference/environment.md"):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.text)
                self.assertIn(marker, self.setup_sh)

    def test_memory_symlink_formula_matches_setup_sh(self):
        # setup.sh: SLUG = workspace root with every "/" turned into "-"; HARNESS_MEM = ~/.claude/projects/$SLUG/memory
        self.assertIn("sed 's#/#-#g'", self.setup_sh)
        self.assertIn('$HOME/.claude/projects/$SLUG/memory', self.setup_sh)
        self.assertIn("memory", self.text)
        self.assertIn("/memory", self.text)

    def test_does_not_tell_the_reader_to_delete_context_without_a_caveat(self):
        # .context/ is the user's captured knowledge, not kit wiring - removing the kit must not casually
        # instruct deleting it
        self.assertIn("your captured knowledge", self.text)


if __name__ == "__main__":
    unittest.main()
