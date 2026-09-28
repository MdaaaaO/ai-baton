#!/bin/sh
# shellcheck disable=SC2034  # OUT / RC are read inside the quoted `check` expressions, which shellcheck cannot see
# setup_sh_scenarios.sh — bats-free scenario test for setup.sh. `sh -e` and POSIX tools only.
#
#   sh context-db/tests/setup_sh_scenarios.sh            # from the kit root; exit 0 = every scenario passed
#
# Each scenario runs setup.sh from a COPY of the kit (no .git, so the hooks step is a no-op) with HOME and PROJECTS in
# a scratch tree, and asserts on the resulting files. `make -C .claude/context-db test` runs it through
# tests/test_setup_sh.py.
set -eu

KIT="$(cd "$(dirname "$0")/../.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
fails=0

# every git call below (setup.sh's own hooks/git-tracked-plugin checks, and the fixture git repos this script
# builds by hand) must ignore the developer's global/system git config: a host with commit.gpgsign=true and no
# signer configured must not fail these scenarios.
. "$(dirname "$0")/hermetic.sh"
hermetic_git_env "$WORK"

pass() { echo "  ok   $1"; }
fail() { echo "  FAIL $1" >&2; fails=$((fails + 1)); }
check() { if eval "$2"; then pass "$1"; else fail "$1"; fi; }

# a kit copy without .git: setup.sh reads templates from it and skips the git-hooks step
KITCOPY="$WORK/kit"
mkdir -p "$KITCOPY"
(cd "$KIT" && find . -path ./.git -prune -o -type f -print | sed 's|^\./||' | while IFS= read -r f; do
  mkdir -p "$KITCOPY/$(dirname "$f")"; cp "$KIT/$f" "$KITCOPY/$f"; done)

# scenario N: a fresh workspace tree with its own HOME; prints the paths setup.sh derives
scenario() {  # scenario <name> → sets WS (workspace root), HOME_DIR, MEM (harness memory dir), DUR (durable memory
              # dir), MIG (where a real-dir migration backs up the pre-migration dir, outside the memory tree)
  WS="$WORK/$1/ws"; HOME_DIR="$WORK/$1/home"
  mkdir -p "$WS" "$HOME_DIR"
  rm -rf "$WS/.claude"; cp -R "$KITCOPY" "$WS/.claude"
  SLUG="$(printf '%s' "$WS" | sed 's#/#-#g')"
  MEM="$HOME_DIR/.claude/projects/$SLUG/memory"; DUR="$WS/.context/memory"; MIG="$WS/.context/state/memory-migrated"
}
recorded() {  # recorded <ws> → the kit.install_mode setup.sh wrote to <ws>'s env store, "" when none (#34)
  python3 -c 'import json, sys; print((json.load(open(sys.argv[1])).get("kit") or {}).get("install_mode", ""))' "$1/.context/reference/env/config.json"
}
run_setup() {  # run_setup [args…] → stdout+stderr in $OUT, exit status in $RC
  set +e
  OUT="$(cd "$WS" && HOME="$HOME_DIR" PROJECTS="$WS" sh "$WS/.claude/setup.sh" "$@" 2>&1)"; RC=$?
  set -e
}

echo "== 1. fresh machine: symlink created, store and files seeded =="
scenario fresh
run_setup
check "exit 0" '[ "$RC" -eq 0 ]'
check "memory symlink -> durable" '[ -L "$MEM" ] && [ "$(cd "$MEM" && pwd -P)" = "$(cd "$DUR" && pwd -P)" ]'
check "blank env store created" '[ -f "$WS/.context/reference/env/config.json" ]'
check "CLAUDE.md, Makefile, environment.md seeded" '[ -f "$WS/CLAUDE.md" ] && [ -f "$WS/Makefile" ] && [ -f "$WS/.context/reference/environment.md" ]'
check "idempotent: second run exits 0 and keeps the link" 'run_setup; [ "$RC" -eq 0 ] && [ -L "$MEM" ] && printf "%s" "$OUT" | grep -q "symlink present -> durable"'

