"""A reusable fake `gh` for a test that spawns a script which shells out to `gh` (#155's Plan item 1) — a
shim on PATH that replays a fixed reply for the first substring of the call's argv it recognises, so a test
never needs a real network call. Every existing gh-touching fixture in this suite (test_pr_watch.py,
test_pr_merge.py) hand-rolls its own bash heredoc instead; those keep working as is (rewriting a passing
fixture into this shape is a separate change) — this is for new tests only, starting with
test_wait_checks.py and test_bot_verdict.py.

Written as a small python3 script (not bash) so a reply body never has to survive shell quoting: `gh api
--jq …` output is exact bytes a caller compares against, and JSON payloads carry quotes, `%`, and newlines
that a bash heredoc would have to escape by hand."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Union

# A route's reply: either the stdout text alone (exit 0, no stderr), or an explicit (exit_code, stdout, stderr).
Reply = Union[str, tuple]


def write_gh_stub(bin_dir: Path, routes: "dict[str, Reply]", log: Path, *, default_exit: int = 9) -> Path:
    """Write `bin_dir/gh`. `routes` maps a substring of the invocation's argv (joined with spaces, matched in
    insertion order — the first hit wins) to its reply. Every call — matched or not — is appended to `log` as
    one line, so a test can assert on exactly what the script under test asked for. A call matching no route
    prints "gh stub: unhandled call: <argv>" to stderr and exits `default_exit` (never silently "succeeds"
    with empty output, which a caller could misread as "no items" instead of "this fixture is incomplete")."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    cfg = {
        "log": str(log),
        "default_exit": default_exit,
        "routes": [
            {"pattern": pattern, "code": (r[0] if isinstance(r, tuple) else 0),
             "out": (r[1] if isinstance(r, tuple) else r), "err": (r[2] if isinstance(r, tuple) else "")}
            for pattern, r in routes.items()
        ],
    }
    routes_file = bin_dir / "gh_routes.json"
    routes_file.write_text(json.dumps(cfg), encoding="utf-8")
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        f"cfg = json.loads(pathlib.Path({str(routes_file)!r}).read_text(encoding='utf-8'))\n"
        "argv = ' '.join(sys.argv[1:])\n"
        "with open(cfg['log'], 'a', encoding='utf-8') as f:\n"
        "    f.write(argv + chr(10))\n"
        "for r in cfg['routes']:\n"
        "    if r['pattern'] in argv:\n"
        "        if r['err']:\n"
        "            sys.stderr.write(r['err'] + chr(10))\n"
        "        if r['out']:\n"
        "            sys.stdout.write(r['out'])\n"
        "        sys.exit(r['code'])\n"
        "sys.stderr.write('gh stub: unhandled call: ' + argv + chr(10))\n"
        "sys.exit(cfg['default_exit'])\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    return gh
