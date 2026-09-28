"""The repo has the community files GitHub looks for, and no doc contradicts the LICENSE that exists:
`SECURITY.md` (a private report channel for a kit whose scripts handle tokens) and `CODE_OF_CONDUCT.md`
(Contributor Covenant) exist at the kit root, and `docs/authoring.md` / `docs/contributing.md` no longer say
the repository has no licence yet — it does (`LICENSE`, MIT). Stdlib unittest.
Run: make -C $BATON/context-db test."""
from __future__ import annotations
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parents[1]

STALE_PHRASES = ("once the repo has one", "once the repository has one")


class RootPolicyFiles(unittest.TestCase):
    def test_security_md_exists_and_names_a_private_report_channel(self):
        p = KIT / "SECURITY.md"
        self.assertTrue(p.is_file(), "SECURITY.md is missing at the kit root")
        text = p.read_text(encoding="utf-8")
        self.assertIn("Security Advisories", text)
        self.assertGreater(len(text), 200, "SECURITY.md looks like a stub, not a real policy")

    def test_code_of_conduct_exists_and_is_the_contributor_covenant(self):
        p = KIT / "CODE_OF_CONDUCT.md"
        self.assertTrue(p.is_file(), "CODE_OF_CONDUCT.md is missing at the kit root")
        text = p.read_text(encoding="utf-8")
        self.assertIn("Contributor Covenant", text)
        self.assertGreater(len(text), 500, "CODE_OF_CONDUCT.md looks like a stub, not the full text")

    def test_license_file_exists(self):
        # the fact both docs below now assume — if this ever goes missing, the docs need re-editing too
        self.assertTrue((KIT / "LICENSE").is_file())


class NoStaleNoLicenceClaim(unittest.TestCase):
    """docs/authoring.md and docs/contributing.md used to say `license` is "added once the repo(sitory) has
    one" — true when the kit had no LICENSE file, false since it does (README.md § License links it)."""

    def test_authoring_doc_does_not_claim_the_repo_has_no_licence(self):
        text = (KIT / "docs" / "authoring.md").read_text(encoding="utf-8")
        for phrase in STALE_PHRASES:
            self.assertNotIn(phrase, text, f"docs/authoring.md still says {phrase!r} — the repo has a LICENSE")

    def test_contributing_doc_does_not_claim_the_repo_has_no_licence(self):
        text = (KIT / "docs" / "contributing.md").read_text(encoding="utf-8")
        for phrase in STALE_PHRASES:
            self.assertNotIn(phrase, text, f"docs/contributing.md still says {phrase!r} — the repo has a LICENSE")

    def test_both_docs_still_mention_the_license_key_is_optional(self):
        # the fix should not overcorrect into claiming `license:` is required on every unit — it stays optional
        authoring = (KIT / "docs" / "authoring.md").read_text(encoding="utf-8")
        contributing = (KIT / "docs" / "contributing.md").read_text(encoding="utf-8")
        self.assertIn("MIT", authoring)
        self.assertIn("MIT", contributing)


if __name__ == "__main__":
    unittest.main()
