#!/usr/bin/env python3
"""release_manifest.py — build and check a release manifest: `commit <sha>` plus one sha256 line
per git-tracked file, so a release tag is a checkable trust point (docs/contributing.md § Releases).

`release.yml` calls `build` right after checking out the tagged commit, attests the manifest
(`actions/attest-build-provenance`), then uploads it as a release asset in the step that publishes the
Release — so a published Release never exists without its attestation. A user verifies the attestation
with `gh attestation verify <manifest> --repo <owner>/<repo> --signer-workflow
<owner>/<repo>/.github/workflows/release.yml`, which proves the manifest came from that workflow run;
`verify` here checks the manifest's own claims — that every hash still matches a checkout — a separate
question attestation alone doesn't answer.

Format (stable: `git ls-files` output sorted again, so two builds of the same tree are byte-identical):

    commit <sha>
    <sha256>  <path>
    <sha256>  <path>
    ...

Usage:
  release_manifest.py build [--root DIR] [--out FILE]        # write (default: stdout) the manifest for HEAD
  release_manifest.py verify MANIFEST [--root DIR]            # every line still matches the tree; prints each
                                                                # problem and exits 1, or "OK" and exits 0
  release_manifest.py verify MANIFEST --root DIR --no-git     # same, for a tree with no git checkout (a
                                                                # Claude Code plugin cache, a plain copy of a
                                                                # release) — see `verify_no_git()`

Stdlib only; `git` (ls-files, rev-parse) is the one external dependency, skipped entirely in `--no-git` mode.
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

COMMIT_PREFIX = "commit "


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                           check=True).stdout.strip()


def tracked_files(root: Path) -> list[str]:
    """Every git-tracked file under `root`, sorted — the manifest's own order never depends on the
    git version or locale `ls-files` ran under, only on this sort."""
    out = _git(root, "ls-files", "-z")
    return sorted(f for f in out.split("\0") if f)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build(root: Path, commit: str | None = None) -> str:
    """The manifest text for every tracked file under `root` at `commit` (default: HEAD)."""
    commit = commit or _git(root, "rev-parse", "HEAD")
    lines = [f"{COMMIT_PREFIX}{commit}"]
    lines.extend(f"{file_sha256(root / rel)}  {rel}" for rel in tracked_files(root))
    return "\n".join(lines) + "\n"


def parse(manifest_text: str) -> tuple[str, dict[str, str]]:
    """(commit, {path: sha256}) from manifest text. Raises ValueError on a malformed manifest — also on a path
    that is absolute or climbs out with `..`: a manifest only ever names files inside the tree it was built from."""
    lines = manifest_text.splitlines()
    if not lines or not lines[0].startswith(COMMIT_PREFIX):
        raise ValueError(f'first line is not "{COMMIT_PREFIX}<sha>"')
    commit = lines[0][len(COMMIT_PREFIX):].strip()
    if not commit:
        raise ValueError("commit line has no sha")
    files: dict[str, str] = {}
    for line in lines[1:]:
        if not line.strip():
            continue
        digest, sep, rel = line.partition("  ")
        if not sep:
            raise ValueError(f"malformed file line: {line!r}")
        if rel.startswith(("/", "\\")) or ".." in rel.replace("\\", "/").split("/") or (len(rel) > 1 and rel[1] == ":"):
            raise ValueError(f"path leaves the tree: {rel!r}")
        files[rel] = digest
    return commit, files


def verify(manifest_text: str, root: Path) -> list[str]:
    """Problems with `manifest_text` against the tree at `root`: a hash mismatch, a file the manifest
    lists that the tree doesn't have, a tracked file the manifest doesn't list, or a commit line that
    doesn't match the tree's HEAD. Empty when everything matches."""
    commit, files = parse(manifest_text)
    problems: list[str] = []
    current = set(tracked_files(root))
    listed = set(files)
    for rel in sorted(listed - current):
        problems.append(f"missing: {rel} (in the manifest, not in the tree)")
    for rel in sorted(current - listed):
        problems.append(f"untracked by the manifest: {rel}")
    for rel in sorted(listed & current):
        actual = file_sha256(root / rel)
        if actual != files[rel]:
            problems.append(f"hash mismatch: {rel}")
    head = _git(root, "rev-parse", "HEAD")
    if commit != head:
        problems.append(f"commit mismatch: manifest says {commit}, tree is at {head}")
    return problems


def verify_no_git(manifest_text: str, root: Path) -> tuple[list[str], list[str]]:
    """(problems, notes) against a tree with no git checkout at all — a Claude Code plugin cache (a plain
    copy of a release, `docs/packaging.md`), not a clone. `verify()` needs `git ls-files`/`rev-parse` for
    two things this can't do without a checkout: knowing which extra files were ever tracked, and reading
    the tree's own HEAD. So the two outcomes split differently here:

    - `problems` (a real failure, same as `verify()`): a manifest-listed file missing from `root`, or
      present with the wrong hash.
    - `notes` (never a failure): a file under `root` the manifest doesn't list — this mode cannot tell a
      hand edit from a file that was simply never tracked, so it is named, not failed on. Runtime noise
      (Python's `__pycache__/` and `*.pyc`, Claude Code's top-level `.in_use/` markers in a plugin cache) is
      skipped outright rather than listed. The commit is never
      checked (there is no `git rev-parse` to check it against) — the manifest's own `commit <sha>` line
      is named in a note instead, so a caller never mistakes silence for a check that ran."""
    commit, files = parse(manifest_text)
    problems: list[str] = []
    for rel in sorted(files):
        p = root / rel
        if not p.is_file():
            problems.append(f"missing: {rel} (in the manifest, not in the tree)")
            continue
        if file_sha256(p) != files[rel]:
            problems.append(f"hash mismatch: {rel}")
    listed = set(files)
    notes: list[str] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        parts = p.relative_to(root).parts
        if "__pycache__" in parts or parts[-1].endswith(".pyc") or parts[0] == ".in_use":
            continue
        rel = "/".join(parts)
        if rel not in listed:
            notes.append(f"untracked by the manifest: {rel}")
    notes.append(f"commit not checked (no git checkout here): manifest says {commit}")
    return problems, notes


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="write the manifest for the working tree's HEAD")
    b.add_argument("--root", default=".", help="repo root (default: cwd)")
    b.add_argument("--out", help="write to this file instead of stdout")

    v = sub.add_parser("verify", help="check a manifest file against the working tree")
    v.add_argument("manifest", help="path to a manifest file")
    v.add_argument("--root", default=".", help="repo root (default: cwd)")
    v.add_argument("--no-git", action="store_true",
                    help="verify a tree with no git checkout (e.g. a Claude Code plugin cache): skip the "
                         "commit check, and list files present but unlisted as notes instead of failures "
                         "(verify_no_git())")

    a = ap.parse_args(argv)
    root = Path(a.root)
    try:
        if a.cmd == "build":
            text = build(root)
            if a.out:
                Path(a.out).write_text(text, encoding="utf-8")
            else:
                sys.stdout.write(text)
            return 0
        manifest_text = Path(a.manifest).read_text(encoding="utf-8")
        if a.no_git:
            problems, notes = verify_no_git(manifest_text, root)
            for n in notes:
                print(f"release_manifest: note: {n}", file=sys.stderr)
        else:
            problems = verify(manifest_text, root)
        for p in problems:
            print(f"release_manifest: {p}", file=sys.stderr)
        if problems:
            return 1
        print("release_manifest: OK")
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as e:
        print(f"release_manifest: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
