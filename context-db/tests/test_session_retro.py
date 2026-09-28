"""session_retro.py on SYNTHETIC transcripts written here (never a real one under ~/.claude): each rule check fires on a
positive and stays quiet on its negative, corrections / denials / failures are extracted, and the CLI's exit codes.
The repo's commit style is pinned to the kit default so no machine's store is read. Stdlib unittest.
Run: make -C .claude/context-db test."""
from __future__ import annotations
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

BIN = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))

import session_retro as sr  # noqa: E402

FOOTER = "🤖 Generated with [Claude Code](https://claude.com/claude-code)"
TS = "2026-09-26T10:{:02d}:00Z"


class Builder:
    """Transcript lines in Claude Code's shape: a user prompt, an assistant turn with tool calls, their results."""

    def __init__(self, cwd: str = "/work/space"):
        self.lines: list[dict] = []
        self.n = 0
        self.cwd = cwd

    def _ts(self) -> str:
        self.n += 1
        return TS.format(self.n % 60)

    def user(self, text: str) -> "Builder":
        self.lines.append({"type": "user", "timestamp": self._ts(), "cwd": self.cwd, "message": {"role": "user", "content": text}})
        return self

    def say(self, text: str) -> "Builder":
        self.lines.append({"type": "assistant", "timestamp": self._ts(), "cwd": self.cwd,
                           "message": {"id": f"m{self.n}", "content": [{"type": "text", "text": text}]}})
        return self

    def call(self, name: str, result: str = "ok", error: bool = False, cwd: str | None = None, **inp) -> "Builder":
        tid = f"toolu_{len(self.lines)}"
        self.lines.append({"type": "assistant", "timestamp": self._ts(), "cwd": cwd or self.cwd,
                           "message": {"id": f"m{self.n}", "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}})
        self.lines.append({"type": "user", "timestamp": self._ts(), "cwd": cwd or self.cwd, "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "content": result, "is_error": error}]}})
        return self

    def bash(self, command: str, **kw) -> "Builder":
        return self.call("Bash", command=command, **kw)

    def write(self, path: Path) -> Path:
        path.write_text("\n".join(json.dumps(line) for line in self.lines) + "\nnot json\n", encoding="utf-8")
        return path


class RetroCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        p = unittest.mock.patch.object(sr.commit_style, "resolve", return_value=("conventional", "test"))
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def report(self, b: Builder) -> dict:
        return sr.Retro(str(b.write(self.dir / "s.jsonl"))).read().report()

    def rules(self, b: Builder) -> list[str]:
        return [h["rule"] for h in self.report(b)["rule_hits"]]


class RuleChecks(RetroCase):
    PR = "gh pr create -R acme/widgets --title 'feat(widgets): add the thing' --body-file /tmp/body.md"

    def pr_body(self, body: str) -> Builder:
        return Builder().user("open the PR").call("Write", file_path="/tmp/body.md", content=body)

    def test_pr_no_labels(self):
        self.assertIn("pr-no-labels", self.rules(self.pr_body(FOOTER).bash(self.PR)))
        self.assertNotIn("pr-no-labels", self.rules(self.pr_body(FOOTER).bash(self.PR + " --label enhancement")))
        later = self.pr_body(FOOTER).bash(self.PR).bash("gh api -X POST repos/acme/widgets/issues/5/labels -f 'labels[]=enhancement'")
        self.assertNotIn("pr-no-labels", self.rules(later))
        listing = self.pr_body(FOOTER).bash(self.PR).bash("gh api repos/acme/widgets/labels")
        self.assertIn("pr-no-labels", self.rules(listing))

    def test_pr_no_footer(self):
        self.assertIn("pr-no-footer", self.rules(self.pr_body("Closes #5\n\n## What\nthe thing").bash(self.PR + " -l x")))
        self.assertNotIn("pr-no-footer", self.rules(self.pr_body(f"Closes #5\n\n{FOOTER}").bash(self.PR + " -l x")))
        heredoc = Builder().user("go").bash(f"cat > /tmp/b2.md <<'EOF'\nCloses #5\n\n{FOOTER}\nEOF")
        heredoc.bash(self.PR.replace("/tmp/body.md", "/tmp/b2.md") + " -l x")
        self.assertNotIn("pr-no-footer", self.rules(heredoc))

    def test_title_style(self):
        bad = Builder().user("go").bash(f"gh pr create --title 'Add the thing.' --label x --body '{FOOTER}'")
        self.assertIn("title-style", self.rules(bad))
        self.assertNotIn("title-style", self.rules(self.pr_body(FOOTER).bash(self.PR + " -l x")))
        commit = Builder().user("go").bash("git commit -qm \"$(cat <<'EOF'\nUpdated stuff\n\nbody\nEOF\n)\"")
        self.assertIn("title-style", self.rules(commit))
        good = Builder().user("go").bash("git -C /r commit -m 'fix(sync): release the lock'")
        self.assertNotIn("title-style", self.rules(good))
        unexpanded = Builder().user("go").bash('git commit -m "$t"')
        self.assertNotIn("title-style", self.rules(unexpanded))

    def test_agent_no_model(self):
        self.assertIn("agent-no-model", self.rules(Builder().user("go").call("Agent", description="fix it", prompt="x")))
        self.assertNotIn("agent-no-model", self.rules(Builder().user("go").call("Agent", description="fix it", prompt="x", model="sonnet")))

    def test_workspace_path_in_github_body(self):
        leak = self.pr_body(f"see .context/igx/notes.md\n{FOOTER}").bash(self.PR + " -l x")
        self.assertIn("workspace-path", self.rules(leak))
        comment = Builder().user("go").bash("gh issue comment 5 -R acme/widgets --body 'log at ~/scratch/out.txt'")
        self.assertIn("workspace-path", self.rules(comment))
        home = "/" + "home" + "/someone/"  # assembled: no path-shaped literal in the source
        mcp = Builder().user("go").call("mcp__github__create_issue", title="x", body=f"from {home}proj")
        self.assertIn("workspace-path", self.rules(mcp))
        clean = Builder().user("go").bash("gh issue comment 5 -R acme/widgets --body 'fixed in #6'")
        self.assertNotIn("workspace-path", self.rules(clean))

    def test_store_write_from_worktree_without_context_root(self):
        wt = "/work/.worktrees/kit_x"
        hit = Builder(cwd=wt).user("go").bash("python3 context-db/bin/kb.py set github.person x y --from user")
        self.assertIn("store-write-no-root", self.rules(hit))
        absolute = Builder().user("go").bash(f"python3 {wt}/context-db/bin/session.py touch --name a-b")
        self.assertIn("store-write-no-root", self.rules(absolute))
        rooted = Builder(cwd=wt).user("go").bash("CONTEXT_ROOT=$(mktemp -d)/.context python3 context-db/bin/kb.py init --blank")
        self.assertNotIn("store-write-no-root", self.rules(rooted))
        mention = Builder(cwd=wt).user("go").bash("sed -i 's/kb.py set/kb.py get/' docs/x.md")
        self.assertNotIn("store-write-no-root", self.rules(mention))
        kit = Builder(cwd=wt).user("go").bash("make -s -C $BATON/context-db session-touch NAME=a-b")
        self.assertNotIn("store-write-no-root", self.rules(kit))
        reads = Builder(cwd=wt).user("go").bash("python3 context-db/bin/kb.py get github.person x")
        self.assertNotIn("store-write-no-root", self.rules(reads))

    def test_merge_without_registry_touch(self):
        self.assertIn("merge-no-registry", self.rules(Builder().user("go").bash("gh pr merge 5 -R acme/widgets --squash")))
        api = Builder().user("go").bash("gh api -X PUT repos/acme/widgets/pulls/5/merge -f merge_method=squash")
        self.assertIn("merge-no-registry", self.rules(api))
        touched = Builder().user("go").bash("gh pr merge 5 --squash").bash("make -s -C $BATON/context-db session-touch NAME=a-b")
        self.assertNotIn("merge-no-registry", self.rules(touched))
        handoff = Builder().user("go").bash("gh pr merge 5 --squash").call("Skill", skill="ai-baton:session-handoff")
        self.assertNotIn("merge-no-registry", self.rules(handoff))

    def test_a_call_that_failed_or_was_denied_does_not_count(self):
        failed = Builder().user("go").bash("gh pr merge 5 --squash", result="Exit code 1\nnot mergeable", error=True)
        self.assertNotIn("merge-no-registry", self.rules(failed))
        denied = Builder().user("go").bash("gh pr create --title 'Bad title' --body x", error=True,
                                           result="The user doesn't want to proceed with this tool use. The tool use was rejected.")
        self.assertEqual(self.rules(denied), [])


class Extraction(RetroCase):
    def test_correction_candidate_with_the_turn_before(self):
        b = (Builder().user("ship the fix").say("Pushing straight to main now.").bash("git push origin main")
             .user("No, don't push to main — I said open a PR").user("thanks, looks good"))
        r = self.report(b)
        corr, fine = r["user_messages"][1], r["user_messages"][2]
        self.assertTrue(corr["correction_hint"])
        self.assertEqual(corr["turn"], 2)
        self.assertIn("Pushing straight to main", corr["prev_assistant"])
        self.assertEqual(corr["prev_tools"], ["Bash"])
        self.assertFalse(fine["correction_hint"])
        self.assertEqual(r["counts"]["corrections"], 1)

    def test_interrupt_is_a_correction_and_system_text_is_not_a_prompt(self):
        b = Builder().user("go").user("[Request interrupted by user for tool use]").user("<system-reminder>x</system-reminder>")
        msgs = self.report(b)["user_messages"]
        self.assertEqual(len(msgs), 2)
        self.assertTrue(msgs[1]["interrupt"] and msgs[1]["correction_hint"])

    def test_denials_failures_skills_and_tool_calls(self):
        b = (Builder().user("<command-name>/pr-open</command-name><command-args>5</command-args>")
             .bash("rm -rf build", error=True, result="Permission to use Bash with command rm -rf build has been denied.")
             .bash("make test", error=True, result="Exit code 2\nFAILED (failures=1)")
             .call("Skill", skill="ai-baton:session-handoff").call("Read", file_path="/x/y.md"))
        r = self.report(b)
        self.assertEqual([d["key"] for d in r["denials"]], ["rm -rf build"])
        self.assertEqual([f["key"] for f in r["failures"]], ["make test"])
        self.assertIn("Exit code 2", r["failures"][0]["error"])
        self.assertEqual(r["skills_invoked"], ["pr-open", "session-handoff"])
        self.assertEqual(r["user_messages"][0]["text"], "/pr-open 5")
        self.assertEqual([c["tool"] for c in r["tool_calls"]], ["Bash", "Bash", "Skill", "Read"])
        self.assertEqual(r["tool_calls"][3]["key"], "/x/y.md")

    def test_clean_session_has_no_hits(self):
        b = Builder().user("read the readme").call("Read", file_path="README.md").say("Done.")
        r = self.report(b)
        self.assertEqual((r["rule_hits"], r["denials"], r["failures"]), ([], [], []))
        self.assertIn("- none", sr.fmt_text(r))


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SESSION_ID"}
        self.env.update(HOME=str(self.home), CONTEXT_ROOT=str(self.home / ".context"))

    def tearDown(self):
        self.tmp.cleanup()

    def run_(self, *args: str, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(BIN / "session_retro.py"), *args], env={**self.env, **env},
                              capture_output=True, text=True)

    def test_no_transcript_is_exit_3(self):
        for args, env in (((), {}), (("--session-id", "nope"), {}), ((), {"CLAUDE_CODE_SESSION_ID": "nope"}),
                          (("--transcript", str(self.home / "missing.jsonl")), {})):
            p = self.run_(*args, **env)
            self.assertEqual(p.returncode, 3, args)
            self.assertEqual(p.stdout, "")
            self.assertIn("no session id / transcript", p.stderr)

    def test_found_by_session_id_prints_json(self):
        proj = self.home / ".claude" / "projects" / "-work-space"
        proj.mkdir(parents=True)
        Builder().user("go").call("Agent", description="d", prompt="p").write(proj / "sess-9.jsonl")
        p = self.run_("--json", CLAUDE_CODE_SESSION_ID="sess-9")
        self.assertEqual(p.returncode, 0, p.stderr)
        r = json.loads(p.stdout)
        self.assertEqual(r["session_id"], "sess-9")
        self.assertEqual([h["rule"] for h in r["rule_hits"]], ["agent-no-model"])
        text = self.run_("--session-id", "sess-9")
        self.assertEqual(text.returncode, 0)
        self.assertIn("agent-no-model", text.stdout)
        self.assertNotIn("tool_calls", text.stdout)


if __name__ == "__main__":
    unittest.main()
