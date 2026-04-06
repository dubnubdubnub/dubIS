#!/usr/bin/env bash
# push-pr.sh — Push commits and create/update a PR.
#
# Handles merged-branch detection: if the current branch's PR was already
# squash-merged, automatically creates a new branch from origin/main,
# cherry-picks new commits onto it, and opens a fresh PR.
#
# Usage:
#   bash scripts/push-pr.sh                          # PR title = last commit subject
#   bash scripts/push-pr.sh --title "fix: the thing" # explicit title
#   bash scripts/push-pr.sh --body "Fixes #123"      # explicit body

set -euo pipefail

# ── Parse arguments ──────────────────────────────────────────────────
TITLE=""
BODY=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --title) TITLE="$2"; shift 2 ;;
    --body)  BODY="$2";  shift 2 ;;
    *)       echo "Unknown arg: $1"; exit 1 ;;
  esac
done

# ── Safety checks ────────────────────────────────────────────────────
if ! git rev-parse --is-inside-work-tree &>/dev/null; then
  echo "Error: not inside a git repository." >&2
  exit 1
fi

BRANCH=$(git rev-parse --abbrev-ref HEAD)

if [[ "$BRANCH" == "main" || "$BRANCH" == "master" ]]; then
  echo "Error: refusing to push directly to $BRANCH." >&2
  exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Error: working tree has uncommitted changes. Commit or stash first." >&2
  exit 1
fi

# Default title = last commit subject
if [[ -z "$TITLE" ]]; then
  TITLE=$(git log -1 --pretty=%s)
fi

# ── Check if branch's PR was already merged ──────────────────────────
MERGED_PR=$(gh pr list --head "$BRANCH" --state merged --json number --jq '.[0].number // empty' 2>/dev/null || true)

if [[ -n "$MERGED_PR" ]]; then
  echo "Branch '$BRANCH' has a merged PR (#$MERGED_PR). Creating new branch..."

  git fetch origin main --quiet

  # Find commits on this branch that aren't in origin/main
  COMMITS=$(git log origin/main..HEAD --reverse --format=%H)

  if [[ -z "$COMMITS" ]]; then
    echo "No new commits beyond what was merged. Nothing to push."
    exit 0
  fi

  N=$(echo "$COMMITS" | wc -l | tr -d ' ')
  echo "Found $N new commit(s) to cherry-pick."

  # Generate new branch name: strip -vN suffix, find next version
  BASE_NAME=$(echo "$BRANCH" | sed 's/-v[0-9]*$//')
  NEXT_V=2
  while git show-ref --verify --quiet "refs/heads/${BASE_NAME}-v${NEXT_V}" 2>/dev/null || \
        git ls-remote --heads origin "${BASE_NAME}-v${NEXT_V}" 2>/dev/null | grep -q .; do
    NEXT_V=$((NEXT_V + 1))
  done
  NEW_BRANCH="${BASE_NAME}-v${NEXT_V}"

  # Create new branch from origin/main
  git checkout -b "$NEW_BRANCH" origin/main --quiet

  # Cherry-pick each commit
  for SHA in $COMMITS; do
    if ! git cherry-pick "$SHA" --quiet 2>/dev/null; then
      echo "Error: cherry-pick conflict on $(git log -1 --pretty=%h $SHA): $(git log -1 --pretty=%s $SHA)" >&2
      echo "Resolve the conflict manually, then run: git cherry-pick --continue" >&2
      echo "Or abort with: git cherry-pick --abort && git checkout $BRANCH" >&2
      exit 1
    fi
  done

  echo "Cherry-picked $N commit(s) onto '$NEW_BRANCH'."

  # Push and create PR
  git push -u origin "$NEW_BRANCH" --quiet
  PR_URL=$(gh pr create --head "$NEW_BRANCH" --title "$TITLE" --body "${BODY:-Continuation of #$MERGED_PR.}")
  echo "Created PR: $PR_URL"
  echo "(Old PR #$MERGED_PR was merged. New branch: $NEW_BRANCH)"

else
  # ── Normal path: push and create/update PR ───────────────────────
  git push -u origin "$BRANCH" --quiet

  EXISTING_PR=$(gh pr list --head "$BRANCH" --state open --json number,url --jq '.[0]' 2>/dev/null || true)

  if [[ -n "$EXISTING_PR" && "$EXISTING_PR" != "null" ]]; then
    PR_NUM=$(echo "$EXISTING_PR" | jq -r '.number')
    PR_URL=$(echo "$EXISTING_PR" | jq -r '.url')
    echo "PR #$PR_NUM updated with new commits: $PR_URL"
  else
    PR_URL=$(gh pr create --head "$BRANCH" --title "$TITLE" --body "${BODY:-}")
    echo "Created PR: $PR_URL"
  fi
fi
