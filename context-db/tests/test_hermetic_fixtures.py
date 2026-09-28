"""Regression coverage for the git/HOME isolation every fixture that spawns git needs: a developer's own
commit.gpgsign / gpg.format / system git config must never make a context-db test fixture fail, because a
fixture never git-commits with the calling process's real environment. A hostile ~/.gitconfig (commit.gpgsign
= true, gpg.format = ssh, a signing key that cannot exist) simulates exactly the failure mode reported against
main: any `git commit` that inherits it fails at once. Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import ast
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from tests.test_review_gate import Repo  # noqa: E402
from tests.test_sign_queue_home import _seed_repo  # noqa: E402
# the module, not ReviewedFreshness itself: importing that TestCase subclass by name here would make unittest's
# module scanner discover (and re-run) its own tests a second time under this module.
from tests import test_kit_verify_noenv  # noqa: E402
from tests import hermetic_env  # noqa: E402


def _hostile_home(tmp: Path) -> Path:
    """A HOME whose global git config forces commit signing through a signer that cannot exist. A fixture that
    builds its subprocess env from a bare dict(os.environ) — instead of overriding HOME itself — inherits this
    and fails its first `git commit`; one that isolates itself never notices it is there."""
    home = tmp / "hostile-home"
    home.mkdir()
    (home / ".gitconfig").write_text(
        "[commit]\n\tgpgsign = true\n"
        "[gpg]\n\tformat = ssh\n"
        "[user]\n\tname = Hostile\n\temail = hostile@example.invalid\n"
        "\tsigningkey = " + str(tmp / "no-such-signing-key") + "\n"
    )
    return home


class ReviewGateRepoIgnoresTheHostHome(unittest.TestCase):
    """test_review_gate.py's Repo() commits fixture history on every construction; it must never inherit the
    calling process's real git config."""

    def test_repo_construction_survives_a_hostile_ambient_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            hostile = _hostile_home(tmp)
            with mock.patch.dict(os.environ, {"HOME": str(hostile)}):
                r = Repo(str(tmp))  # sh() raises AssertionError if a `git commit` here fails
            self.assertTrue((r.root / ".git").is_dir())


class SignQueueSeedIgnoresTheHostHome(unittest.TestCase):
    """test_sign_queue_home.py's _seed_repo() seeds a throw-away repo with a real commit; it must never inherit
    the calling process's real git config either."""

    def test_seed_repo_survives_a_hostile_ambient_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            hostile = _hostile_home(tmp)
            root = tmp / "ws"
            root.mkdir()
            with mock.patch.dict(os.environ, {"HOME": str(hostile)}):
                wt = _seed_repo(root)  # raises CalledProcessError (check=True) if a `git commit` here fails
            self.assertTrue((wt / ".git").is_dir())


class KitVerifyNoEnvRepoIgnoresTheHostHome(unittest.TestCase):
    """test_kit_verify_noenv.py's ReviewedFreshness.repo() seeds a throw-away repo with a real commit (to give
    `reviewed:` freshness a git history to check against); it must never inherit the calling process's real git
    config either."""

    def test_repo_construction_survives_a_hostile_ambient_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            hostile = _hostile_home(tmp)
            repo_dir = tmp / "kit"
            repo_dir.mkdir()
            with mock.patch.dict(os.environ, {"HOME": str(hostile)}):
                # raises CalledProcessError if `git commit` fails
                kit = test_kit_verify_noenv.ReviewedFreshness().repo(str(repo_dir))
            self.assertTrue((kit / ".git").is_dir())


class SetupShScenariosIgnoreTheHostHome(unittest.TestCase):
    """setup_sh_scenarios.sh seeds several git-tracked fixtures by hand (a git-tracked plugin dir, a seeded
    `.claude/` for the stale-seed and install-mode scenarios); every one of them must ignore the shell's own
    ambient HOME, not only the HOME= it prefixes onto setup.sh itself."""

    def test_every_scenario_survives_a_hostile_ambient_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            hostile = _hostile_home(tmp)
            env = dict(os.environ, HOME=str(hostile))
            r = subprocess.run(["sh", str(HERE / "setup_sh_scenarios.sh")], env=env, capture_output=True,
                               text=True, timeout=600)
        self.assertEqual(r.returncode, 0, "\n" + r.stdout + "\n" + r.stderr)
        self.assertIn("setup.sh scenarios: all passed", r.stdout)
        self.assertNotIn("FAIL", r.stdout + r.stderr)


class HermeticEnvTrustParameter(unittest.TestCase):
    """#288 review: disabling the global git config to keep a fixture hermetic also drops a host's own
    `safe.directory` entries, so a read-only git call against a REAL checkout (test_stale_doc_refs.py,
    test_retire_profiles.py — never a throw-away fixture) needs that checkout trusted explicitly, or it fails
    with "detected dubious ownership" on a checkout owned by another user."""

    def test_trust_adds_a_safe_directory_entry(self):
        env = hermetic_env("/tmp/wherever", trust="/some/kit/checkout")
        self.assertEqual(env["GIT_CONFIG_COUNT"], "1")
        self.assertEqual(env["GIT_CONFIG_KEY_0"], "safe.directory")
        self.assertEqual(env["GIT_CONFIG_VALUE_0"], "/some/kit/checkout")

    def test_no_trust_means_no_safe_directory_override(self):
        env = hermetic_env("/tmp/wherever")
        self.assertNotIn("GIT_CONFIG_COUNT", env)
        self.assertNotIn("GIT_CONFIG_KEY_0", env)


