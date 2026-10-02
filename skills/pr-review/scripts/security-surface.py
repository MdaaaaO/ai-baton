#!/usr/bin/env python3
"""security-surface.py <bundle-dir>
Deterministic security-surface classifier for the `--deep` review lens gate (owner decision, 2026-10-01:
a conditional fourth lens, not always-on). Reads the bundle `fetch-context.sh` writes under <bundle-dir> —
`bundle.json` (the changed-paths list) and `diff.patch` (the unified diff) — no model call, no network, no `gh`.
Prints exactly one line:
  SURFACE <reason>[, <reason>...]   - at least one gate below fired, in this fixed order
  NONE                              - none did
Reasons:
  - "CI workflow file" / "action definition" / "hook or permission settings" — a changed path (or, for
    a renamed file, the path it had before) matches the corresponding glob set (PATH_REASONS below),
    whatever the diff inside that file says.
  - "token or credential handling" / "input reaching a shell or eval" / "permission or grant changes" — a
    fixed pattern (LINE_PATTERNS below) matched on an ADDED line (a `diff.patch` `+` line, the `+++` file
    header excluded) of a path that is not itself a test-fixture path (TEST_FIXTURE_GLOBS). A pattern that
    appears only on a removed (`-`) line, or only under a test-fixture path, does not count.
The list leans towards firing: a false SURFACE costs one extra lens, a false NONE loses it.
`fetch-context.sh` leaves `diff.patch` empty when the whole-PR diff could not be fetched (a huge PR); the
added lines then come from the per-file patches the bundle names (`files[].diff`, under `diffs/`).
A `bundle.json` or `diff.patch` that is missing, unreadable or does not parse as expected is a setup
problem, never a silent NONE: one line on stderr, exit 2. The same holds when `diff.patch` is empty, a
file with added lines has no per-file patch to read (GitHub sends none for a large file) and nothing
that could be read fired: the unread lines may hold a surface, so the answer is exit 2, not NONE.
"""
import fnmatch
import json
import os
import re
import sys


def glob_any(path, globs):
    return any(fnmatch.fnmatch(path, g) or fnmatch.fnmatch(path, g.replace("**/", "")) for g in globs)


CI_WORKFLOW_GLOBS = [".github/workflows/**", ".gitlab-ci.yml", ".circleci/config.yml", ".circleci/**"]
ACTION_GLOBS = ["action.yml", "action.yaml", "**/action.yml", "**/action.yaml", ".github/actions/**"]
HOOK_PERM_GLOBS = ["hooks/**", "**/hooks/**", "hooks.json", "**/hooks.json", "settings.json",
                   "**/settings.json", "settings.local.json", "**/settings.local.json",
                   ".claude/settings*.json", "**/.claude/settings*.json"]

PATH_REASONS = [
    ("CI workflow file", CI_WORKFLOW_GLOBS),
    ("action definition", ACTION_GLOBS),
    ("hook or permission settings", HOOK_PERM_GLOBS),
]

# A pattern-matched path never counts a line reason — test fixtures, golden files and sample data
# routinely contain the shapes below on purpose.
TEST_FIXTURE_GLOBS = ["test/**", "tests/**", "**/test/**", "**/tests/**", "**/testdata/**",
                      "**/fixtures/**", "**/__fixtures__/**", "**/test_*.py", "**/*_test.py",
                      "**/*.test.*"]

LINE_PATTERNS = [
    ("token or credential handling", [
        # no \b in front: an env-var name carries the word after an underscore (`<PREFIX>_API_KEY=`)
        re.compile(r"(?i)(?<![A-Za-z0-9])(api[_-]?key|secret[_-]?key|access[_-]?key|auth[_-]?token|"
                   r"session[_-]?token|private[_-]?key|password|passwd|client[_-]?secret)\b[\"']?\s*[:=]"),
        re.compile(r"\b[A-Z][A-Z0-9_]*_(?:TOKEN|SECRET|PASSWORD)\b"),
        re.compile(r"\bgh\s+auth\s+token\b"),
        re.compile(r"(?i)\bAuthorization\b[\"'\]\s:=]*(?:Bearer|token|Basic)\b"),
        re.compile(r"\bsecrets\.[A-Za-z_][A-Za-z0-9_]*"),
    ]),
    ("input reaching a shell or eval", [
        re.compile(r"\beval\s*\("),
        re.compile(r"\beval\s+[\"']?\$"),
        # a line that starts with `|` is a table row, not a pipeline
        re.compile(r"^(?!\s*\|).*\|\s*(?:sudo\s+)?(?:ba|da|z)?sh\b"),
        re.compile(r"\bexec\s*\("),
        re.compile(r"\bos\.system\s*\("),
        re.compile(r"shell\s*=\s*True"),
    ]),
    ("permission or grant changes", [
        re.compile(r"(?i)\bpermissions\s*:\s*write"),
        re.compile(r"(?i)\bGRANT\s+ALL\b"),
        re.compile(r'(?i)"Effect"\s*:\s*"Allow"'),
        re.compile(r"\bchmod\s+(777|a\+rwx)\b"),
        re.compile(r"(?i)\bsudo\b"),
        re.compile(r"\b(?:permissionMode|bypassPermissions|dangerouslyDisableSandbox)\b"),
        re.compile(r"--dangerously-skip-permissions\b"),
        re.compile(r"^\s*(?:tools|allowed-tools|disallowedTools)\s*:"),
    ]),
]


