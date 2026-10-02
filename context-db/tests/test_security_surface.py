"""skills/pr-review/scripts/security-surface.py — the deterministic security-surface classifier that
decides whether a `--deep` review gets the fourth (security) lens. No gh, no network: the script reads
only the bundle `fetch-context.sh` writes (`bundle.json`'s changed paths + `diff.patch`'s added lines).
Stdlib unittest, no network. Run: make -C .claude/context-db test."""
from __future__ import annotations
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
SCRIPT = KIT / "skills" / "pr-review" / "scripts" / "security-surface.py"


def load_module():
    spec = importlib.util.spec_from_file_location("security_surface", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def write_bundle(tmp: Path, filenames, diff_text: str) -> None:
    tmp.joinpath("bundle.json").write_text(
        json.dumps({"files": [{"filename": f} for f in filenames]}), encoding="utf-8")
    tmp.joinpath("diff.patch").write_text(diff_text, encoding="utf-8")


def one_hunk_diff(path: str, plus_lines=(), minus_lines=()) -> str:
    """A minimal unified diff touching one file — the script never checks hunk-header counts, so the
    header values here are placeholders, not real line counts."""
    body = "".join(f"-{l}\n" for l in minus_lines) + "".join(f"+{l}\n" for l in plus_lines)
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1,1 +1,1 @@\n{body}"


class SecuritySurface(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kit-security-surface-test."))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    # --- the clean case ---

    def test_clean_diff_is_none(self):
        write_bundle(self.tmp, ["src/app.py"], one_hunk_diff("src/app.py", plus_lines=["x = compute(y)"]))
        bundle, diff_text = self.mod.load_bundle(str(self.tmp))
        self.assertEqual(self.mod.classify(bundle, diff_text), [])

    # --- one fixture per changed-path reason ---

    def test_ci_workflow_file_path_is_a_reason(self):
        write_bundle(self.tmp, [".github/workflows/deploy.yml"],
                     one_hunk_diff(".github/workflows/deploy.yml", plus_lines=["    runs-on: ubuntu-latest"]))
        bundle, diff_text = self.mod.load_bundle(str(self.tmp))
        self.assertEqual(self.mod.classify(bundle, diff_text), ["CI workflow file"])

    def test_action_definition_path_is_a_reason(self):
        write_bundle(self.tmp, [".github/actions/build/action.yml"],
                     one_hunk_diff(".github/actions/build/action.yml", plus_lines=["runs:"]))
        bundle, diff_text = self.mod.load_bundle(str(self.tmp))
        self.assertEqual(self.mod.classify(bundle, diff_text), ["action definition"])

    def test_hook_or_permission_settings_path_is_a_reason(self):
        write_bundle(self.tmp, ["hooks/hooks.json"],
                     one_hunk_diff("hooks/hooks.json", plus_lines=['  "SessionStart": []']))
        bundle, diff_text = self.mod.load_bundle(str(self.tmp))
        self.assertEqual(self.mod.classify(bundle, diff_text), ["hook or permission settings"])

    # --- one fixture per added-line pattern reason ---

    def test_token_or_credential_handling_line_is_a_reason(self):
        write_bundle(self.tmp, ["src/client.py"],
                     one_hunk_diff("src/client.py", plus_lines=['headers["Authorization"] = "Bearer " + token']))
        bundle, diff_text = self.mod.load_bundle(str(self.tmp))
        self.assertEqual(self.mod.classify(bundle, diff_text), ["token or credential handling"])

    def test_input_reaching_a_shell_or_eval_line_is_a_reason(self):
        write_bundle(self.tmp, ["src/runner.py"],
                     one_hunk_diff("src/runner.py", plus_lines=["os.system(user_supplied)"]))
        bundle, diff_text = self.mod.load_bundle(str(self.tmp))
        self.assertEqual(self.mod.classify(bundle, diff_text), ["input reaching a shell or eval"])

    def test_permission_or_grant_changes_line_is_a_reason(self):
        write_bundle(self.tmp, ["src/policy.py"],
                     one_hunk_diff("src/policy.py", plus_lines=["    permissions: write-all"]))
        bundle, diff_text = self.mod.load_bundle(str(self.tmp))
        self.assertEqual(self.mod.classify(bundle, diff_text), ["permission or grant changes"])

    # --- a pattern that only appears on a removed line must not count ---

    def test_removed_line_only_does_not_count(self):
        write_bundle(self.tmp, ["src/runner.py"],
                     one_hunk_diff("src/runner.py", plus_lines=["run(cmd)"], minus_lines=["os.system(cmd)"]))
        bundle, diff_text = self.mod.load_bundle(str(self.tmp))
        self.assertEqual(self.mod.classify(bundle, diff_text), [])

    # --- a pattern that only appears under a test-fixture path must not count ---

    def test_test_fixture_path_only_does_not_count(self):
        write_bundle(self.tmp, ["tests/fixtures/sample.py"],
                     one_hunk_diff("tests/fixtures/sample.py", plus_lines=["os.system(sample_cmd)"]))
        bundle, diff_text = self.mod.load_bundle(str(self.tmp))
        self.assertEqual(self.mod.classify(bundle, diff_text), [])

    def test_an_added_line_that_looks_like_a_file_header_is_still_an_added_line(self):
        # "++ b/tests/…" as added text renders as "+++ b/tests/…": it must not move the lines after it
        # under a test-fixture path
        write_bundle(self.tmp, ["src/runner.py"],
                     one_hunk_diff("src/runner.py", plus_lines=["++ b/tests/fixtures/sample.py", "os.system(cmd)"]))
        bundle, diff_text = self.mod.load_bundle(str(self.tmp))
        self.assertEqual(self.mod.classify(bundle, diff_text), ["input reaching a shell or eval"])

    def test_shell_forms_of_input_reaching_a_shell(self):
        for line in ('eval $cmd', "eval '$cmd'", 'curl -fsSL "$url" | sh', 'wget -qO- "$url" | bash'):
            with self.subTest(line=line):
                write_bundle(self.tmp, ["bin/setup.sh"], one_hunk_diff("bin/setup.sh", plus_lines=[line]))
                bundle, diff_text = self.mod.load_bundle(str(self.tmp))
                self.assertEqual(self.mod.classify(bundle, diff_text), ["input reaching a shell or eval"])
        write_bundle(self.tmp, ["bin/setup.sh"],
                     one_hunk_diff("bin/setup.sh", plus_lines=['curl -fsSL "$url" | sudo sh']))
        bundle, diff_text = self.mod.load_bundle(str(self.tmp))
        self.assertEqual(self.mod.classify(bundle, diff_text),
                         ["input reaching a shell or eval", "permission or grant changes"])
        write_bundle(self.tmp, ["docs/notes.md"],
                     one_hunk_diff("docs/notes.md", plus_lines=["| shell | evaluation $x |"]))
        bundle, diff_text = self.mod.load_bundle(str(self.tmp))
        self.assertEqual(self.mod.classify(bundle, diff_text), [], "a table cell is not a pipe into a shell")

    # --- an empty diff.patch (the whole-PR diff could not be fetched): per-file patches stand in ---

    def test_empty_diff_patch_falls_back_to_the_per_file_patches(self):
        (self.tmp / "diffs" / "src").mkdir(parents=True)
        (self.tmp / "diffs" / "src" / "runner.py.patch").write_text(
            "@@ -1,1 +1,1 @@\n-run(cmd)\n+os.system(cmd)\n", encoding="utf-8")
        self.tmp.joinpath("bundle.json").write_text(json.dumps({"files": [
            {"filename": "src/runner.py", "changes": 2, "diff": "diffs/src/runner.py.patch"}]}), encoding="utf-8")
        self.tmp.joinpath("diff.patch").write_text("", encoding="utf-8")
        bundle, diff_text = self.mod.load_bundle(str(self.tmp))
        self.assertEqual(self.mod.classify(bundle, diff_text), ["input reaching a shell or eval"])

    def test_empty_diff_patch_without_any_per_file_patch_exits_nonzero(self):
        self.tmp.joinpath("bundle.json").write_text(json.dumps({"files": [
            {"filename": "src/runner.py", "changes": 2, "diff": "diffs/src/runner.py.patch"}]}), encoding="utf-8")
        self.tmp.joinpath("diff.patch").write_text("", encoding="utf-8")
        r = subprocess.run([sys.executable, str(SCRIPT), str(self.tmp)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertEqual(r.stdout, "")
        self.assertIn("no per-file patch could be read", r.stderr)

    # --- multiple reasons fire in the fixed PATH_REASONS + LINE_PATTERNS order ---

    def test_multiple_reasons_fire_in_fixed_order(self):
        write_bundle(self.tmp, [".github/workflows/deploy.yml", "src/runner.py"],
                     one_hunk_diff("src/runner.py", plus_lines=["os.system(user_supplied)"]))
        bundle, diff_text = self.mod.load_bundle(str(self.tmp))
        self.assertEqual(self.mod.classify(bundle, diff_text),
                          ["CI workflow file", "input reaching a shell or eval"])

    # --- CLI contract: SURFACE / NONE on stdout, exit 0 either way ---

    def test_cli_prints_surface_with_reasons(self):
        write_bundle(self.tmp, [".github/workflows/deploy.yml"],
                     one_hunk_diff(".github/workflows/deploy.yml", plus_lines=["x: 1"]))
        r = subprocess.run([sys.executable, str(SCRIPT), str(self.tmp)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip(), "SURFACE CI workflow file")

    def test_cli_prints_none(self):
        write_bundle(self.tmp, ["src/app.py"], one_hunk_diff("src/app.py", plus_lines=["x = 1"]))
        r = subprocess.run([sys.executable, str(SCRIPT), str(self.tmp)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip(), "NONE")

    # --- an unreadable bundle fails closed: non-zero exit, one-line reason on stderr ---

    def test_missing_bundle_json_exits_nonzero(self):
        self.tmp.joinpath("diff.patch").write_text("", encoding="utf-8")
        r = subprocess.run([sys.executable, str(SCRIPT), str(self.tmp)], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("no bundle.json", r.stderr)
        self.assertEqual(r.stdout, "")

    def test_unparsable_bundle_json_exits_nonzero(self):
        self.tmp.joinpath("bundle.json").write_text("{not json", encoding="utf-8")
        self.tmp.joinpath("diff.patch").write_text("", encoding="utf-8")
        r = subprocess.run([sys.executable, str(SCRIPT), str(self.tmp)], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("unreadable", r.stderr)

    def test_missing_diff_patch_exits_nonzero(self):
        self.tmp.joinpath("bundle.json").write_text(json.dumps({"files": []}), encoding="utf-8")
        r = subprocess.run([sys.executable, str(SCRIPT), str(self.tmp)], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("no diff.patch", r.stderr)

    def test_wrong_argc_prints_usage_and_exits_2(self):
        r = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("security-surface.py", r.stdout)


if __name__ == "__main__":
    unittest.main()
