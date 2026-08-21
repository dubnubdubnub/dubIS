#!/usr/bin/env bash
# ci-scrub-workspace.sh — make a REUSED CI workspace behave like a fresh
# checkout, minus the caches we keep on purpose.
#
# Why this exists
# ---------------
# The self-hosted single-machine legs check out with `clean: false`. What that
# actually buys is NOT a skipped clone: actions/checkout reuses the existing
# `.git` either way, and `clean: true` only adds `git clean -ffdx` +
# `git reset --hard`. The one thing `clean: false` genuinely preserves is the
# ignored caches — above all `node_modules/` — which is worth keeping on a box
# whose npm cache is warm.
#
# The cost is that EVERY other piece of untracked/ignored state also survives:
# `test-results/` from the previous run, `cache.db`, the gitignored `data/*.csv`
# and `data/*.json` (including `data/preferences.json`), and any file left
# behind by a branch that was checked out here weeks ago. None of that exists on
# the ubuntu legs, so the two legs are not running the same test. That is the
# difference between "macOS found a real bug" and "macOS is haunted".
#
# So: clean everything a fresh checkout would not have, EXCEPT the deliberate
# caches, and then assert that no tracked file has drifted. Tracked files are
# supposed to be restored by `git checkout --force` even with `clean: false`;
# if they ever aren't, the generated-and-committed files (js/api-map.js,
# docs/openapi-v1.json, js/inventory-record.d.ts, docs/code-map.md) would be
# silently stale and their CI guards would be validating the wrong bytes. Assert
# it rather than trust it.
#
# Deliberately bash 3.2 compatible (macOS ships bash 3.2 as /bin/bash, so no
# `mapfile`, no associative arrays).
#
# Usage:
#   bash scripts/ci-scrub-workspace.sh             # report, remove, assert
#   bash scripts/ci-scrub-workspace.sh --dry-run   # report + assert, remove nothing
#
# Exit 0 = workspace is now equivalent to a fresh checkout (plus the kept
# caches). Exit 1 = tracked files have drifted, so this run cannot be trusted.

set -euo pipefail

# Ignored paths carried over between runs on purpose.
#   node_modules      — the entire point of `clean: false`; `npm install`
#                       reconciles it against package-lock.json anyway.
#   .venv             — an in-repo virtualenv a human may have made here; the CI
#                       legs use ~/dubis-venv, so leaving it costs nothing.
#   .claude           — agent worktrees/state; never read by any test.
#   scripts/ci_watcher — the CI watcher's SQLite state is documented (.gitignore)
#                       as living on m4-air, i.e. on this very machine. Deleting
#                       it would destroy production state to tidy a test run.
KEEP="node_modules .venv .claude scripts/ci_watcher"

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help)
      sed -n '2,40p' "$0"
      exit 0
      ;;
    *)
      echo "ci-scrub-workspace.sh: unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "::error::ci-scrub-workspace.sh: $PWD is not inside a git work tree — refusing to delete anything."
  exit 1
fi

EXCLUDES=()
for k in $KEEP; do
  EXCLUDES+=("--exclude=$k")
done

# Preview first so the log always shows exactly what this run inherited. A long
# list here is itself the finding: it means the workspace had been accumulating.
PREVIEW="$(git clean -xdn "${EXCLUDES[@]}")"
if [ -z "$PREVIEW" ]; then
  COUNT=0
else
  COUNT="$(printf '%s\n' "$PREVIEW" | grep -c '^' || true)"
fi

echo "── ci-scrub-workspace: $COUNT inherited path(s) to remove ──"
if [ "$COUNT" -gt 0 ]; then
  printf '%s\n' "$PREVIEW"
fi
echo "── carried over on purpose: $KEEP ──"

if [ "$DRY_RUN" -eq 1 ]; then
  echo "ci-scrub-workspace: --dry-run, nothing removed."
else
  # Single -f (not -ff): a nested git repository is skipped with a warning
  # rather than destroyed. Nothing in this repo should contain one, and if one
  # ever appears, losing it silently is worse than leaving it.
  git clean -xdf "${EXCLUDES[@]}"
fi

# Tracked-file drift. `git checkout --force` should already guarantee this even
# with clean: false; a failure here means the checkout did not fully reset and
# every staleness guard in this run is checking the wrong bytes.
DRIFT="$(git status --porcelain --untracked-files=no)"
if [ -n "$DRIFT" ]; then
  echo "::error title=Reused workspace has drifted::Tracked files differ from the checked-out commit."
  echo "::error::Generated-and-committed files (js/api-map.js, docs/openapi-v1.json, js/inventory-record.d.ts, docs/code-map.md) cannot be trusted in this run, and neither can their staleness guards."
  printf '%s\n' "$DRIFT"
  exit 1
fi

if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  {
    echo "- Workspace hygiene: removed $COUNT inherited path(s); carried over \`$KEEP\`; no tracked-file drift."
  } >> "$GITHUB_STEP_SUMMARY"
fi

echo "ci-scrub-workspace: workspace matches a fresh checkout (plus the kept caches)."
