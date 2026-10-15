"""The squash commit's footers: auto-merge.yml sends `<title> (#<n>)` plus the PR body's trailing footer block
(cut by .github/scripts/squash-footers.jq) as the merge's commit_title / commit_message, and the pr-title check
(.github/scripts/check-pr-footers.sh) fails a PR whose breaking change would not reach main as a
`BREAKING CHANGE:` footer. The filter and the check run for real; auto-merge's merge step is cut from the
workflow and run against a stub `gh` (no network, no token) that serves the PR and records the request.
Stdlib unittest (and the `jq` binary the workflows use). Run: make -C .claude/context-db test."""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
SCRIPTS = KIT / ".github" / "scripts"
FILTER = SCRIPTS / "squash-footers.jq"
CHECK = SCRIPTS / "check-pr-footers.sh"
WORKFLOWS = KIT / ".github" / "workflows"


def footers(body: str) -> str:
    p = subprocess.run(["jq", "-Rjs", "-f", str(FILTER)], input=body, capture_output=True, text=True, timeout=10)
    if p.returncode:
        raise AssertionError(p.stderr)
    return p.stdout


@unittest.skipUnless(shutil.which("jq"), "jq not installed")
class FooterBlock(unittest.TestCase):
    def test_the_trailing_block_after_a_blank_line(self):
        body = "Closes #5\n\n## What\n\nProse.\n\nBREAKING CHANGE: run kb.py migrate on every machine\nRefs #6\n"
        self.assertEqual(footers(body), "BREAKING CHANGE: run kb.py migrate on every machine\nRefs #6")

    def test_windows_line_endings_and_trailing_blanks(self):
        self.assertEqual(footers("Prose.\r\n\r\nBREAKING CHANGE: x\r\nCloses #1\r\n\r\n"), "BREAKING CHANGE: x\nCloses #1")

    def test_a_body_of_footers_only(self):
        self.assertEqual(footers("BREAKING CHANGE: x"), "BREAKING CHANGE: x")
        self.assertEqual(footers("Closes #3"), "Closes #3")

    def test_no_block_when_the_body_ends_in_prose(self):
        self.assertEqual(footers("BREAKING CHANGE: x\n\nA closing paragraph."), "")
        self.assertEqual(footers(""), "")

    def test_footer_shaped_lines_glued_to_prose_are_not_a_block(self):
        self.assertEqual(footers("Prose ends here\nBREAKING CHANGE: x"), "")

    def test_only_footer_lines_never_the_rest_of_the_body(self):
        body = "Closes #5\n\n## Verified\n\n- [x] `make ci`\n<!-- hint -->\nhttps://example.com/a\n\nSigned-off-by: a <a@example.com>"
        self.assertEqual(footers(body), "Signed-off-by: a <a@example.com>")
        self.assertEqual(footers("x\n\n- [x] done\nhttps://example.com/a"), "")


    # What pr-open appends after the body: the diagram-plan marker and the session footer line.
    MARKER = "<!-- diagram-plan: facets=ci,docs dominant=ci intent=bugfix runs=ci -->"
    SESSION = "session `kit-01`"

    def test_footer_before_the_marker_and_the_session_line(self):
        body = f"Closes #1\n\n## Machines\n\nRun it.\n\nBREAKING CHANGE: run kb.py migrate\n\n{self.MARKER}\n\n{self.SESSION}\n\n"
        self.assertEqual(footers(body), "BREAKING CHANGE: run kb.py migrate")

    def test_footer_block_glued_to_the_session_line(self):
        body = f"Prose.\n\nBREAKING CHANGE: run kb.py migrate\nRefs #2\n{self.SESSION}"
        self.assertEqual(footers(body), "BREAKING CHANGE: run kb.py migrate\nRefs #2")

    def test_marker_directly_above_the_block(self):
        body = f"Prose.\n\n{self.MARKER}\nBREAKING CHANGE: run kb.py migrate\n\n{self.SESSION}"
        self.assertEqual(footers(body), "BREAKING CHANGE: run kb.py migrate")

    def test_only_known_tokens_are_footers(self):
        self.assertEqual(footers("Prose.\n\nResult: 16 OK"), "")
        self.assertEqual(footers("Prose.\n\nNote: see above\nBREAKING CHANGE: x"), "")
        self.assertEqual(footers("Prose.\n\nco-authored-by: a <a@example.com>\nCloses #3"), "co-authored-by: a <a@example.com>\nCloses #3")

