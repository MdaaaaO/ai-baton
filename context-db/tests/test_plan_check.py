"""plan_check.py — the mechanical refusals of a drafted epic plan (skills/epic-decompose § 3 and § 4). Drives the real
CLI on child drafts in a temp dir and, for the ledger path, on a temp content root. Run: make -C context-db test T=test_plan_check.
DW-1 is the refusal set: an overlap in one wave, a missing area in a wave of two or more, a wave wider than the worker
limit, caps over the epic budget, each on its own line."""
from __future__ import annotations
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from tests import hermetic_env  # noqa: E402

PLAN = HERE.parent / "bin" / "plan_check.py"
SWARM = HERE.parent / "bin" / "swarm.py"


class PlanCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.env = hermetic_env(self.tmp)
        Path(self.env["CONTEXT_ROOT"]).mkdir()

    def child(self, n: int, area: str | None, wave: str = "1; no dependency") -> Path:
        lines = [f"# child {n}", ""]
        if area is not None:
            lines.append(f"**Area** — {area}")
        lines.append(f"**Wave** — {wave}")
        path = self.tmp / f"child-{n}.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def run_plan(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(PLAN), *map(str, args)], capture_output=True, text=True, env=self.env,
                              stdin=subprocess.DEVNULL)

    def lines(self, p: subprocess.CompletedProcess) -> list[str]:
        self.assertNotIn("Traceback", p.stderr)
        return p.stdout.splitlines()


class Refusals(PlanCase):
    def test_dw1_overlap_in_one_wave_is_refused_with_its_own_line(self):
        a = self.child(51, "`src/export/runner.py`, `tests/test_retry.py`")
        b = self.child(52, "`src/export/`, `tests/test_metrics.py`")
        p = self.run_plan("--worker-limit", 4, a, b)
        self.assertEqual((p.returncode, self.lines(p)), (3, ["OVERLAP #51 #52 src/export/runner.py"]))

    def test_dw1_overlap_ignores_the_plus_and_matches_equal_paths(self):
        a = self.child(1, "`+docs/new.md`")
        b = self.child(2, "`docs/new.md`")
        self.assertEqual(self.lines(self.run_plan("--worker-limit", 4, a, b)), ["OVERLAP #1 #2 docs/new.md"])

    def test_no_glob_and_no_prefix_without_a_slash(self):
        a = self.child(1, "`src/export`")
        b = self.child(2, "`src/export_more.py`, `src/*`")
        p = self.run_plan("--worker-limit", 4, a, b)
        self.assertEqual(p.returncode, 0, p.stdout)

    def test_dw1_a_dependency_permits_overlap_direct_or_through_others(self):
        a = self.child(1, "`src/`")
        b = self.child(2, "`src/x.py`", "2; depends on #1")
        c = self.child(3, "`src/y.py`", "3; depends on #2")
        p = self.run_plan("--worker-limit", 4, a, b, c)
        self.assertEqual((p.returncode, self.lines(p)), (0, ["OK 3 children, 3 waves"]))

    def test_dw1_overlap_across_waves_is_refused_without_a_dependency(self):
        a = self.child(1, "`src/`")
        b = self.child(2, "`src/x.py`", "2; no dependency")
        p = self.run_plan("--worker-limit", 4, a, b)
        self.assertEqual((p.returncode, self.lines(p)), (3, ["OVERLAP #1 #2 src/x.py"]))

    def test_money_prints_cents_and_no_exponent(self):
        a = self.child(1, "`a.py`")
        p = self.run_plan("--worker-limit", 4, "--money-left", 1000000, "--cap", "1=1000000.5", a)
        self.assertEqual(self.lines(p), ["OVER BUDGET $1000000.5 > $1000000"])

    def test_dw1_no_area_in_a_wave_of_two_is_refused(self):
        a = self.child(7, None)
        b = self.child(8, "`src/b.py`")
        p = self.run_plan("--worker-limit", 4, a, b)
        self.assertEqual((p.returncode, self.lines(p)), (3, ["NO AREA #7"]))

    def test_a_lone_child_with_no_area_may_run(self):
        a = self.child(7, None)
        p = self.run_plan("--worker-limit", 4, a)
        self.assertEqual((p.returncode, self.lines(p)), (0, ["OK 1 children, 1 waves"]))

    def test_dw1_a_wave_wider_than_the_worker_limit_is_refused(self):
        kids = [self.child(n, f"`src/f{n}.py`") for n in (1, 2, 3)]
        p = self.run_plan("--worker-limit", 2, *kids)
        self.assertEqual((p.returncode, self.lines(p)), (3, ["WIDE WAVE 1 3 > 2"]))

    def test_dw1_caps_over_the_money_left_are_refused(self):
        a = self.child(1, "`a.py`")
        b = self.child(2, "`b.py`")
        p = self.run_plan("--worker-limit", 4, "--money-left", 10, "--cap", "1=6", "--cap", "2=5", a, b)
        self.assertEqual((p.returncode, self.lines(p)), (3, ["OVER BUDGET $11 > $10"]))
        p = self.run_plan("--worker-limit", 4, "--money-left", 11, "--cap", "1=6", "--cap", "2=5", a, b)
        self.assertEqual(p.returncode, 0, p.stdout)

    def test_every_refusal_is_on_its_own_line(self):
        a = self.child(1, "`src/`")
        b = self.child(2, "`src/x.py`")
        c = self.child(3, None)
        p = self.run_plan("--worker-limit", 2, "--money-left", 1, "--cap", "1=2", a, b, c)
        self.assertEqual(self.lines(p), ["OVERLAP #1 #2 src/x.py", "NO AREA #3", "WIDE WAVE 1 3 > 2", "OVER BUDGET $2 > $1"])
        self.assertEqual(p.returncode, 3)


