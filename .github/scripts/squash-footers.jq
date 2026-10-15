# squash-footers.jq — the conventional footer block that ends a PR body: what auto-merge.yml passes as the
# squash commit's message, and what check-pr-footers.sh judges. One parser for both.
#
#   jq -Rjs -f .github/scripts/squash-footers.jq < body.md      (input: the body as one raw string)
#
# A footer is one line with a known token: `BREAKING CHANGE: <step>` (or BREAKING-CHANGE), `Refs #12`,
# `Closes #12`, `Fixes #12`, `Resolves #12`, `Co-authored-by: …`, `Signed-off-by: …`, `Reviewed-by: …` — so a
# prose line like `Result: 16 OK` is never one. What a PR tool appends after the block is set aside first: trailing
# blank lines, whole-line HTML comments (the diagram-plan marker) and the session footer line (session `<name>`).
# The block is the run of footer lines that then ends the body, after a blank line or a whole-line HTML comment
# (or the whole body). Output: those lines, or "" when the body ends in anything else. Nothing else of the body —
# prose, HTML comments, code — ever reaches the commit. Pure jq: the body is data, never evaluated.
def blank: test("^\\s*$");
def comment: test("^\\s*<!--.*-->\\s*$");
def session_line: test("^session `[^`]+`\\s*$");
def footer:
  test("^(BREAKING CHANGE|BREAKING-CHANGE): \\S")
  or test("^(Refs|Closes|Fixes|Resolves|Co-authored-by|Signed-off-by|Reviewed-by)(: \\S| #[0-9]+$)"; "i");
def trim_tail:
  if length > 0 and (.[-1] | blank or comment or session_line) then .[:-1] | trim_tail else . end;

gsub("\r"; "") | split("\n") | trim_tail as $lines
| ([$lines | to_entries[] | select(.value | footer | not) | .key] | last // -1) as $prose
| if $prose == -1 or ($lines[$prose] | blank or comment)
  then $lines[$prose + 1:] | join("\n")
  else ""
  end
