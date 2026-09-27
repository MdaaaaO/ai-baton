"""review_gate.py / review_evidence.py: the tier-0 gate and the tier-1 evidence, on a throw-away git repo — leak
shapes on added lines only (a pre-existing leak is not a finding, the allow-list holds), version/updated/CHANGELOG bumps
for changed, new and removed units, the wording-only exemption, README edits not counting, the pre-flags (swallowed
errors, vocabulary in a unit without the capability), rules read from the base. Fact-shaped literals are assembled at
run time. Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BIN = HERE.parent / "bin"
KIT = HERE.parents[1]
sys.path.insert(0, str(BIN))
import review_evidence as evidence  # noqa: E402
import review_gate as gate  # noqa: E402

LEAK = "C0" + "AB12CD3EF"  # Slack-shaped, assembled
TOKEN = "ghp_" + "A" * 24
MAIL = "someone@" + "corp-example" + ".io"
UNIT = """---
name: demo
description: A demo skill. Use when asked to demo. Not for anything else.
metadata:
  version: "{v}"
  updated: "{u}"
  reviewed: "2026-09-01"
{req}---

# Demo

1. Do the thing.
{body}
"""


def sh(cwd: Path, *args: str) -> str:
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(f"{args}: {r.stderr}")
    return r.stdout


class Repo:
    """A tiny kit-shaped repo: main with one unit and a CHANGELOG, a branch with the change under test."""

    def __init__(self, tmp: str):
        self.root = Path(tmp) / "kit"
        self.root.mkdir()
        sh(self.root, "git", "init", "-q", "-b", "main")
        sh(self.root, "git", "config", "user.email", "t@example.com")
        sh(self.root, "git", "config", "user.name", "t")
        self.write("skills/demo/SKILL.md", UNIT.format(v="1", u="2026-09-01", req="", body=""))
        self.write("skills/demo/README.md", "# demo\n")
        self.write("skills/kit-health/allow.txt", "docs/allowed.md:" + LEAK + "$\n")  # assembled: no scanner reads this file as a leak
        self.write("docs/CHANGELOG.md", "# Kit changelog\n\n- 2026-09-01 · demo v1 (#1) — born.\n")
        self.write("docs/REVIEW.md", "# Rules v1\n")
        self.write("agents/tri.md", "---\nname: tri\ndescription: x\nmetadata:\n  version: \"2\"\n  updated: \"2026-09-01\"\n  reviewed: \"2026-09-01\"\n---\nbody\n")
        self.commit("base")
        sh(self.root, "git", "checkout", "-q", "-b", "pr")

    def write(self, rel: str, text: str) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def rm(self, rel: str) -> None:
        (self.root / rel).unlink()

    def commit(self, msg: str) -> None:
        sh(self.root, "git", "add", "-A")
        sh(self.root, "git", "commit", "-q", "-m", msg, "--allow-empty")

    def gate(self, skip_bump: bool = False) -> list[str]:
        return gate.run("main", "HEAD", skip_bump=skip_bump, cwd=self.root)[0]

    def ev(self) -> dict:
        return evidence.build("main", "HEAD", cwd=self.root)


class Leaks(unittest.TestCase):
    def test_added_line_with_a_shape_is_a_stop_with_path_line_and_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Repo(tmp)
            home = "/home/" + "somebody" + "/x"  # assembled: the gate scans this file's own added lines in CI
            r.write("docs/note.md", f"# note\n\nchannel {LEAK}\nmail {MAIL}\ntoken {TOKEN}\nhome {home}\nok /home/<user>/x ok ~/x ok /home/user/x\n")
            r.commit("leaky")
            f = r.gate(skip_bump=True)
            self.assertEqual(len(f), 4, f)
            self.assertTrue(f[0].startswith("[STOP] docs/note.md:3 — Slack channel/DM id"), f[0])
            self.assertIn("e-mail address", f[1]); self.assertIn("docs/note.md:4", f[1])
            self.assertIn("GitHub token", f[2])
            self.assertIn("home path", f[3]); self.assertIn("docs/note.md:6", f[3])
            self.assertTrue(all(fx.endswith("(REVIEW.md § 2.1)") for fx in f))

    def test_pre_existing_leak_deleted_file_and_allow_list_are_not_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Repo(tmp)
            sh(r.root, "git", "checkout", "-q", "main")
            r.write("docs/old.md", f"channel {LEAK}\n")  # a leak already on main
            r.write("docs/gone.md", f"channel {LEAK}\n")
            r.commit("main has a leak")
            sh(r.root, "git", "checkout", "-q", "pr"); sh(r.root, "git", "merge", "-q", "main")
            r.write("docs/old.md", f"channel {LEAK}\nnew harmless line\n")  # touched, but the leak line is not added
            r.rm("docs/gone.md")
            r.write("docs/allowed.md", f"allowed {LEAK}\n")  # allow-listed exact match
            r.write("skills/demo/SKILL.md", UNIT.format(v="1", u="2026-09-01", req="", body=f"requires: {LEAK} is a declaration line\n"))
            r.commit("touch")
            self.assertEqual(r.gate(skip_bump=True), [])

    def test_example_mail_and_placeholder_home_are_fine(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Repo(tmp)
            r.write("docs/n.md", "you@example.com and 1234+bot@users.noreply.github.com and git@github.com:o/r.git and /home/$USER and /Users/<you>/x and /home/runner/work\n")
            r.commit("fine")
            self.assertEqual(r.gate(skip_bump=True), [])


class Bumps(unittest.TestCase):
    def test_changed_unit_needs_version_updated_and_changelog(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Repo(tmp)
            r.write("skills/demo/SKILL.md", UNIT.format(v="1", u="2026-09-01", req="", body="2. Do more.\n"))
            r.commit("no bump")
            f = r.gate()
            self.assertEqual(len(f), 2, f)
            self.assertIn("skills/demo/SKILL.md:1 — files of `demo` changed but `metadata.version` is 1 (base 1)", f[0])
            self.assertIn("docs/CHANGELOG.md:1 — `demo` changed but no added CHANGELOG line names it", f[1])
            self.assertTrue(all(fx.endswith("(docs/contributing.md § Versioning)") for fx in f))
            self.assertEqual(r.gate(skip_bump=True), [])  # the wording-only claim
            r.write("skills/demo/SKILL.md", UNIT.format(v="2", u="2026-09-01", req="", body="2. Do more.\n"))  # same-day updated is fine
            r.write("docs/CHANGELOG.md", "# Kit changelog\n\n- 2026-09-02 · demo v2 (#2) — more.\n- 2026-09-01 · demo v1 (#1) — born.\n")
            r.commit("bumped")
            self.assertEqual(r.gate(), [])
            r.write("skills/demo/SKILL.md", UNIT.format(v="3", u="2026-08-01", req="", body="2. Do more.\n"))  # updated went backwards
            r.commit("back")
            self.assertTrue(any("`metadata.updated` is '2026-08-01' (base '2026-09-01')" in fx for fx in r.gate()))

    def test_readme_only_and_engine_changes_need_no_bump(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Repo(tmp)
            r.write("skills/demo/README.md", "# demo\n\nmore words\n")
            r.write("context-db/bin/x.py", "print(1)\n")
            r.commit("readme + engine")
            self.assertEqual(r.gate(), [])

    def test_script_change_counts_as_the_units_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Repo(tmp)
            r.write("skills/demo/run.sh", "#!/bin/sh\necho hi\n")
            r.commit("script")
            f = r.gate()
            self.assertTrue(any("files of `demo` changed but `metadata.version` is 1" in fx for fx in f), f)

    def test_new_and_removed_units(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Repo(tmp)
            r.write("skills/newbie/SKILL.md", UNIT.format(v="1", u="2026-09-02", req="", body="").replace("name: demo", "name: newbie"))
            r.rm("agents/tri.md")
            r.commit("new + removed, no changelog")
            f = r.gate()
            self.assertTrue(any("new unit `newbie` has no CHANGELOG line" in fx for fx in f), f)
            self.assertTrue(any("`tri` is removed but no added CHANGELOG line" in fx for fx in f), f)
            r.write("docs/CHANGELOG.md", "# Kit changelog\n\n- 2026-09-02 · newbie v1 (new) · tri (removed) (#3).\n- 2026-09-01 · demo v1 (#1) — born.\n")
            r.commit("changelog")
            self.assertEqual(r.gate(), [])

    def test_cli_exit_status_and_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Repo(tmp)
            r.write("skills/demo/SKILL.md", UNIT.format(v="1", u="2026-09-01", req="", body="2. Do more.\n"))
            r.commit("no bump")
            p = subprocess.run([sys.executable, str(BIN / "review_gate.py"), "--base", "main"], cwd=r.root, capture_output=True, text=True)
            self.assertEqual(p.returncode, 1)
            self.assertIn("review-gate: FAIL — 1 changed file(s), 1 unit(s) [demo], 2 finding(s)", p.stdout)
            p = subprocess.run([sys.executable, str(BIN / "review_gate.py"), "--base", "main", "--skip-bump", "--json"], cwd=r.root, capture_output=True, text=True)
            self.assertEqual((p.returncode, json.loads(p.stdout)), (0, []))


class Evidence(unittest.TestCase):
    def test_units_requires_preflags_and_rules_from_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Repo(tmp)
            body = "2. Query Jira for the sprint; `gh api x 2>/dev/null || true`.\n3. Post to the tracker.\n"
            r.write("skills/demo/SKILL.md", UNIT.format(v="2", u="2026-09-02", req="  requires: \"slack\"\n", body=body))
            r.write("skills/demo/run.py", "try:\n    x = 1\nexcept Exception: pass\ntry:\n    y = 2\nexcept:\n    y = 0\n")
            r.write("docs/CHANGELOG.md", "# Kit changelog\n\n- 2026-09-02 · demo v2 (#2).\n- 2026-09-01 · demo v1 (#1) — born.\n")
            r.write("docs/REVIEW.md", "# Rules v2 — the PR tries to rewrite them\n")
            r.commit("pr")
            ev = r.ev()
            self.assertEqual(ev["tier0"], [])
            u = ev["units"][0]
            self.assertEqual((u["name"], u["status"], u["requires_before"], u["requires_after"], u["version"]), ("demo", "changed", [], ["slack"], [1, 2]))
            self.assertIn(("skills/demo/SKILL.md", "2>/dev/null"), {(s["path"], s["match"]) for s in ev["swallowed"]})
            self.assertEqual(sorted(s["match"] for s in ev["swallowed"] if s["path"].endswith("run.py")), ["except Exception: pass", "except:"])
            vocab = {(v["word"], v["system"]) for v in ev["vocabulary"]}
            self.assertIn(("Jira", "jira"), vocab)  # slack is required, Jira is not
            self.assertFalse(any(v["system"] == "slack" for v in ev["vocabulary"]))
            rules, source = evidence.rules_from_base("main", r.root)
            self.assertEqual((rules, source), ("# Rules v1\n", "main:docs/REVIEW.md"))  # the base's copy, not the PR's
            text = evidence.render(ev, source)
            self.assertIn("| `demo` | changed | — | slack | 1 → 2 |", text)
            self.assertIn("PASSED", text)
            self.assertIn("says `Jira` (capability `jira` not in requires)", text)

    def test_tier0_failure_is_shown_as_decided(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Repo(tmp)
            r.write("skills/demo/SKILL.md", UNIT.format(v="1", u="2026-09-01", req="", body="2. more\n"))
            r.commit("no bump")
            ev = r.ev()
            self.assertEqual(len(ev["tier0"]), 2)
            self.assertIn("**FAILED** — the PR is red until these are fixed; do not repeat them as findings", evidence.render(ev, "x"))

    def test_cli_writes_evidence_and_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Repo(tmp)
            r.write("docs/n.md", "hello\n")
            r.commit("doc")
            out = Path(tmp) / "rv" / "evidence.md"
            p = subprocess.run([sys.executable, str(BIN / "review_evidence.py"), "--base", "main", "--out", str(out)], cwd=r.root, capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertTrue(out.is_file() and (out.parent / "RULES.md").read_text() == "# Rules v1\n")
            self.assertIn("No skill or agent changed", out.read_text())


class KitAgrees(unittest.TestCase):
    def test_vocab_map_covers_every_capability_and_the_kit_passes_its_own_gate_shapes(self):
        import kb
        self.assertEqual(set(kb.SYSTEM_VOCAB), set(kb.SYSTEMS))
        for rx, _w in gate.PII_SHAPES:
            gate.re.compile(rx)


if __name__ == "__main__":
    unittest.main()
