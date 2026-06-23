#!/usr/bin/env bash
# verify.sh — run every CI gate locally before pushing a PR.
# Usage:
#   bash scripts/verify.sh          # all gates except E2E
#   bash scripts/verify.sh --e2e    # include Playwright functional suite

PY="${PYTHON:-python}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FIXTURES_DIR="tests/fixtures/generated"

# ── helpers ──────────────────────────────────────────────────────────────────

FAILED_STEPS=()
PASSED_STEPS=()

run_step() {
    local label="$1"
    shift
    echo ""
    echo "── $label ──"
    "$@"
    local rc=$?
    if [ $rc -eq 0 ]; then
        PASSED_STEPS+=("$label")
    else
        FAILED_STEPS+=("$label")
    fi
    return $rc
}

# ── run all steps ─────────────────────────────────────────────────────────────

cd "$REPO_ROOT" || { echo "ERROR: cannot cd to $REPO_ROOT"; exit 1; }

# 1. ruff
run_step "ruff" ruff check .

# 2. test fixtures — regenerate, then check for staleness
echo ""
echo "── fixtures (regenerate) ──"
"$PY" scripts/generate-test-fixtures.py
fix_rc=$?
if [ $fix_rc -ne 0 ]; then
    echo "ERROR: generate-test-fixtures.py failed (exit $fix_rc)"
    FAILED_STEPS+=("fixtures")
else
    # check if anything changed in the generated dir
    changed="$(git status --porcelain -- "$FIXTURES_DIR")"
    if [ -n "$changed" ]; then
        echo ""
        echo "FAIL: test fixtures were stale and have been regenerated — commit them or JS/vitest will fail in CI."
        echo "Changed files:"
        echo "$changed"
        FAILED_STEPS+=("fixtures")
    else
        PASSED_STEPS+=("fixtures")
    fi
fi

# 3. code-map
run_step "code-map" "$PY" scripts/gen-code-map.py --check

# 3b. inventory-record types
run_step "inventory-types" "$PY" scripts/gen-inventory-types.py --check
invtypes_rc=$?
if [ $invtypes_rc -ne 0 ]; then
    echo "  → inventory-record.d.ts stale — run \`python scripts/gen-inventory-types.py\` and commit."
fi
codemap_rc=$?
if [ $codemap_rc -ne 0 ]; then
    echo "  → code-map stale — run \`python scripts/gen-code-map.py\` and commit."
fi

# 4. manifests
run_step "manifests" "$PY" scripts/check-manifests.py

# 5. layout-tokens
run_step "layout-tokens" "$PY" scripts/check-layout-tokens.py --check
layout_rc=$?
if [ $layout_rc -ne 0 ]; then
    echo "  → layout-token check failed — if token line numbers moved, run \`python scripts/regen-layout-ignore.py\`, then recheck."
fi

# 6. pytest
run_step "pytest" "$PY" -m pytest tests/python/ -q

# 7. eslint
run_step "eslint" npx eslint js/

# 8. tsc
run_step "tsc" npx tsc --noEmit

# 9. vitest
run_step "vitest" npx vitest run --project core

# 10. E2E (opt-in)
if [ "$1" = "--e2e" ]; then
    run_step "playwright (functional)" npx playwright test --project functional
else
    echo ""
    echo "── E2E (skipped) ──"
    echo "  E2E skipped — run \`bash scripts/verify.sh --e2e\` to include Playwright."
fi

# ── final summary ─────────────────────────────────────────────────────────────

echo ""
echo "════════════════════════════════════════"
echo "  verify.sh — summary"
echo "════════════════════════════════════════"

for s in "${PASSED_STEPS[@]}"; do
    echo "  ✓  $s"
done
for s in "${FAILED_STEPS[@]}"; do
    echo "  ✗  $s"
done

echo ""
num_failed=${#FAILED_STEPS[@]}
if [ "$num_failed" -eq 0 ]; then
    echo "  PASS"
    exit 0
else
    echo "  FAIL ($num_failed failed)"
    exit 1
fi
