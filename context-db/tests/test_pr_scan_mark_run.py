"""pr-scan.sh --mark-only used to read `$BASE_OUT/latest` unconditionally — a sweep started after a brief
was shown but before the main session got around to marking it would silently shift `latest` out from
under the mark call, surfacing the WRONG run's rows. `--mark-only` now requires `--run <dir>` naming the
exact run the brief came from: no `latest` fallback, a dir outside `$BASE_OUT` is refused, and a dir with
no finished `queue.json` still exits 5 (same as before). Builds run dirs and a `queue.json` by hand —
mark-only never calls `gh`, so there is no sweep to stub. No network. Run: make -C .claude/context-db
test."""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
PR_SCAN = KIT / "skills" / "pr-scan" / "pr-scan.sh"
OWNER = "acme"
REPO = f"{OWNER}/widgets"


def row(pr: int, head: str) -> dict:
    return {"repo": REPO, "pr": pr, "head": head, "src": "sweep", "kind": "new",
            "surfaced": False, "auto": {"eligible": False}}


@unittest.skipUnless(shutil.which("jq") and shutil.which("bash"), "jq and bash needed")
class MarkOnlyRequiresRun(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kit-prscan-markrun-test."))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env_dir = self.tmp / "ws" / ".context" / "reference" / "env"; env_dir.mkdir(parents=True)
        env_dir.joinpath("config.json").write_text(json.dumps(
            {"github": {"review_bot": "", "sandbox_token_prefix": ""}}))
        self.state_dir = self.tmp / "ws" / ".context" / "state" / "pr-review"; self.state_dir.mkdir(parents=True)
        self.state_dir.joinpath("config.json").write_text(json.dumps({
            "login": "tester", "owner": OWNER, "sweep_repos": [], "days": 14, "max_rows": 8,
            "enrich_cap": 40, "deep_lines": 600, "bots": [],
            "auto_approve": {"mode": "off", "bot_authors": []},
            "auto_comment": {"mode": "off", "prios": [1], "max_per_tick": 3},
        }))
        self.base_out = self.tmp / "scanout"; self.base_out.mkdir()

    def make_run(self, name: str, rows: list[dict], under_base: bool = True) -> Path:
        parent = self.base_out if under_base else self.tmp / "elsewhere"
        parent.mkdir(exist_ok=True)
        d = parent / name; d.mkdir()
        (d / "queue.json").write_text(json.dumps(rows))
        return d

    def env(self) -> dict:
        base = {k: v for k, v in os.environ.items() if k not in ("CONTEXT_ROOT", "PR_REVIEW_HOME", "PR_SCAN_OUT")}
        base.update(CONTEXT_ROOT=str(self.tmp / "ws" / ".context"), PR_SCAN_OUT=str(self.base_out))
        return base

    def mark(self, run_dir, *, quiet: bool = True) -> subprocess.CompletedProcess:
        args = ["bash", str(PR_SCAN), "--mark-only"]
        if run_dir is not None:
            args += ["--run", str(run_dir)]
        if quiet:
            args.append("--quiet")
        return subprocess.run(args, env=self.env(), capture_output=True, text=True, timeout=30)

    def ledger_rows(self) -> list[dict]:
        p = self.state_dir / "ledger.jsonl"
        if not p.exists():
            return []
        return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]

    # --- the exact named run marks, even when `latest` points at a newer one ---

    def test_marking_a_named_run_ignores_a_newer_latest(self):
        run_a = self.make_run("run.a", [row(101, "a" * 40)])
        run_b = self.make_run("run.b", [row(102, "b" * 40)])
        (self.base_out / "latest").symlink_to(run_b, target_is_directory=True)   # latest points at B, not A

        r = self.mark(run_a)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        prs = sorted(row_["pr"] for row_ in self.ledger_rows())
        self.assertEqual(prs, [101], "marking --run run.a must surface only run.a's rows, never run.b's "
                          "(the 'latest' symlink), even though latest points at the newer run")

    # --- a --run path that resolves outside $BASE_OUT is refused, not followed ---

    def test_a_run_dir_outside_base_out_is_refused(self):
        outside = self.make_run("run.outside", [row(103, "c" * 40)], under_base=False)

        r = self.mark(outside)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("outside", r.stderr, r.stderr)
        self.assertEqual(self.ledger_rows(), [], "a refused --run must never write the ledger")

    def test_a_symlink_under_base_out_that_points_outside_is_refused(self):
        outside = self.make_run("run.outside", [row(106, "f" * 40)], under_base=False)
        (self.base_out / "run.link").symlink_to(outside, target_is_directory=True)

        r = self.mark(self.base_out / "run.link")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(self.ledger_rows(), [])

    # --- the old refusals still hold: no dir, or a sweep that never wrote queue.json, exits 5 ---

    def test_a_missing_run_dir_or_unfinished_sweep_exits_5(self):
        unfinished = self.base_out / "run.unfinished"; unfinished.mkdir()
        for target in (self.base_out / "run.gone", unfinished):
            r = self.mark(target)
            self.assertEqual(r.returncode, 5, f"{target.name}: {r.stdout + r.stderr}")
        self.assertEqual(self.ledger_rows(), [])

    # --- --run is mandatory: omitting it is a usage error, not a 'latest' fallback ---

    def test_run_is_required_no_latest_fallback(self):
        run_a = self.make_run("run.a", [row(104, "d" * 40)])
        (self.base_out / "latest").symlink_to(run_a, target_is_directory=True)

        r = self.mark(None)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.ledger_rows(), [], "omitting --run must never silently fall back to 'latest'")

    # --- a second --mark-only --run on the same run stays a safe no-op ---

    def test_a_second_mark_on_the_same_run_is_a_noop(self):
        run_a = self.make_run("run.a", [row(105, "e" * 40)])

        first = self.mark(run_a)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        second = self.mark(run_a)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)

        self.assertEqual([row_["pr"] for row_ in self.ledger_rows()], [105],
                          "a second --mark-only --run on the same run must not double-append")


@unittest.skipUnless(shutil.which("jq") and shutil.which("bash") and shutil.which("flock"), "jq, bash and flock needed")
class RefusedSweepLeavesLatest(unittest.TestCase):
    """#44 review: the sweep repointed `latest` at a fresh, empty run dir BEFORE taking `.scan.lock`, so a second
    sweep that the lock then refused (exit 3) had already moved `latest` off the running sweep's dir."""
    setUp, make_run, env = MarkOnlyRequiresRun.setUp, MarkOnlyRequiresRun.make_run, MarkOnlyRequiresRun.env

    def test_a_sweep_refused_by_the_scan_lock_leaves_latest_and_the_run_dirs_alone(self):
        run_a = self.make_run("run.a", [row(101, "a" * 40)])
        (self.base_out / "latest").symlink_to(run_a, target_is_directory=True)
        holder = subprocess.Popen(["flock", str(self.state_dir / ".scan.lock"), "sleep", "30"])
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        for _ in range(50):   # wait until the holder has the lock
            if subprocess.run(["flock", "-n", str(self.state_dir / ".scan.lock"), "true"]).returncode != 0:
                break
            subprocess.run(["sleep", "0.1"])
        r = subprocess.run(["bash", str(PR_SCAN), "--quiet"], env=self.env(), capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertEqual(os.readlink(self.base_out / "latest"), str(run_a))
        self.assertEqual(sorted(p.name for p in self.base_out.glob("run.*")), ["run.a"])

if __name__ == "__main__":
    unittest.main()
