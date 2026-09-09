#!/usr/bin/env bash
# guard-primary-checkout.sh — PreToolUse hook: refuse file edits made in the
# primary git checkout, so feature work happens in a worktree.
#
# Why this exists as an enforced hook rather than a line in CLAUDE.md: the
# primary checkout is shared by every concurrent Claude session in this repo.
# Uncommitted files left there get swept into ANOTHER session's commit and
# pushed under its PR — the work is not lost, but it lands in a PR whose title
# and review scope have nothing to do with it, and untangling it afterwards
# means either rewriting a branch someone else is actively using or shipping
# the change under their name. That has already happened once (server-picker
# files landed inside a commit about column resizing), while CLAUDE.md had said
# "each Claude instance works in a separate git worktree" the whole time.
# Prose was advisory. This is not.
#
# Claude Code's built-in `worktree.bgIsolation` covers only *background*
# sessions; an ordinary interactive session in the shared checkout is exactly
# the case it does not catch, which is the case that caused the collision.
#
# Reads the PreToolUse payload on stdin and answers with a permission decision.
# Silent (exit 0, no output) when the edit is fine — the common path.
#
# Escape hatch: DUBIS_ALLOW_MAIN_EDITS=1 allows the edit. For deliberate work
# on the shared checkout (resolving a conflict, a release chore) where a
# worktree would be the wrong tool.

set -uo pipefail

payload="$(cat)"

# Escape hatch first: cheapest check, and it must work even if git is unhappy.
if [[ "${DUBIS_ALLOW_MAIN_EDITS:-}" == "1" ]]; then
  exit 0
fi

# NotebookEdit names its target notebook_path; Edit and Write use file_path.
file="$(printf '%s' "$payload" | jq -r '
  .tool_input.file_path // .tool_input.notebook_path // empty
')"
[[ -z "$file" ]] && exit 0

# Resolve against the directory, not the file: Write targets a path that does
# not exist yet, and `git -C` on a missing path fails.
dir="$(dirname "$file")"
while [[ ! -d "$dir" && "$dir" != "/" && "$dir" != "." ]]; do
  dir="$(dirname "$dir")"
done
[[ -d "$dir" ]] || exit 0

# Not in a git repo at all (a scratchpad file, ~/.claude/CLAUDE.md): not ours
# to police.
git_dir="$(git -C "$dir" rev-parse --absolute-git-dir 2>/dev/null)" || exit 0
common_dir="$(git -C "$dir" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || exit 0

# The whole test. A linked worktree's git-dir is <repo>/.git/worktrees/<name>
# while its common-dir stays <repo>/.git; in the primary checkout the two are
# the same path. Nothing else distinguishes them as cheaply, and this needs no
# hard-coded repo path — it keeps working if the checkout moves.
[[ "$git_dir" != "$common_dir" ]] && exit 0

branch="$(git -C "$dir" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
repo_root="$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null || echo '?')"
slug="$(printf '%s' "$branch" | tr -c 'A-Za-z0-9._-' '-' | cut -c1-40)"

# The message is built in a quoted heredoc rather than inside the jq program:
# it contains apostrophes ("session's"), and a jq filter in single quotes ends
# at the first one — which silently turned this whole hook into a bash syntax
# error that only fired on the deny path.
read -r -d '' reason <<EOF
This is the PRIMARY checkout ($repo_root, branch $branch), which is shared with
every other concurrent Claude session in this repo. Editing here risks your
uncommitted files being swept into another session's commit and pushed under its
PR.

Start this task in its own worktree, then make the edit there:

  git worktree add -b claude/<scope>-<desc> .claude/worktrees/$slug origin/main

If you already have uncommitted work here, move it to the worktree before
continuing rather than committing it in place.

To edit the shared checkout on purpose (resolving a conflict, a release chore),
re-run with DUBIS_ALLOW_MAIN_EDITS=1 in the environment.
EOF

jq -n --arg reason "$reason" '{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    permissionDecision: "deny",
    permissionDecisionReason: $reason
  }
}'