@unittest.skipUnless(shutil.which("jq"), "jq not installed")
class CheckPrFooters(unittest.TestCase):
    def run_check(self, title: str, body: str, author: str = "someone"):
        env = {k: v for k, v in os.environ.items() if k not in ("PR_TITLE", "PR_BODY", "PR_AUTHOR")}
        env.update(PR_TITLE=title, PR_BODY=body, PR_AUTHOR=author)
        return subprocess.run(["bash", str(CHECK)], env=env, capture_output=True, text=True, timeout=30)

    def test_a_bang_title_needs_the_footer(self):
        p = self.run_check("feat(engine)!: sync the store", "Closes #1\n\n## Machines\n\nRun kb.py migrate.")
        self.assertEqual(p.returncode, 1)
        self.assertIn("marks a breaking change", p.stderr)

    def test_a_bang_title_with_the_footer_passes_and_prints_the_block(self):
        p = self.run_check("feat!: x", "Closes #1\n\nProse.\n\nBREAKING CHANGE: run kb.py migrate")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout, "BREAKING CHANGE: run kb.py migrate\n")

    def test_a_footer_outside_the_block_would_be_dropped(self):
        p = self.run_check("feat(x): y", "Closes #1\n\nBREAKING CHANGE: run kb.py migrate\n\n## Verified\n\nall green")
        self.assertEqual(p.returncode, 1)
        self.assertIn("outside the footer block", p.stderr)

    def test_comments_and_code_do_not_count(self):
        body = ("Closes #1\n\n<!-- a hint:\nBREAKING CHANGE: <step>\n-->\n```\nBREAKING CHANGE: an example\n```\n"
                "Inline `BREAKING CHANGE: x` too.\n")
        p = self.run_check("feat(x): y", body)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout, "")

    def test_pr_open_tails_pass_a_breaking_title(self):
        m, s = FooterBlock.MARKER, FooterBlock.SESSION
        for body in (f"Closes #1\n\nProse.\n\nBREAKING CHANGE: run x\n\n{m}\n\n{s}\n",
                     f"Closes #1\n\nProse.\n\nBREAKING CHANGE: run x\n{s}",
                     f"Closes #1\n\nProse.\n\n{m}\nBREAKING CHANGE: run x\n\n{s}"):
            p = self.run_check("feat(x)!: y", body)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(p.stdout, "BREAKING CHANGE: run x\n")

    def test_a_fence_closes_only_on_its_own_marker(self):
        body = "Closes #1\n\n~~~\n```\nBREAKING CHANGE: inside the tilde fence\n~~~\n\nAll prose.\n"
        p = self.run_check("feat(x): y", body)
        self.assertEqual(p.returncode, 0, p.stderr)
        p = self.run_check("feat(x): y", "Closes #1\n\n```\n~~~\n```\nBREAKING CHANGE: after the fence\n\nprose")
        self.assertEqual(p.returncode, 1)
        self.assertIn("outside the footer block", p.stderr)

    def test_the_pr_template_passes(self):
        body = (KIT / ".github" / "pull_request_template.md").read_text(encoding="utf-8").replace("Closes #", "Closes #1")
        p = self.run_check("fix(x): y", body)
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_release_and_dependabot_are_exempt(self):
        self.assertEqual(self.run_check("chore(release): 1.0.0", "BREAKING CHANGE: a\n\nnotes").returncode, 0)
        self.assertEqual(self.run_check("build(deps)!: bump", "BREAKING CHANGE: upstream\n\nx", "dependabot[bot]").returncode, 0)

    def test_pr_title_runs_the_check_from_the_base_checkout(self):
        text = (WORKFLOWS / "pr-title.yml").read_text(encoding="utf-8")
        self.assertIn("./.github/scripts/check-pr-footers.sh", text)
        self.assertIn("PR_BODY: ${{ github.event.pull_request.body }}", text)


