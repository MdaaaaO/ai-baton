#!/usr/bin/env python3
"""Run the engine's unittest suite in parallel: one subprocess per test module, N at a time (#615).

Run serially, the suite outgrew CI's job budgets: 2,640 tests took about ten minutes, enough to cancel the 10-minute
python-floor leg and push every other leg running it to its own limit. Each module runs as
`python -m unittest tests.<module> -v` in a fresh interpreter, so modules share no process state. CONTEXT_ROOT is
dropped from each child's env, so the test package builds that child its own blank store (tests/__init__.py § 3)
and two modules never write one store at the same time.

A module's output prints as one block when it finishes, so blocks never interleave. The longest modules start first
(file size stands in for run time) so the last one to start is a short one. Exit 0 only when every module passed.

    python3 tests/run_parallel.py [--jobs N] [module …]   # from context-db/; `make test [JOBS=N]` calls it
"""
import argparse
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]  # context-db/, the directory `-m unittest tests.<module>` runs from
TESTS = ENGINE / "tests"
NO_TESTS_RAN = 5  # unittest's exit code (3.12+) for a module that collected no tests: not a failure


def modules(names):
    if names:
        return [n.removeprefix("tests.").removesuffix(".py") for n in names]
    found = sorted(TESTS.glob("test_*.py"), key=lambda p: p.stat().st_size, reverse=True)
    return [p.stem for p in found]


def run_one(module):
    env = {k: v for k, v in os.environ.items() if k != "CONTEXT_ROOT"}
    start = time.monotonic()
    proc = subprocess.run([sys.executable, "-m", "unittest", f"tests.{module}", "-v"], cwd=ENGINE, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    return module, proc.returncode, proc.stdout, time.monotonic() - start


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--jobs", "-j", type=int, default=os.cpu_count() or 2,
                    help="modules run at once (default: the CPU count)")
    ap.add_argument("module", nargs="*", help="test modules to run (default: every tests/test_*.py)")
    args = ap.parse_args(argv)
    todo = modules(args.module)
    jobs = max(1, min(args.jobs, len(todo)))
    lock = threading.Lock()
    ran = skipped = 0
    failed = []
    start = time.monotonic()

    def report(result):
        nonlocal ran, skipped
        module, rc, out, secs = result
        ok = rc in (0, NO_TESTS_RAN)
        with lock:
            m = re.search(r"^Ran (\d+) tests?", out, re.M)
            ran += int(m.group(1)) if m else 0
            skipped += sum(int(s) for s in re.findall(r"skipped=(\d+)", out.splitlines()[-1] if out else ""))
            if not ok:
                failed.append(module)
            print(f"== tests.{module} ({secs:.1f}s) {'ok' if ok else f'FAILED (exit {rc})'}")
            sys.stdout.write(out if out.endswith("\n") else out + "\n")
            sys.stdout.flush()

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for fut in [pool.submit(run_one, m) for m in todo]:
            report(fut.result())

    print("-" * 70)
    print(f"Ran {ran} tests in {time.monotonic() - start:.1f}s across {len(todo)} modules, {jobs} at a time")
    if failed:
        print(f"FAILED: {len(failed)} module(s): " + " ".join(f"tests.{m}" for m in sorted(failed)))
        return 1
    print(f"OK (skipped={skipped})" if skipped else "OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
