"""review_gate.py / review_evidence.py: the tier-0 gate and the tier-1 evidence, on a throw-away git repo — leak
shapes on added lines only (a pre-existing leak is not a finding, the allow-list holds), version/updated/CHANGELOG bumps
for changed, new and removed units, the wording-only exemption, README edits not counting, the pre-flags (swallowed
errors, vocabulary in a unit without the capability), rules read from the base. Fact-shaped literals are assembled at
run time. Stdlib unittest. Run: make -C .claude/context-db test."""
from __future__ import annotations
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
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


class GateFromBase(unittest.TestCase):
    """#129: the PR under review cannot widen the allow-list that judges it; the diff is read NUL-separated."""

    def test_an_allow_line_added_by_the_pr_does_not_excuse_its_own_leak(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Repo(tmp)
            r.write("skills/kit-health/allow.txt", "docs/allowed.md:" + LEAK + "$\ndocs/sneak.md:.*\n")
            r.write("docs/sneak.md", f"token {TOKEN}\n")
            r.commit("widen the list and leak")
            f = r.gate(skip_bump=True)
            self.assertTrue(any(x.startswith("[STOP] docs/sneak.md:1 — GitHub token") for x in f), f)

    def test_the_base_allow_list_still_applies(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Repo(tmp)
            r.write("docs/allowed.md", f"ok {LEAK}\n")
            r.commit("allowed by the base list")
            self.assertEqual(r.gate(skip_bump=True), [])

    def test_quoted_renamed_and_binary_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Repo(tmp)
            r.write("docs/n\u00e4me \"q\".md", f"token {TOKEN}\n")           # core.quotePath would quote this
            r.write("docs/old.md", "line one\nline two\nline three\nline four\n")
            r.commit("seed")
            sh(r.root, "git", "update-ref", "refs/heads/main", "HEAD")        # the seed is base now
            sh(r.root, "git", "mv", "docs/old.md", "docs/new.md")
            r.write("docs/new.md", "line one\nline two\nline three\nline four\n" + f"mail {MAIL}\n")
            r.write("docs/blob.bin", "")
            (r.root / "docs/blob.bin").write_bytes(b"\x00\x01" + TOKEN.encode() + b"\x00")
            r.write("docs/\u00fcber.md", f"sub {MAIL.replace('@', '+tag@')}\n")
            r.commit("rename + binary + non-ascii")
            files = gate.changed_files("main", "HEAD", r.root)
            self.assertEqual(files.get("docs/new.md"), "R", files)
            self.assertIn("docs/\u00fcber.md", files)
            self.assertEqual(gate.binary_files("main", "HEAD", r.root), {"docs/blob.bin"})
            f = r.gate(skip_bump=True)
            self.assertTrue(any(x.startswith("[STOP] docs/new.md:5 — e-mail address") for x in f), f)
            self.assertTrue(any(x.startswith("[STOP] docs/\u00fcber.md:1 — e-mail address") for x in f), f)
            self.assertFalse(any("blob.bin" in x for x in f), f)


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

    def test_real_number_behind_a_placeholder_prefix_is_a_stop(self):
        # #92: `KEY-` is exempt from the ticket-key shape; a non-canonical number behind it is still a finding
        with tempfile.TemporaryDirectory() as tmp:
            r = Repo(tmp)
            r.write("docs/note.md", "# note\n\nsee KEY-" + "9876 and key-" + "9876-foo\nok KEY-123 key-123-<slug>\n")
            r.commit("placeholder")
            f = r.gate(skip_bump=True)
            self.assertEqual(len(f), 1, f)  # one hit per line; the canonical line is clean
            self.assertIn("docs/note.md:3", f[0]); self.assertIn("placeholder prefix", f[0])

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


class Tree(unittest.TestCase):
    def test_tree_scans_every_file_not_only_the_diff(self):
        # #95: a leak already on main is invisible to the diff scan but not to --tree; the allow-list still applies
        with tempfile.TemporaryDirectory() as tmp:
            r = Repo(tmp)
            sh(r.root, "git", "checkout", "-q", "main")
            r.write("docs/old.md", f"# old\n\nchannel {LEAK}\n")
            r.write("docs/allowed.md", f"allowed {LEAK}\n")
            r.write("docs/fine.md", "mail t@example.invalid and u@host.test.\n")  # an RFC 2606 reserved TLD is no address
            r.write("docs/sub.md", "mail " + "x@mail.test" + ".acmecorp.com\n")  # ...but only as the LAST label
            r.commit("main has a leak")
            self.assertEqual(gate.run("main", "HEAD", skip_bump=True, cwd=r.root)[0], [])
            f, n = gate.tree_findings("HEAD", cwd=r.root)
            self.assertEqual(len(f), 2, f)
            self.assertTrue(f[0].startswith("[STOP] docs/old.md:3 — Slack channel/DM id"), f[0])
            self.assertTrue(f[1].startswith("[STOP] docs/sub.md:1 — e-mail address"), f[1])
            self.assertIn("in the tree", f[0])
            self.assertGreater(n, 3)

    def test_tree_cli_exit_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Repo(tmp)
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(gate.main(["--tree", "--repo", str(r.root)]), 0)
            self.assertIn("review-gate --tree: OK", out.getvalue())
            r.write("docs/old.md", f"channel {LEAK}\n"); r.commit("leak")
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(gate.main(["--tree", "--repo", str(r.root)]), 1)
            self.assertIn("review-gate --tree: FAIL", out.getvalue())


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


class SwallowForms(unittest.TestCase):
    """#145: every swallow spelling the workspace rule names, in every script type the kit ships."""

    def test_each_form_is_flagged_in_hooks_mjs_and_shell(self):
        forms = ["a 2>/dev/null", "b &>/dev/null", "c >/dev/null 2>&1", "d 2>&1 >/dev/null", "e || true", "f || :",
                 "g || echo none"]
        with tempfile.TemporaryDirectory() as tmp:
            r = Repo(tmp)
            r.write("hooks/pre-push", "#!/bin/sh\n" + "\n".join(forms) + "\n")
            r.write("skills/pr-open/check.mjs", "run() || true\n")
            r.write("bin/tool", "#!/usr/bin/env python3\ntry:\n    x()\nexcept OSError:\n    pass\nexcept ValueError: pass\n")
            r.write("docs/prose", "no shebang: a 2>/dev/null here is prose\n")
            r.write("hooks/clean", "#!/bin/sh\nx >/dev/null\ny || true_value\n")
            r.write("docs/logo.png", "")
            (r.root / "docs/logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe\x00\x00 2>/dev/null")  # not UTF-8: must not crash
            r.commit("scripts")
            sw = {(s["path"], s["line"]) for s in r.ev()["swallowed"]}
            self.assertEqual({n for pth, n in sw if pth == "hooks/pre-push"}, set(range(2, 2 + len(forms))), sw)
            self.assertIn(("skills/pr-open/check.mjs", 1), sw)
            self.assertIn(("bin/tool", 6), sw)
            self.assertFalse(any(pth in ("docs/prose", "hooks/clean") for pth, _ in sw), sw)


class UpdatedInUtc(unittest.TestCase):
    def test_tomorrow_utc_is_not_the_future_but_the_day_after_is(self):
        from datetime import datetime, timedelta, timezone
        today = datetime.now(timezone.utc).date()
        with tempfile.TemporaryDirectory() as tmp:
            r = Repo(tmp)
            r.write("skills/demo/SKILL.md", UNIT.format(v="2", u=(today + timedelta(days=1)).isoformat(), req="", body=""))
            r.write("docs/CHANGELOG.md", "# Kit changelog\n\n- x · demo v2 (#2).\n- 2026-09-01 · demo v1 (#1) — born.\n")
            r.commit("a UTC+14 author's today")
            self.assertEqual(r.gate(), [])
            r.write("skills/demo/SKILL.md", UNIT.format(v="2", u=(today + timedelta(days=2)).isoformat(), req="", body=""))
            r.commit("really in the future")
            self.assertTrue(any("in the future" in f for f in r.gate()))


class KitAgrees(unittest.TestCase):
    def test_vocab_map_covers_every_capability_and_the_kit_passes_its_own_gate_shapes(self):
        import kb
        self.assertEqual(set(kb.SYSTEM_VOCAB), set(kb.SYSTEMS))
        for rx, _w in gate.PII_SHAPES:
            gate.re.compile(rx)


if __name__ == "__main__":
    unittest.main()
