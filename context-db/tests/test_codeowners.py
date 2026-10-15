"""codeowners.py — pr-open's reviewer resolver: GitHub's CODEOWNERS matching rule as a pure function (glob forms,
last matching line wins, no negation, no escapes), the `gh` lookups mocked (the readability gate, location order,
the PR's base ref, 404 vs a real failure, paged files, the PR author left out), the exit codes, and the head-move
`check` line (team satisfied by a member's review or the fallback, extra requests only noted).
Stdlib unittest, no network. Run: make -C .claude/context-db test."""
from __future__ import annotations
import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

KIT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(KIT / "context-db" / "bin"))
sys.path.insert(0, str(KIT / "skills" / "pr-open"))

import codeowners as co  # noqa: E402

FILE = """\
# a comment
*                       @octocat
*.py                    @example/py-team
/docs/                  @example/docs-team  # trailing comment
docs/**/*.png           @example/design
/src/api/**             @example/api-team @hubot
/src/api/internal/      @example/sec-team
/build/logs             @example/ops
/vendor/                                    
apps/                   @example/apps
/README.md              docs@example.com
"""
RULES = co.parse_codeowners(FILE)


def owners(path: str) -> list[str] | None:
    return co.owners_for(path, RULES)


class Matching(unittest.TestCase):
    def test_star_alone_owns_everything_at_any_depth(self):
        self.assertEqual(owners("Makefile"), ["@octocat"])
        self.assertEqual(owners("a/b/c/data.txt"), ["@octocat"])

    def test_extension_glob_matches_any_depth_and_beats_the_earlier_star(self):
        self.assertEqual(owners("tool.py"), ["@example/py-team"])
        self.assertEqual(owners("a/b/tool.py"), ["@example/py-team"])

    def test_anchored_directory_owns_everything_beneath_it(self):
        self.assertEqual(owners("docs/guide.md"), ["@example/docs-team"])
        self.assertEqual(owners("docs/deep/er/guide.md"), ["@example/docs-team"])
        self.assertEqual(owners("other/docs/guide.md"), ["@octocat"])  # anchored: not a nested docs/

    def test_last_matching_line_wins(self):
        self.assertEqual(owners("docs/deep/a.png"), ["@example/design"])  # `**` between segments, later than /docs/
        self.assertEqual(owners("docs/a.png"), ["@example/design"])  # `**` also matches zero segments
        self.assertEqual(owners("docs/script.py"), ["@example/docs-team"])  # /docs/ is later than *.py

    def test_double_star_suffix_and_a_later_narrower_rule(self):
        self.assertEqual(owners("src/api/handler.go"), ["@example/api-team", "@hubot"])
        self.assertEqual(owners("src/api/internal/key.go"), ["@example/sec-team"])
        self.assertEqual(owners("src/api/internal/deep/key.py"), ["@example/sec-team"])

    def test_double_star_suffix_does_not_match_the_directory_name_itself(self):
        self.assertEqual(owners("src/api"), ["@octocat"])

    def test_directory_named_without_a_trailing_slash_owns_beneath_it(self):
        self.assertEqual(owners("build/logs/today.txt"), ["@example/ops"])
        self.assertEqual(owners("build/logs"), ["@example/ops"])
        self.assertEqual(owners("build/logsx/today.txt"), ["@octocat"])

    def test_unanchored_directory_matches_at_any_depth(self):
        self.assertEqual(owners("apps/x.txt"), ["@example/apps"])
        self.assertEqual(owners("web/apps/x.txt"), ["@example/apps"])

    def test_a_matching_line_with_no_owners_un_owns_the_path(self):
        self.assertEqual(owners("vendor/lib/x.txt"), [])

    def test_star_stays_inside_one_segment(self):
        rules = co.parse_codeowners("/docs/*  @example/docs-team\n")
        self.assertEqual(co.owners_for("docs/a.md", rules), ["@example/docs-team"])
        self.assertIsNone(co.owners_for("docs/sub/a.md", rules))
        self.assertIsNone(co.owners_for("x/docs/a.md", rules))

    def test_slash_in_the_middle_anchors_the_pattern(self):
        rules = co.parse_codeowners("docs/*  @example/docs-team\n")
        self.assertEqual(co.owners_for("docs/a.md", rules), ["@example/docs-team"])
        self.assertIsNone(co.owners_for("x/docs/a.md", rules))

    def test_question_mark_is_one_non_slash_character(self):
        rules = co.parse_codeowners("/a?c.txt  @octocat\n")
        self.assertEqual(co.owners_for("abc.txt", rules), ["@octocat"])
        self.assertIsNone(co.owners_for("a/c.txt", rules))
        self.assertIsNone(co.owners_for("abbc.txt", rules))

    def test_no_negation_a_leading_bang_is_read_literally(self):
        rules = co.parse_codeowners("*  @octocat\n!*.md  @hubot\n")
        self.assertEqual(co.owners_for("notes.md", rules), ["@octocat"])  # the `!` line matches nothing real
        self.assertEqual(co.owners_for("!notes.md", rules), ["@hubot"])

    def test_no_match_is_none_not_empty(self):
        self.assertIsNone(co.owners_for("anything", co.parse_codeowners("/only/  @octocat\n")))

    def test_regex_metacharacters_in_a_pattern_are_literal(self):
        rules = co.parse_codeowners("/a+b(1).txt  @octocat\n")
        self.assertEqual(co.owners_for("a+b(1).txt", rules), ["@octocat"])
        self.assertIsNone(co.owners_for("aab(1).txt", rules))

    def test_comments_and_no_escapes(self):
        rules = co.parse_codeowners("# note\n\n*.md @hubot # inline\n\\#hash.txt @octocat\n")
        self.assertEqual(rules, [("*.md", ["@hubot"]), ("\\#hash.txt", ["@octocat"])])  # `\` read literally
        self.assertIsNone(co.owners_for("#hash.txt", rules))
        self.assertEqual(co.parse_codeowners("#hash.txt @octocat\n"), [])  # a leading `#` is a comment, full stop


