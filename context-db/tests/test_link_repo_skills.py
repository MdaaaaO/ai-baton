"""Repo-level skills at SessionStart: `kit_profile.link_repo_skills` links each
`<workspace>/<repo>/.claude/skills/<skill>/` (with a `SKILL.md`, depth 1) as `<workspace>/.claude/skills/<skill>`, so a
session started at the workspace root sees it before it touches a file in that repo. A taken name (a real dir or a
link elsewhere) is skipped with one line; only links into a repo's `.claude/skills/` that lost their `SKILL.md` are
pruned; a second run changes nothing and prints nothing; a real skill dir is never touched; a clone install (where
`.claude` is the kit checkout) is left alone. hooks/hooks.json's SessionStart command calls it through
`kit_profile.py link-repo-skills`. Every test works in a throw-away workspace.
Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
BIN = HERE.parent / "bin"
sys.path.insert(0, str(BIN))
import kit_profile  # noqa: E402

CMD = json.loads((KIT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]["SessionStart"][0]["hooks"][0]["command"]


def load_kit_health():
    spec = importlib.util.spec_from_file_location("kit_health_under_test", KIT / "skills" / "kit-health" / "kit-health.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def make_skill(root: Path, repo: str, name: str, body: bool = True) -> Path:
    d = root / repo / ".claude" / "skills" / name
    d.mkdir(parents=True)
    if body:
        (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
    return d


class Fixture(unittest.TestCase):
    """Two repos (`alpha`: two skills, `beta`: one), a clash (`shared` exists as a real dir in the workspace's own
    `.claude/skills/`), a stale link (`gone`, whose repo skill lost its SKILL.md) and a foreign link that must stay."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = Path(self._tmp.name).resolve() / "ws"
        (self.ws / ".context" / "reference" / "env").mkdir(parents=True)
        self.dst = self.ws / ".claude" / "skills"
        self.dst.mkdir(parents=True)
        self.crafting = make_skill(self.ws, "alpha", "crafting")
        self.shared_alpha = make_skill(self.ws, "alpha", "shared")
        self.stash = make_skill(self.ws, "beta", "stash")
        (self.dst / "shared").mkdir()  # the workspace's own skill: a real dir
        (self.dst / "shared" / "SKILL.md").write_text("own\n", encoding="utf-8")
        gone = make_skill(self.ws, "beta", "gone")
        (self.dst / "gone").symlink_to(gone)
        (gone / "SKILL.md").unlink()  # the link is now stale
        self.elsewhere = Path(self._tmp.name) / "elsewhere"
        self.elsewhere.mkdir()
        (self.dst / "foreign").symlink_to(self.elsewhere)  # points outside any repo: never pruned


class LinkRepoSkills(Fixture):
    def test_links_the_skills_and_reports_the_clash(self):
        msgs = kit_profile.link_repo_skills(self.ws)
        self.assertEqual(os.readlink(self.dst / "crafting"), str(self.crafting))
        self.assertEqual(os.readlink(self.dst / "stash"), str(self.stash))
        self.assertEqual(len(msgs), 1, msgs)
        self.assertIn("shared in alpha not linked", msgs[0])

    def test_a_real_skill_dir_is_never_touched(self):
        kit_profile.link_repo_skills(self.ws)
        own = self.dst / "shared"
        self.assertFalse(own.is_symlink())
        self.assertEqual((own / "SKILL.md").read_text(encoding="utf-8"), "own\n")

    def test_prunes_only_a_stale_repo_skill_link(self):
        kit_profile.link_repo_skills(self.ws)
        self.assertFalse((self.dst / "gone").is_symlink())  # its SKILL.md is gone
        self.assertTrue((self.dst / "foreign").is_symlink())  # not a repo skill link: kept
        self.assertTrue(self.elsewhere.is_dir())

    def test_a_link_to_a_live_repo_skill_that_was_renamed_is_kept(self):
        (self.dst / "renamed").symlink_to(self.stash)  # still has its SKILL.md
        kit_profile.link_repo_skills(self.ws)
        self.assertTrue((self.dst / "renamed").is_symlink())

    def test_second_run_changes_nothing_and_is_silent_apart_from_the_clash(self):
        kit_profile.link_repo_skills(self.ws)
        before = sorted((p.name, os.readlink(p) if p.is_symlink() else "") for p in self.dst.iterdir())
        msgs = kit_profile.link_repo_skills(self.ws)
        after = sorted((p.name, os.readlink(p) if p.is_symlink() else "") for p in self.dst.iterdir())
        self.assertEqual(before, after)
        self.assertEqual(len(msgs), 1)  # only the standing clash, never a "linked" line

    def test_no_clash_and_nothing_to_do_is_fully_silent(self):
        for p in list(self.dst.iterdir()):
            if p.is_symlink():
                p.unlink()
        (self.dst / "shared" / "SKILL.md").unlink()
        (self.dst / "shared").rmdir()
        make_skill(self.ws, "gamma", "extra")
        self.assertEqual(kit_profile.link_repo_skills(self.ws), [])
        self.assertEqual(kit_profile.link_repo_skills(self.ws), [])

    def test_a_taken_name_that_is_a_link_elsewhere_is_skipped(self):
        (self.dst / "stash").symlink_to(self.elsewhere)
        msgs = kit_profile.link_repo_skills(self.ws)
        self.assertEqual(os.readlink(self.dst / "stash"), str(self.elsewhere))
        self.assertTrue(any("stash in beta not linked" in m for m in msgs), msgs)

    def test_depth_one_only(self):
        wt = self.ws / "alpha" / ".claude" / "worktrees" / "wt1" / ".claude" / "skills" / "deep"
        wt.mkdir(parents=True)
        (wt / "SKILL.md").write_text("x\n", encoding="utf-8")
        hidden = make_skill(self.ws, ".worktrees", "hidden")
        nested = self.ws / "alpha" / "sub" / ".claude" / "skills" / "nested"
        nested.mkdir(parents=True)
        (nested / "SKILL.md").write_text("x\n", encoding="utf-8")
        kit_profile.link_repo_skills(self.ws)
        for n in ("deep", "hidden", "nested"):
            self.assertFalse((self.dst / n).is_symlink(), n)
        self.assertTrue(hidden.is_dir())

    def test_a_dir_without_skill_md_is_not_linked(self):
        make_skill(self.ws, "beta", "empty", body=False)
        kit_profile.link_repo_skills(self.ws)
        self.assertFalse((self.dst / "empty").is_symlink())

    def test_creates_the_skills_dir_only_when_there_is_something_to_link(self):
        bare = self.ws.parent / "bare"
        (bare / "repo").mkdir(parents=True)
        self.assertEqual(kit_profile.link_repo_skills(bare), [])
        self.assertFalse((bare / ".claude").exists())
        make_skill(bare, "repo", "one")
        self.assertEqual(kit_profile.link_repo_skills(bare), [])
        self.assertTrue((bare / ".claude" / "skills" / "one").is_symlink())

    def test_a_clone_install_is_left_alone(self):
        (self.ws / ".claude" / "WORKSPACE.md").write_text("rules\n", encoding="utf-8")  # `.claude` is the kit checkout
        self.assertEqual(kit_profile.link_repo_skills(self.ws), [])
        self.assertFalse((self.dst / "crafting").exists())
        self.assertTrue((self.dst / "gone").is_symlink())  # nothing pruned either
        self.assertEqual(kit_profile.unlinked_repo_skills(self.ws), [])


