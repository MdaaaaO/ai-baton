"""check_links.py and kit_verify's make-target check: a relative Markdown link must resolve, a cited
`make -C .claude/context-db <target>` must exist. Stdlib unittest. Run: make -C .claude/context-db test."""
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
