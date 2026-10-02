"""ledger-append.sh: the single writer for `.context/state/pr-review/ledger.jsonl`, shared by every
caller that uses it (`pr-scan.sh --mark-only --run <dir>`, and the main session per
`pr-review/reference/runner.md`) instead of hand-rolling its own `flock` + `jq` append — `submit-review.sh`
and `reply-threads.sh` still append their own rows inline, not through this script. Covers: a basic append,
that concurrent callers never interleave or lose a row, that one bad line on stdin writes nothing and
exits 1, that an empty stdin is a no-op (exit 0), usage errors (exit 2), and that a caller who cannot get
the lock within its timeout gets exit 3 with nothing written. No network. Stdlib unittest, no `jq`-less
hosts assumed (the repo already requires `jq` for every other shell script test). Run: make -C
.claude/context-db test."""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
LEDGER_APPEND = KIT / "skills" / "pr-review" / "scripts" / "ledger-append.sh"
PORTABLE = KIT / "skills" / "_lib" / "portable.sh"


def run(ledger: Path, stdin: str, env: dict | None = None, args: list[str] | None = None):
    full_env = {**os.environ}
    if env:
        full_env.update(env)
    return subprocess.run(["bash", str(LEDGER_APPEND), str(ledger), *(args or [])],
                          input=stdin, env=full_env, capture_output=True, text=True, timeout=30)


@unittest.skipUnless(shutil.which("jq") and shutil.which("bash"), "jq and bash needed")
class LedgerAppend(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kit-ledger-append-test."))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ledger = self.tmp / "ledger.jsonl"

    def rows(self):
        if not self.ledger.exists():
            return []
        return [json.loads(line) for line in self.ledger.read_text().splitlines() if line.strip()]

    def test_appends_one_row(self):
        row = {"repo": "junkorg/widget", "pr": 1, "head": "a" * 40, "status": "surfaced", "ts": "2026-01-01T00:00:00Z"}
        r = run(self.ledger, json.dumps(row) + "\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.rows(), [row])

    def test_appends_multiple_rows_in_one_call(self):
        rows = [{"repo": "junkorg/widget", "pr": n, "head": "b" * 40, "status": "surfaced"} for n in (1, 2, 3)]
        stdin = "".join(json.dumps(r) + "\n" for r in rows)
        r = run(self.ledger, stdin)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.rows(), rows)

    def test_empty_stdin_is_a_no_op(self):
        r = run(self.ledger, "")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.ledger.exists())

    def test_blank_only_stdin_is_a_no_op(self):
        r = run(self.ledger, "\n\n   \n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.ledger.exists())

    def test_a_bad_line_writes_nothing_and_exits_1(self):
        good = {"repo": "junkorg/widget", "pr": 1, "head": "c" * 40, "status": "surfaced"}
        stdin = json.dumps(good) + "\nnot json\n"
        r = run(self.ledger, stdin)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("line 2", r.stderr)
        self.assertFalse(self.ledger.exists())

    def test_a_json_scalar_line_is_not_a_row_and_exits_1(self):
        r = run(self.ledger, '"just a string"\n')
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertFalse(self.ledger.exists())

    def test_missing_ledger_path_is_a_usage_error(self):
        r = subprocess.run(["bash", str(LEDGER_APPEND)], input="", capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_unknown_flag_is_a_usage_error(self):
        r = run(self.ledger, "", args=["--bogus"])
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_lock_flag_with_no_value_is_a_usage_error(self):
        r = run(self.ledger, "", args=["--lock"])
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_concurrent_appends_do_not_interleave_or_lose_rows(self):
        n_writers = 20
        results: list[int] = [None] * n_writers  # type: ignore[list-item]

        def write(i: int):
            row = {"repo": "junkorg/widget", "pr": i, "head": f"{i:040x}", "status": "surfaced"}
            r = run(self.ledger, json.dumps(row) + "\n")
            results[i] = r.returncode

        threads = [threading.Thread(target=write, args=(i,)) for i in range(n_writers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(results, [0] * n_writers, results)
        rows = self.rows()
        self.assertEqual(len(rows), n_writers, rows)                       # no row lost
        self.assertEqual(sorted(r["pr"] for r in rows), list(range(n_writers)))  # no row corrupted/duplicated
        # every line parsed as exactly one JSON object — no interleaving landed two rows on one line
        lines = [ln for ln in self.ledger.read_text().splitlines() if ln.strip()]
        self.assertEqual(len(lines), n_writers, lines)

    def test_cannot_acquire_the_lock_within_the_timeout_writes_nothing(self):
        lock = self.tmp / ".ledger.lock"
        started = self.tmp / "holder.started"
        # holds the SAME with_lock primitive ledger-append.sh uses, so this exercises the real contention
        # path on whichever backend this host has (flock, or the portable mkdir-based lock dir)
        holder_script = (
            f'. "{PORTABLE}"\n'
            f'with_lock "{lock}" || exit 9\n'
            f'touch "{started}"\n'
            f'sleep 5\n'
        )
        holder = subprocess.Popen(["bash", "-c", holder_script])
        try:
            deadline = time.time() + 10
            while not started.exists() and time.time() < deadline:
                time.sleep(0.05)
            self.assertTrue(started.exists(), "lock holder never started")

            row = {"repo": "junkorg/widget", "pr": 1, "head": "d" * 40, "status": "surfaced"}
            t0 = time.time()
            r = run(self.ledger, json.dumps(row) + "\n", env={"LEDGER_APPEND_TIMEOUT": "2"}, args=["--lock", str(lock)])
            elapsed = time.time() - t0
            self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
            self.assertGreaterEqual(elapsed, 2)
            self.assertFalse(self.ledger.exists())
        finally:
            holder.wait(timeout=10)

    def test_lock_defaults_next_to_the_ledger_path(self):
        row = {"repo": "junkorg/widget", "pr": 1, "head": "e" * 40, "status": "surfaced"}
        r = run(self.ledger, json.dumps(row) + "\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # the default lock lives beside the ledger, not under a CWD-relative ".ledger.lock"
        self.assertFalse((Path.cwd() / ".ledger.lock").exists())


if __name__ == "__main__":
    unittest.main()
