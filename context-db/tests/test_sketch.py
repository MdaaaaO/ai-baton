"""skills/ticket-open/sketch.py: the ticket Sketch's marker (docs/diagrams.md § The sketch marker) —
`stamp` writes it, `parse` reads it back, `check` runs the pickup comparison against a repo's default
branch. Covers the round trip (stamp -> parse -> same fields), that a re-stamp replaces the marker rather
than duplicating it and that its hash moves when the drawn content does, and every `check` outcome
(`SKETCH OK`, `MOVED <path>`, a `+path` whose parent still exists, `STALE SKETCH`, `MALFORMED SKETCH`,
`NO SKETCH`) against a throw-away git repo. Also locks the one-line `Sketch: SKIP` shape `ticket-open`'s
own prose promises for every non-`opus` ticket. Stdlib unittest, no network. Run: make -C .claude/context-db test."""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
sys.path.insert(0, str(KIT / "skills" / "ticket-open"))
sys.path.insert(0, str(HERE.parent))

import sketch  # noqa: E402
from tests import hermetic_env  # noqa: E402

REPO_SLUG = "acme/widgets"  # the kit's own placeholder (CONTRIBUTING.md § Placeholders only)
REPO_SLUG_B = "acme/gadgets"  # a second placeholder repo, for a ticket that touches two repos


def run(argv: list[str]) -> tuple[int, str]:
    """Runs `sketch.main`, capturing stdout. A bad-usage path (invalid `--paths`/`--components`) raises
    `SystemExit` directly rather than returning through `main`'s int — caught here so callers see one
    uniform (code, stdout) shape, same as the real CLI's exit status."""
    out = io.StringIO()
    try:
        with redirect_stdout(out):
            rc = sketch.main(["sketch.py", *argv])
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    return rc, out.getvalue()


def git(repo_dir: Path, *args: str) -> None:
    # env=hermetic_env(repo_dir): a throw-away fixture repo must never inherit the host's own git config
    subprocess.run(["git", "-C", str(repo_dir), *args], check=True, capture_output=True, text=True,
                    env=hermetic_env(repo_dir))


class MarkerRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sketch_path = Path(self.tmp.name) / "sketch.txt"
        self.sketch_path.write_text("**Sketch**\nWHERE: a talks to b\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_stamp_then_parse_gives_back_the_same_fields(self):
        rc, stamped = run(["stamp", str(self.sketch_path), "--repo-slug", REPO_SLUG,
                            "--paths", "a.py,+c/new.py", "--components", "worker"])
        self.assertEqual(rc, 0)
        self.sketch_path.write_text(stamped, encoding="utf-8")
        rc, out = run(["parse", str(self.sketch_path)])
        self.assertEqual(rc, 0)
        parsed = json.loads(out)
        self.assertEqual(parsed["repo"], REPO_SLUG)
        self.assertEqual(parsed["paths"], ["+c/new.py", "a.py"])  # sorted, deduplicated
        self.assertEqual(parsed["components"], ["worker"])
        self.assertRegex(parsed["hash"], r"^[0-9a-f]{12}$")

    def test_restamp_replaces_the_marker_rather_than_duplicating_it(self):
        rc, stamped = run(["stamp", str(self.sketch_path), "--repo-slug", REPO_SLUG, "--paths", "a.py"])
        self.assertEqual(rc, 0)
        self.sketch_path.write_text(stamped, encoding="utf-8")
        rc, restamped = run(["stamp", str(self.sketch_path), "--repo-slug", REPO_SLUG,
                              "--paths", "a.py", "--components", "worker"])
        self.assertEqual(rc, 0)
        self.assertEqual(restamped.count("<!-- sketch:"), 1, "a re-stamp must replace, not add a second marker")
        self.assertEqual(stamped, restamped.replace("components=worker", "components=-"),
                          "same content, same hash — only the requested fields differ")

    def test_stamping_different_content_gives_a_different_hash(self):
        rc, stamped = run(["stamp", str(self.sketch_path), "--repo-slug", REPO_SLUG, "--paths", "a.py"])
        self.assertEqual(rc, 0)
        first_hash = json.loads(run(["parse", str(self._write(stamped))])[1])["hash"]

        edited_path = Path(self.tmp.name) / "edited.txt"
        edited_path.write_text(
            self.sketch_path.read_text(encoding="utf-8").replace("a talks to b", "a talks to b and c now"),
            encoding="utf-8",
        )
        rc, restamped = run(["stamp", str(edited_path), "--repo-slug", REPO_SLUG, "--paths", "a.py"])
        self.assertEqual(rc, 0)
        second_hash = json.loads(run(["parse", str(self._write(restamped))])[1])["hash"]
        self.assertNotEqual(first_hash, second_hash)

    def _write(self, text: str) -> Path:
        p = Path(self.tmp.name) / f"v{len(list(Path(self.tmp.name).glob('v*')))}.txt"
        p.write_text(text, encoding="utf-8")
        return p


class CheckAgainstARepo(unittest.TestCase):
    """One throw-away repo, built once, whose default branch (`main`) fixes what `check` can find."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        bare_origin = base / "origin"
        bare_origin.mkdir()
        git(bare_origin, "init", "-q", "-b", "main")
        (bare_origin / "a").mkdir()
        (bare_origin / "a" / "mod.py").write_text("x = 1\n", encoding="utf-8")
        (bare_origin / "keep.py").write_text("y = 2\n", encoding="utf-8")
        git(bare_origin, "add", "-A")
        git(bare_origin, "-c", "user.email=t@example.test", "-c", "user.name=t", "commit", "-q", "-m", "init")
        cls.clone = base / "clone"
        subprocess.run(["git", "clone", "-q", str(bare_origin), str(cls.clone)], check=True, capture_output=True,
                        env=hermetic_env(base))
        git(cls.clone, "remote", "set-url", "origin", f"https://example.test/{REPO_SLUG}.git")
        # a second repo's clone, for the two-repo-ticket tests — same tree, a different origin slug
        cls.clone_b = base / "clone_b"
        subprocess.run(["git", "clone", "-q", str(bare_origin), str(cls.clone_b)], check=True, capture_output=True,
                        env=hermetic_env(base))
        git(cls.clone_b, "remote", "set-url", "origin", f"https://example.test/{REPO_SLUG_B}.git")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _write(self, text: str) -> Path:
        p = Path(tempfile.mkstemp(dir=self.tmp.name, suffix=".txt")[1])
        p.write_text(text, encoding="utf-8")
        return p

    def _stamped(self, body: str, paths: str, components: str = "-", repo_slug: str = REPO_SLUG) -> str:
        src = self._write(body)
        rc, stamped = run(["stamp", str(src), "--repo-slug", repo_slug, "--paths", paths, "--components", components])
        self.assertEqual(rc, 0)
        return stamped

    def _restamped(self, text: str, paths: str, components: str = "-", repo_slug: str = REPO_SLUG) -> str:
        """Stamps `text` (already carrying one or more markers) for another repo, or re-stamps the
        same repo — same CLI call `_stamped` makes, just fed existing stamped text instead of a fresh body."""
        rc, out = run(["stamp", str(self._write(text)), "--repo-slug", repo_slug, "--paths", paths,
                        "--components", components])
        self.assertEqual(rc, 0)
        return out

    def test_sketch_ok_when_every_path_is_still_on_the_default_branch(self):
        stamped = self._stamped("**Sketch**\nWHERE: keep.py talks to a/mod.py\n", "keep.py,a/mod.py")
        rc, out = run(["check", str(self._write(stamped)), "--repo-dir", str(self.clone), "--base", "main"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "SKETCH OK")

    def test_moved_when_a_path_is_gone_from_the_default_branch(self):
        stamped = self._stamped("**Sketch**\nWHERE: gone.py talks to keep.py\n", "gone.py,keep.py")
        rc, out = run(["check", str(self._write(stamped)), "--repo-dir", str(self.clone), "--base", "main"])
        self.assertEqual(rc, 3)
        self.assertEqual(out.strip(), "MOVED gone.py")

    def test_plus_path_is_ok_when_its_parent_directory_exists(self):
        stamped = self._stamped("**Sketch**\nWHERE: a new file next to a/mod.py\n", "+a/new.py")
        rc, out = run(["check", str(self._write(stamped)), "--repo-dir", str(self.clone), "--base", "main"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "SKETCH OK")

    def test_plus_path_is_moved_when_its_parent_directory_does_not_exist(self):
        stamped = self._stamped("**Sketch**\nWHERE: a file under a directory that was never there\n", "+nope/new.py")
        rc, out = run(["check", str(self._write(stamped)), "--repo-dir", str(self.clone), "--base", "main"])
        self.assertEqual(rc, 3)
        self.assertEqual(out.strip(), "MOVED +nope/new.py")

    def test_stale_when_the_sketch_text_changed_after_stamping(self):
        stamped = self._stamped("**Sketch**\nWHERE: keep.py talks to a/mod.py\n", "keep.py")
        edited = stamped.replace("talks to", "now talks to")
        rc, out = run(["check", str(self._write(edited)), "--repo-dir", str(self.clone), "--base", "main"])
        self.assertEqual(rc, 3)
        self.assertTrue(out.startswith("STALE SKETCH "), out)
        self.assertIn("→", out)

    def test_malformed_when_a_sketch_shaped_line_does_not_parse(self):
        text = "**Sketch**\nWHERE: x\n<!-- sketch: repo=acme/widgets paths=keep.py hash=deadbeefcafe -->\n"
        rc, out = run(["check", str(self._write(text)), "--repo-dir", str(self.clone), "--base", "main"])
        self.assertEqual(rc, 3)
        self.assertTrue(out.startswith("MALFORMED SKETCH "), out)

    def test_no_sketch_exits_zero_on_a_skip_only_ticket(self):
        text = "Sketch: SKIP (sonnet-sized, specified fix)\n"
        rc, out = run(["check", str(self._write(text)), "--repo-dir", str(self.clone), "--base", "main"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "NO SKETCH")

    def test_no_sketch_when_the_only_marker_is_for_a_different_repo(self):
        stamped = self._stamped("**Sketch**\nWHERE: x\n", "keep.py")
        other = stamped.replace(f"repo={REPO_SLUG}", "repo=other/repo")
        rc, out = run(["check", str(self._write(other)), "--repo-dir", str(self.clone), "--base", "main"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "NO SKETCH")

    def test_two_repo_ticket_checks_ok_from_both_clones_even_after_restamping_one(self):
        # a ticket that touches two repos: stamp for repo A, then stamp that result for repo B —
        # `check` from either clone must hash the same boundary `stamp` used, not drag in the other
        # repo's marker line (the STALE SKETCH regression this test guards against).
        stamped_a = self._stamped("**Sketch**\nWHERE: keep.py talks to a/mod.py\n", "keep.py")
        stamped_ab = self._restamped(stamped_a, "keep.py", repo_slug=REPO_SLUG_B)

        rc, out = run(["check", str(self._write(stamped_ab)), "--repo-dir", str(self.clone), "--base", "main"])
        self.assertEqual((rc, out.strip()), (0, "SKETCH OK"))
        rc, out = run(["check", str(self._write(stamped_ab)), "--repo-dir", str(self.clone_b), "--base", "main"])
        self.assertEqual((rc, out.strip()), (0, "SKETCH OK"))

        # re-stamping repo A (unchanged content) after B must not disturb either repo's check
        restamped = self._restamped(stamped_ab, "keep.py", repo_slug=REPO_SLUG)
        rc, out = run(["check", str(self._write(restamped)), "--repo-dir", str(self.clone), "--base", "main"])
        self.assertEqual((rc, out.strip()), (0, "SKETCH OK"))
        rc, out = run(["check", str(self._write(restamped)), "--repo-dir", str(self.clone_b), "--base", "main"])
        self.assertEqual((rc, out.strip()), (0, "SKETCH OK"))


class StampValidation(unittest.TestCase):
    def test_a_leading_slash_path_is_bad_usage(self):
        rc, _ = run(["stamp", "-", "--repo-slug", REPO_SLUG, "--paths", "/abs/path"])
        self.assertEqual(rc, 2)

    def test_a_malformed_component_name_is_bad_usage(self):
        rc, _ = run(["stamp", "-", "--repo-slug", REPO_SLUG, "--components", "not a name"])
        self.assertEqual(rc, 2)


class TicketOpenSketchSkipLine(unittest.TestCase):
    """docs/diagrams.md's zoom table gates the Sketch on the ticket's Sizing line: every ticket that is
    not Sizing `opus` writes the one-line `Sketch: SKIP (<reason>)` instead of drawing anything."""

    def setUp(self):
        self.body = (KIT / "skills" / "ticket-open" / "SKILL.md").read_text(encoding="utf-8")

    def test_skill_names_the_skip_line_and_the_opus_gate(self):
        self.assertIn("Sketch: SKIP", self.body)
        self.assertIn("opus", self.body)
        self.assertIn("docs/diagrams.md", self.body)

    def test_skill_does_not_restate_the_contract_it_links(self):
        # the whiteboard-defence contract's own section headers live in docs/diagrams.md, not duplicated here
        self.assertNotIn("## The sketch marker", self.body)
        self.assertNotIn("## The four questions", self.body)


if __name__ == "__main__":
    unittest.main()
