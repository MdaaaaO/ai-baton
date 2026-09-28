"""The test package. Importing it does two things before any test module loads an engine module.

1. It scrubs the variables a Claude Code session exports into its Bash commands, so `make test` gives the same result
   inside a plugin-install session as in CI (#20): the SessionStart hook exports CLAUDE_PROJECT_DIR, BATON and
   WORKSPACE_* (kit_profile.py session-env), and Claude Code sets CLAUDE_PLUGIN_ROOT, CLAUDE_ENV_FILE and
   CLAUDE_PLUGIN_OPTION_* for hooks. Tests that need one set it themselves.
2. It pins the suite to a throw-away env store. Without CONTEXT_ROOT the engine resolves the workspace's live
   `.context/` (from a kit worktree too), and a test that writes through the inherited root would change the owner's
   real store. So: CONTEXT_ROOT unset → a blank store (environment "ci", as CI builds it) in a fresh temp dir, removed
   at exit; CONTEXT_ROOT set → it must lie under the temp dir, must not be the store beside this kit, and must not
   name a real environment — anything else stops the run before a single test imports the engine.
   `make -C context-db test` builds the same blank store itself.
"""
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SESSION_VARS = ("CLAUDE_PROJECT_DIR", "BATON", "CLAUDE_PLUGIN_ROOT", "CLAUDE_ENV_FILE")
SESSION_PREFIXES = ("WORKSPACE_", "CLAUDE_PLUGIN_OPTION_")
TEST_ENVIRONMENT = "ci"  # the only environment name a store the suite runs on may carry
KIT = Path(__file__).resolve().parents[2]

for _k in [k for k in os.environ if k in SESSION_VARS or k.startswith(SESSION_PREFIXES)]:
    del os.environ[_k]


def store_problem(ctx: str) -> str:
    """Why the suite must not run with CONTEXT_ROOT=`ctx`, or "" when it is a throw-away store."""
    root = Path(ctx).expanduser().resolve()
    tmp = Path(tempfile.gettempdir()).resolve()
    if root == tmp or tmp not in root.parents:
        return f"it is not under the temp dir {tmp}"
    for sibling in (KIT.parent / ".context", KIT.parent.parent / ".context"):  # where the engine finds a live store
        if root == sibling.resolve():
            return "it is the workspace store beside this kit"
    cfg = root / "reference" / "env" / "config.json"
    try:
        env = json.loads(cfg.read_text(encoding="utf-8")).get("environment") if cfg.is_file() else None
    except (OSError, ValueError, AttributeError):
        env = None
    if env not in (None, "", TEST_ENVIRONMENT):
        return f"its env store names the environment {env!r} (a test store is blank, environment {TEST_ENVIRONMENT!r})"
    return ""


def blank_store() -> str:
    """A fresh blank env store (environment "ci") in its own temp dir, removed when the run exits."""
    tmp = tempfile.mkdtemp(prefix="kit-tests-")
    atexit.register(shutil.rmtree, tmp, True)
    ctx = os.path.join(tmp, ".context")
    env = dict(os.environ, CONTEXT_ROOT=ctx)
    kb = str(KIT / "context-db" / "bin" / "kb.py")
    for args in (["init", "--blank"], ["config-set", "environment", TEST_ENVIRONMENT]):
        p = subprocess.run([sys.executable, kb, *args], env=env, capture_output=True, text=True)
        if p.returncode:
            raise SystemExit(f"tests: could not create a throw-away env store ({' '.join(args)}): {p.stderr.strip()}")
    return ctx


_ctx = os.environ.get("CONTEXT_ROOT", "").strip()
if not _ctx:
    os.environ["CONTEXT_ROOT"] = blank_store()
elif store_problem(_ctx):
    raise SystemExit(f"tests: refusing to run on CONTEXT_ROOT={_ctx} — {store_problem(_ctx)}. The suite only ever runs "
                     "on a throw-away store: `make -C context-db test` builds one, or unset CONTEXT_ROOT and the suite "
                     "builds its own.")
