"""skills/repo-docs/readme-check.py — the house-style front-page checker."""
import contextlib
import importlib.util
import io
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("readme_check", KIT / "skills" / "repo-docs" / "readme-check.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

BADGE = "[![CI](https://example.invalid/ci.svg)](https://example.invalid/ci)"
GOOD = "\n".join([
    "# tool", "", "**One line on what it is.**", "",
    BADGE, BADGE, BADGE, "",
    "It turns a thing into another thing for you.", "",
    "```sh", "pip install tool", "```", "",
    "## Usage", "", "Run it.", "",
    "## License", "", "MIT.", "",
])


def results(text: str, kind: str = "readme") -> dict:
    return {name: status for status, name, _ in rc.check(rc.parse(text), kind)}


class Readme(unittest.TestCase):
    def test_a_short_front_page_passes(self):
        r = results(GOOD)
        self.assertNotIn("FAIL", r.values(), r)
        self.assertEqual(r["section: install"], "ok")  # the first code block installs it

    def test_a_wall_of_text_fails_length_pitch_and_paragraphs(self):
        wall = " ".join(["word"] * 200)
        r = results(f"# tool\n\n{wall}\n\n{wall} {wall} {wall} {wall} {wall} {wall} {wall} {wall}\n")
        for rule in ("length", "pitch", "badges", "paragraphs", "first code block", "section: license"):
            self.assertEqual(r[rule], "FAIL", rule)

    def test_code_below_the_first_screen_fails(self):
        r = results(GOOD.replace("```sh", "\n" * 40 + "```sh", 1))
        self.assertEqual(r["first code block"], "FAIL")

    def test_code_block_contents_are_not_prose(self):
        stats = rc.parse(GOOD.replace("pip install tool", "\n".join(["x " * 300] * 5)))
        self.assertLess(stats["total_words"], 100)

    def test_a_fat_table_row_fails(self):
        r = results(GOOD + "\n| a | " + "b" * 450 + " |\n")
        self.assertEqual(r["table rows"], "FAIL")

    def test_list_lines_do_not_count_as_pitch(self):
        stats = rc.parse(GOOD.replace("It turns", "- " + "item " * 100 + "\n\nIt turns", 1))
        self.assertLess(stats["pitch_words"], 20)

    def test_paragraph_band_is_ok_warn_fail_not_ok_twice(self):
        # a paragraph right at the warn floor is ok, one word over is warn (not "ok" again), one word
        # past the fail ceiling is FAIL — the band used to collapse the whole warn range into "ok".
        def status_for(n):
            body = GOOD.replace(
                "It turns a thing into another thing for you.", " ".join(["word"] * n), 1
            )
            return results(body)["paragraphs"]

        self.assertEqual(status_for(90), "ok")
        self.assertEqual(status_for(91), "warn")
        self.assertEqual(status_for(150), "warn")
        self.assertEqual(status_for(151), "FAIL")

    def test_badge_from_another_host_and_unlinked_image_are_counted(self):
        # an <img> badge from a host other than shields.io, and a plain (unlinked) markdown image,
        # both used to be read as prose instead of as a badge.
        other_host_img = '<img alt="cov" src="https://badges.example.invalid/cov.svg">'
        unlinked_md_image = "![build](https://ci.example.invalid/badge.svg)"
        text = "\n".join([
            "# tool", "", "**One line on what it is.**", "",
            other_host_img, unlinked_md_image, BADGE, "",
            "It does a thing.", "",
            "```sh", "pip install tool", "```", "",
        ])
        self.assertEqual(rc.parse(text)["badges"], 3)

    def test_heading_indented_inside_a_details_block_starts_a_section(self):
        # a `## ` heading indented (as authors commonly do inside <details>) used to be invisible to
        # the section detector, which only matched a heading at column 0.
        text = "\n".join([
            "# tool", "", "Pitch.", "",
            "<details>", "<summary>Alternate install</summary>", "",
            "  ## Via conda", "", "conda install thing", "</details>", "",
            "## License", "", "MIT.", "",
        ])
        titles = [t for t, _ in rc.parse(text)["sections"]]
        self.assertIn("Via conda", titles)


class Contributing(unittest.TestCase):
    def test_short_version_in_the_intro_counts(self):
        r = results("# Contributing\n\n**The short version**\n\n- Open an issue.\n\n## Setup\n\nRun it.\n",
                    "contributing")
        self.assertEqual(r["section: short version"], "ok")
        self.assertNotIn("badges", r)  # a CONTRIBUTING has no badge rule

    def test_missing_short_version_fails(self):
        r = results("# Contributing\n\nRead the rules.\n\n## Setup\n\nRun it.\n", "contributing")
        self.assertEqual(r["section: short version"], "FAIL")

    def test_kind_follows_the_file_name(self):
        # the kit's own front pages are the live calibration: they must stay green
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(rc.main([str(KIT / "CONTRIBUTING.md")]), 0)
            self.assertEqual(rc.main([str(KIT / "README.md")]), 0)


if __name__ == "__main__":
    unittest.main()
