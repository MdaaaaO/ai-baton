"""skills/ticket-open/sketch.py: the ticket Sketch's marker (docs/diagrams.md § The sketch marker) —
`stamp` writes it, `parse` reads it back, `check` runs the pickup comparison against a repo's default
branch. Covers the round trip (stamp -> parse -> same fields), that a re-stamp replaces the marker rather
than duplicating it and that its hash moves when the drawn content does, and every `check` outcome
(`SKETCH OK`, `MOVED <path>`, a `+path` whose parent still exists, `STALE SKETCH`, `MALFORMED SKETCH`,
`NO SKETCH`) against a throw-away git repo. Also locks the one-line `Sketch: SKIP` shape `ticket-open`'s
own prose promises for every non-`opus` ticket, and the exact status lines `docs/diagrams.md` § The exact
lines promises `ticket-update`, `ticket-close` and `ticket-pickup` carry once a ticket's sketch moves
through a Pivot, a Close or a Pickup. Stdlib unittest, no network. Run: make -C .claude/context-db test."""
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


FENCE = "`" * 3  # built at run time so this file's own fenced text stays unambiguous
JIRA_BODY = (
    "**Sketch**\n"
    f"{FENCE}\n+---------+      +---------+\n| ticket  | ---> | sketch  | [NEW]\n+---------+      +---------+\n{FENCE}\n"
    "| Object | Before | After | Note |\n|---|---|---|---|\n| body | Goal | + Sketch | opus only |\n"
)


