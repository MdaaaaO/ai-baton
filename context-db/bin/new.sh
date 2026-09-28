#!/bin/sh
# new.sh — scaffold a new context doc from a template, with frontmatter filled in.
# Called by `make -C .claude/context-db new TYPE=<type> DOMAIN=<domain> SLUG=<slug> [TITLE="..."]`.
#
# The doc is placed under the CONTENT root (`kit_profile.py context`: CONTEXT_ROOT, else docs/layout.md's default);
# templates come from the env store's overrides first (.context/reference/env/_templates/<type>.md,
# via `kit_profile.py template <type>`), then the ENGINE dir (_templates/, next to this bin/).
# Refuses to clobber, and prints the path. Run `make index` afterwards (the Makefile chains it).
set -eu
# `[a-z]` / `[!a-z0-9._-]` below are meant as plain ASCII ranges; some locales collate letters
# case-insensitively, which would silently accept a SLUG/DOMAIN like "Upper" (#139) on a machine
# whose default locale isn't C — force it so the path-safety check means the same thing everywhere.
export LC_ALL=C

# Engine dir (holds _templates/) — the parent of this bin/.
ENGINE="$(cd "$(dirname "$0")/.." && pwd)"
ENV_DOMAINS="$(python3 "$ENGINE/bin/kit_profile.py" domains | tr '\n' '|' | sed 's/|$//')"
# Content root — kit_profile.py's context_root(), the one resolver (it honours CONTEXT_ROOT, which the Makefile passes).
CTX="$(python3 "$ENGINE/bin/kit_profile.py" context)"

TYPE="${TYPE:?set TYPE= (epic|reference|repo|meeting|1on1|oncall|self-assessment|pr-review|log)}"
SLUG="${SLUG:?set SLUG= (kebab-case file slug, e.g. key-123-foo or 2026-09-11-team-sync)}"
DOMAIN="${DOMAIN:-}"
TITLE="${TITLE:-$SLUG}"
TODAY="$(date +%F)"

# SLUG and DOMAIN become path components: one lower-case name each, never `/`, `..` or a leading dot (#139)
name_ok() { case "$2" in ""|.*|*[!a-z0-9._-]*) echo "$1 '$2' must match [a-z0-9][a-z0-9._-]* (no '/', no leading '.')" >&2; exit 2;; esac; }
name_ok SLUG "$SLUG"
[ -z "$DOMAIN" ] || name_ok DOMAIN "$DOMAIN"

# Resolve destination directory + default domain from TYPE.
case "$TYPE" in
  meeting)          DIR="$CTX/meetings";          DOMAIN="${DOMAIN:-meetings}" ;;
  1on1)             DIR="$CTX/1on1";              DOMAIN="${DOMAIN:-1on1}" ;;
  oncall)           DIR="$CTX/on-call";           DOMAIN="${DOMAIN:-on-call}" ;;
  self-assessment)  DOMAIN="${DOMAIN:-self-assessment}"
    # week files live in weeks/ (the domain root holds README/initiatives/inbox); mkdir -p below
    # creates it on a fresh environment that has never scaffolded a week before.
    DIR="$CTX/$DOMAIN/weeks" ;;
  pr-review)        DIR="$CTX/pr-reviews";        DOMAIN="${DOMAIN:-pr-reviews}" ;;
  repo)             DIR="$CTX/repos";             DOMAIN="${DOMAIN:-repos}" ;;
  reference)        DIR="$CTX/reference";         DOMAIN="${DOMAIN:-reference}" ;;
  epic)
    : "${DOMAIN:?epic needs DOMAIN= (${ENV_DOMAINS:-a domain from the env store})}"
    # a domain that keeps its epics in a sub-folder (e.g. airflow/epics/) is honoured when it exists
    if [ -d "$CTX/$DOMAIN/epics" ]; then DIR="$CTX/$DOMAIN/epics"; else DIR="$CTX/$DOMAIN"; fi ;;
  log)              DIR="$CTX/${DOMAIN:?log needs DOMAIN=}" ;;
  *) echo "unknown TYPE '$TYPE'" >&2; exit 2 ;;
esac
name_ok DOMAIN "$DOMAIN"  # a default or an epic/log domain given above
# env-store template override for this type (empty when the store has none); a failed lookup is an error,
# never a silent fall-through to the built-in template (#139)
ENV_TMPL="$(python3 "$ENGINE/bin/kit_profile.py" template "$TYPE")" \
  || { echo "new.sh: kit_profile.py template $TYPE failed — fix the env store (kit-health) before scaffolding" >&2; exit 1; }

mkdir -p "$DIR"
DEST="$DIR/$SLUG.md"
[ -e "$DEST" ] && { echo "refusing to clobber existing $DEST" >&2; exit 1; }

TMPL="$ENV_TMPL"
[ -n "$TMPL" ] && [ -f "$TMPL" ] || TMPL="$ENGINE/_templates/$TYPE.md"
[ -f "$TMPL" ] || TMPL="$ENGINE/_templates/default.md"

# Substitute placeholders in the template. Every value is escaped for the sed replacement (`\`, `&` and the
# `|` delimiter would otherwise corrupt the scaffold or fail: a TITLE like `A/B & C`); a newline in a
# value is turned into a space, a template stays one line per field.
sed_esc() { printf '%s' "$1" | tr '\n' ' ' | sed -e 's/[\\&|]/\\&/g'; }
sed -e "s|{{TITLE}}|$(sed_esc "$TITLE")|g" \
    -e "s|{{TYPE}}|$(sed_esc "$TYPE")|g" \
    -e "s|{{DOMAIN}}|$(sed_esc "$DOMAIN")|g" \
    -e "s|{{DATE}}|$TODAY|g" \
    -e "s|{{SLUG}}|$(sed_esc "$SLUG")|g" \
    "$TMPL" > "$DEST"

echo "$DEST"
