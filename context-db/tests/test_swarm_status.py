"""swarm-status's script: the digest from a fixture ledger (six sections, the operator's four lines), when one is due, and the
record the swarm panel reads. A brand-new script, so there is no main copy to run these against.
Stdlib unittest. Run: make -C .claude/context-db test T=test_swarm_status."""
from __future__ import annotations
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "bin"))
from tests import hermetic_env  # noqa: E402

KIT = HERE.parents[1]
DIGEST = KIT / "skills" / "swarm-status" / "scripts" / "digest.py"
SWARM = KIT / "context-db" / "bin" / "swarm.py"
BRIEF = KIT / "skills" / "pr-open" / "operator_brief.py"
KEY = "epic-1"
NOW = "2026-11-05T12:00:00Z"
SECTIONS = ("Shipped", "Blocked", "Decisions open", "Spend", "Stalls", "Next")


class Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.env = hermetic_env(Path(self._tmp.name).resolve())
        self.root = Path(self.env["CONTEXT_ROOT"])
        self.root.mkdir()

    def run_py(self, script: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True, env=self.env,
                              input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL)

    def ok(self, script: Path, *args: str, stdin: str | None = None) -> str:
        p = self.run_py(script, *args, stdin=stdin)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout

    def ledger_file(self) -> Path:
        return self.root / "state" / "swarm" / f"{KEY}.json"

    def patch(self, ticket: str, **fields) -> None:
        led = json.loads(self.ledger_file().read_text(encoding="utf-8"))
        led["tickets"][ticket].update(fields)
        self.ledger_file().write_text(json.dumps(led), encoding="utf-8")

    def fixture(self) -> None:
        """Ticket 1 merged, 2 blocked on a FAIL, 3 claimed and stalled, 4 ready; one open decision; the plan approved."""
        self.ok(SWARM, "init", KEY, "--repo", "owner/repo", "--session", "orch-1", "--epic-usd", "10")
        for t in "1234":
            self.ok(SWARM, "add", KEY, t)
        self.ok(SWARM, "approve", KEY, "--decision", "go on the plan")
        self.ok(SWARM, "decision", KEY, "--question", "keep the old flag name?", "--for-ticket", "4")
        self.ok(SWARM, "assign", KEY, "3", "--kind", "subagent", "--name", "lane-3", "--branch", "feat/3-x")
        self.patch("1", status="done", pr=101, title="the first piece", spent=1.5)
        self.patch("2", status="blocked", blocked={"reason": "FAIL on the merged commit", "who": "owner"}, spent=2.0)
        self.patch("3", status="claimed", last_progress="2026-11-05T08:00:00Z", spent=0.75)

    def render(self, *extra: str) -> str:
        return self.ok(DIGEST, "render", KEY, "--now", NOW, *extra)


