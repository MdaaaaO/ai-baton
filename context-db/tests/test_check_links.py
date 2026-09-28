"""check_links.py and kit_verify's make-target check: a relative Markdown link must resolve, a cited
`make -C $BATON/context-db <target>` must exist. Stdlib unittest. Run: make -C $BATON/context-db test."""
from __future__ import annotations
import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
BIN = HERE.parent / "bin"
sys.path.insert(0, str(BIN))

import check_links  # noqa: E402
import kit_verify  # noqa: E402


def main(*argv: str) -> int:
    with contextlib.redirect_stdout(io.StringIO()):
        return check_links.main(list(argv))


def main_capture(*argv: str) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = check_links.main(list(argv))
    return code, buf.getvalue()


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


class Links(unittest.TestCase):
    def repo(self, tmp: str, files: dict[str, str]) -> Path:
        repo = Path(tmp)
        for rel, text in files.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(text, encoding="utf-8")
        git(repo, "init", "-q")
        git(repo, "add", "-A")
        return repo

    def test_good_links_pass_and_a_missing_target_is_reported_with_its_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp, {
                "README.md": "see [docs](docs/a.md) and [section](docs/a.md#top)\n\nline three [gone](docs/missing.md)\n",
                "docs/a.md": "[back](../README.md) and [root-relative](docs/a.md)\n",
            })
            self.assertEqual(check_links.broken_links(repo / "docs" / "a.md", repo), [])
            self.assertEqual(check_links.broken_links(repo / "README.md", repo), [(3, "docs/missing.md")])
            self.assertEqual(main("--repo", str(repo)), 1)

    def test_skips_placeholders_urls_anchors_context_and_fenced_code(self):
        text = ("[web](https://example.invalid/x) [mail](mailto:someone) [anchor](#here) [ph](<path-to-file>.md)\n"
                "[bare](url) [store](.context/INDEX.md) [store2](../.context/x.md)\n"
                "```\n[in fence](nowhere/at/all.md)\n```\n"
                "~~~\n[in tilde fence](also/nowhere.md)\n~~~\n"
                "![image](img/missing.png)\n")
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp, {"a.md": text})
            self.assertEqual(check_links.broken_links(repo / "a.md", repo), [])

    def test_templates_are_not_in_the_default_file_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp, {
                "a.md": "x\n", "b.template.md": "[seeded](elsewhere/x.md)\n",
                "environment-template/c.md": "[seeded](elsewhere/y.md)\n", "docs/templates/d.md": "[seeded](z.md)\n",
                "docs/e.md": "y\n",
            })
            files = [p.relative_to(repo).as_posix() for p in check_links.tracked_markdown(repo)]
            self.assertEqual(files, ["a.md", "docs/e.md"])
            self.assertEqual(main("--repo", str(repo)), 0)

    def test_kit_has_no_broken_links(self):
        self.assertEqual(main("--repo", str(KIT)), 0)


class SectionPointers(unittest.TestCase):
    """`<file> § <heading>` pointers: the heading (or a `**Bold**` run-in label) must exist in the target."""

    def repo(self, tmp: str, files: dict[str, str]) -> Path:
        repo = Path(tmp)
        for rel, text in files.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(text, encoding="utf-8")
        git(repo, "init", "-q")
        git(repo, "add", "-A")
        return repo

    def test_a_heading_prefix_and_a_bold_run_in_label_both_resolve(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp, {
                "a.md": "see `docs/b.md` § Setup steps and `docs/b.md` § Notes for more\n",
                "docs/b.md": "# Doc\n\n## Setup steps, in order\n\n**Notes.** more text follows\n",
            })
            self.assertEqual(check_links.broken_section_pointers(repo / "a.md", repo), [])

    def test_trailing_words_that_do_not_narrow_are_dropped_one_at_a_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp, {
                "a.md": "what `docs/b.md` § Skills asks of the text\n",
                "docs/b.md": "## Skills — the contract, on one screen\n",
            })
            self.assertEqual(check_links.broken_section_pointers(repo / "a.md", repo), [])

    def test_a_heading_the_target_does_not_have_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp, {
                "a.md": "see `docs/b.md` § Nowhere-at-all\n",
                "docs/b.md": "## Setup\n",
            })
            self.assertEqual(check_links.broken_section_pointers(repo / "a.md", repo),
                              [(1, "docs/b.md § Nowhere-at-all")])
            self.assertEqual(main("--repo", str(repo)), 1)

    def test_bare_claude_md_and_an_unresolved_same_name_file_are_not_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp, {
                "a.md": "`CLAUDE.md` § Nothing-that-exists and SKILL.md § Also-nowhere\n",
            })
            self.assertEqual(check_links.broken_section_pointers(repo / "a.md", repo), [])

    def test_kit_has_no_dangling_section_pointers(self):
        self.assertEqual(main("--repo", str(KIT)), 0)