echo "== 2. real memory dir: every note migrated and verified before the dir goes =="
scenario migrate
mkdir -p "$MEM/sub" "$DUR"
printf 'fresh note\n' > "$MEM/new.md"
printf 'nested note\n' > "$MEM/sub/deep.md"
printf 'with space\n' > "$MEM/has space.md"
printf 'same on both sides\n' > "$MEM/same.md"
printf 'same on both sides\n' > "$DUR/same.md"
printf 'harness copy\n' > "$MEM/both.md"
printf 'durable copy\n' > "$DUR/both.md"
mkdir -p "$MEM/notes.v2" "$DUR/notes.v2"   # a dotted directory, and a conflicting note with NO extension of its own
printf 'harness dotted-dir note\n' > "$MEM/notes.v2/readme"
printf 'durable dotted-dir note\n' > "$DUR/notes.v2/readme"
run_setup
check "exit 0" '[ "$RC" -eq 0 ]'
check "new notes arrive in durable (incl. nested and a name with a space)" '[ "$(cat "$DUR/new.md")" = "fresh note" ] && [ "$(cat "$DUR/sub/deep.md")" = "nested note" ] && [ "$(cat "$DUR/has space.md")" = "with space" ]'
check "an identical note on both sides is just kept, no conflict copy" '[ "$(cat "$DUR/same.md")" = "same on both sides" ] && [ ! -e "$DUR/same.from-harness.md" ]'
check "a differing note keeps the durable copy under its name" '[ "$(cat "$DUR/both.md")" = "durable copy" ]'
check "the differing harness copy is saved alongside it, not dropped" '[ -f "$DUR/both.from-harness.md" ] && [ "$(cat "$DUR/both.from-harness.md")" = "harness copy" ]'
check "conflict_name splits only the basename: a dotted directory is left alone" '[ -f "$DUR/notes.v2/readme.from-harness" ] && [ "$(cat "$DUR/notes.v2/readme.from-harness")" = "harness dotted-dir note" ] && [ ! -e "$DUR/notes.from-harness.v2/readme" ]'
check "harness dir replaced by the symlink" '[ -L "$MEM" ]'
check "report counts 6 verified, 3 new, 1 already durable, 2 conflicting" 'printf "%s" "$OUT" | grep -q "6 files verified, 3 new, 1 already durable, 2 conflicting"'
check "summary names the conflicting file" 'printf "%s" "$OUT" | grep -q "both.md -> both.from-harness.md"'
check "no staging dir left behind" '[ -z "$(ls -d "$WS/.context/.memory-migrate."* 2>/dev/null)" ]'
check "the pre-migration dir is kept as a recoverable backup under .context/state/, outside the memory tree" 'b="$(ls -d "$MIG"/* 2>/dev/null | head -1)"; [ -n "$b" ] && [ "$(cat "$b/both.md")" = "harness copy" ] && [ "$(cat "$b/new.md")" = "fresh note" ]'
check "the memory dir itself holds no migration backup (the harness would read it back as a memory)" '[ -z "$(find "$DUR" -maxdepth 1 -name ".migrated-*" 2>/dev/null)" ]'

echo "== 2b. a second migration hitting the same conflict does not overwrite the first from-harness copy =="
rm -f "$MEM"   # setup.sh replaced the real dir with a symlink; recreate a real dir (a sandbox recreate does this)
mkdir -p "$MEM"
printf 'harness copy v2\n' > "$MEM/both.md"
run_setup
check "exit 0" '[ "$RC" -eq 0 ]'
check "the durable copy is still untouched" '[ "$(cat "$DUR/both.md")" = "durable copy" ]'
check "the first from-harness copy from scenario 2 survives untouched" '[ "$(cat "$DUR/both.from-harness.md")" = "harness copy" ]'
check "the new conflict gets a counted-up name instead of overwriting it" '[ "$(cat "$DUR/both.from-harness.2.md")" = "harness copy v2" ]'
check "the summary names the counted-up file" 'printf "%s" "$OUT" | grep -q "both.md -> both.from-harness.2.md"'

