"""One slug rule (#166): `kit_profile.harness_project_slug()` is the one Python definition of the harness's
`~/.claude/projects/<slug>` naming (every path separator becomes `-`); setup.sh and the setup_sh_scenarios.sh
test fixture keep their own shell copies (`sed 's#/#-#g'`) because setup.sh must run before an env store —
and so before Python — is guaranteed usable. This asserts all three still agree, and that kit-health.py calls
the shared function rather than its own inline copy. Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import re
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
BIN = HERE.parent / "bin"
sys.path.insert(0, str(BIN))
import kit_profile  # noqa: E402

# Fact-shaped sample paths, assembled at run time rather than typed as environment-specific literals.
SAMPLE_PATHS = [
    "/".join(["", "home", "u", "work", "proj"]),
    "/".join(["", "srv", "a.b", "c-d_e"]),
    "relative/sub/path",
    "/" + "solo",
    "onlyword",
]


def _shell_slug_expr(script: Path) -> str:
    """The exact `sed` expression a shell script uses for the slug — extracted from its source so this test
    fails the moment the copy drifts from `s#/#-#g`, instead of hand-duplicating the pattern here."""
    text = script.read_text(encoding="utf-8")
    m = re.search(r"sed '(s#/#-#g)'", text)
    assert m, f"{script}: no `sed 's#/#-#g'` slug line found"
    return m.group(1)


def _run_shell_slug(script: Path, path: str) -> str:
    expr = _shell_slug_expr(script)
    r = subprocess.run(["sed", expr], input=path, capture_output=True, text=True, check=True)
    return r.stdout.rstrip("\n")


class SlugRuleTest(unittest.TestCase):
    def test_setup_sh_and_test_fixture_agree_with_kit_profile(self):
        for p in SAMPLE_PATHS:
            want = kit_profile.harness_project_slug(p)
            self.assertEqual(_run_shell_slug(KIT / "setup.sh", p), want, p)
            self.assertEqual(_run_shell_slug(KIT / "context-db" / "tests" / "setup_sh_scenarios.sh", p), want, p)

    def test_harness_project_slug_replaces_every_separator(self):
        self.assertEqual(kit_profile.harness_project_slug("/a/b/c"), "-a-b-c")
        self.assertEqual(kit_profile.harness_project_slug("a/b"), "a-b")
        self.assertEqual(kit_profile.harness_project_slug("noslash"), "noslash")

    def test_kit_health_uses_the_shared_function_not_an_inline_copy(self):
        text = (KIT / "skills" / "kit-health" / "kit-health.py").read_text(encoding="utf-8")
        self.assertIn("kit_profile.harness_project_slug(", text)
        self.assertNotIn('.replace("/", "-")', text)


if __name__ == "__main__":
    unittest.main()