class FencedMarker(unittest.TestCase):
    """The Jira shape: the marker run alone inside one bare fence (that tracker renders no HTML comment),
    after a fenced ASCII WHERE block. Both shapes stamp the same hash, and `check`/`parse` read both."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        origin = base / "origin"
        origin.mkdir()
        git(origin, "init", "-q", "-b", "main")
        (origin / "keep.py").write_text("y = 2\n", encoding="utf-8")
        git(origin, "add", "-A")
        git(origin, "-c", "user.email=t@example.test", "-c", "user.name=t", "commit", "-q", "-m", "init")
        cls.clone = base / "clone"
        subprocess.run(["git", "clone", "-q", str(origin), str(cls.clone)], check=True, capture_output=True,
                        env=hermetic_env(base))
        git(cls.clone, "remote", "set-url", "origin", f"https://example.test/{REPO_SLUG}.git")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _write(self, text: str) -> str:
        p = Path(tempfile.mkstemp(dir=self.tmp.name, suffix=".txt")[1])
        p.write_text(text, encoding="utf-8")
        return str(p)

    def _stamp(self, text: str, tracker: str | None = None, slug: str = REPO_SLUG) -> str:
        argv = ["stamp", self._write(text), "--repo-slug", slug, "--paths", "keep.py"]
        if tracker:
            argv += ["--tracker", tracker]
        rc, out = run(argv)
        self.assertEqual(rc, 0)
        return out

    def _check(self, text: str) -> tuple[int, str]:
        rc, out = run(["check", self._write(text), "--repo-dir", str(self.clone), "--base", "main"])
        return rc, out.strip()

    def test_jira_stamp_puts_the_marker_alone_in_a_fence_after_a_blank_line(self):
        lines = self._stamp(JIRA_BODY, "jira").split("\n")
        self.assertEqual(lines[-1], "")
        self.assertEqual(lines[-2], FENCE)
        self.assertTrue(lines[-3].startswith("<!-- sketch: repo=acme/widgets "), lines[-3])
        self.assertEqual(lines[-4], FENCE)
        self.assertEqual(lines[-5], "")

    def test_github_stamp_is_the_bare_marker_with_or_without_the_flag(self):
        default = self._stamp(JIRA_BODY)
        self.assertEqual(default, self._stamp(JIRA_BODY, "github"))
        self.assertTrue(default.rstrip("\n").split("\n")[-1].startswith("<!-- sketch:"))
        self.assertEqual(default.count(FENCE), JIRA_BODY.count(FENCE), "the bare shape adds no fence")

    def test_both_shapes_carry_the_same_hash(self):
        h = [json.loads(run(["parse", self._write(self._stamp(JIRA_BODY, t))])[1])["hash"] for t in ("github", "jira")]
        self.assertEqual(h[0], h[1])

    def test_jira_stamp_then_parse_round_trips_the_fields(self):
        rc, out = run(["parse", self._write(self._stamp(JIRA_BODY, "jira"))])
        self.assertEqual(rc, 0)
        parsed = json.loads(out)
        self.assertEqual((parsed["repo"], parsed["paths"], parsed["components"]), (REPO_SLUG, ["keep.py"], []))

    def test_jira_stamp_then_check_is_sketch_ok(self):
        self.assertEqual(self._check(self._stamp(JIRA_BODY, "jira")), (0, "SKETCH OK"))

    def test_check_reads_a_hand_fenced_marker_without_calling_it_stale(self):
        # the bare-stamped marker moved into a fence by hand, as a tracker editor would leave it
        bare = self._stamp(JIRA_BODY).rstrip("\n").split("\n")
        fenced = "\n".join(bare[:-1] + ["", FENCE, bare[-1], FENCE]) + "\n"
        self.assertEqual(self._check(fenced), (0, "SKETCH OK"))

    def test_editing_the_drawing_above_a_fenced_marker_is_still_stale(self):
        edited = self._stamp(JIRA_BODY, "jira").replace("[NEW]", "[CHANGED]")
        rc, out = self._check(edited)
        self.assertEqual(rc, 3)
        self.assertTrue(out.startswith("STALE SKETCH "), out)

    def test_a_closing_fence_right_above_a_bare_marker_is_part_of_the_drawing(self):
        # the ASCII WHERE block's own closing fence sits directly above a GitHub-shaped marker
        text = f"**Sketch**\n{FENCE}\n[a] ---> [b]\n{FENCE}\n"
        self.assertEqual(self._check(self._stamp(text)), (0, "SKETCH OK"))

    def test_jira_restamp_is_idempotent_and_keeps_one_fence(self):
        once = self._stamp(JIRA_BODY, "jira")
        twice = self._stamp(once, "jira")
        self.assertEqual(once, twice)
        self.assertEqual(twice.count("<!-- sketch:"), 1)

    def test_two_repos_share_one_fence_and_both_check_ok(self):
        ab = self._stamp(self._stamp(JIRA_BODY, "jira"), "jira", slug=REPO_SLUG_B)
        tail = ab.rstrip("\n").split("\n")[-4:]
        self.assertEqual([tail[0], tail[3]], [FENCE, FENCE])
        self.assertEqual(sum(ln.startswith("<!-- sketch:") for ln in tail), 2)
        self.assertEqual(self._check(ab), (0, "SKETCH OK"))
        self.assertEqual(self._check(self._stamp(ab, "jira")), (0, "SKETCH OK"))

    def test_restamping_a_fenced_ticket_for_github_unfences_it(self):
        out = self._stamp(self._stamp(JIRA_BODY, "jira"), "github")
        self.assertEqual(out.count(FENCE), JIRA_BODY.count(FENCE))
        self.assertEqual(self._check(out), (0, "SKETCH OK"))

    def _hand_fenced(self, opener: str, closer: str) -> str:
        bare = self._stamp(JIRA_BODY).rstrip("\n").split("\n")
        return "\n".join(bare[:-1] + ["", opener, bare[-1], closer]) + "\n"

    def test_every_fence_flavour_parses_and_checks_ok(self):
        tilde, longer = "~" * 3, "`" * 4
        for opener, closer in ((FENCE + "text", FENCE), (longer, longer), (tilde, tilde), (longer + "json", longer)):
            with self.subTest(opener=opener):
                text = self._hand_fenced(opener, closer)
                rc, out = run(["parse", self._write(text)])
                self.assertEqual(rc, 0)
                self.assertEqual(json.loads(out)["repo"], REPO_SLUG)
                self.assertEqual(self._check(text), (0, "SKETCH OK"))

    def test_a_shorter_closer_does_not_close_a_longer_fence(self):
        # four-backtick opener, three-backtick line below: the fence is not closed, so it is no marker fence
        text = self._hand_fenced("`" * 4, FENCE)
        rc, out = self._check(text)
        self.assertEqual(rc, 3)
        self.assertTrue(out.startswith("STALE SKETCH "), out)

    def test_a_fence_line_after_a_bare_marker_does_not_fence_it(self):
        # GitHub shape: the WHERE block's closing fence sits right above a bare marker and a comment that
        # starts with a fence is joined to it by one newline — the marker is still bare, not fenced
        bare = self._stamp(f"**Sketch**\n{FENCE}\n[a] ---> [b]\n{FENCE}\n")
        joined = bare + f"{FENCE}\nnote\n{FENCE}\n"
        self.assertEqual(self._check(joined), (0, "SKETCH OK"))

    def test_an_unknown_tracker_kind_is_bad_usage(self):
        rc, _ = run(["stamp", "-", "--repo-slug", REPO_SLUG, "--tracker", "linear"])
        self.assertEqual(rc, 2)


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


class TicketUpdateRedrawRule(unittest.TestCase):
    """docs/diagrams.md's zoom table gates `ticket-update`'s Pivot redraw on a sketch marker existing;
    the skill's own prose must name the gate, the before -> after rendering and the re-stamp call."""

    def setUp(self):
        self.body = (KIT / "skills" / "ticket-update" / "SKILL.md").read_text(encoding="utf-8")

    def test_skill_names_the_marker_gate_and_the_restamp(self):
        self.assertIn("sketch marker", self.body)
        self.assertIn("before", self.body)
        self.assertIn("after", self.body)
        self.assertIn(
            "sketch.py stamp --repo-slug <owner/repo> --paths <entry>[,<entry>…]\n"
            "  --components <name>[,<name>…]",
            self.body,
        )
        self.assertIn("docs/diagrams.md", self.body)

    def test_skill_says_restamp_uses_the_pivots_new_placements(self):
        self.assertIn("the pivot's NEW placements", self.body)

    def test_skill_says_no_marker_means_no_redraw(self):
        self.assertIn("No marker on the ticket", " ".join(self.body.split()))


