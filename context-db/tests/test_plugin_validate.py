"""plugin_validate.sh (make plugin-validate): `claude plugin validate` over the root, skills/ and agents/, strict
but for the one root-CLAUDE.md warning the kit accepts on purpose (#521 — the file is the clone install's memory
file, docs/layout.md; a plugin install never loads it). A fake `claude` on PATH plays the CLI: the old one that
prints no warning, the 2.1.28x+ one that prints the accepted warning, one with a second warning, one that errors,
one whose skills/ pass is what fails, and the fail-closed cases: a reworded banner the script can't count, a
lone warning that isn't the accepted one, a count that disagrees with the bullets. No network, no real CLI. Run: make -C .claude/context-db test."""
from __future__ import annotations
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
SCRIPT = KIT / "context-db" / "bin" / "plugin_validate.sh"

ACCEPTED = ("  ❯ root: CLAUDE.md at the plugin root is not loaded as project context. "
            "To ship context with your plugin, use a skill (skills/<name>/SKILL.md) instead.")
OTHER = "  ❯ root: plugin.json: unknown key `colour`"
HEAD = f"Validating marketplace manifest: {KIT}/.claude-plugin/marketplace.json\n\nValidating plugin: {KIT}/CLAUDE.md\n\n"


def banner(warnings: list[str]) -> str:
    if not warnings:
        return HEAD + "✔ Validation passed\n"
    return HEAD + f"⚠ Found {len(warnings)} warning{'s' if len(warnings) != 1 else ''}:\n\n" + "\n".join(warnings) + "\n\n✔ Validation passed with warnings\n"


@unittest.skipUnless(shutil.which("sh"), "sh needed")
class PluginValidate(unittest.TestCase):
    """`root_out`/`root_rc` is what the fake prints and returns for the root (non-strict) pass; `strict_rc` for the
    skills/ and agents/ passes. Every call is logged so the test can see the three invocations."""

    def run_with_fake(self, root_out: str, root_rc: int = 0, strict_rc: int = 0) -> tuple[subprocess.CompletedProcess, list[str]]:
        tmp = Path(tempfile.mkdtemp(prefix="kit-plugin-validate-test."))
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "root.out").write_text(root_out, encoding="utf-8")
        fake = tmp / "claude"
        fake.write_text(
            "#!/bin/sh\n"
            f'echo "$*" >> "{tmp}/calls"\n'
            'case " $* " in\n'
            f'  *" --strict "*) exit {strict_rc} ;;\n'
            f'  *) cat "{tmp}/root.out"; exit {root_rc} ;;\n'
            "esac\n", encoding="utf-8")
        fake.chmod(0o755)
        env = {**os.environ, "PATH": f"{tmp}{os.pathsep}{os.environ.get('PATH', '')}"}
        r = subprocess.run(["sh", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)
        calls = (tmp / "calls").read_text(encoding="utf-8").splitlines() if (tmp / "calls").exists() else []
        return r, calls

    def test_a_cli_with_no_warning_passes_and_runs_all_three_passes(self):
        r, calls = self.run_with_fake(banner([]))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("plugin validate: OK", r.stdout)
        self.assertEqual(calls, ["plugin validate .", "plugin validate --strict skills", "plugin validate --strict agents"])

    def test_the_accepted_root_claude_md_warning_passes(self):
        r, calls = self.run_with_fake(banner([ACCEPTED]))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("plugin validate: OK", r.stdout)
        self.assertIn("not loaded as project context", r.stdout)  # the validator's output is still shown
        self.assertEqual(len(calls), 3)

    def test_any_other_warning_fails_like_strict_and_names_it(self):
        r, calls = self.run_with_fake(banner([ACCEPTED, OTHER]))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("beyond the accepted root CLAUDE.md one", r.stderr)
        self.assertIn("unknown key `colour`", r.stderr)
        self.assertNotIn("not loaded as project context", r.stderr)  # only the unexpected one is named
        self.assertEqual(calls, ["plugin validate ."])  # stops before skills/agents

    def test_a_lone_warning_that_is_not_the_accepted_one_fails(self):
        r, calls = self.run_with_fake(banner([OTHER]))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("beyond the accepted root CLAUDE.md one", r.stderr)
        self.assertIn("unknown key `colour`", r.stderr)
        self.assertEqual(calls, ["plugin validate ."])

    def test_a_changed_bullet_glyph_cannot_slip_a_warning_through(self):
        # the count line still says 2, so a CLI that stops printing `❯` bullets still fails here
        out = HEAD + "⚠ Found 2 warnings:\n\n" + ACCEPTED.replace("❯", "-") + "\n" + OTHER.replace("❯", "-") + "\n\n✔ Validation passed with warnings\n"
        r, calls = self.run_with_fake(out)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("unknown key `colour`", r.stderr)
        self.assertEqual(calls, ["plugin validate ."])

    def test_warnings_without_a_readable_count_fail_closed(self):
        # a reworded banner: the script can't tell how many warnings there are, so it must not say OK
        out = HEAD + "Warnings:\n" + ACCEPTED + "\n" + OTHER + "\n\n✔ Validation passed with warnings\n"
        r, calls = self.run_with_fake(out)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("no 'Found N warning' count", r.stderr)
        self.assertNotIn("plugin validate: OK", r.stdout)
        self.assertEqual(calls, ["plugin validate ."])

    def test_a_validator_error_at_the_root_fails_with_its_exit_code(self):
        r, calls = self.run_with_fake("✘ Validation failed\n", root_rc=1)
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("plugin validate: OK", r.stdout)
        self.assertEqual(calls, ["plugin validate ."])

    def test_a_strict_failure_under_skills_or_agents_fails(self):
        r, calls = self.run_with_fake(banner([ACCEPTED]), strict_rc=1)
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("plugin validate: OK", r.stdout)
        self.assertEqual(calls, ["plugin validate .", "plugin validate --strict skills"])


if __name__ == "__main__":
    unittest.main()
