"""carousel_check.py — the Stop hook that warns when a turn ends on a prose ask or offer with no AskUserQuestion call.

Hook inputs are fixtures piped to the script with invented text: a flagged reply prints one `systemMessage` JSON line;
a silent case prints nothing; exit is always 0. Also the pattern checks (`kind`), the headless `Decisions` block, the
turn walker the stats script shares, and the wiring of the hook in hooks.json and settings.json.
Stdlib unittest. Run: make -C .claude/context-db test T=test_carousel_check."""
from __future__ import annotations
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]
BIN = HERE.parent / "bin"
SCRIPT = BIN / "carousel_check.py"
sys.path.insert(0, str(BIN))
import carousel_check  # noqa: E402
import carousel_stats  # noqa: E402


def user(text="go"):
    return {"type": "user", "message": {"role": "user", "content": text}}


def tool_result():
    return {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}


def says(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def calls(name):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": name, "input": {}}]}}


COMPACT = {"type": "system", "subtype": "compact_boundary"}


def hook(recs, **extra):
    """Run the hook on a transcript made of `recs`; return (exit code, stdout)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "t.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
        payload = {"hook_event_name": "Stop", "transcript_path": str(path), "stop_hook_active": False, **extra}
        r = subprocess.run([sys.executable, str(SCRIPT), "hook"], input=json.dumps(payload), capture_output=True, text=True)
        return r.returncode, r.stdout


class Flagged(unittest.TestCase):
    def assert_flagged(self, recs, **extra):
        code, out = hook(recs, **extra)
        self.assertEqual(code, 0)
        msg = json.loads(out)["systemMessage"]
        self.assertIn("AskUserQuestion", msg)
        return msg

    def test_an_optional_offer_at_the_end(self):
        msg = self.assert_flagged([user(), says("Done, the report is in the folder.\n\nTell me if you want the long version.")])
        self.assertIn("offer", msg)

    def test_a_question_at_the_end(self):
        msg = self.assert_flagged([user(), says("Both branches build.\n\nWhich of the two should the merge use?")])
        self.assertIn("question", msg)

    def test_a_waits_for_you_list(self):
        text = "Everything else is merged.\n\n**Waits for you**\n- the release tag\n- the old branch cleanup"
        msg = self.assert_flagged([user(), says(text)])
        self.assertIn("waits-list", msg)

    def test_the_reply_comes_from_the_hook_input_when_given(self):
        self.assert_flagged([user(), says("All done.")], last_assistant_message="All done. Want me to run the other suite too?")

    def test_a_carousel_in_an_earlier_turn_does_not_cover_this_turn(self):
        self.assert_flagged([user(), calls("AskUserQuestion"), tool_result(), says("Picked."), user("next"),
                             says("Fixed.\nLet me know if you want it applied to the other repo.")])

    def test_tool_results_do_not_start_a_new_turn(self):
        recs = [user(), calls("AskUserQuestion"), tool_result(), says("Applied.\nLet me know if you need more.")]
        self.assertEqual(hook(recs), (0, ""))  # the carousel is in the same turn


class Silent(unittest.TestCase):
    def assert_silent(self, recs, **extra):
        self.assertEqual(hook(recs, **extra), (0, ""))

    def test_a_status_report_that_asks_nothing(self):
        self.assert_silent([user(), says("Merged the change.\n\nThe suite is green, 3 files changed, nothing is waiting.")])

    def test_the_turn_called_the_carousel(self):
        self.assert_silent([user(), calls("AskUserQuestion"), tool_result(), says("Which one do you prefer? Tell me if unsure.")])

    def test_a_headless_decisions_block(self):
        text = ("Built the report.\n\nDecisions\n1. Publish now? — (1) yes, Recommended (2) no\n"
                "2. Which label? — (1) docs, Recommended (2) fix")
        self.assert_silent([user(), says(text)])
        self.assert_silent([user(), says(text.replace("Decisions", "**Decisions**"))])

    def test_a_question_inside_a_code_block(self):
        text = "The prompt reads:\n\n```\nWould you like to continue?\n```\n"
        self.assert_silent([user(), says(text)])
        self.assert_silent([user(), says("The prompt reads `should I continue?` and nothing more.")])

    def test_a_question_in_the_middle_is_not_the_end(self):
        text = "Why did it fail? The cache was stale.\nI cleared it and reran; the check passes.\nThe fix is in the branch."
        self.assert_silent([user(), says(text)])

    def test_a_turn_that_ended_on_a_tool_call(self):
        self.assert_silent([user(), says("Want me to look?"), calls("Bash")])

    def test_stop_hook_active_is_silent(self):
        self.assert_silent([user(), says("Tell me if you want more.")], stop_hook_active=True)

    def test_bad_input_is_silent_and_exits_zero(self):
        for raw in ("", "not json", "{}", json.dumps({"transcript_path": "/nonexistent/x.jsonl"}), "[1]"):
            r = subprocess.run([sys.executable, str(SCRIPT), "hook"], input=raw, capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stdout), (0, ""), raw)


class Patterns(unittest.TestCase):
    def test_kinds(self):
        cases = {
            "Done.\nSay the word and I will ship it.": "offer",
            "Done.\nIf you'd like, I can add a test.": "offer",
            "Done.\nThis needs your go before it can merge.": "offer",
            "Done.\n## Open decisions\n- the name": "waits-list",
            "Done.\nWaiting on you:\n- sign-off": "waits-list",
            "Done.\nIs that the right reading?": "question",
            "Done. Is that right?**": "question",
            "Done. Nothing else is open.": "",
            "": "",
        }
        for text, want in cases.items():
            self.assertEqual(carousel_check.kind(text), want, text)

    def test_an_offer_far_from_the_end_does_not_count(self):
        text = "Let me know if the build fails.\n" + "\n".join(f"line {i}" for i in range(5)) + "\nAll green."
        self.assertEqual(carousel_check.kind(text), "")

    def test_decisions_block_needs_a_numbered_item(self):
        self.assertTrue(carousel_check.has_decisions_block("x\nDecisions\n1. a — (1) b"))
        self.assertFalse(carousel_check.has_decisions_block("The Decisions we took are listed above."))


class Stats(unittest.TestCase):
    def test_turn_walker_counts_calls_asks_and_the_compaction_split(self):
        recs = [user(), calls("AskUserQuestion"), tool_result(), says("ok"),
                user(), says("Tell me if you want more."),
                COMPACT, {"type": "user", "isCompactSummary": True, "message": {"content": "summary"}},
                user(), says("Which one?"), user(), says("Done.")]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.jsonl"
            path.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
            row = carousel_stats.count(str(path))
        want = dict(turns=5 - 1, turns_pre=2, turns_post=2, calls=1, asks=2, Q=1, O=1, W=0,
                    calls_pre=1, asks_pre=1, calls_post=0, asks_post=1, compacted=1)
        self.assertEqual(row, want)

    def test_the_table_has_numbers_and_row_labels_only(self):
        t = carousel_stats.table([dict.fromkeys(carousel_stats.COLS, 1)] * 2)
        rows = [ln.split("|")[1].strip() for ln in t.splitlines()[2:]]
        self.assertEqual(rows, ["S1", "S2", "total"])


class Wiring(unittest.TestCase):
    def stop_commands(self, path):
        groups = json.loads(path.read_text(encoding="utf-8"))["hooks"]["Stop"]
        return [h["command"] for g in groups for h in g["hooks"]]

    def test_both_install_paths_run_the_check_on_stop_and_never_fail(self):
        for name, root in (("hooks/hooks.json", "${CLAUDE_PLUGIN_ROOT}"), ("settings.json", "${CLAUDE_PROJECT_DIR:-.}/.claude")):
            cmds = [c for c in self.stop_commands(KIT / name) if "carousel_check.py" in c]
            self.assertEqual(len(cmds), 1, name)
            self.assertIn(f"{root}/context-db/bin/carousel_check.py\" hook", cmds[0])
            self.assertTrue(cmds[0].rstrip().endswith("|| true"), name)

    def test_the_hook_script_is_not_a_blocking_form(self):
        src = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn('"decision"', src)
        self.assertNotIn("exit(2)", src)


if __name__ == "__main__":
    unittest.main()