# `gh api` stub for the merge step: the PR from $STUB_PR, the filter from the checkout (what main's copy serves),
# and the PUT's --input file copied to $STUB_OUT/merge.json.
GH_STUB = """#!/bin/sh
[ "$1" = api ] || exit 2
shift
put=""; input=""; path=""
while [ $# -gt 0 ]; do
  case "$1" in
    -X) put="$2"; shift 2 ;;
    -H) shift 2 ;;
    --input) input="$2"; shift 2 ;;
    *) path="$1"; shift ;;
  esac
done
case "$path" in
  */pulls/*/merge) [ "$put" = PUT ] && cp "$input" "$STUB_OUT/merge.json" && echo '{"merged":true}' ;;
  */contents/.github/scripts/squash-footers.jq\\?ref=*) cat "$STUB_FILTER" ;;
  */pulls/*) cat "$STUB_PR" ;;
  *) echo "unexpected: $path" >&2; exit 1 ;;
esac
"""


@unittest.skipUnless(shutil.which("jq"), "jq not installed")
class AutoMergeSquashMessage(unittest.TestCase):
    STEP = None

    @classmethod
    def setUpClass(cls):
        text = (WORKFLOWS / "auto-merge.yml").read_text(encoding="utf-8")
        step = text[text.index("      - name: Squash-merge\n"):]
        run = step[step.index("        run: |\n") + len("        run: |\n"):]
        cls.STEP = "\n".join(line[10:] for line in run.splitlines())

    def merge(self, title: str, body: str | None) -> tuple[dict, str, Path]:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        tmp = Path(td.name)
        stub = tmp / "bin"
        stub.mkdir()
        (stub / "gh").write_text(GH_STUB)
        (stub / "gh").chmod(0o755)
        (tmp / "pr.json").write_text(json.dumps({"number": 7, "title": title, "body": body}))
        (tmp / "runner").mkdir()
        env = {**os.environ, "PATH": f"{stub}{os.pathsep}{os.environ.get('PATH', '')}", "REPO": "owner/repo",
               "SHA": "a" * 40, "MAIN_SHA": "b" * 40, "PR": "7", "GH_TOKEN": "stub", "READ_TOKEN": "stub",
               "RUNNER_TEMP": str(tmp / "runner"), "STUB_PR": str(tmp / "pr.json"), "STUB_FILTER": str(FILTER),
               "STUB_OUT": str(tmp)}
        p = subprocess.run(["bash", "-c", self.STEP], env=env, cwd=tmp, capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return json.loads((tmp / "merge.json").read_text()), p.stdout, tmp

    def test_title_with_the_pr_number_and_only_the_footer_block(self):
        req, _, _ = self.merge("feat(engine): sync the store", "Closes #5\n\n## What\n\nProse.\n\nBREAKING CHANGE: run kb.py migrate")
        self.assertEqual(req, {"merge_method": "squash", "sha": "a" * 40, "commit_title": "feat(engine): sync the store (#7)",
                               "commit_message": "BREAKING CHANGE: run kb.py migrate"})

    def test_no_footer_block_sends_no_message(self):
        for body in ("Closes #5\n\nJust prose.", None):
            req, _, _ = self.merge("fix(x): y", body)
            self.assertNotIn("commit_message", req)
            self.assertEqual(req["commit_title"], "fix(x): y (#7)")

    def test_pr_text_is_data_never_shell(self):
        title = 'fix(x): $(touch pwned-title) `touch pwned-tick` "quote\\'
        body = "x\n::warning::injected\n\nBREAKING CHANGE: $(touch pwned-body)"
        req, out, tmp = self.merge(title, body)
        self.assertEqual(req["commit_title"], title + " (#7)")
        self.assertEqual(req["commit_message"], "BREAKING CHANGE: $(touch pwned-body)")
        self.assertEqual(sorted(p.name for p in tmp.glob("pwned*")), [])
        self.assertNotIn("::warning::injected", out)
        self.assertNotIn("touch", out)


if __name__ == "__main__":
    unittest.main()