class Resolve(unittest.TestCase):
    def test_union_in_first_seen_order_without_duplicates(self):
        out, skipped = co.resolve_owners(["src/api/a.go", "docs/x.md", "src/api/b.go"], RULES)
        self.assertEqual(out, ["team:api-team", "user:hubot", "team:docs-team"])
        self.assertEqual(skipped, [])

    def test_email_owner_is_skipped_and_named(self):
        out, skipped = co.resolve_owners(["README.md"], RULES)
        self.assertEqual((out, skipped), ([], ["docs@example.com"]))

    def test_owner_forms(self):
        self.assertEqual(co.normalise_owner("@example/team-x"), "team:team-x")
        self.assertEqual(co.normalise_owner("@octocat"), "user:octocat")
        self.assertIsNone(co.normalise_owner("a@example.com"))

    def test_unowned_path_adds_nothing(self):
        self.assertEqual(co.resolve_owners(["vendor/x"], RULES), ([], []))

    def test_compare_flags_unrequested_owners_and_lists_extra_requests(self):
        missing, extra = co.compare(["team:a", "user:b", "user:c", "team:d"], ["team:a", "team:gone", "user:bot"],
                                    {"user:c", "team:d"}, ["bot"])
        self.assertEqual((missing, extra), (["user:b"], ["team:gone"]))


class FakeGh:
    """Replaces codeowners.run_gh: maps an `api` path fragment to (rc, stdout, stderr); logs every call."""
    def __init__(self, routes: dict[str, tuple[int, str, str]]):
        self.routes, self.calls = routes, []

    def __call__(self, args: list[str]):
        self.calls.append(args)
        path = next(a for a in args if a.startswith(("repos/", "orgs/"))).split("?", 1)[0]
        for frag, resp in self.routes.items():
            if path.endswith(frag):
                return resp
        return 1, "", "HTTP 404: Not Found"

    def paths(self) -> list[str]:
        return [next(a for a in c if a.startswith(("repos/", "orgs/"))) for c in self.calls]