echo "== 2c. a harness dir that already holds an unclaimed .from-harness note does not lose it either =="
rm -f "$MEM"
mkdir -p "$MEM"
printf 'harness copy v3\n' > "$MEM/both.md"
printf 'a note that already used the from-harness name\n' > "$MEM/both.from-harness.3.md"
run_setup
check "exit 0" '[ "$RC" -eq 0 ]'
check "the pre-existing from-harness.3 note (new to durable) is kept under its own name" '[ "$(cat "$DUR/both.from-harness.3.md")" = "a note that already used the from-harness name" ]'
check "the new conflict counts past it rather than colliding" '[ "$(cat "$DUR/both.from-harness.4.md")" = "harness copy v3" ]'

echo "== 2d. migration backups are pruned to the newest 3 =="
i=1
while [ "$i" -le 2 ]; do
  rm -f "$MEM"; mkdir -p "$MEM"; printf 'filler %s\n' "$i" > "$MEM/filler$i.md"
  run_setup
  i=$((i + 1))
done
check "exit 0 on the last run" '[ "$RC" -eq 0 ]'
check "5 migrations so far (2, 2b, 2c and this loop), still only 3 backups kept" '[ "$(ls -d "$MIG"/* 2>/dev/null | wc -l | tr -d " ")" -eq 3 ]'
check "the run that crossed the limit says so" 'printf "%s" "$OUT" | grep -q "pruned 1 old migration backup"'
mkdir -p "$MIG/someone-else"; : > "$MIG/someone-else/keep.txt"
rm -f "$MEM"; mkdir -p "$MEM"; printf 'filler x\n' > "$MEM/fillerx.md"
run_setup
check "a stray dir under the backups is never deleted" '[ -f "$MIG/someone-else/keep.txt" ]'
check "a stray dir takes no backup slot: still 3 backup-named dirs" '[ "$(ls -d "$MIG"/[0-9]* 2>/dev/null | wc -l | tr -d " ")" -eq 3 ]'

echo "== 3. a failed copy leaves the original untouched and exits non-zero =="
scenario failcopy
mkdir -p "$MEM" "$WORK/fakebin"
printf 'precious\n' > "$MEM/note.md"
printf '#!/bin/sh\nexit 1\n' > "$WORK/fakebin/cmp"; chmod +x "$WORK/fakebin/cmp"   # every byte-verify fails
set +e
OUT="$(cd "$WS" && HOME="$HOME_DIR" PROJECTS="$WS" PATH="$WORK/fakebin:$PATH" sh "$WS/.claude/setup.sh" 2>&1)"; RC=$?
set -e
check "exit non-zero" '[ "$RC" -ne 0 ]'
check "message names the incomplete migration" 'printf "%s" "$OUT" | grep -q "memory migration incomplete (0 of 1 files copied)"'
check "original notes untouched, no symlink" '[ -d "$MEM" ] && [ ! -L "$MEM" ] && [ "$(cat "$MEM/note.md")" = "precious" ]'
check "staging dir removed" '[ -z "$(ls -d "$WS/.context/.memory-migrate."* 2>/dev/null)" ]'

echo "== 4. a symlink to another target is reported and left alone; --force repoints =="
scenario elsewhere
mkdir -p "$(dirname "$MEM")" "$WORK/elsewhere/notes"
printf 'orphan?\n' > "$WORK/elsewhere/notes/x.md"
ln -s "$WORK/elsewhere/notes" "$MEM"
run_setup
check "exit 0 (a warning, not a failure)" '[ "$RC" -eq 0 ]'
check "warning names the other target" 'printf "%s" "$OUT" | grep -q "WARNING: memory symlink points to"'
check "link untouched" '[ "$(cd "$MEM" && pwd -P)" = "$(cd "$WORK/elsewhere/notes" && pwd -P)" ]'
run_setup --force
check "--force repoints to durable" '[ "$RC" -eq 0 ] && [ "$(cd "$MEM" && pwd -P)" = "$(cd "$DUR" && pwd -P)" ] && printf "%s" "$OUT" | grep -q "repointed"'
check "unknown argument is exit 2" 'run_setup --bogus; [ "$RC" -eq 2 ]'
check "--help prints the whole header incl. the PROJECTS override note" 'run_setup --help; [ "$RC" -eq 0 ] && printf "%s" "$OUT" | grep -q "override with PROJECTS=" && ! printf "%s" "$OUT" | grep -q "^set -eu"'

