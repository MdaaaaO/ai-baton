"""The test package. Importing it does four things before any test module loads an engine module.

1. It scrubs the variables a Claude Code session exports into its Bash commands, so `make test` gives the same result
   inside a plugin-install session as in CI (#20): the SessionStart hook exports CLAUDE_PROJECT_DIR, BATON and
   WORKSPACE_* (kit_profile.py session-env), and Claude Code sets CLAUDE_PLUGIN_ROOT, CLAUDE_ENV_FILE and
   CLAUDE_PLUGIN_OPTION_* for hooks. Tests that need one set it themselves. It also drops any inherited GIT_*
   variable (GIT_CONFIG_GLOBAL, GIT_AUTHOR_NAME, …), so a developer's own git environment cannot leak into a
   fixture's subprocess env by accident.
2. It isolates every test's scratch dir from the real session running the suite: kit_profile.scratch() keys
   itself on CLAUDE_CODE_SESSION_ID under $XDG_RUNTIME_DIR (else $TMPDIR, only ever consulted when XDG_RUNTIME_DIR
   is unset), and both variables are inherited from the Claude Code session running `make ci`/`make test` — so a
   test that registers a session (e.g. test_gen_sessions.py's `stale-no-prompt` fixture, through session.py's name
   stamp) or that runs a hook would overwrite the REAL session's `session-name` file or append to its `hooks.log`.
   So: XDG_RUNTIME_DIR is unconditionally pointed at a fresh temp dir (removed at exit, the original value
   restored) — always set, so scratch()'s $TMPDIR branch can never run for anything this suite spawns, which is
   why TMPDIR itself is left alone: mutating it would also reshape tempfile.gettempdir()'s cache the moment a
   fixture re-imports this package in a subprocess (test_store_isolation.py does exactly that), silently
   narrowing every other test's notion of "the temp dir" — and any inherited CLAUDE_CODE_SESSION_ID is dropped —
   every subprocess a fixture spawns inherits os.environ, so this alone covers the whole suite. A test that needs
   a session id (e.g. test_scratch.py, test_session.py, test_heartbeat.py, test_session_stats.py) sets
   CLAUDE_CODE_SESSION_ID itself, against this same temp runtime dir — never the real one.
3. It pins the suite to a throw-away env store. Without CONTEXT_ROOT the engine resolves the workspace's live
   `.context/` (from a kit worktree too), and a test that writes through the inherited root would change the owner's
   real store. So: CONTEXT_ROOT unset → a blank store (environment "ci", as CI builds it) in a fresh temp dir, removed
   at exit; CONTEXT_ROOT set → it must lie under the temp dir, must not be the store beside this kit, and must not
   name a real environment — anything else stops the run before a single test imports the engine.
   `make -C context-db test` builds the same blank store itself.
4. It gives every test that spawns git, sh or make a ready-made hermetic subprocess env (`hermetic_env` below), so
   a host's own commit.gpgsign / gpg.format / system git config can never make a fixture fail: a fixture repo is
   built with its own throw-away HOME, no global or system git config, and fixed author/committer placeholders.
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
GIT_PREFIX = "GIT_"
TEST_ENVIRONMENT = "ci"  # the only environment name a store the suite runs on may carry
KIT = Path(__file__).resolve().parents[2]

for _k in [k for k in os.environ if k in SESSION_VARS or k.startswith(SESSION_PREFIXES) or k.startswith(GIT_PREFIX)]:
    del os.environ[_k]


def _restore_runtime_env(saved: dict) -> None:
    for _k, _v in saved.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v


# Never let a test's scratch dir land in the real session's $XDG_RUNTIME_DIR — see point 2 above. TMPDIR is
# deliberately untouched (also point 2): scratch() never reaches it once XDG_RUNTIME_DIR is always set.
atexit.register(_restore_runtime_env, {k: os.environ.get(k) for k in ("XDG_RUNTIME_DIR", "CLAUDE_CODE_SESSION_ID")})
_runtime_dir = tempfile.mkdtemp(prefix="kit-tests-runtime-")
atexit.register(shutil.rmtree, _runtime_dir, True)
os.environ["XDG_RUNTIME_DIR"] = _runtime_dir
os.environ.pop("CLAUDE_CODE_SESSION_ID", None)


def hermetic_env(tmp, trust=None) -> dict:
    """A subprocess env for a test that spawns git, sh or make against a throw-away fixture: `tmp` becomes both
    HOME and TMPDIR (no inherited .gitconfig, no real scratch dir), the global and system git config are both
    disabled so a host's commit.gpgsign / gpg.format can never reach the fixture, GIT_TERMINAL_PROMPT is off so a
    missing credential never hangs the suite, author/committer are fixed placeholders, and CONTEXT_ROOT /
    SIGN_QUEUE_DIR default under the same tmp dir (a caller that needs a specific store or queue overrides them
    afterward). Build every fixture's git/sh/make subprocess env from this, never from a bare dict(os.environ).

    `trust`: for a read-only git call against a REAL checkout (this kit's own working tree, not a throw-away
    fixture) — disabling the global config above also drops a host's own `safe.directory` entries, so a checkout
    owned by another user (common for a mounted or root-owned clone) fails with "detected dubious ownership"
    without one. Pass the checkout's path and it is trusted via GIT_CONFIG_COUNT/KEY/VALUE, which applies
    regardless of GIT_CONFIG_GLOBAL/NOSYSTEM."""
    tmp = str(tmp)
    env = dict(os.environ, HOME=tmp, TMPDIR=tmp, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
               GIT_TERMINAL_PROMPT="0", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid",
               CONTEXT_ROOT=os.path.join(tmp, "store"), SIGN_QUEUE_DIR=os.path.join(tmp, "q"))
    if trust is not None:
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="safe.directory", GIT_CONFIG_VALUE_0=str(trust))
    return env


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
