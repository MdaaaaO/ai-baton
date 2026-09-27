""".github/scripts/check-pr-issue.sh — the pr-issue check (#43): which PR bodies link an issue, which PRs are exempt,
and that code spans and HTML comments never link. Driven through the environment exactly as the workflow calls it,
with a stub `gh` first on PATH (no network, no token) and a `perl` that fails, so the parser provably needs none.
Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
SCRIPT = KIT / ".github" / "scripts" / "check-pr-issue.sh"

# Issue 404 does not exist, 7 is a pull request, everything else is an issue.
GH_STUB = """#!/bin/sh
case "$1" in
  auth) exit 0 ;;
  api)
    case "$2" in
      */issues/404) echo '{"message":"Not Found"}'; exit 1 ;;
      */issues/7) echo pr ;;
      *) echo issue ;;
    esac ;;
  *) exit 2 ;;
esac
"""


class CheckPrIssue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        stub = Path(self.tmp.name)
        for name, body in (("gh", GH_STUB), ("perl", "#!/bin/sh\necho 'perl called' >&2\nexit 97\n")):
            (stub / name).write_text(body)
            (stub / name).chmod(0o755)
        self.env = {**os.environ, "PATH": f"{stub}{os.pathsep}{os.environ.get('PATH', '')}",
                    "GH_TOKEN": "stub", "GITHUB_REPOSITORY": "owner/repo"}
        for k in ("PR_TITLE", "PR_BODY", "PR_AUTHOR"):
            self.env.pop(k, None)

    def tearDown(self):
        self.tmp.cleanup()

    def run_check(self, body: str, title: str = "fix(x): a change", author: str = "someone"):
        env = {**self.env, "PR_TITLE": title, "PR_BODY": body, "PR_AUTHOR": author}
        return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)

    def links(self, body: str) -> list[str]:
        p = self.run_check(body)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("perl", p.stderr)
        return p.stdout.splitlines()

    def test_closing_keywords_any_case_and_colon(self):
        self.assertEqual(self.links("Closes #12"), ["closes 12"])
        self.assertEqual(self.links("fixes #3\nRESOLVED: #4\nclose #5"), ["closes 3", "closes 4", "closes 5"])
        self.assertEqual(self.links("Fixes:   #9 and Fixed #10"), ["closes 10", "closes 9"])

    def test_refs(self):
        self.assertEqual(self.links("Refs #2"), ["refs 2"])
        self.assertEqual(self.links("ref: #3\r\nCloses #4\r\n"), ["closes 4", "refs 3"])

    def test_missing_link_fails(self):
        for body in ("", "no ticket here", "Closes 12", "see #12", "Closes#12"):
            p = self.run_check(body)
            self.assertEqual(p.returncode, 1, body)
            self.assertIn("links no issue", p.stderr)

    def test_code_and_comments_do_not_link(self):
        for body in ("<!-- Closes #1 -->",
                     "<!--\nCloses #1\n-->",
                     "```\nCloses #1\n```",
                     "```sh\nRefs #1\n```",
                     "use `Closes #1` in the body",
                     "<!-- a --> `Refs #1` <!-- Closes #2 -->"):
            p = self.run_check(body)
            self.assertEqual(p.returncode, 1, f"{body!r} linked: {p.stdout!r}")

    def test_prose_around_stripped_spans_still_links(self):
        self.assertEqual(self.links("<!-- hint: Closes # -->\nCloses #8\n```\nRefs #1\n```\n`Refs #2` Refs #3"),
                         ["closes 8", "refs 3"])
        # Non-greedy: text between two comments / two fences is prose.
        self.assertEqual(self.links("<!-- a -->Closes #5<!-- b -->"), ["closes 5"])
        self.assertEqual(self.links("```\nx\n```\nRefs #6\n```\ny\n```"), ["refs 6"])

    def test_unclosed_spans_are_left_as_prose(self):
        self.assertEqual(self.links("<!-- never closed\nCloses #11"), ["closes 11"])
        self.assertEqual(self.links("```\nCloses #11"), ["closes 11"])
        # An inline span does not cross a line.
        self.assertEqual(self.links("a `b\nCloses #11 `c"), ["closes 11"])

    def test_exempt_release_and_dependabot(self):
        p = self.run_check("", title="chore(release): 1.2.3")
        self.assertEqual((p.returncode, p.stdout), (0, ""))
        self.assertIn("exempt", p.stderr)
        p = self.run_check("", title="build(deps): bump x", author="dependabot[bot]")
        self.assertEqual((p.returncode, p.stdout), (0, ""))
        self.assertIn("exempt", p.stderr)
        # Only the release prefix exempts, not a title that merely mentions it.
        self.assertEqual(self.run_check("", title="fix: chore(release): x").returncode, 1)

    def test_linked_issue_must_exist_and_not_be_a_pr(self):
        p = self.run_check("Closes #404")
        self.assertEqual(p.returncode, 1)
        self.assertIn("does not exist", p.stderr)
        p = self.run_check("Refs #7")
        self.assertEqual(p.returncode, 1)
        self.assertIn("pull request", p.stderr)

    def test_local_form_reads_title_and_body_file(self):
        body = Path(self.tmp.name) / "body.md"
        body.write_text("Refs #21\n")
        p = subprocess.run(["bash", str(SCRIPT), "feat: x", str(body)], env=self.env,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual((p.returncode, p.stdout), (0, "refs 21\n"), p.stderr)


if __name__ == "__main__":
    unittest.main()
