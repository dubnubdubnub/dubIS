#!/usr/bin/env bash
# merge-prs.sh — Check open PRs, merge those with passing CI, report failures.
# Usage: bash scripts/merge-prs.sh [--dry-run]
set -euo pipefail

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

# List open PRs (number, title, branch)
prs=$(gh pr list --state open --json number,title,headRefName --jq '.[] | "\(.number)\t\(.title)\t\(.headRefName)"')

if [[ -z "$prs" ]]; then
  echo "No open PRs."
  exit 0
fi

merged=()
failed=()
commented=()

while IFS=$'\t' read -r num title branch; do
  echo "--- PR #${num}: ${title} ---"

  # Check CI status
  checks_output=$(gh pr checks "$num" 2>&1) || true

  # Parse check results
  has_pending=false
  has_fail=false
  all_pass=true

  while IFS=$'\t' read -r check_name check_status _rest; do
    status=$(echo "$check_status" | xargs)  # trim whitespace
    case "$status" in
      pass) ;;
      fail)
        has_fail=true
        all_pass=false
        echo "  FAIL: $check_name"
        ;;
      pending)
        has_pending=true
        all_pass=false
        echo "  PENDING: $check_name"
        ;;
    esac
  done <<< "$checks_output"

  if $has_fail; then
    echo "  -> Skipping (CI failing)"
    # Comment if not already commented recently
    last_comment=$(gh pr view "$num" --json comments --jq '.comments[-1].body // ""' 2>/dev/null || echo "")
    if [[ "$last_comment" != *"CI"*"failing"* && "$last_comment" != *"Please check"* ]]; then
      if ! $DRY_RUN; then
        gh pr comment "$num" --body "CI is failing. Please check the logs and fix."
      fi
      commented+=("$num")
      echo "  -> Commented"
    fi
    failed+=("#${num}: ${title}")
    continue
  fi

  if $has_pending; then
    echo "  -> Skipping (checks still pending)"
    failed+=("#${num}: ${title} (pending)")
    continue
  fi

  # All checks pass — show brief diff summary
  echo "  Files changed:"
  gh pr diff "$num" --name-only | sed 's/^/    /'

  if $DRY_RUN; then
    echo "  -> Would merge (dry run)"
    merged+=("#${num}: ${title}")
  else
    # Merge
    merge_output=$(gh pr merge "$num" --squash --delete-branch --admin 2>&1) || true
    if echo "$merge_output" | grep -q "Merged\|already been merged\|failed to delete local branch"; then
      echo "  -> Merged"
      merged+=("#${num}: ${title}")
    else
      echo "  -> Merge failed: $merge_output"
      failed+=("#${num}: ${title} (merge failed)")
    fi
  fi
done <<< "$prs"

# Pull latest main
if [[ ${#merged[@]} -gt 0 ]] && ! $DRY_RUN; then
  echo ""
  echo "Pulling latest main..."
  git pull origin main 2>&1 | tail -3
fi

# Summary
echo ""
echo "=== Summary ==="
if [[ ${#merged[@]} -gt 0 ]]; then
  echo "Merged (${#merged[@]}):"
  printf '  %s\n' "${merged[@]}"
fi
if [[ ${#failed[@]} -gt 0 ]]; then
  echo "Skipped (${#failed[@]}):"
  printf '  %s\n' "${failed[@]}"
fi
if [[ ${#commented[@]} -gt 0 ]]; then
  echo "Commented on: ${commented[*]}"
fi
if [[ ${#merged[@]} -eq 0 && ${#failed[@]} -eq 0 ]]; then
  echo "Nothing to do."
fi