_ALLOWLIST_MARKER = "hermetic-exempt:"


def _target_key(node: ast.AST) -> str | None:
    """A stable string key for an expression we track as a possible env: a bare name, or a simple `self.attr`."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    return None


def _imported_hermetic_env_name(tree: ast.Module) -> str | None:
    """The local name hermetic_env is bound to, if — and only if — this file actually imports the real one from
    the tests package (`from tests import hermetic_env`, or the relative `from . import hermetic_env`); None
    otherwise. A file that defines its own `def hermetic_env(...):` (never imported) must NOT be trusted just
    because the name matches — that shadow is exactly the look-alike this guard exists to catch (#288 review)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module == "tests" or (node.module is None and node.level >= 1)):
            for alias in node.names:
                if alias.name == "hermetic_env":
                    return alias.asname or alias.name
    return None


def _calls_hermetic_env(node: ast.AST, name: str) -> bool:
    """True when somewhere under `node` there is a call to `name` (the locally-imported hermetic_env)."""
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name
               for n in ast.walk(node))


def _trusted_funcs(tree: ast.Module) -> tuple[str, ...]:
    """The function names this file's calls may use as a hermetic env source: the imported hermetic_env() (never
    a same-named local def — see _imported_hermetic_env_name), plus a local `_env` only if ITS OWN BODY calls
    that imported hermetic_env() — trusting either name alone would let a look-alike that never actually
    isolates anything pass the guard by coincidence of naming."""
    imported = _imported_hermetic_env_name(tree)
    if imported is None:
        return ()
    trusted = [imported]
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_env" and _calls_hermetic_env(node, imported):
            trusted.append("_env")
            break
    return tuple(trusted)


def _is_hermetic_call(node: ast.AST, trusted_funcs: tuple[str, ...]) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in trusted_funcs


def _is_hermetic_expr(node: ast.AST, hermetic: set[str], trusted_funcs: tuple[str, ...]) -> bool:
    """`node` is (or is built from) a call to hermetic_env()/a file's own _env() that itself calls hermetic_env():
    the call itself, a name or `self.attr` already known hermetic, or a dict literal that `**`-spreads one of
    those as its base."""
    if _is_hermetic_call(node, trusted_funcs):
        return True
    key = _target_key(node)
    if key is not None and key in hermetic:
        return True
    if isinstance(node, ast.Dict):
        return any(k is None and _is_hermetic_expr(v, hermetic, trusted_funcs) for k, v in zip(node.keys, node.values))
    return False


def _collect_hermetic_names(tree: ast.Module, trusted_funcs: tuple[str, ...]) -> set[str]:
    """Every single-target assignment in `tree` (name or `self.attr`) that is fixed-point-reachable from a call
    to one of `trusted_funcs` — a direct assignment, or a dict merged from one two or more hops away."""
    names: set[str] = set()
    assigns = [n for n in ast.walk(tree) if isinstance(n, ast.Assign) and len(n.targets) == 1]
    for _ in range(len(assigns) + 1):  # a later assignment may build on one found only in a previous pass
        added = False
        for n in assigns:
            key = _target_key(n.targets[0])
            if key and key not in names and _is_hermetic_expr(n.value, names, trusted_funcs):
                names.add(key)
                added = True
        if not added:
            break
    return names


def _list_literal_assigns(tree: ast.Module) -> dict[str, ast.AST]:
    """name -> its list/tuple literal, to resolve a `[*name, ...]` or bare `name` argv to its first element."""
    return {n.targets[0].id: n.value for n in ast.walk(tree)
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
            and isinstance(n.value, (ast.List, ast.Tuple))}


def _argv_is_git(node: ast.AST, list_assigns: dict[str, ast.AST]) -> bool:
    """True when `node` (a subprocess call's first positional arg) is an argv starting with the literal "git" —
    directly, through a `[*name, ...]` spread, or through a bare `name` — where `name` was assigned a list/tuple
    literal starting with "git" somewhere in the file."""
    def starts_with_git(seq) -> bool:
        if not isinstance(seq, (ast.List, ast.Tuple)) or not seq.elts:
            return False
        first = seq.elts[0]
        return isinstance(first, ast.Constant) and first.value == "git"

    if starts_with_git(node):
        return True
    if (isinstance(node, (ast.List, ast.Tuple)) and node.elts and isinstance(node.elts[0], ast.Starred)
            and isinstance(node.elts[0].value, ast.Name)):
        return starts_with_git(list_assigns.get(node.elts[0].value.id))
    if isinstance(node, ast.Name):
        return starts_with_git(list_assigns.get(node.id))
    return False


