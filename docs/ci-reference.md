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

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| "Branch already merged" error on push | Pushing to a branch whose PR was squash-merged | Run `bash scripts/push-pr.sh` — it auto-creates a new branch |
| JS test fixture mismatch | Backend changed but fixtures not regenerated | `python scripts/generate-test-fixtures.py && git add tests/fixtures/generated/` |
| Playwright tests fail only on ubuntu | Playwright browsers only installed on ubuntu runner | Check if test needs `install-pw: true` in CI matrix |
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
