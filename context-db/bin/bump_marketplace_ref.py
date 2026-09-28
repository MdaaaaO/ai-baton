#!/usr/bin/env python3
"""bump_marketplace_ref.py — keep `.claude-plugin/marketplace.json`'s plugin source pinned to the
release conventional-release just cut.

conventional-release's `version-files` (`.conventional-release.toml`) can only write a top-level
`"version"` string in a JSON file — see `versionfiles.py` in the conventional-release package. The
marketplace entry's pin lives at `plugins[].source.ref`, a different field name, nested under
`source`, carrying the tag prefix ("v0.5.0", not "0.5.0"): a path and a shape that mechanism cannot
reach. So `workspace.mk`'s `kit_release` target runs this script, inside the release worktree, right
after conventional-release bumps `VERSION` / `plugin.json` and before it removes the worktree — the
ref lands as a second commit on the same release branch, which squash-merges into one `chore(release):`
commit with everything else. `kit_verify.py`'s `check_plugin_manifest` is the drift gate: it fails the
build if `ref` and `plugin.json`'s `version` ever disagree, whether this step ran or someone edited
either file by hand.

Usage: bump_marketplace_ref.py [--root DIR]   # default: the repo containing this file
Prints "unchanged" or "ref -> vX.Y.Z" and exits 0; exits 1 (message on stderr) when a manifest is
missing, unparsable, or the entry's source isn't already a pinned github source to fix in place.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Paths, not the PLUGIN_MANIFEST/MARKETPLACE_MANIFEST names kit_verify.py owns for the same two files
# (test_engine_hygiene.py's OneHome check keeps a shared string constant to one module) — importing
# kit_verify here would also pull in kb.py/leak_shapes.py/frontmatter.py for two path literals.
PLUGIN_MANIFEST_PATH = ".claude-plugin/plugin.json"
MARKETPLACE_MANIFEST_PATH = ".claude-plugin/marketplace.json"


def bump(root: Path) -> str:
    """Rewrite the marketplace entry's `source.ref` to `v<plugin.json version>`. Returns "unchanged"
    or "ref -> vX.Y.Z"; raises SystemExit(str) on a manifest shape it won't guess at."""
    plugin_path = root / PLUGIN_MANIFEST_PATH
    market_path = root / MARKETPLACE_MANIFEST_PATH
    plugin = json.loads(plugin_path.read_text(encoding="utf-8"))
    name, version = plugin.get("name"), plugin.get("version")
    if not isinstance(name, str) or not name:
        raise SystemExit(f"{plugin_path}: missing string \"name\"")
    if not isinstance(version, str) or not version:
        raise SystemExit(f"{plugin_path}: missing string \"version\"")
    market = json.loads(market_path.read_text(encoding="utf-8"))
    entries = market.get("plugins")
    if not isinstance(entries, list):
        raise SystemExit(f"{market_path}: no plugins[] array")
    entry = next((e for e in entries if isinstance(e, dict) and e.get("name") == name), None)
    if entry is None:
        raise SystemExit(f"{market_path}: no plugins[] entry named {name!r}")
    source = entry.get("source")
    if not isinstance(source, dict) or source.get("source") != "github" or not source.get("repo"):
        raise SystemExit(
            f'{market_path}: plugins[].source for {name!r} is not a pinned github source '
            f'(expected {{"source": "github", "repo": ..., "ref": ...}}) — this script only updates '
            f'an existing ref, it does not migrate a relative-path source'
        )
    target = f"v{version}"
    if source.get("ref") == target:
        return "unchanged"
    source["ref"] = target
    market_path.write_text(json.dumps(market, indent=2) + "\n", encoding="utf-8")
    return f"ref -> {target}"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[2]),
                     help="repo root holding .claude-plugin/ (default: this checkout)")
    a = ap.parse_args(argv)
    try:
        print(bump(Path(a.root)))
    except (OSError, json.JSONDecodeError) as e:
        print(f"bump_marketplace_ref: {e}", file=sys.stderr)
        return 1
    except SystemExit as e:
        print(f"bump_marketplace_ref: {e.code}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