class Inputs(PlanCase):
    def test_no_worker_limit_and_no_key_is_a_usage_error(self):
        p = self.run_plan(self.child(1, "`a`"))
        self.assertEqual(p.returncode, 2)
        self.assertIn("no worker limit", p.stderr)

    def test_a_draft_without_a_wave_or_a_bad_name_is_a_read_error(self):
        bad = self.tmp / "child-3.md"
        bad.write_text("**Area** — `a`\n", encoding="utf-8")
        self.assertEqual(self.run_plan("--worker-limit", 4, bad).returncode, 2)
        odd = self.tmp / "notes.md"
        odd.write_text("**Wave** — 1\n", encoding="utf-8")
        self.assertEqual(self.run_plan("--worker-limit", 4, odd).returncode, 2)
        self.assertEqual(self.run_plan("--worker-limit", 4, self.tmp / "child-9.md").returncode, 2)

    def test_a_bad_cap_is_a_usage_error(self):
        self.assertEqual(self.run_plan("--worker-limit", 4, "--cap", "x", self.child(1, "`a`")).returncode, 2)


class FromLedger(PlanCase):
    def swarm(self, *args):
        p = subprocess.run([sys.executable, str(SWARM), *args], capture_output=True, text=True, env=self.env,
                           stdin=subprocess.DEVNULL)
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_dw1_the_key_supplies_the_worker_limit_and_the_money_left(self):
        self.swarm("init", "epic-1", "--repo", "o/r", "--session", "s", "--max-workers", "2", "--epic-usd", "20")
        self.swarm("add", "epic-1", "90", "--cap-usd", "5")  # an unmerged child outside the plan holds $5
        kids = [self.child(n, f"`src/f{n}.py`") for n in (1, 2, 3)]
        p = self.run_plan("--key", "epic-1", "--cap", "1=6", "--cap", "2=6", "--cap", "3=6", *kids)
        self.assertEqual(self.lines(p), ["WIDE WAVE 1 3 > 2", "OVER BUDGET $18 > $15"])
        self.assertEqual(p.returncode, 3)

    def test_an_unknown_ledger_is_a_read_error(self):
        p = self.run_plan("--key", "nope", self.child(1, "`a`"))
        self.assertEqual(p.returncode, 2)


if __name__ == "__main__":
    unittest.main()
