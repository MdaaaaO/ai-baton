"""Engine hygiene: no dead code or second copies in context-db/bin — no unused top-level import, one home for each
shared helper and constant (the local-time renderer in kit_profile, the session doc's headings in session.py), the
review gate's CLI and the evidence builder on one findings path, the issue-ref walk pruned before it enters `.git/`
or `node_modules/`, every scaffolded template taking `domain:` from new.sh. Stdlib unittest.
Run: make -C .claude/context-db test."""
from __future__ import annotations
import ast
import io
import os
import sys
import tempfile
import unittest
from collections import defaultdict
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
BIN = HERE.parent / "bin"
TEMPLATES = HERE.parent / "_templates"
sys.path.insert(0, str(BIN))
sys.path.insert(0, str(HERE.parent))
import kit_profile  # noqa: E402
import kit_verify  # noqa: E402
import review_gate as gate  # noqa: E402


def modules() -> dict[str, ast.Module]:
    return {p.name: ast.parse(p.read_text(encoding="utf-8")) for p in sorted(BIN.glob("*.py"))}


def docstring_free(fn: ast.FunctionDef) -> str:
    return ast.dump(ast.Module(body=[b for b in fn.body if not (isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant))],
                               type_ignores=[]))


class DeadCode(unittest.TestCase):
    def test_no_unused_top_level_import(self):
        """A module-level `import x` the module never names misleads a reader into looking for its use."""
        unused = []
        for name, tree in modules().items():
            imported = {}
            for node in tree.body:
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    for a in node.names:
                        bound = (a.asname or a.name).split(".")[0]
                        if bound != "annotations":
                            imported[bound] = node.lineno
            used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
            unused += [f"{name}:{line} {bound}" for bound, line in imported.items() if bound not in used]
        self.assertEqual(unused, [])

    def test_no_forwarding_copy_of_the_subagent_dir_resolver(self):
        """transcripts.py owns the subagents/ path; no other engine module keeps a wrapper of it."""
        owners = sorted(name for name, tree in modules().items() for n in tree.body
                        if isinstance(n, ast.FunctionDef) and n.name == "subagent_dir")
        self.assertEqual(owners, ["transcripts.py"])


class OneHome(unittest.TestCase):
    def test_one_local_time_renderer(self):
        owners = sorted(name for name, tree in modules().items() for n in tree.body
                        if isinstance(n, ast.FunctionDef) and n.name.lstrip("_") == "local_str")
        self.assertEqual(owners, ["kit_profile.py"])

    def test_local_str_renders_and_passes_through(self):
        self.assertEqual(kit_profile.local_str("2026-09-23T18:21:00Z", ZoneInfo("Etc/GMT-2")), "2026-09-23 08:21 PM +02")
        self.assertEqual(kit_profile.local_str("2026-09-23T18:21:00Z", ZoneInfo("UTC")), "2026-09-23 06:21 PM UTC")
        for raw in ("", "not a time", "2026-09-23"):
            self.assertEqual(kit_profile.local_str(raw), raw)

    def test_no_function_defined_twice_with_the_same_body(self):
        seen: dict[tuple[str, str], list[str]] = defaultdict(list)
        for name, tree in modules().items():
            for n in tree.body:
                if isinstance(n, ast.FunctionDef):
                    seen[(n.name, docstring_free(n))].append(name)
        self.assertEqual({k[0]: v for k, v in seen.items() if len(v) > 1}, {})

    def test_no_string_constant_defined_twice(self):
        """A module-level string two modules must agree on (a heading one writes and the other parses) lives in one."""
        seen: dict[tuple[str, str], list[str]] = defaultdict(list)
        for name, tree in modules().items():
            for n in tree.body:
                if (isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
                        and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)):
                    seen[(n.targets[0].id, n.value.value)].append(name)
        self.assertEqual({k[0]: v for k, v in seen.items() if len(v) > 1}, {})


class GateOnePath(unittest.TestCase):
    """The CLI's check selector and review_evidence.py both go through run(): one findings path, not two copies."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)

    def test_run_honours_the_check_selector(self):
        with mock.patch.object(gate, "changed_files", return_value={}), \
                mock.patch.object(gate, "leak_findings", return_value=["L"]), \
                mock.patch.object(gate, "bump_findings", return_value=["B"]):
            for check, want in (("all", ["L", "B"]), ("leak", ["L"]), ("bump", ["B"])):
                self.assertEqual(gate.run("b", "h", cwd=self.repo, check=check)[0], want)
                self.assertEqual(gate.run("b", "h", skip_bump=True, cwd=self.repo, check=check)[0],
                                 [x for x in want if x != "B"])

    def test_main_goes_through_run(self):
        with mock.patch.object(gate, "run", return_value=([], {})) as run, redirect_stdout(io.StringIO()):
            self.assertEqual(gate.main(["leak", "--base", "b", "--head", "h", "--repo", str(self.repo)]), 0)
        run.assert_called_once_with("b", "h", False, self.repo, "leak")


class WalkPruned(unittest.TestCase):
    def test_issue_ref_walk_never_enters_a_skipped_dir(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "CHANGELOG.md").write_text("* fix ([#40](https://github.com/o/r/issues/40))\n", encoding="utf-8")
        (root / "bin").mkdir()
        (root / "bin" / "ok.py").write_text("x = 1  # (#12)\n", encoding="utf-8")
        for skipped in sorted(kit_verify.ISSUE_REF_SKIP_DIRS):
            (root / skipped / "deep").mkdir(parents=True)
            (root / skipped / "deep" / "x.md").write_text("see #999\n", encoding="utf-8")
        scanned: list[str] = []
        real = os.scandir

        def spy(path="."):
            scanned.append(os.fspath(path))
            return real(path)

        errors: list[str] = []
        with mock.patch("os.scandir", spy):
            kit_verify.check_issue_refs(errors, root)
        self.assertEqual(errors, [])
        self.assertTrue(scanned, "the walk scanned nothing — the spy is not wired")
        entered = [p for p in scanned if set(Path(p).relative_to(root).parts) & kit_verify.ISSUE_REF_SKIP_DIRS]
        self.assertEqual(entered, [])


class Templates(unittest.TestCase):
    def test_scaffolded_templates_take_domain_from_new_sh(self):
        """new.sh fills `{{DOMAIN}}` from DOMAIN= (or the type's default); a hardcoded value reads as if it mattered and
        ignores DOMAIN=. The self-assessment charter is not scaffolded by new.sh — setup.sh seeds it verbatim."""
        wrong = []
        for p in sorted(TEMPLATES.glob("*.md")):
            if p.name == "self-assessment-charter.md":
                continue
            lines = [ln for ln in p.read_text(encoding="utf-8").split("\n") if ln.startswith("domain:")]
            if lines != ["domain: {{DOMAIN}}"]:
                wrong.append(f"{p.name}: {lines}")
        self.assertEqual(wrong, [])


if __name__ == "__main__":
    unittest.main()