def run_main(routes: dict, *argv: str):
    """(the real exit code, stdout, stderr, the fake). The repo read answers `main` unless a test overrides it."""
    fake = FakeGh({"repos/o/r": (0, "main\n", ""), **routes})
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(co, "run_gh", fake), redirect_stdout(out), redirect_stderr(err):
        try:
            rc = co.main(list(argv))
        except SystemExit as e:
            rc = 0 if e.code is None else e.code if isinstance(e.code, int) else 1
    return rc, out.getvalue(), err.getvalue(), fake


SMALL = "* @octocat\n/src/ @example/core\n"
PR_INFO = "hubot\nu:octocat\nt:stale-team\n"


class ResolveCli(unittest.TestCase):
    def test_paths_prints_lines_and_exits_0(self):
        rc, out, _, _ = run_main({"contents/.github/CODEOWNERS": (0, SMALL, "")},
                                 "resolve", "o/r", "--paths", "src/a.py", "README.md")
        self.assertEqual((rc, out), (0, "team:core\nuser:octocat\n"))

    def test_no_match_exits_3_with_nothing_on_stdout(self):
        rc, out, err, _ = run_main({"contents/.github/CODEOWNERS": (0, "/src/ @example/core\n", "")},
                                   "resolve", "o/r", "--paths", "README.md")
        self.assertEqual((rc, out), (3, ""))
        self.assertIn("nothing to request", err)

    def test_location_order_and_404_fallthrough(self):
        rc, out, _, fake = run_main({"contents/docs/CODEOWNERS": (0, SMALL, "")},
                                    "resolve", "o/r", "--paths", "src/a.py")
        self.assertEqual((rc, out), (0, "team:core\n"))
        tried = [c.split("contents/")[1] for c in fake.paths() if "contents/" in c]
        self.assertEqual(tried, [".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"])
        self.assertEqual(fake.paths()[0], "repos/o/r")  # the readability gate runs first

    def test_first_location_found_wins(self):
        rc, out, _, fake = run_main({"contents/.github/CODEOWNERS": (0, "* @example/first\n", ""),
                                     "contents/CODEOWNERS": (0, "* @example/second\n", "")},
                                    "resolve", "o/r", "--paths", "x")
        self.assertEqual(out, "team:first\n")
        self.assertEqual([c for c in fake.paths() if "contents/" in c], ["repos/o/r/contents/.github/CODEOWNERS"])

    def test_no_file_anywhere_exits_3(self):
        rc, out, err, _ = run_main({}, "resolve", "o/r", "--paths", "x")
        self.assertEqual((rc, out), (3, ""))
        self.assertIn("no CODEOWNERS file", err)

    def test_a_real_gh_failure_is_not_no_owners(self):
        rc, out, err, _ = run_main({"contents/.github/CODEOWNERS": (1, "", "HTTP 502: Bad Gateway")},
                                   "resolve", "o/r", "--paths", "x")
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("FAIL", err)
        self.assertIn("502", err)

    def test_an_unreadable_repo_is_a_fail_not_no_codeowners(self):
        rc, out, err, fake = run_main({"repos/o/r": (1, "", "gh: Not Found (HTTP 404)")}, "resolve", "o/r", "--paths", "x")
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("repo not readable", err)
        self.assertNotIn("no CODEOWNERS file", err)
        self.assertFalse(any("contents/" in c for c in fake.paths()))

    def test_an_unreadable_pr_is_a_fail_not_no_codeowners(self):
        for cmd in ("resolve", "check"):
            rc, out, err, _ = run_main({"pulls/9": (1, "", "gh: Not Found (HTTP 404)")}, cmd, "o/r", "--pr", "9")
            self.assertEqual((rc, out), (2, ""), cmd)
            self.assertIn("not readable", err)

    def test_paths_reads_the_default_branch(self):
        rc, _, _, fake = run_main({"contents/.github/CODEOWNERS": (0, SMALL, "")}, "resolve", "o/r", "--paths", "x")
        self.assertEqual(rc, 0)
        self.assertTrue(all("?ref=" not in c for c in fake.paths()))

    def test_pr_reads_codeowners_on_the_base_branch(self):
        routes = {"contents/.github/CODEOWNERS": (0, "* @example/core\n", ""), "pulls/9/files": (0, "a\n", ""),
                  "pulls/9": (0, "hubot\nb:release/1.x\n", "")}
        rc, out, _, fake = run_main(routes, "resolve", "o/r", "--pr", "9")
        self.assertEqual((rc, out), (0, "team:core\n"))
        self.assertIn("repos/o/r/contents/.github/CODEOWNERS?ref=release%2F1.x", fake.paths())
        self.assertNotIn("repos/o/r", fake.paths())  # --pr: the PR read is the gate

    def test_no_file_on_the_base_branch_names_it(self):
        routes = {"pulls/9": (0, "hubot\nb:release\n", "")}
        rc, out, err, _ = run_main(routes, "resolve", "o/r", "--pr", "9")
        self.assertEqual((rc, out), (3, ""))
        self.assertIn("base branch release", err)

    def test_pr_uses_the_files_with_rename_sources_and_drops_the_author(self):
        routes = {"contents/.github/CODEOWNERS": (0, "* @octocat\n/old/ @example/old-team\n/src/ @hubot\n", ""),
                  "pulls/9/files": (0, "src/new.py\nold/was.py\n", ""),
                  "pulls/9": (0, "hubot\n", "")}
        rc, out, err, fake = run_main(routes, "resolve", "o/r", "--pr", "9")
        self.assertEqual((rc, out), (0, "team:old-team\n"))
        self.assertIn("PR author", err)
        files_call = next(c for c in fake.calls if c[-3].endswith("/files") or any(x.endswith("/files") for x in c))
        self.assertIn("--paginate", files_call)

    def test_pr_whose_only_owner_is_its_author_exits_3(self):
        routes = {"contents/.github/CODEOWNERS": (0, "* @hubot\n", ""), "pulls/9/files": (0, "a\n", ""),
                  "pulls/9": (0, "hubot\n", "")}
        rc, out, _, _ = run_main(routes, "resolve", "o/r", "--pr", "9")
        self.assertEqual((rc, out), (3, ""))

    def test_pr_and_paths_are_exclusive_and_one_is_required(self):
        for argv in (("resolve", "o/r"), ("resolve", "o/r", "--pr", "1", "--paths", "x")):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
                co.main(list(argv))
            self.assertEqual(cm.exception.code, 2)