def _git_subprocess_problems(path: Path) -> list[str]:
    """Every subprocess.run/check_output/Popen call in `path` whose argv starts with "git" and whose env= is not
    (traceably) hermetic_env()/_env()-built, unless its own source line(s) carry a hermetic-exempt comment."""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    lines = src.splitlines()
    trusted_funcs = _trusted_funcs(tree)
    hermetic = _collect_hermetic_names(tree, trusted_funcs)
    list_assigns = _list_literal_assigns(tree)
    problems = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        call_name = (func.attr if isinstance(func, ast.Attribute)
                     else func.id if isinstance(func, ast.Name) else None)
        if call_name not in ("run", "check_output", "Popen"):
            continue
        if not _argv_is_git(node.args[0], list_assigns):
            continue
        env_kw = next((kw for kw in node.keywords if kw.arg == "env"), None)
        if env_kw is not None and _is_hermetic_expr(env_kw.value, hermetic, trusted_funcs):
            continue
        start, end = node.lineno, getattr(node, "end_lineno", node.lineno)
        if _ALLOWLIST_MARKER in "\n".join(lines[start - 1:end]):
            continue
        problems.append(f'{path.name}:{start}: subprocess.{call_name}() spawns "git" with no hermetic env=')
    return problems


class NoUnhermeticGitCallsGuard(unittest.TestCase):
    """Static guard against reintroducing the bug this file regression-tests: every subprocess.run / check_output
    / Popen in context-db/tests/*.py whose argv starts with "git" must pass env= built from hermetic_env() —
    trusted only when the file actually imports it from the tests package (never a same-named local def), used
    directly, through a file's own `_env()` wrapper (trusted only when ITS OWN BODY calls that imported
    hermetic_env()), or a dict merged from one (`{**hermetic_base, ...}`) — never a bare dict(os.environ) copy or
    no env= at all. A call that genuinely needs the host's own git config (there are none today) can be
    allowlisted with a `# hermetic-exempt: <reason>` comment on its own source line(s)."""

    def test_every_git_subprocess_call_uses_a_hermetic_env(self):
        problems = []
        for path in sorted(HERE.glob("*.py")):
            problems.extend(_git_subprocess_problems(path))
        self.assertEqual(problems, [], "\n" + "\n".join(problems))


class GuardDoesNotTrustAnEnvWrapperByNameAlone(unittest.TestCase):
    """#288 review: a fixture that defines its own `_env()` but never actually calls hermetic_env() inside it
    must still be flagged — trusting the name `_env` alone would let a look-alike wrapper (built straight from
    dict(os.environ), the exact shape this whole file exists to catch) pass the guard by coincidence."""

    def test_a_env_wrapper_that_never_calls_hermetic_env_is_flagged(self):
        src = (
            "import subprocess\n\n"
            "def _env(home):\n"  # looks like the real thing, but never calls hermetic_env()
            "    return {'HOME': str(home)}\n\n"
            "def seed(repo):\n"
            "    subprocess.run(['git', 'init', '-q', str(repo)], check=True, env=_env(repo))\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test_scratch_env.py"
            path.write_text(src, encoding="utf-8")
            problems = _git_subprocess_problems(path)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn(f"{path.name}:", problems[0])

    def test_a_env_wrapper_that_does_call_hermetic_env_is_trusted(self):
        src = (
            "import subprocess\n"
            "from tests import hermetic_env\n\n"  # the real import every fixture uses; never executed, only parsed
            "def _env(home):\n"
            "    return hermetic_env(home)\n\n"
            "def seed(repo):\n"
            "    subprocess.run(['git', 'init', '-q', str(repo)], check=True, env=_env(repo))\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test_scratch_env.py"
            path.write_text(src, encoding="utf-8")
            problems = _git_subprocess_problems(path)
        self.assertEqual(problems, [], problems)


class GuardRequiresHermeticEnvToBeImportedFromTests(unittest.TestCase):
    """#288 review: the guard used to trust any call named `hermetic_env`, so a file that shadowed it with its
    own `def hermetic_env(...):` (never actually importing the real, isolating one) would still pass. A call
    must only be trusted when the file imports hermetic_env from the tests package."""

    def test_a_local_hermetic_env_redefinition_is_flagged(self):
        src = (
            "import subprocess\n\n"
            "def hermetic_env(tmp):\n"  # a local shadow, not `from tests import hermetic_env` — never trusted
            "    return {'HOME': str(tmp)}\n\n"
            "def seed(repo):\n"
            "    subprocess.run(['git', 'init', '-q', str(repo)], check=True, env=hermetic_env(repo))\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test_scratch_env.py"
            path.write_text(src, encoding="utf-8")
            problems = _git_subprocess_problems(path)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn(f"{path.name}:", problems[0])

    def test_the_relative_import_form_is_recognized_too(self):
        src = (
            "import subprocess\n"
            "from . import hermetic_env\n\n"
            "def seed(repo):\n"
            "    subprocess.run(['git', 'init', '-q', str(repo)], check=True, env=hermetic_env(repo))\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test_scratch_env.py"
            path.write_text(src, encoding="utf-8")
            problems = _git_subprocess_problems(path)
        self.assertEqual(problems, [], problems)


if __name__ == "__main__":
    unittest.main()
