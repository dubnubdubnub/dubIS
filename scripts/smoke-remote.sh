#!/usr/bin/env bash
# Post-deploy smoke check for a running dubis-server instance.
#
# Usage: scripts/smoke-remote.sh <base_url> [token]
#
#   scripts/smoke-remote.sh http://localhost:8080
#   scripts/smoke-remote.sh https://dubis-server.<tailnet>.ts.net testtoken123
#
# Checks:
#   1. GET /v1/health returns 200 with {"ok": true} — unauthenticated, always.
#   2. If a token is given: GET /v1/parts with `Authorization: Bearer <token>`
#      returns 200, AND the same request WITHOUT a token returns 401 — proving
#      DUBIS_AUTH_MODE=on is actually enforcing, not just present.
#
# Exits non-zero with a clear message on the first failing check. Intended to
# run on Linux ARC pods (cluster-DNS smoke, per the runbook) as well as any
# POSIX shell with curl — no bash-only or GNU-only flags.

set -eu

BASE_URL="${1:-}"
TOKEN="${2:-}"

if [ -z "$BASE_URL" ]; then
    echo "usage: smoke-remote.sh <base_url> [token]" >&2
    exit 2
fi

# Strip any trailing slash so "$BASE_URL/v1/health" never double-slashes.
BASE_URL="${BASE_URL%/}"

fail() {
    echo "SMOKE FAIL: $1" >&2
    exit 1
}

echo "==> GET $BASE_URL/v1/health"
health_body="$(curl -sS -w '\n%{http_code}' "$BASE_URL/v1/health")" \
    || fail "curl to /v1/health failed (network/connection error)"
health_code="$(printf '%s' "$health_body" | tail -n1)"
health_json="$(printf '%s' "$health_body" | sed '$d')"

[ "$health_code" = "200" ] || fail "/v1/health returned HTTP $health_code, expected 200 (body: $health_json)"

case "$health_json" in
    *'"ok"'*true*|*'"ok": true'*|*'"ok":true'*)
        ;;
    *)
        fail "/v1/health body did not contain ok:true — got: $health_json"
        ;;
esac
echo "    OK: $health_json"

if [ -z "$TOKEN" ]; then
    echo "==> No token given — skipping authed-path checks."
    echo "SMOKE PASS (health only)"
    exit 0
fi

echo "==> GET $BASE_URL/v1/parts (no token — expect 401)"
noauth_code="$(curl -sS -o /dev/null -w '%{http_code}' "$BASE_URL/v1/parts")" \
    || fail "curl to /v1/parts (no token) failed (network/connection error)"
[ "$noauth_code" = "401" ] || fail "/v1/parts without a token returned HTTP $noauth_code, expected 401 (auth is not enforcing)"
echo "    OK: 401 as expected"

echo "==> GET $BASE_URL/v1/parts (bearer token — expect 200)"
auth_code="$(curl -sS -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $TOKEN" "$BASE_URL/v1/parts")" \
    || fail "curl to /v1/parts (with token) failed (network/connection error)"
[ "$auth_code" = "200" ] || fail "/v1/parts with bearer token returned HTTP $auth_code, expected 200"
echo "    OK: 200 as expected"

echo "SMOKE PASS"