class CheckCli(unittest.TestCase):
    ROUTES = {"contents/.github/CODEOWNERS": (0, "/src/ @example/core @octocat\n", ""),
              "pulls/9/files": (0, "src/a.py\n", ""), "pulls/9/reviews": (0, "", "")}

    def routes(self, info: str, reviews: str = "", **extra) -> dict:
        return {**self.ROUTES, "pulls/9": (0, info, ""), "pulls/9/reviews": (0, reviews, ""), **extra}

    def test_unchanged_set_is_one_ok_line(self):
        rc, out, _, _ = run_main(self.routes("hubot\nu:octocat\nt:core\n"), "check", "o/r", "--pr", "9")
        self.assertEqual((rc, out), (0, "OWNERS OK team:core user:octocat\n"))

    def test_missing_owners_are_one_line_exit_3_and_extra_requests_only_a_note(self):
        rc, out, err, _ = run_main(self.routes("hubot\nt:stale-team\nu:by-hand\n"), "check", "o/r", "--pr", "9")
        self.assertEqual((rc, out), (3, "OWNERS CHANGED +team:core +user:octocat\n"))
        self.assertIn("note: requested but owning no changed path (left as is): team:stale-team user:by-hand", err)
        self.assertEqual(err.count("note:"), 1)

    def test_extra_requests_alone_are_ok(self):
        rc, out, err, _ = run_main(self.routes("hubot\nt:core\nu:octocat\nu:by-hand\n"), "check", "o/r", "--pr", "9")
        self.assertEqual((rc, out), (0, "OWNERS OK team:core user:octocat\n"))
        self.assertIn("user:by-hand", err)

    def test_a_reviewed_user_and_an_ignored_bot_are_not_changes(self):
        rc, out, err, _ = run_main(self.routes("hubot\nt:core\nu:review-bot\n", reviews="octocat\n"),
                                   "check", "o/r", "--pr", "9", "--ignore", "review-bot")
        self.assertEqual((rc, out), (0, "OWNERS OK team:core user:octocat\n"))
        self.assertNotIn("note:", err)

    def test_a_team_member_review_satisfies_the_dropped_team(self):
        routes = self.routes("hubot\n", reviews="octocat\nmona\n",
                             **{"orgs/o/teams/core/members": (0, "mona\nother\n", "")})
        rc, out, _, _ = run_main(routes, "check", "o/r", "--pr", "9")
        self.assertEqual((rc, out), (0, "OWNERS OK team:core user:octocat\n"))

    def test_a_non_member_review_does_not_satisfy_the_team(self):
        routes = self.routes("hubot\n", reviews="octocat\nstranger\n",
                             **{"orgs/o/teams/core/members": (0, "mona\n", "")})
        rc, out, _, _ = run_main(routes, "check", "o/r", "--pr", "9")
        self.assertEqual((rc, out), (3, "OWNERS CHANGED +team:core\n"))

    def test_unreadable_membership_falls_back_to_ever_requested_plus_a_review(self):
        timeline = {"issues/9/timeline": (0, "core\n", "")}
        forbidden = {"orgs/o/teams/core/members": (1, "", "HTTP 403: Forbidden")}
        rc, out, err, _ = run_main(self.routes("hubot\n", reviews="octocat\n", **forbidden, **timeline),
                                   "check", "o/r", "--pr", "9")
        self.assertEqual((rc, out), (0, "OWNERS OK team:core user:octocat\n"))
        self.assertIn("fallback", err)
        # never requested → the fallback does not apply; a review by the ignored bot alone does not count either
        for reviews, tl, ignore in (("octocat\n", "", ()), ("review-bot\n", "core\n", ("--ignore", "review-bot"))):
            routes = self.routes("hubot\nu:octocat\n", reviews=reviews, **forbidden,
                                 **{"issues/9/timeline": (0, tl, "")})
            rc, out, _, _ = run_main(routes, "check", "o/r", "--pr", "9", *ignore)
            self.assertEqual((rc, out), (3, "OWNERS CHANGED +team:core\n"), reviews)

    def test_a_failed_membership_read_is_a_fail(self):
        routes = self.routes("hubot\n", reviews="mona\n", **{"orgs/o/teams/core/members": (1, "", "HTTP 502: Bad Gateway")})
        rc, out, err, _ = run_main(routes, "check", "o/r", "--pr", "9")
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("FAIL", err)

    def test_the_author_own_review_satisfies_nothing(self):
        routes = self.routes("hubot\n", reviews="hubot\n")
        rc, out, _, fake = run_main(routes, "check", "o/r", "--pr", "9")
        self.assertEqual((rc, out), (3, "OWNERS CHANGED +team:core +user:octocat\n"))
        self.assertFalse(any(c.startswith("orgs/") for c in fake.paths()))

    def test_no_codeowners_and_no_requests_is_ok_none(self):
        routes = {"pulls/9": (0, "hubot\n", ""), "pulls/9/reviews": (0, "", "")}
        rc, out, _, _ = run_main(routes, "check", "o/r", "--pr", "9")
        self.assertEqual((rc, out), (0, "OWNERS OK none\n"))


if __name__ == "__main__":
    unittest.main()
