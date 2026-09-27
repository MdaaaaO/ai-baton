"""#20: the suite passes inside a Claude Code plugin-install session, whose Bash carries CLAUDE_PROJECT_DIR, BATON and
WORKSPACE_* — the three tests that used to read them run in a child with junk values set. Stdlib unittest."""
from __future__ import annotations
import os
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


class AmbientSessionEnv(unittest.TestCase):
    def test_suite_ignores_a_sessions_exports(self):
        junk = {"CLAUDE_PROJECT_DIR": "/nonexistent/ws", "BATON": "/nonexistent/kit", "CLAUDE_PLUGIN_ROOT": "/nonexistent/kit",
                "WORKSPACE_GITHUB_LOGIN": "octo-" + "junk", "WORKSPACE_USER": "Junk " + "Person", "WORKSPACE_TZ": "Asia/" + "Tokyo",
                "CLAUDE_PLUGIN_OPTION_TZ": "EDT"}
        r = subprocess.run([sys.executable, "-m", "unittest",
                            "tests.test_identity.KitHealth.test_machine_section_accepts_options_without_the_file",
                            "tests.test_identity.ManifestAndHook.test_the_kit_agrees_and_the_hook_runs_end_to_end",
                            "tests.test_kit_verify_noenv.ProjectDirContextRoot.test_plugin_path_finds_the_projects_context_dir"],
                           cwd=HERE.parent, env={**os.environ, **junk}, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