class ClaudePrefixedMakeCalls(unittest.TestCase):
    """`docs/` must not tell a contributor to run `make -C .claude/context-db …`: only a clone install has a
    kit at `.claude/`. `docs/new-environment.md` is exempt — its install prompt runs before `$BATON` exists."""

    def repo(self, tmp: str, files: dict[str, str]) -> Path:
        repo = Path(tmp)
        for rel, text in files.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(text, encoding="utf-8")
        git(repo, "init", "-q")
        git(repo, "add", "-A")
        return repo

    def test_a_docs_file_citing_the_clone_prefix_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp, {"docs/x.md": "run `make -C .claude/context-db ci`\n"})
            self.assertEqual(check_links.claude_prefixed_make_calls(repo / "docs" / "x.md"), [1])
            code, out = main_capture("--repo", str(repo))
            self.assertEqual(code, 1)
            self.assertIn("docs/x.md:1:", out)

    def test_new_environment_and_files_outside_docs_are_exempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp, {
                "docs/new-environment.md": "run `make -C .claude/context-db ci`\n",
                "CLAUDE.md": "run `make -C .claude/context-db ci`\n",
            })
            self.assertEqual(main("--repo", str(repo)), 0)

    def test_kit_docs_say_baton_not_a_clone_path(self):
        self.assertEqual(main("--repo", str(KIT)), 0)


class ListingFailures(unittest.TestCase):
    """A listing that failed, or found nothing, is never a green run over zero files."""

    def test_outside_a_git_checkout_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.md").write_text("[x](missing.md)\n", encoding="utf-8")
            p = subprocess.run([sys.executable, str(BIN / "check_links.py"), "--repo", tmp], capture_output=True, text=True,
                               env={"PATH": "/usr/bin:/bin", "GIT_CEILING_DIRECTORIES": str(Path(tmp).parent)})
            self.assertNotEqual(p.returncode, 0, p.stdout)
            self.assertIn("cannot list the tracked Markdown files", p.stderr)
            self.assertNotIn("Traceback", p.stderr)

    def test_a_repo_without_markdown_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True)
            p = subprocess.run([sys.executable, str(BIN / "check_links.py"), "--repo", tmp], capture_output=True, text=True)
            self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
            self.assertIn("no Markdown files to check", p.stderr)


class MakeTargets(unittest.TestCase):
    def test_cited_targets_are_extracted_with_lines(self):
        text = "run `make -C .claude/context-db ci` then\n`make -s -C .claude/context-db kit-verify`\nnot a make line\n"
        self.assertEqual(kit_verify.cited_make_targets(text), [(1, "ci"), (2, "kit-verify")])

    def test_a_real_target_passes_and_an_unknown_one_is_an_error(self):
        have = kit_verify.make_targets()
        self.assertIn("ci", have)
        self.assertIn("kit-verify", have)
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "good.md"
            good.write_text("`make -C .claude/context-db ci`\n", encoding="utf-8")
            bad = Path(tmp) / "bad.md"
            bad.write_text("\n`make -C .claude/context-db no-such-target-ever`\n", encoding="utf-8")
            errors: list[str] = []
            kit_verify.check_make_targets(errors, files=[good])
            self.assertEqual(errors, [])
            kit_verify.check_make_targets(errors, files=[bad])
            self.assertEqual(len(errors), 1)
            self.assertIn("bad.md:2: cites `make -C $BATON/context-db no-such-target-ever`", errors[0])

    def test_kit_docs_cite_only_real_targets(self):
        errors: list[str] = []
        kit_verify.check_make_targets(errors)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
