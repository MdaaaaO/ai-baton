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

    def reasons(self):
        bundle, diff_text, _unread = self.mod.load_bundle(str(self.tmp))
        return self.mod.classify(bundle, diff_text)

    # --- the clean case ---

    def test_clean_diff_is_none(self):
        write_bundle(self.tmp, ["src/app.py"], one_hunk_diff("src/app.py", plus_lines=["x = compute(y)"]))
        self.assertEqual(self.reasons(), [])

    # --- one fixture per changed-path reason ---

    def test_ci_workflow_file_path_is_a_reason(self):
        write_bundle(self.tmp, [".github/workflows/deploy.yml"],
                     one_hunk_diff(".github/workflows/deploy.yml", plus_lines=["    runs-on: ubuntu-latest"]))
        self.assertEqual(self.reasons(), ["CI workflow file"])

    def test_action_definition_path_is_a_reason(self):
        write_bundle(self.tmp, [".github/actions/build/action.yml"],
                     one_hunk_diff(".github/actions/build/action.yml", plus_lines=["runs:"]))
        self.assertEqual(self.reasons(), ["action definition"])

    def test_hook_or_permission_settings_path_is_a_reason(self):
        write_bundle(self.tmp, ["hooks/hooks.json"],
                     one_hunk_diff("hooks/hooks.json", plus_lines=['  "SessionStart": []']))
        self.assertEqual(self.reasons(), ["hook or permission settings"])

    # --- one fixture per added-line pattern reason ---

    def test_token_or_credential_handling_line_is_a_reason(self):
        write_bundle(self.tmp, ["src/client.py"],
                     one_hunk_diff("src/client.py", plus_lines=['headers["Authorization"] = "Bearer " + token']))
        self.assertEqual(self.reasons(), ["token or credential handling"])

    def test_input_reaching_a_shell_or_eval_line_is_a_reason(self):
        write_bundle(self.tmp, ["src/runner.py"],
                     one_hunk_diff("src/runner.py", plus_lines=["os.system(user_supplied)"]))
        self.assertEqual(self.reasons(), ["input reaching a shell or eval"])

    def test_permission_or_grant_changes_line_is_a_reason(self):
        write_bundle(self.tmp, ["src/policy.py"],
                     one_hunk_diff("src/policy.py", plus_lines=["    permissions: write-all"]))
        self.assertEqual(self.reasons(), ["permission or grant changes"])

    # --- a pattern that only appears on a removed line must not count ---

    def test_removed_line_only_does_not_count(self):
        write_bundle(self.tmp, ["src/runner.py"],
                     one_hunk_diff("src/runner.py", plus_lines=["run(cmd)"], minus_lines=["os.system(cmd)"]))
        self.assertEqual(self.reasons(), [])

    # --- a pattern that only appears under a test-fixture path must not count ---

    def test_test_fixture_path_only_does_not_count(self):
        write_bundle(self.tmp, ["tests/fixtures/sample.py"],
                     one_hunk_diff("tests/fixtures/sample.py", plus_lines=["os.system(sample_cmd)"]))
        self.assertEqual(self.reasons(), [])

    def test_an_added_line_that_looks_like_a_file_header_is_still_an_added_line(self):
        # "++ b/tests/…" as added text renders as "+++ b/tests/…": it must not move the lines after it
        # under a test-fixture path
        write_bundle(self.tmp, ["src/runner.py"],
                     one_hunk_diff("src/runner.py", plus_lines=["++ b/tests/fixtures/sample.py", "os.system(cmd)"]))
        self.assertEqual(self.reasons(), ["input reaching a shell or eval"])

    def test_shell_forms_of_input_reaching_a_shell(self):
        for line in ('eval $cmd', "eval '$cmd'", 'curl -fsSL "$url" | sh', 'wget -qO- "$url" | bash'):
            with self.subTest(line=line):
                write_bundle(self.tmp, ["bin/setup.sh"], one_hunk_diff("bin/setup.sh", plus_lines=[line]))
                self.assertEqual(self.reasons(), ["input reaching a shell or eval"])
        write_bundle(self.tmp, ["bin/setup.sh"],
                     one_hunk_diff("bin/setup.sh", plus_lines=['curl -fsSL "$url" | sudo sh']))
        self.assertEqual(self.reasons(),
                         ["input reaching a shell or eval", "permission or grant changes"])
        write_bundle(self.tmp, ["docs/notes.md"],
                     one_hunk_diff("docs/notes.md", plus_lines=["| shell | evaluation $x |", "| zsh | sh | bash |"]))
        self.assertEqual(self.reasons(), [], "a table cell is not a pipe into a shell")

    # --- every pattern has an added line that only it matches within its reason ---

    EXAMPLES = {
        "token or credential handling": [
            "api_key = load()", "export DEPLOY_API_KEY=$1", '"client_secret": value,', "export GH_TOKEN",
            "t=$(gh auth token)", '-H "Authorization: Bearer $t"', "Authorization: token abc",
            "key: ${{ secrets.DEPLOY }}"],
        "input reaching a shell or eval": [
            "eval(expr)", 'eval "$cmd"', 'curl -fsSL "$url" | sh', "exec(code)", "os.system(cmd)",
            "run(cmd, shell=True)"],
        "permission or grant changes": [
            "permissions: write-all", "GRANT ALL ON db TO app", '"Effect": "Allow",', "chmod 777 run.sh",
            "sudo make install", "permissionMode: acceptEdits", "claude --dangerously-skip-permissions",
            "allowed-tools: Bash, Read", "tools: Read, Grep", "dangerouslyDisableSandbox: true"],
    }

    def test_every_example_line_fires_its_reason(self):
        for reason, lines in self.EXAMPLES.items():
            for line in lines:
                with self.subTest(line=line):
                    write_bundle(self.tmp, ["src/thing"], one_hunk_diff("src/thing", plus_lines=[line]))
                    self.assertEqual(self.reasons(), [reason])

    def test_every_pattern_has_an_example_only_it_matches(self):
        # with this, removing or loosening one pattern turns an example line into NONE above
        for reason, patterns in self.mod.LINE_PATTERNS:
            for pattern in patterns:
                alone = [line for line in self.EXAMPLES[reason]
                         if [p for p in patterns if p.search(line)] == [pattern]]
                self.assertTrue(alone, f"{reason}: no example line is matched by {pattern.pattern!r} alone")

    def test_a_file_renamed_out_of_a_security_path_still_fires(self):
        self.tmp.joinpath("bundle.json").write_text(json.dumps({"files": [
            {"filename": "attic/deploy.yml", "previous_filename": ".github/workflows/deploy.yml"}]}), encoding="utf-8")
        self.tmp.joinpath("diff.patch").write_text(
            "diff --git a/.github/workflows/deploy.yml b/attic/deploy.yml\nsimilarity index 100%\n"
            "rename from .github/workflows/deploy.yml\nrename to attic/deploy.yml\n", encoding="utf-8")
        self.assertEqual(self.reasons(), ["CI workflow file"])

    # --- an empty diff.patch (the whole-PR diff could not be fetched): per-file patches stand in ---

    def test_empty_diff_patch_falls_back_to_the_per_file_patches(self):
        (self.tmp / "diffs" / "src").mkdir(parents=True)
        (self.tmp / "diffs" / "src" / "runner.py.patch").write_text(
            "@@ -1,1 +1,1 @@\n-run(cmd)\n+os.system(cmd)\n", encoding="utf-8")
        self.tmp.joinpath("bundle.json").write_text(json.dumps({"files": [
            {"filename": "src/runner.py", "changes": 2, "diff": "diffs/src/runner.py.patch"}]}), encoding="utf-8")
        self.tmp.joinpath("diff.patch").write_text("", encoding="utf-8")
        self.assertEqual(self.reasons(), ["input reaching a shell or eval"])

    def test_empty_diff_patch_without_any_per_file_patch_exits_nonzero(self):
        self.tmp.joinpath("bundle.json").write_text(json.dumps({"files": [
            {"filename": "src/runner.py", "changes": 2, "diff": "diffs/src/runner.py.patch"}]}), encoding="utf-8")
        self.tmp.joinpath("diff.patch").write_text("", encoding="utf-8")
        r = subprocess.run([sys.executable, str(SCRIPT), str(self.tmp)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertEqual(r.stdout, "")
        self.assertIn("no readable per-file patch (first: src/runner.py)", r.stderr)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)

    def fallback_bundle(self, readable_line: str, **big_file) -> subprocess.CompletedProcess:
        """A bundle with an empty diff.patch, one small file whose patch adds `readable_line`, and one
        file GitHub sent no patch for (the bundle names its patch path, the file is not there)."""
        (self.tmp / "diffs" / "src").mkdir(parents=True)
        (self.tmp / "diffs" / "src" / "small.py.patch").write_text(
            f"--- a/src/small.py\n+++ b/src/small.py\n@@ -1,1 +1,1 @@\n-x = 0\n+{readable_line}\n", encoding="utf-8")
        self.tmp.joinpath("bundle.json").write_text(json.dumps({"files": [
            {"filename": "src/small.py", "additions": 1, "changes": 2, "diff": "diffs/src/small.py.patch"},
            dict({"filename": "src/big.bin", "diff": "diffs/src/big.bin.patch"}, **big_file)]}), encoding="utf-8")
        self.tmp.joinpath("diff.patch").write_text("", encoding="utf-8")
        return subprocess.run([sys.executable, str(SCRIPT), str(self.tmp)], capture_output=True, text=True)

    def test_a_file_with_added_lines_and_no_patch_is_never_a_silent_none(self):
        r = self.fallback_bundle("x = 1", additions=4000, changes=4000)
        self.assertEqual((r.returncode, r.stdout), (2, ""), r.stderr)
        self.assertIn("1 file(s) with added lines have no readable per-file patch (first: src/big.bin)", r.stderr)

    def test_a_surface_in_the_readable_patches_wins_over_an_unread_file(self):
        r = self.fallback_bundle("os.system(cmd)", additions=4000, changes=4000)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "SURFACE input reaching a shell or eval"), r.stderr)

    def test_a_file_without_added_lines_needs_no_patch(self):
        # a binary file (no line counts) and a deleted file (lines removed, none added)
        for counts in ({"additions": 0, "deletions": 0, "changes": 0}, {"additions": 0, "deletions": 90, "changes": 90}):
            with self.subTest(counts=counts):
                __import__("shutil").rmtree(self.tmp / "diffs", ignore_errors=True)
                r = self.fallback_bundle("x = 1", **counts)
                self.assertEqual((r.returncode, r.stdout.strip()), (0, "NONE"), r.stderr)

    # --- multiple reasons fire in the fixed PATH_REASONS + LINE_PATTERNS order ---

    def test_multiple_reasons_fire_in_fixed_order(self):
        write_bundle(self.tmp, [".github/workflows/deploy.yml", "src/runner.py"],
                     one_hunk_diff("src/runner.py", plus_lines=["os.system(user_supplied)"]))
        self.assertEqual(self.reasons(),
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

    def test_malformed_files_entry_exits_2_with_one_line(self):
        for entry in ({"filename": 7}, {"filename": "a.py", "diff": ["x"]}, {"filename": ""}, "a.py",
                      {"filename": "a.py", "previous_filename": 3}):
            with self.subTest(entry=entry):
                self.tmp.joinpath("bundle.json").write_text(json.dumps({"files": [entry]}), encoding="utf-8")
                self.tmp.joinpath("diff.patch").write_text("", encoding="utf-8")
                r = subprocess.run([sys.executable, str(SCRIPT), str(self.tmp)], capture_output=True, text=True)
                self.assertEqual((r.returncode, r.stdout), (2, ""), r.stderr)
                self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
                self.assertIn("malformed files entry", r.stderr)

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