class TicketCloseAsBuiltLine(unittest.TestCase):
    """docs/diagrams.md § The exact lines names `As-built: #<pr> — Sketch: matches` (or `differs (…)`) as
    the close comment's line, gated on the ticket carrying a sketch marker; `ticket-close`'s own prose
    must carry that exact shape rather than paraphrase it."""

    def setUp(self):
        self.body = (KIT / "skills" / "ticket-close" / "SKILL.md").read_text(encoding="utf-8")

    def test_skill_names_the_as_built_line_and_its_two_outcomes(self):
        self.assertIn("As-built: #<pr> — Sketch: matches", self.body)
        self.assertIn("differs (+a, −b)", self.body)
        self.assertIn("## Diagrams", self.body)
        self.assertIn("sketch.py check", self.body)

    def test_skill_gates_the_line_on_a_marker(self):
        squeezed = " ".join(self.body.split())
        self.assertIn("only when the ticket carries a sketch marker", squeezed)
        self.assertIn("No marker", squeezed)


class TicketPickupSketchCheckLines(unittest.TestCase):
    """docs/diagrams.md § The exact lines names the four `sketch.py check` outcomes a pickup report
    carries; `ticket-pickup`'s own prose must quote each one and say a `MOVED` sketch blocks the build."""

    def setUp(self):
        self.body = (KIT / "skills" / "ticket-pickup" / "SKILL.md").read_text(encoding="utf-8")

    def test_skill_names_every_check_outcome(self):
        for line in ("SKETCH OK", "MOVED <path>", "STALE SKETCH <old>", "MALFORMED SKETCH <line>", "NO SKETCH"):
            self.assertIn(line, self.body)
        self.assertIn("sketch.py check", self.body)

    def test_skill_says_moved_blocks_the_build(self):
        self.assertIn("redrawn before the build", self.body)

    def test_skill_names_the_base_flag_for_a_non_main_default_branch(self):
        squeezed = " ".join(self.body.split())
        self.assertIn("defaultBranchRef", squeezed)
        self.assertIn("--base origin/<default branch>", squeezed)

    def test_skill_says_exit_1_reports_fail_not_no_sketch(self):
        squeezed = " ".join(self.body.split())
        self.assertIn("report the `FAIL` line from stderr", squeezed)
        self.assertIn("never read the empty stdout as `NO SKETCH`", squeezed)


class TicketOpenJiraSketchShape(unittest.TestCase):
    """`ticket-open` step 4 and docs/diagrams.md must name the Jira shape the script emits."""

    def setUp(self):
        self.skill = (KIT / "skills" / "ticket-open" / "SKILL.md").read_text(encoding="utf-8")
        self.diagrams = (KIT / "docs" / "diagrams.md").read_text(encoding="utf-8")

    def test_skill_passes_the_tracker_kind_to_stamp_and_names_the_ascii_where(self):
        self.assertIn("--tracker <tracker.kind>", self.skill)
        self.assertIn("fenced ASCII", self.skill)
        self.assertNotIn("a nested list for WHERE", self.skill)

    def test_diagrams_doc_names_the_fenced_shape_and_how_to_verify_it(self):
        for needle in ("fenced ASCII", "renderedFields", "[NEW]", "DEPLOY", "--tracker jira"):
            self.assertIn(needle, self.diagrams)