echo "== 4b. a dangling symlink (target gone) is repointed without --force =="
scenario dangling
mkdir -p "$(dirname "$MEM")"
ln -s "$WORK/dangling/no-such-target" "$MEM"
run_setup
check "exit 0" '[ "$RC" -eq 0 ]'
check "repointed to durable and says it was dangling" '[ "$(cd "$MEM" && pwd -P)" = "$(cd "$DUR" && pwd -P)" ] && printf "%s" "$OUT" | grep -q "symlink was dangling"'

echo "== 4d. a link whose target exists but cannot be entered is NOT treated as dangling =="
scenario unenterable
mkdir -p "$(dirname "$MEM")" "$WORK/unenterable"
printf 'a file, not a dir\n' > "$WORK/unenterable/notes.txt"
ln -s "$WORK/unenterable/notes.txt" "$MEM"    # `cd` fails, yet something is there
run_setup
check "exit 0 with the warning, link left alone" '[ "$RC" -eq 0 ] && printf "%s" "$OUT" | grep -q "WARNING: memory symlink points to" && [ "$(readlink "$MEM")" = "$WORK/unenterable/notes.txt" ]'
check "not reported as dangling" '! printf "%s" "$OUT" | grep -q "was dangling"'

echo "== 4c. a harness dir holding a non-regular entry is refused, untouched =="
scenario nonregular
mkdir -p "$MEM"
printf 'note\n' > "$MEM/a.md"
ln -s "$MEM/a.md" "$MEM/alias.md"
run_setup
check "exit non-zero" '[ "$RC" -ne 0 ]'
check "names the entry and leaves the dir" 'printf "%s" "$OUT" | grep -q "not regular files" && printf "%s" "$OUT" | grep -q "alias.md" && [ -d "$MEM" ] && [ ! -L "$MEM" ] && [ -f "$MEM/a.md" ]'
check "no staging dir created" '[ -z "$(ls -d "$WS/.context/.memory-migrate."* 2>/dev/null)" ]'

echo "== 5. identity keys read with POSIX grep =="
scenario identity
printf '{"env": {"WORKSPACE_USER": "Some One", "WORKSPACE_GITHUB_LOGIN": "", "WORKSPACE_TZ": "UTC"}}\n' > "$WS/.claude/settings.local.json"
run_setup
check "set / NOT SET reported per key" 'printf "%s" "$OUT" | grep -q "WORKSPACE_USER: set" && printf "%s" "$OUT" | grep -q "WORKSPACE_GITHUB_LOGIN: NOT SET" && printf "%s" "$OUT" | grep -q "WORKSPACE_TZ: set"'

echo "== 6. a broken env store fails the step loudly =="
scenario broken
mkdir -p "$WS/.context/reference/env"
printf '{not json' > "$WS/.context/reference/env/config.json"
run_setup
check "exit non-zero" '[ "$RC" -ne 0 ]'
check "one-line cause, no traceback" 'printf "%s" "$OUT" | grep -q "invalid JSON" && ! printf "%s" "$OUT" | grep -q Traceback'

echo "== 7. housekeeping: the kit's __pycache__ dirs and empty leftover dirs are swept, the workspace is not touched =="
scenario leftover
mkdir -p "$WS/.claude/old-template/templates" "$WS/.claude/skills/gone-skill/__pycache__" "$WS/keep-empty"
printf 'x' > "$WS/.claude/skills/gone-skill/__pycache__/x.pyc"
run_setup
check "exit 0" '[ "$RC" -eq 0 ]'
check "empty leftover dirs and the bytecode cache are gone" '[ ! -e "$WS/.claude/old-template" ] && [ ! -e "$WS/.claude/skills/gone-skill" ] && [ -z "$(find "$WS/.claude" -name __pycache__ 2>/dev/null)" ]'
check "an empty dir in the workspace itself stays" '[ -d "$WS/keep-empty" ]'