class Unreadable(Exception):
    """A bundle.json / diff.patch that cannot be read or does not parse as expected."""


def load_bundle(bundle_dir):
    """Return (bundle dict, diff text, unread) under bundle_dir, or raise Unreadable with a one-line
    reason. `unread` names the files whose added lines are in neither `diff.patch` nor a per-file patch."""
    bundle_path = os.path.join(bundle_dir, "bundle.json")
    diff_path = os.path.join(bundle_dir, "diff.patch")
    try:
        with open(bundle_path, encoding="utf-8") as f:
            bundle = json.load(f)
    except FileNotFoundError:
        raise Unreadable(f"no bundle.json under {bundle_dir}")
    except (OSError, ValueError) as e:  # ValueError: bad JSON, or bytes that are not UTF-8
        raise Unreadable(f"bundle.json under {bundle_dir} is unreadable: {e}")
    if not isinstance(bundle, dict) or not isinstance(bundle.get("files"), list):
        raise Unreadable(f"bundle.json under {bundle_dir} has no files list")
    for f in bundle["files"]:
        if not (isinstance(f, dict) and isinstance(f.get("filename"), str) and f["filename"]
                and isinstance(f.get("diff") or "", str) and isinstance(f.get("previous_filename") or "", str)):
            raise Unreadable(f"bundle.json under {bundle_dir} has a malformed files entry")
    try:
        with open(diff_path, encoding="utf-8") as f:
            diff_text = f.read()
    except FileNotFoundError:
        raise Unreadable(f"no diff.patch under {bundle_dir}")
    except (OSError, ValueError) as e:
        raise Unreadable(f"diff.patch under {bundle_dir} is unreadable: {e}")
    unread = []
    if not diff_text.strip():
        diff_text, unread = per_file_diff(bundle, bundle_dir)
    return bundle, diff_text, unread


def per_file_diff(bundle, bundle_dir):
    """(diff, unread) for a bundle whose `diff.patch` is empty: the unified diff rebuilt from the
    bundle's per-file patches, and the files that have added lines but no patch that could be read."""
    parts, unread = [], []
    for f in bundle["files"]:
        name, patch = f["filename"], None
        if f.get("diff"):
            try:
                with open(os.path.join(bundle_dir, f["diff"]), encoding="utf-8") as fh:
                    patch = fh.read()
            except (OSError, ValueError):
                pass
        if patch is not None:
            parts.append(f"diff --git a/{name} b/{name}\n--- a/{name}\n+++ b/{name}\n{patch}\n")
        elif f.get("additions", f.get("changes")):
            unread.append(name)
    return "".join(parts), unread


def added_lines_by_file(diff_text):
    """Yield (path, line_text) for every added content line in a unified diff. The `+++ b/<path>` file
    header is read only between a `diff --git` line and the file's first hunk: inside a hunk, a line that
    starts with `+++ ` is an added line whose own text starts with `++`, never a new file."""
    path, in_header = None, False
    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            path, in_header = None, True
        elif in_header:
            if raw.startswith("+++ "):
                p = raw[4:].strip()
                path = p[2:] if p.startswith("b/") else p
            elif raw.startswith("@@"):
                in_header = False
        elif raw.startswith("+"):
            yield path, raw[1:]


def changed_paths(bundle):
    """Every changed path, and for a renamed file the path it was renamed from."""
    return [p for f in bundle["files"] for p in (f["filename"], f.get("previous_filename")) if p]


def classify(bundle, diff_text):
    """The fired reasons, in PATH_REASONS + LINE_PATTERNS order."""
    hit = {reason: False for reason, _ in PATH_REASONS + LINE_PATTERNS}
    for reason, globs in PATH_REASONS:
        hit[reason] = any(glob_any(p, globs) for p in changed_paths(bundle))
    for path, line in added_lines_by_file(diff_text):
        if path and glob_any(path, TEST_FIXTURE_GLOBS):
            continue
        for reason, patterns in LINE_PATTERNS:
            if not hit[reason] and any(p.search(line) for p in patterns):
                hit[reason] = True
    return [reason for reason, _ in PATH_REASONS + LINE_PATTERNS if hit[reason]]


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    try:
        bundle, diff_text, unread = load_bundle(sys.argv[1])
    except Unreadable as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
    reasons = classify(bundle, diff_text)
    if not reasons and unread:
        print(f"error: diff.patch under {sys.argv[1]} is empty and {len(unread)} file(s) with added lines "
              f"have no readable per-file patch (first: {unread[0]})", file=sys.stderr)
        sys.exit(2)
    print(("SURFACE " + ", ".join(reasons)) if reasons else "NONE")


if __name__ == "__main__":
    main()
