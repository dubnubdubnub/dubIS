# CI Reference

## Suite selection

CI auto-detects which suites to run based on changed files in PRs:

| Changed files | Suites triggered |
|---------------|-----------------|
| `js/`, `css/`, `index.html`, test fixtures | JS + Quality |
| `*.py`, `requirements*.txt`, `tests/python/` | Python |
| `pnp_server.py`, `openpnp/`, `tests/pnp-e2e/` | PnP E2E + Python |
| `.github/` | All (CI config changes) |
| `data/constants.json` | JS |
| Docs only (`*.md`, config files) | Lint only |
| Unrecognized files | All (safe fallback) |

Lint (eslint + tsc + ruff) always runs. Cross-compute PnP E2E only runs with an explicit tag. Push to main always runs all suites.

Superseded PR runs are automatically cancelled (concurrency groups).

## Override with commit-message tags

Add a `[ci: <suite>]` tag to your commit message to override auto-detection:

    fix(api): handle empty CSV

    [ci: python]

| Tag | What runs | Wall clock |
|-----|-----------|------------|
| `[ci: all]` | Everything | ~3 min |
| `[ci: lint]` | ESLint + tsc + ruff only (no tests) | ~8s |
| `[ci: js]` | Full JS: lint + types + vitest core + Playwright E2E | ~55s |
| `[ci: python]` | Full Python: ruff + fixture check + pytest | ~17s |
| `[ci: pnp-e2e]` | PnP same-machine E2E (both runners) | ~28s |
| `[ci: pnp-cross]` | PnP cross-compute E2E (depends on pnp-e2e) | ~56s |
| `[ci: quality]` | Visual/a11y: contrast, style-audit, accessibility E2E (warns, never blocks) | ~49s |

## Live distributor test tier

The `live` pytest marker gates tests that hit real distributor endpoints (`test_lcsc_live`, `test_pololu_live`, `test_mouser_live`, `test_digikey_session_live`). The DigiKey test uses `DigikeyClient.ensure_session(interactive=True)` — a warm cookie cache validates silently; a stale cache opens a browser to re-login.

These tests are **deselected by default** via `addopts = ["-m", "not live"]` in `pyproject.toml`, so `pytest tests/python/ -v` (and all CI runs) never collect them.

**Run locally** when touching distributor clients, normalizers, or `scripts/capture-distributor-fixtures.py`, and before merging that work:

```bash
pytest -m live   # requires network, cached DigiKey cookies (data/digikey_cookies.json),
                 # and a Mouser API key (data/mouser_credentials.json)
                 # missing credentials → actionable failure, not a skip
```

Latency is recorded and printed; there are no threshold assertions. If live runs reveal upstream API drift, refresh the committed fixtures:

```bash
python scripts/capture-distributor-fixtures.py
git add tests/fixtures/generated/distributor-scrapes.json
```

The capture → commit → replay flow: `scripts/capture-distributor-fixtures.py` writes `tests/fixtures/generated/distributor-scrapes.json`; `tests/python/test_normalizers.py` replays all four distributors offline from that file. Design doc: `docs/plans/2026-05-31-live-distributor-test-tier-design.md`.

### Scheduled fixture auto-refresh (`refresh-fixtures.yml`)

A scheduled workflow keeps the **public** fixtures (LCSC + Pololu) fresh without any credentials on CI. It runs weekly (Mondays 06:00 UTC) and on manual `workflow_dispatch`, invoking `capture-distributor-fixtures.py --refresh-if-stale --public-only` on the same internet-connected self-hosted runner as the Python job. Per-distributor `captured_at` timestamps drive it: the script re-captures only blocks older than 30 days (via `distributor_fixtures.stale_distributors`), so most weekly runs are a no-op.

It uses **no secrets** — Mouser (API key) and DigiKey (cookies) are deliberately skipped and stay local-only (`pytest -m live`). When a public fixture does change, the job does **not** push to protected `main`; it force-updates the `automation/refresh-fixtures` branch and opens (or reuses) a PR, so the normal offline replay tests validate the captured data before a human merges. Design doc: `docs/plans/2026-05-31-live-distributor-test-tier-design.md`.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| "Branch already merged" error on push | Pushing to a branch whose PR was squash-merged | Run `bash scripts/push-pr.sh` — it auto-creates a new branch |
| JS test fixture mismatch | Backend changed but fixtures not regenerated | `python scripts/generate-test-fixtures.py && git add tests/fixtures/generated/` |
| Playwright tests fail only on ubuntu | Playwright browsers only installed on ubuntu runner | Check if test needs `install-pw: true` in CI matrix |
| Visual snapshot "doesn't exist" / fails only on ubuntu | Golden-image baselines are per-platform; CI Linux runner lacks `-linux.png` | Regenerate on the runner — see [visual-testing.md](visual-testing.md) |
| Quality suite "fails" | Quality tier uses `continue-on-error: true` | These are warnings, not blockers — quality failures don't block merge |
| ruff or eslint fails after refactor | New file not covered by existing config | Check `pyproject.toml` excludes and `eslint.config.mjs` includes |

## Manual CI triggers (workflow_dispatch)

```bash
gh workflow run ci.yml --ref <branch>                          # all suites
gh workflow run ci.yml --ref <branch> -f suite=lint            # lint only, no tests
gh workflow run ci.yml --ref <branch> -f suite=js
gh workflow run ci.yml --ref <branch> -f suite=python
gh workflow run ci.yml --ref <branch> -f suite=pnp-e2e
gh workflow run ci.yml --ref <branch> -f suite=pnp-cross
gh workflow run ci.yml --ref <branch> -f suite=quality
```

## PnP E2E Tests

Same-machine (CI default): `python tests/pnp-e2e/run_test.py`
Cross-compute: `python tests/pnp-e2e/run_test.py --remote-openpnp ux430`

The `--remote-openpnp` flag starts dubIS locally and launches OpenPnP on a remote
host via SSH. Uses `~/.ssh/config` for connection settings. Cross-compute tests
verify the real network path (dubIS ↔ OpenPnP over Tailscale).