echo "== 8. --personal: the zero-config GitHub-only path fills the store and the identity files, no questions =="
scenario personal
mkdir -p "$WORK/fakegh"
cat > "$WORK/fakegh/gh" <<'GH'
#!/bin/sh
case "$*" in
  *"api user"*) echo '{"login":"octo-tester","name":"Octo Tester"}' ;;
  *"repo list"*) printf 'octo-tester/widgets\n' ;;
  *"auth token"*) echo placeholder-token ;;
  *) exit 1 ;;
esac
GH
chmod +x "$WORK/fakegh/gh"
set +e
OUT="$(cd "$WS" && HOME="$HOME_DIR" PROJECTS="$WS" PATH="$WORK/fakegh:$PATH" sh "$WS/.claude/setup.sh" --personal 2>&1)"; RC=$?
set -e
check "exit 0" '[ "$RC" -eq 0 ]'
check "tracker is GitHub issues, environment named after the login, every system off" 'python3 -c "
import json,sys; c=json.load(open(sys.argv[1])); sys.exit(0 if c[\"tracker\"][\"kind\"]==\"github\" and c[\"environment\"]==\"octo-tester\" and not any(c[\"systems\"].values()) else 1)" "$WS/.context/reference/env/config.json"'
check "identity written into settings.local.json and the pr-review config" 'grep -q "\"WORKSPACE_GITHUB_LOGIN\": \"octo-tester\"" "$WS/.claude/settings.local.json" && grep -q "\"login\": \"octo-tester\"" "$WS/.context/state/pr-review/config.json"'
check "report says every identity key is set" 'printf "%s" "$OUT" | grep -q "WORKSPACE_GITHUB_LOGIN: set" && ! printf "%s" "$OUT" | grep -q "NOT SET"'
check "kit-verify accepts the store it wrote" '(cd "$WS/.claude" && CONTEXT_ROOT="$WS/.context" python3 context-db/bin/kit_verify.py >/dev/null 2>&1)'
check "idempotent: a second --personal run rewrites nothing" 'OUT="$(cd "$WS" && HOME="$HOME_DIR" PROJECTS="$WS" PATH="$WORK/fakegh:$PATH" sh "$WS/.claude/setup.sh" --personal 2>&1)" && printf "%s" "$OUT" | grep -q "environment already named"'
check "--help names --personal" 'run_setup --help; printf "%s" "$OUT" | grep -q -- "--personal"'

echo "== 9. run from a plugin-style dir (not .claude/): the workspace root is CLAUDE_PROJECT_DIR, else the cwd =="
scenario plugin
mv "$WS/.claude" "$WORK/plugin/cache-kit" 2>/dev/null || { mkdir -p "$WORK/plugin"; mv "$WS/.claude" "$WORK/plugin/cache-kit"; }
touch "$WORK/plugin/stamp"; sleep 1  # anything below the plugin dir newer than this was written by setup.sh
set +e
OUT="$(cd "$WS" && HOME="$HOME_DIR" PROJECTS='' CLAUDE_PROJECT_DIR='' sh "$WORK/plugin/cache-kit/setup.sh" 2>&1)"; RC=$?
set -e
check "exit 0" '[ "$RC" -eq 0 ]'
check "store and files land in the cwd, not beside the plugin" '[ -f "$WS/.context/reference/env/config.json" ] && [ -f "$WS/CLAUDE.md" ] && [ ! -e "$WORK/plugin/.context" ]'
check "plugin path: the install mode is recorded as plugin (#34)" '[ "$(recorded "$WS")" = plugin ]'
check "plugin path: CLAUDE.md imports no .claude/WORKSPACE.md and no Makefile is seeded (#3)" '! grep -q "^@.claude/WORKSPACE.md" "$WS/CLAUDE.md" && grep -q "^@.context/reference/environment.md" "$WS/CLAUDE.md" && [ ! -e "$WS/Makefile" ] && [ ! -e "$WS/.CLAUDE.md.seed" ]'
printf 'include .claude/workspace.mk\n' > "$WS/Makefile"; printf '@.claude/WORKSPACE.md\n' >> "$WS/CLAUDE.md"
set +e; OUT="$(cd "$WS" && HOME="$HOME_DIR" PROJECTS='' CLAUDE_PROJECT_DIR='' sh "$WORK/plugin/cache-kit/setup.sh" 2>&1)"; RC=$?; set -e
check "plugin path: a dangling import/include is named, never added (#3)" '[ "$RC" -eq 0 ] && printf "%s" "$OUT" | grep -q "remove that line (the plugin" && printf "%s" "$OUT" | grep -q "every make fails: remove that line"'
mkdir -p "$WS/.claude"; printf 'rules\n' > "$WS/.claude/WORKSPACE.md"; printf 'include .claude/workspace.mk\n' > "$WS/Makefile"; : > "$WS/.claude/workspace.mk"
sed -i.bak '/^@\.claude\/WORKSPACE\.md$/d' "$WS/CLAUDE.md" && rm -f "$WS/CLAUDE.md.bak"
set +e; OUT="$(cd "$WS" && HOME="$HOME_DIR" PROJECTS='' CLAUDE_PROJECT_DIR='' sh "$WORK/plugin/cache-kit/setup.sh" --refresh-seeds 2>&1)"; RC=$?; set -e
check "plugin path: a .claude/ copy nobody imports is named, a working include is not 'nothing to include' (#3)" '[ "$RC" -eq 0 ] && printf "%s" "$OUT" | grep -q "loads nowhere" && printf "%s" "$OUT" | grep -q "includes .claude/workspace.mk (the file exists)"'
check "plugin path: --refresh-seeds never proposes the WORKSPACE.md import back (#3)" '! printf "%s" "$OUT" | grep -q "^ *+@.claude/WORKSPACE.md"'
rm -f "$WS/Makefile" "$WS/.claude/WORKSPACE.md" "$WS/.claude/workspace.mk"
# a git-tracked plugin dir (a marketplace checkout): CLAUDE.md's age check still reads the kit's history (#3)
git -C "$WORK/plugin/cache-kit" init -q && git -C "$WORK/plugin/cache-kit" add -A >/dev/null 2>&1 \
  && git -C "$WORK/plugin/cache-kit" -c user.name=t -c user.email=t commit -q -m seed >/dev/null 2>&1
python3 -c 'import os, sys; os.utime(sys.argv[1], (946684800, 946684800))' "$WS/CLAUDE.md"  # 2000-01-01
set +e; OUT="$(cd "$WS" && HOME="$HOME_DIR" PROJECTS='' CLAUDE_PROJECT_DIR='' sh "$WORK/plugin/cache-kit/setup.sh" --refresh-seeds 2>&1)"; RC=$?; set -e
check "git-tracked plugin dir: a stale CLAUDE.md is detected against the kit template, diffed against the trimmed one (#3)" '[ "$RC" -eq 0 ] && printf "%s" "$OUT" | grep -q "seed CLAUDE.md predates its template CLAUDE.example.md" && ! printf "%s" "$OUT" | grep -q "^ *+@.claude/WORKSPACE.md"'
check "a git checkout not named .claude is a dev-checkout, recorded as such, seeded like a plugin (#34)" '[ "$(recorded "$WS")" = dev-checkout ] && printf "%s" "$OUT" | grep -q "install mode: dev-checkout — recorded in kit.install_mode (was plugin)"'
rm -rf "$WORK/plugin/cache-kit/.git"
check "settings.local.json is seeded under <root>/.claude/, nothing is written below the plugin dir" '[ -f "$WS/.claude/settings.local.json" ] && [ ! -e "$WORK/plugin/cache-kit/settings.local.json" ] && [ -z "$(find "$WORK/plugin/cache-kit" -type f -newer "$WORK/plugin/stamp" -not -path "*/__pycache__*" 2>/dev/null)" ]'
mkdir -p "$WORK/plugin/proj"
set +e
OUT="$(cd "$WORK" && HOME="$HOME_DIR" PROJECTS='' CLAUDE_PROJECT_DIR="$WORK/plugin/proj" sh "$WORK/plugin/cache-kit/setup.sh" 2>&1)"; RC=$?
set -e
check "CLAUDE_PROJECT_DIR wins over the cwd" '[ "$RC" -eq 0 ] && [ -f "$WORK/plugin/proj/.context/reference/env/config.json" ] && [ ! -e "$WORK/.context" ]'
check "settings.local.json follows the project dir" '[ -f "$WORK/plugin/proj/.claude/settings.local.json" ] && [ ! -e "$WORK/plugin/cache-kit/settings.local.json" ]'
check "no git-ignore warning for a project that is not a git repo" '! printf "%s" "$OUT" | grep -q "not git-ignored"'
mkdir -p "$WORK/plugin/repo" && git -C "$WORK/plugin/repo" init -q
set +e
OUT="$(cd "$WORK" && HOME="$HOME_DIR" PROJECTS='' CLAUDE_PROJECT_DIR="$WORK/plugin/repo" sh "$WORK/plugin/cache-kit/setup.sh" 2>&1)"; RC=$?
set -e
check "a git project that does not ignore the file gets one warning naming it" '[ "$RC" -eq 0 ] && printf "%s" "$OUT" | grep -q "settings.local.json is not git-ignored"'
printf ".claude/settings.local.json\n" > "$WORK/plugin/repo/.gitignore"
set +e
OUT="$(cd "$WORK" && HOME="$HOME_DIR" PROJECTS='' CLAUDE_PROJECT_DIR="$WORK/plugin/repo" sh "$WORK/plugin/cache-kit/setup.sh" 2>&1)"; RC=$?
set -e
check "an ignored file gets no warning" '[ "$RC" -eq 0 ] && ! printf "%s" "$OUT" | grep -q "not git-ignored"'

echo "== 10. seeds vs templates: a copy older than its template's last commit is reported; --refresh-seeds prints the diff =="
scenario seeds
run_setup
check "first run: no stale seed" '! printf "%s" "$OUT" | grep -q "predate their template"'
git -C "$WS/.claude" init -q 2>/dev/null && git -C "$WS/.claude" add -A >/dev/null 2>&1 && git -C "$WS/.claude" -c user.email=t@example.com -c user.name=t commit -q -m seed >/dev/null 2>&1
# the copies were seeded before that commit; make them "current" so only the one we back-date counts as stale
touch "$WS/.context/README.md" "$WS/.context/reference/environment.md" "$WS/CLAUDE.md" "$WS/.context/self-assessment/README.md"
touch -t "200101010"000 "$WS/.context/README.md"  # 12 digits split so no scanner reads a timestamp as an account id
printf '\n<!-- template fix -->\n' >> "$WS/.claude/context-db/context-README.template.md"
git -C "$WS/.claude" add -A >/dev/null 2>&1 && git -C "$WS/.claude" -c user.email=t@example.com -c user.name=t commit -q -m fix >/dev/null 2>&1
run_setup
check "a stale seed is one hint line, the copy is not touched" 'printf "%s" "$OUT" | grep -q "1 seeded file(s) predate their template" && ! grep -q "template fix" "$WS/.context/README.md"'
run_setup --refresh-seeds
check "--refresh-seeds names the pair and prints the diff" 'printf "%s" "$OUT" | grep -q "seed .context/README.md predates its template context-db/context-README.template.md" && printf "%s" "$OUT" | grep -q "+<!-- template fix -->"'
check "--help names --refresh-seeds" 'run_setup --help; printf "%s" "$OUT" | grep -q -- "--refresh-seeds"'

echo "== 11. the home directory is never the workspace root (#68) =="
scenario homeroot
set +e
OUT="$(cd "$WS" && HOME="$WS" PROJECTS="$WS" sh "$WS/.claude/setup.sh" 2>&1)"; RC=$?
set -e
check "setup.sh refuses \$HOME as the root with exit 2 and creates nothing" '[ "$RC" -eq 2 ] && printf "%s" "$OUT" | grep -q "refusing the home directory" && [ ! -d "$WS/.context" ]'

echo "== 12. install mode (#34): one rule, recorded in kit.install_mode; hints follow the mode =="
scenario modes
run_setup
check "a .claude/ copy is a clone, recorded" '[ "$RC" -eq 0 ] && [ "$(recorded "$WS")" = clone ] && printf "%s" "$OUT" | grep -q "install mode: clone — recorded in kit.install_mode"'
check "a re-run finds it recorded" 'run_setup; [ "$RC" -eq 0 ] && printf "%s" "$OUT" | grep -q "install mode: clone (kit.install_mode)"'
check "a clone labels its skills as the workspace's .claude/skills (#69)" 'printf "%s" "$OUT" | grep -q "^  workspace (.claude/skills): .*kit-health"'
git -C "$WS/.claude" init -q
cp -R "$KITCOPY" "$WORK/modes/kitdev" && git -C "$WORK/modes/kitdev" init -q
set +e; OUT="$(cd "$WS" && HOME="$HOME_DIR" PROJECTS="$WS" sh "$WORK/modes/kitdev/setup.sh" 2>&1)"; RC=$?; set -e
check "a dev checkout beside the workspace's clone keeps clone recorded" '[ "$RC" -eq 0 ] && [ "$(recorded "$WS")" = clone ] && printf "%s" "$OUT" | grep -q "install mode: dev-checkout — a development checkout beside"'
check "a dev checkout labels its skills by its own path (#69)" 'printf "%s" "$OUT" | grep -q "^  kit skills (dev checkout $WORK/modes/kitdev): .*kit-health" && ! printf "%s" "$OUT" | grep -q "workspace (.claude/skills)"'
mkdir -p "$WORK/modes/cache" && cp -R "$KITCOPY" "$WORK/modes/cache/1.0.0"
rm -f "$WS/.context/reference/environment.md"
set +e; OUT="$(cd "$WS" && HOME="$HOME_DIR" PROJECTS="$WS" sh "$WORK/modes/cache/1.0.0/setup.sh" 2>&1)"; RC=$?; set -e
check "a switch to a plugin install is recorded and the old clone's wiring is named" '[ "$RC" -eq 0 ] && [ "$(recorded "$WS")" = plugin ] && printf "%s" "$OUT" | grep -q "(was clone)" && printf "%s" "$OUT" | grep -q "the old clone.s wiring may be left"'
check "plugin-path hints name the plugin's own kit path, never .claude/context-db" 'printf "%s" "$OUT" | grep -q "cache/1.0.0/context-db index" && ! printf "%s" "$OUT" | grep -q "make -C .claude/context-db"'
PLUGIN_VERSION="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["version"])' "$KITCOPY/.claude-plugin/plugin.json")"
check "a plugin install labels its skills by plugin version and path, not the workspace's .claude/skills (#69)" 'printf "%s" "$OUT" | grep -q "^  kit skills (plugin $PLUGIN_VERSION, $WORK/modes/cache/1.0.0/skills): .*kit-health" && ! printf "%s" "$OUT" | grep -q "workspace (.claude/skills)"'
# every kit path a plugin-install line names must exist (#69): none may point into the workspace's .claude/ at a kit file
check "no plugin-install output line names a kit file under .claude/ (#69)" '! printf "%s" "$OUT" | grep -E "(^|[^~/.$[:alnum:]_-])\.claude/(context-db|skills|agents|docs|hooks|setup\.sh|sync\.sh|CLAUDE\.example\.md|environment-template)"'

echo
if [ "$fails" -eq 0 ]; then echo "setup.sh scenarios: all passed"; else echo "setup.sh scenarios: $fails FAILED" >&2; exit 1; fi