class ConnectorRewrites(unittest.TestCase):
    """A tracker connector that rewrites markdown on store (Jira, #589: `|---|` → `| --- |`, `| |` → `|  |`,
    `-` → `*`, nested indent 2 → 4, whitespace-only lines between nested items, `[` → `\\[`) must leave the
    hash alone, so `check` on the stored text is `SKETCH OK` without a restamp. A real edit still moves it,
    and a fenced block (the ASCII WHERE box) is hashed verbatim."""

    POSTED = (
        "**Sketch**\n"
        "```\n"
        "+--------+     +--------+\n"
        "| intake | --> | report |\n"
        "+--------+     +--------+\n"
        "```\n"
        "\n"
        "| Unit | Change |\n"
        "|---|---|\n"
        "| `a/mod.py` | reads the [config] |\n"
        "| `keep.py` | |\n"
        "\n"
        "- WHY: one hash function\n"
        "  - not a read-back [a]\n"
    )
    STORED = (
        "**Sketch**\n"
        "```\n"
        "+--------+     +--------+\n"
        "| intake | --> | report |\n"
        "+--------+     +--------+\n"
        "```\n"
        "\n"
        "| Unit | Change |\n"
        "| --- | --- |\n"
        "| `a/mod.py` | reads the \\[config\\] |\n"
        "| `keep.py` |  |\n"
        "\n"
        "* WHY: one hash function\n"
        "  \n"
        "    * not a read-back \\[a\\]\n"
    )

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        origin = base / "origin"
        origin.mkdir()
        git(origin, "init", "-q", "-b", "main")
        (origin / "keep.py").write_text("y = 2\n", encoding="utf-8")
        git(origin, "add", "-A")
        git(origin, "-c", "user.email=t@example.test", "-c", "user.name=t", "commit", "-q", "-m", "init")
        cls.clone = base / "clone"
        subprocess.run(["git", "clone", "-q", str(origin), str(cls.clone)], check=True, capture_output=True,
                        env=hermetic_env(base))
        git(cls.clone, "remote", "set-url", "origin", f"https://example.test/{REPO_SLUG}.git")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _write(self, text: str) -> str:
        p = Path(tempfile.mkstemp(dir=self.tmp.name, suffix=".txt")[1])
        p.write_text(text, encoding="utf-8")
        return str(p)

    def _marker_run(self, stamped: str) -> str:
        """The fenced marker run `stamp --tracker jira` appended — the part the connector leaves alone."""
        idx = stamped.index("\n```\n<!-- sketch:")
        return stamped[idx:]

    @staticmethod
    def _hash(text: str) -> str:
        return sketch.content_hash(sketch.normalize_lines(text.split("\n")))

    def test_the_connectors_rewrite_hashes_the_same_as_the_posted_text(self):
        self.assertEqual(self._hash(self.POSTED), self._hash(self.STORED))

    def test_check_on_the_stored_text_is_sketch_ok_without_a_restamp(self):
        rc, stamped = run(["stamp", self._write(self.POSTED), "--repo-slug", REPO_SLUG, "--paths", "keep.py",
                           "--tracker", "jira"])
        self.assertEqual(rc, 0)
        stored = self.STORED.rstrip("\n") + self._marker_run(stamped)
        rc, out = run(["check", self._write(stored), "--repo-dir", str(self.clone), "--base", "main"])
        self.assertEqual((rc, out.strip()), (0, "SKETCH OK"), out)

    def test_a_changed_table_cell_is_still_stale(self):
        edited = self.STORED.replace("reads the \\[config\\]", "writes the \\[config\\]")
        self.assertNotEqual(self._hash(self.POSTED), self._hash(edited))

    def test_an_added_bullet_is_still_stale(self):
        self.assertNotEqual(self._hash(self.POSTED), self._hash(self.STORED + "* a third reason\n"))

    def test_a_fenced_line_is_hashed_verbatim(self):
        # inside the ASCII box `|` and `-` are drawing: the canonical table form must not touch them
        spaced = self.POSTED.replace("| intake | --> | report |", "|  intake  | --> |  report  |")
        self.assertNotEqual(self._hash(self.POSTED), self._hash(spaced))
        aligned = self.POSTED.replace("+--------+     +--------+\n| intake", "+--------+     +--------+\n\n| intake")
        self.assertNotEqual(self._hash(self.POSTED), self._hash(aligned))

    def test_a_separator_row_keeps_its_alignment_colons(self):
        self.assertEqual(sketch.canonical_line("| :--- | ---: | :-: |"), "|:---|---:|:---:|")
        self.assertEqual(sketch.canonical_line("|:--|--:|"), "|:---|---:|")

    def test_docs_name_the_canonical_form_and_the_one_time_restamp(self):
        doc = (KIT / "docs" / "diagrams.md").read_text(encoding="utf-8")
        self.assertIn("canonical markdown", doc)
        self.assertIn("reads `STALE SKETCH` once", doc)


if __name__ == "__main__":
    unittest.main()
