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


_HERMETIC_FUNCS = ("hermetic_env", "_env")
_ALLOWLIST_MARKER = "hermetic-exempt:"


def _target_key(node: ast.AST) -> str | None:
    """A stable string key for an expression we track as a possible env: a bare name, or a simple `self.attr`."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    return None


def _is_hermetic_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _HERMETIC_FUNCS


def _is_hermetic_expr(node: ast.AST, hermetic: set[str]) -> bool:
    """`node` is (or is built from) a call to hermetic_env()/a file's own _env(): the call itself, a name or
    `self.attr` already known hermetic, or a dict literal that `**`-spreads one of those as its base."""
    if _is_hermetic_call(node):
        return True
    key = _target_key(node)
    if key is not None and key in hermetic:
        return True
    if isinstance(node, ast.Dict):
        return any(k is None and _is_hermetic_expr(v, hermetic) for k, v in zip(node.keys, node.values))
    return False


def _collect_hermetic_names(tree: ast.Module) -> set[str]:
    """Every single-target assignment in `tree` (name or `self.attr`) that is fixed-point-reachable from a call
    to hermetic_env()/_env() — a direct assignment, or a dict merged from one two or more hops away."""
    names: set[str] = set()
    assigns = [n for n in ast.walk(tree) if isinstance(n, ast.Assign) and len(n.targets) == 1]
    for _ in range(len(assigns) + 1):  # a later assignment may build on one found only in a previous pass
        added = False
        for n in assigns:
            key = _target_key(n.targets[0])
            if key and key not in names and _is_hermetic_expr(n.value, names):
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
    hermetic = _collect_hermetic_names(tree)
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
        if env_kw is not None and _is_hermetic_expr(env_kw.value, hermetic):
            continue
        start, end = node.lineno, getattr(node, "end_lineno", node.lineno)
        if _ALLOWLIST_MARKER in "\n".join(lines[start - 1:end]):
            continue
        problems.append(f'{path.name}:{start}: subprocess.{call_name}() spawns "git" with no hermetic env=')
    return problems


class NoUnhermeticGitCallsGuard(unittest.TestCase):
    """Static guard against reintroducing the bug this file regression-tests: every subprocess.run / check_output
    / Popen in context-db/tests/*.py whose argv starts with "git" must pass env= built from hermetic_env() —
    directly, through a file's own `_env()` wrapper, or a dict merged from one (`{**hermetic_base, ...}`) — never
    a bare dict(os.environ) copy or no env= at all. A call that genuinely needs the host's own git config (there
    are none today) can be allowlisted with a `# hermetic-exempt: <reason>` comment on its own source line(s)."""

    def test_every_git_subprocess_call_uses_a_hermetic_env(self):
        problems = []
        for path in sorted(HERE.glob("*.py")):
            problems.extend(_git_subprocess_problems(path))
        self.assertEqual(problems, [], "\n" + "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