class Render(Case):
    def test_six_sections_in_order_then_the_operator_lines(self):
        """DW-4: the digest from a fixture ledger."""
        self.fixture()
        out = self.render()
        heads = [line.strip("*") for line in out.splitlines() if line.startswith("**")]
        self.assertEqual(heads, [*SECTIONS, "For the operator"])
        for label in ("Can now", "Do differently", "Check with", "Not covered"):
            self.assertIn(f"- {label}: ", out)

    def test_the_sections_carry_the_ledger(self):
        self.fixture()
        out = self.render()
        self.assertIn("#1 the first piece (PR #101, done)", out)
        self.assertIn("#2: FAIL on the merged commit; owner must act", out)
        self.assertIn("keep the old flag name? (#4)", out)
        self.assertIn("Budget: $4.25 of $10.00", out)
        self.assertIn("#3 lane-3: $0.75 / no cap", out)
        self.assertIn("assign #4", out)

    def test_the_first_line_is_marked_and_the_text_is_stable(self):
        self.fixture()
        a, b = self.render(), self.render()
        self.assertEqual(a, b)
        self.assertIn("<!-- swarm-digest -->", a.splitlines()[0])

    def test_the_operator_lines_pass_the_brief_check(self):
        self.fixture()
        out = self.render()
        block = "## For the operator\n" + out.split("**For the operator**\n", 1)[1]
        p = self.run_py(BRIEF, "check", "-", stdin=block + "\nsession `x`\n")
        self.assertEqual((p.returncode, p.stderr.strip()), (0, ""), p.stderr)

    def test_an_empty_epic_says_nothing_in_every_section(self):
        self.ok(SWARM, "init", KEY, "--repo", "owner/repo", "--session", "orch-1")
        out = self.render()
        self.assertEqual(out.count("- Nothing."), 4)  # shipped, blocked, decisions, stalls
        self.assertIn("Approve the plan", out)

    def test_json_has_the_six_sections_and_four_lines(self):
        self.fixture()
        d = json.loads(self.render("--json"))
        for name in SECTIONS:
            self.assertIn(name, d)
        self.assertEqual(len(d["operator"]), 4)

    def test_the_orchestrator_is_not_a_worker_in_not_covered(self):
        """One worker and the orchestrator both unread: the line says 1 worker, not 2."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("digest_under_test", DIGEST)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        rep = {"plan_approved": True, "no_assign": [], "wave": None}
        budget = {"unread": [{"who": "1"}, {"who": "orchestrator"}]}
        lines = mod.operator_lines({"key": KEY}, {"_rep": rep, "_budget": budget, "Shipped": [], "Decisions open": []})
        self.assertEqual(lines[3], "- Not covered: Spend of 1 worker with no reading.")

    def test_the_owners_asks_of_the_integrate_step_are_in_decisions_open(self):
        """Step 7 files `needs owner of <path>` and `needs the owner's merge` as decisions; render shows them as they are."""
        self.fixture()
        self.ok(SWARM, "decision", KEY, "--question", "needs owner of infra/deploy.yaml: only the author can review it",
                "--for-ticket", "3")
        self.ok(SWARM, "decision", KEY, "--question", "needs the owner's merge of PR 12: hooks/x.sh", "--for-ticket", "4")
        out = self.render()
        self.assertIn("needs owner of infra/deploy.yaml", out)
        self.assertIn("needs the owner's merge of PR 12", out)

    def test_a_verified_row_is_shipped(self):
        self.fixture()
        self.patch("4", status="verified", title="the fourth")
        self.assertIn("#4 the fourth (verified)", self.render())

    def test_no_ledger_is_exit_1(self):
        self.assertEqual(self.run_py(DIGEST, "render", "nope").returncode, 1)

    def test_render_writes_nothing(self):
        self.fixture()
        before = self.ledger_file().read_bytes()
        self.render()
        self.assertEqual(self.ledger_file().read_bytes(), before)
        self.assertFalse((self.root / "state" / "swarm" / KEY / "digest.json").exists())


class Due(Case):
    def due(self, now: str, *extra: str) -> str:
        return self.ok(DIGEST, "due", KEY, "--now", now, *extra).strip()

    def test_due_before_the_first_post_then_after_the_window(self):
        self.fixture()
        self.assertEqual(self.due(NOW), "due")
        self.ok(DIGEST, "posted", KEY, "--now", NOW, stdin="text\n")
        self.assertEqual(self.due("2026-11-05T15:59:00Z"), "not due: next 2026-11-05T16:00:00Z")
        self.assertEqual(self.due("2026-11-05T16:00:00Z"), "due")

    def test_a_wave_end_is_due_inside_the_window(self):
        self.fixture()
        self.ok(DIGEST, "posted", KEY, "--now", NOW, stdin="text\n")
        self.assertEqual(self.due("2026-11-05T12:30:00Z", "--wave-end"), "due")

    def test_digest_hours_is_read_from_the_store(self):
        self.fixture()
        d = self.root / "reference" / "env"
        d.mkdir(parents=True)
        (d / "config.json").write_text(json.dumps({"environment": "ci", "swarm": {"digest_hours": 1}}), encoding="utf-8")
        self.ok(DIGEST, "posted", KEY, "--now", NOW, stdin="text\n")
        self.assertEqual(self.due("2026-11-05T13:00:00Z"), "due")

    def test_posted_records_time_and_text_and_refuses_empty(self):
        self.fixture()
        self.ok(DIGEST, "posted", KEY, "--now", NOW, stdin="the digest\n")
        rec = json.loads((self.root / "state" / "swarm" / KEY / "digest.json").read_text(encoding="utf-8"))
        self.assertEqual(rec, {"at": NOW, "text": "the digest\n"})
        self.assertEqual(self.run_py(DIGEST, "posted", KEY, stdin="  \n").returncode, 2)


if __name__ == "__main__":
    unittest.main()