class UnlinkedRepoSkills(Fixture):
    def test_names_the_clash_and_the_not_yet_linked(self):
        got = {n: (repo, why) for n, repo, why in kit_profile.unlinked_repo_skills(self.ws)}
        self.assertEqual(got["shared"], ("alpha", "taken"))
        self.assertEqual(got["crafting"], ("alpha", "missing"))
        self.assertEqual(got["stash"], ("beta", "missing"))

    def test_only_the_clash_remains_after_linking(self):
        kit_profile.link_repo_skills(self.ws)
        self.assertEqual(kit_profile.unlinked_repo_skills(self.ws), [("shared", "alpha", "taken")])


class SymlinkedWorkspace(Fixture):
    """The hook links from the resolved workspace path, while a caller (kit-health) may hold the workspace as a
    symlinked spelling: both must agree that a linked skill is linked."""

    def setUp(self):
        super().setUp()
        self.alias = self.ws.parent / "alias"
        self.alias.symlink_to(self.ws)

    def test_nothing_linked_is_reported_unlinked_through_a_symlinked_root(self):
        kit_profile.link_repo_skills(self.alias)
        self.assertEqual(kit_profile.unlinked_repo_skills(self.alias), [("shared", "alpha", "taken")])
        self.assertEqual(kit_profile.link_repo_skills(self.alias), kit_profile.link_repo_skills(self.ws))

    def test_kit_health_does_not_warn_through_a_symlinked_root(self):
        (self.dst / "shared" / "SKILL.md").unlink()
        (self.dst / "shared").rmdir()
        kit_profile.link_repo_skills(self.ws)
        self.assertEqual(kit_profile.unlinked_repo_skills(self.alias), [])
        kh = load_kit_health()
        r = kh.Report()
        kh.repo_skills_wiring(r, self.alias)
        self.assertEqual(r.counts[kh.WARN], 0, "\n".join(r.lines))

    def test_a_stale_link_is_pruned_through_a_symlinked_root(self):
        kit_profile.link_repo_skills(self.alias)
        self.assertFalse((self.dst / "gone").is_symlink())


class SessionStartHook(Fixture):
    """hooks/hooks.json's first SessionStart command, run the way Claude Code runs it."""

    def run_hook(self, **env: str) -> subprocess.CompletedProcess:
        base = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_", "WORKSPACE_"))}
        return subprocess.run(["sh", "-c", CMD], capture_output=True, text=True,
                              env={**base, "CLAUDE_PLUGIN_ROOT": str(KIT), "CONTEXT_ROOT": str(self.ws / ".context"),
                                   "KIT_SCRATCH": str(self.ws.parent / "scratch"), **env})

    def test_the_hook_links_the_skills_and_prints_nothing_to_the_session(self):
        r = self.run_hook(CLAUDE_PROJECT_DIR=str(self.ws))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(os.readlink(self.dst / "crafting"), str(self.crafting))
        self.assertEqual(os.readlink(self.dst / "stash"), str(self.stash))
        self.assertNotIn("repo skills", r.stdout)  # stdout is the session's context
        log = (self.ws.parent / "scratch" / "hooks.log")
        self.assertIn("shared in alpha not linked", log.read_text(encoding="utf-8") if log.exists() else r.stderr)

    def test_a_session_started_inside_a_repo_links_into_the_workspace(self):
        r = self.run_hook(CLAUDE_PROJECT_DIR=str(self.ws / "beta"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.dst / "crafting").is_symlink())

    def test_outside_a_workspace_the_hook_links_nothing(self):
        elsewhere = Path(self._tmp.name) / "plain"
        make_skill(elsewhere, "repo", "one")
        r = self.run_hook(CLAUDE_PROJECT_DIR=str(elsewhere))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((elsewhere / ".claude").exists())


if __name__ == "__main__":
    unittest.main()
