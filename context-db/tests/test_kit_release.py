"""workspace.mk's kit_release / kit_release_dry against a temp workspace and a bare origin (#85): a clone workspace
cuts from its `.claude/` by default, a plugin-shaped workspace (no `.claude/`) from `KIT_CHECKOUT`, and without a
checkout the target stops with one line naming `KIT_CHECKOUT`. conventional-release is faked through `_CREL` (a
command-line variable beats the file's `:=`), so no network and no uv. Run: make -C .claude/context-db test."""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
MAKE = shutil.which("make")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests import hermetic_env  # noqa: E402


def _env(home: Path) -> dict:
    """hermetic_env(home) with any inherited proxy or make-runtime variable stripped too — this file's own
    concern, not hermetic_env's."""
    return {k: v for k, v in hermetic_env(home).items()
            if "proxy" not in k.lower() and not k.startswith("MAKE") and k not in ("MFLAGS", "CONTEXT_ROOT")}


@unittest.skipUnless(MAKE, "make not installed")
class KitRelease(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kit-release-test."))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.env = _env(self.tmp)
        self.origin = self.tmp / "origin.git"
        seed = self.tmp / "seed"
        self.git("init", "-q", "--bare", str(self.origin), cwd=self.tmp)
        self.git("-c", "init.defaultBranch=main", "init", "-q", str(seed), cwd=self.tmp)
        (seed / "context-db" / "bin").mkdir(parents=True)
        shutil.copy(KIT / "context-db" / "bin" / "kit_profile.py", seed / "context-db" / "bin")
        (seed / "VERSION").write_text("0.1.0\n")
        self.git("add", "-A", cwd=seed)
        self.git("commit", "-qm", "init", cwd=seed)
        self.git("push", "-q", str(self.origin), "HEAD:main", cwd=seed)
        self.git("--git-dir", str(self.origin), "symbolic-ref", "HEAD", "refs/heads/main", cwd=self.tmp)
        self.ws = self.tmp / "ws"
        self.ws.mkdir()
        # the fake release tool: prints its arguments and the directory it ran in (the throwaway worktree)
        self.crel = self.tmp / "crel.sh"
        self.crel.write_text('#!/bin/sh\necho "CREL $* in $(basename "$PWD")"\n')
        self.crel.chmod(0o755)

    def git(self, *args, cwd):
        subprocess.run(["git", *args], cwd=cwd, env=self.env, check=True, capture_output=True)

    def clone(self, dest: Path) -> Path:
        self.git("clone", "-q", str(self.origin), str(dest), cwd=self.tmp)
        return dest

    def make(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run([MAKE, "-s", "-f", str(KIT / "workspace.mk"), f"_CREL={self.crel}", *args],
                              cwd=self.ws, env=self.env, capture_output=True, text=True)

    def assert_dry_ran(self, r, checkout: Path):
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("CREL release --dry-run in kit_release-run", r.stdout)
        self.assertFalse((self.ws / ".worktrees" / "kit_release-run").exists())  # the throwaway worktree is gone
        wts = subprocess.run(["git", "-C", str(checkout), "worktree", "list"], env=self.env,
                             capture_output=True, text=True, check=True).stdout
        self.assertEqual(len(wts.strip().splitlines()), 1, wts)  # and pruned from the checkout

    def test_clone_defaults_to_dot_claude(self):
        checkout = self.clone(self.ws / ".claude")
        self.assert_dry_ran(self.make("kit_release_dry"), checkout)

    def test_plugin_workspace_uses_kit_checkout(self):
        for name in ("kit-clone", "kit clone"):  # a path with a space stays one argument
            with self.subTest(name=name):
                checkout = self.clone(self.tmp / name)
                self.assert_dry_ran(self.make("kit_release_dry", f"KIT_CHECKOUT={checkout}"), checkout)

    def test_workspace_path_with_a_space(self):
        # the throwaway worktree lives under the workspace: its path must stay one argument in every git call
        self.ws = self.tmp / "my ws"
        self.ws.mkdir()
        checkout = self.clone(self.ws / ".claude")
        self.assert_dry_ran(self.make("kit_release_dry"), checkout)

    def test_no_checkout_names_kit_checkout(self):
        for extra in ((), (f"KIT_CHECKOUT={self.tmp / 'missing'}",)):
            with self.subTest(extra=extra):
                r = self.make("kit_release_dry", *extra)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("KIT_CHECKOUT=", r.stdout)
                self.assertNotIn("CREL", r.stdout)
                self.assertFalse((self.ws / ".worktrees").exists())


@unittest.skipUnless(MAKE, "make not installed")
class KitReleaseMarketplaceRef(unittest.TestCase):
    """kit_release's post-CREL step: after a real (non-dry) release commit, bump_marketplace_ref.py runs
    in the release worktree and its change reaches origin as a second commit on the release branch — still
    inside the one open PR, since a squash-merge later collapses both into one `chore(release):` commit.
    conventional-release is faked (branch + VERSION/plugin.json bump + commit + push + a PR-URL line), and so
    is `gh` (only `pr edit --add-label` needs to exit 0 — no real GitHub call)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kit-release-mkt-test."))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.env = _env(self.tmp)
        self.origin = self.tmp / "origin.git"
        seed = self.tmp / "seed"
        self.git("init", "-q", "--bare", str(self.origin), cwd=self.tmp)
        self.git("-c", "init.defaultBranch=main", "init", "-q", str(seed), cwd=self.tmp)
        (seed / "context-db" / "bin").mkdir(parents=True)
        shutil.copy(KIT / "context-db" / "bin" / "kit_profile.py", seed / "context-db" / "bin")
        shutil.copy(KIT / "context-db" / "bin" / "bump_marketplace_ref.py", seed / "context-db" / "bin")
        (seed / "VERSION").write_text("0.1.0\n")
        (seed / ".claude-plugin").mkdir()
        (seed / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "x", "version": "0.1.0"}))
        (seed / ".claude-plugin" / "marketplace.json").write_text(json.dumps(
            {"name": "m", "owner": {"name": "o"},
             "plugins": [{"name": "x", "source": {"source": "github", "repo": "o/x", "ref": "v0.1.0"}}]}))
        self.git("add", "-A", cwd=seed)
        self.git("commit", "-qm", "init", cwd=seed)
        self.git("push", "-q", str(self.origin), "HEAD:main", cwd=seed)
        self.git("--git-dir", str(self.origin), "symbolic-ref", "HEAD", "refs/heads/main", cwd=self.tmp)
        self.ws = self.tmp / "ws"
        self.ws.mkdir()
        fakebin = self.tmp / "fakebin"
        fakebin.mkdir()
        # the fake release tool: on a real (non-dry) `release`, cuts the branch itself, bumps VERSION and
        # plugin.json the way conventional-release's version-files does, commits, pushes, and prints a PR line —
        # everything workspace.mk's own recipe expects back from the real tool
        self.crel = fakebin / "crel.sh"
        self.crel.write_text(
            "#!/bin/sh\nset -e\n"
            'if [ "$1" = "release" ] && [ "$2" != "--dry-run" ]; then\n'
            "  git checkout -q -b release/v0.2.0\n"
            '  printf "0.2.0\\n" > VERSION\n'
            "  python3 -c \"import json,pathlib; p=pathlib.Path('.claude-plugin/plugin.json'); "
            "d=json.loads(p.read_text()); d['version']='0.2.0'; p.write_text(json.dumps(d))\"\n"
            "  git add VERSION .claude-plugin/plugin.json\n"
            '  git commit -q -m "chore(release): 0.2.0"\n'
            "  git push -q origin release/v0.2.0  # no upstream, like a tool that pushes a refspec\n"
            '  echo "https://github.com/o/x/pull/1"\n'
            "fi\n"
        )
        self.crel.chmod(0o755)
        gh = fakebin / "gh"
        gh.write_text('#!/bin/sh\necho "gh $*" >&2\nexit 0\n')
        gh.chmod(0o755)
        self.env["PATH"] = f"{fakebin}:{self.env['PATH']}"

    def git(self, *args, cwd):
        subprocess.run(["git", *args], cwd=cwd, env=self.env, check=True, capture_output=True)

    def clone(self, dest: Path) -> Path:
        self.git("clone", "-q", str(self.origin), str(dest), cwd=self.tmp)
        return dest

    def test_pins_the_marketplace_ref_as_a_second_commit_on_the_release_branch(self):
        self.clone(self.ws / ".claude")
        r = subprocess.run([MAKE, "-s", "-f", str(KIT / "workspace.mk"), f"_CREL={self.crel}", "kit_release"],
                            cwd=self.ws, env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        show = subprocess.run(
            ["git", "--git-dir", str(self.origin), "log", "--format=%s", "release/v0.2.0"],
            env=self.env, capture_output=True, text=True, check=True).stdout
        self.assertIn("chore(release): pin marketplace ref", show)
        blob = subprocess.run(
            ["git", "--git-dir", str(self.origin), "show", "release/v0.2.0:.claude-plugin/marketplace.json"],
            env=self.env, capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(blob)["plugins"][0]["source"]["ref"], "v0.2.0")


class ConventionalReleaseVersionPin(unittest.TestCase):
    """The default `_CREL` (no test overriding it via the command line, as KitRelease above does) reads an exact
    version from `.github/versions.env` next to workspace.mk — one pin, not the floating range release.yml,
    pr-title.yml and this file each held before it existed. `KIT=` on the command line points the `-include` at a
    fixture directory, the same seam the file's own header documents for naming the kit."""

    @staticmethod
    def crel_for(version: str) -> str:
        # `make test` (docs/contributing.md) itself runs as a make recipe, so this process may already carry
        # MAKELEVEL/MAKEFLAGS from that outer make — strip them (as _env() above does for KitRelease) so the
        # inner make below prints nothing but the target's own output, not an inherited "Entering directory".
        env = {k: v for k, v in os.environ.items() if not k.startswith(("MAKE", "MFLAGS"))}
        # `--eval` is GNU make 4+ only (Apple ships 3.81); an included wrapper makefile works on both.
        with tempfile.TemporaryDirectory() as tmp:
            kit = Path(tmp)
            (kit / ".github").mkdir()
            (kit / ".github" / "versions.env").write_text(f"CONVENTIONAL_RELEASE_VERSION={version}\n")
            wrapper = kit / "print-crel.mk"
            wrapper.write_text(f"include {KIT / 'workspace.mk'}\nprint-crel: ; @printf '%s' \"$(_CREL)\"\n", encoding="utf-8")
            r = subprocess.run([MAKE, "-f", str(wrapper), "-s", "print-crel", f"KIT={kit}"],
                               capture_output=True, text=True, cwd=tmp, env=env, check=True)
            return r.stdout

    def test_reads_the_exact_version_no_floating_range(self):
        pinned = "0." + "9.9"  # fact-shaped, assembled at run time
        self.assertEqual(self.crel_for(pinned),
                         f"uvx -q --from conventional-release=={pinned} conventional-release")

    def test_no_trailing_blanks_in_the_value(self):
        # a comment on the `_CREL` line itself would leave blanks before it in the value (the same class of bug
        # test_ci_hygiene.py's EngineMakefile check guards for CONTEXT) — assert the printed value has none.
        value = self.crel_for("1.2.3")
        self.assertEqual(value, value.strip())


if __name__ == "__main__":
    unittest.main()
