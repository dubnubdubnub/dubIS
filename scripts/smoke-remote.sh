#!/usr/bin/env bash
# Post-deploy smoke check for a running dubis-server instance.
#
# Usage: scripts/smoke-remote.sh <base_url> [token]
#
#   scripts/smoke-remote.sh http://localhost:8080
#   scripts/smoke-remote.sh https://dubis-server.<tailnet>.ts.net testtoken123
#
# ASSUMPTION: this script targets the remote-deploy scenario, where the
# server always runs with DUBIS_AUTH_MODE=on (see server/auth.py). It does
# NOT handle a server running in auth `off` mode, where /v1/parts returns 200
# even without a token — running this script against an `off`-mode server
# will correctly FAIL the no-token check, which is the point: it's meant to
# catch exactly that misconfiguration in a deploy that's supposed to be
# authed.
#
# Checks (all run unconditionally — none require a token):
#   1. GET /v1/health returns 200 with {"ok": true} — unauthenticated.
#   2. GET /v1/parts WITHOUT a token returns 401 — proving DUBIS_AUTH_MODE=on
#      is actually enforcing, not just present. Needs no token, so it always
#      runs, token or no token passed on the command line.
#   3. If a token is given: GET /v1/parts with `Authorization: Bearer <token>`
#      returns 200 — proving that token is actually valid/accepted.
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

printf '%s' "$health_json" | grep -Eq '"ok"[[:space:]]*:[[:space:]]*true' \
    || fail "/v1/health body did not contain \"ok\": true — got: $health_json"
echo "    OK: $health_json"

echo "==> GET $BASE_URL/v1/parts (no token — expect 401)"
noauth_body="$(curl -sS -w '\n%{http_code}' "$BASE_URL/v1/parts")" \
    || fail "curl to /v1/parts (no token) failed (network/connection error)"
noauth_code="$(printf '%s' "$noauth_body" | tail -n1)"
noauth_json="$(printf '%s' "$noauth_body" | sed '$d')"
[ "$noauth_code" = "401" ] || fail "/v1/parts without a token returned HTTP $noauth_code, expected 401 (auth is not enforcing) (body: $noauth_json)"
echo "    OK: 401 as expected"

if [ -z "$TOKEN" ]; then
    echo "==> No token given — skipping bearer-token check."
    echo "SMOKE PASS (health + no-token-401 only)"
    exit 0
fi

echo "==> GET $BASE_URL/v1/parts (bearer token — expect 200)"
auth_code="$(curl -sS -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $TOKEN" "$BASE_URL/v1/parts")" \
    || fail "curl to /v1/parts (with token) failed (network/connection error)"
[ "$auth_code" = "200" ] || fail "/v1/parts with bearer token returned HTTP $auth_code, expected 200"
echo "    OK: 200 as expected"

echo "SMOKE PASS"
