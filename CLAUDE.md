# dubIS — Codebase Guide

## Architecture

Desktop app: Python (pywebview) backend + vanilla JS/HTML/CSS frontend.

| Layer | Files |
|-------|-------|
| **Backend** | `app.py` (webview launcher), `inventory_api.py` (file I/O, inventory CRUD), `categorize.py` (part categorization), `digikey_client.py` (Digikey browser session + scraping), `lcsc_client.py` (LCSC API fetching) |
| **Frontend** | `index.html`, `css/styles.css`, 20 JS ES modules (no build step, no framework) |
| **Data** | CSV files in `data/` — `inventory.csv`, `purchase_ledger.csv`, `adjustments.csv`, `preferences.json`, `constants.json` |

## JS Module Dependency Graph

Entry point: `<script type="module" src="js/app-init.js">` in `index.html`.

### Infrastructure modules
- `js/event-bus.js` — pub/sub event system + Events enum
- `js/constants.js` — shared constants loaded from `data/constants.json`
- `js/ui-helpers.js` — DOM utilities (toasts, escaping, modals, dropzones, price inputs)
- `js/api.js` — pywebview bridge + AppLog (← ui-helpers)
- `js/undo-redo.js` — undo/redo stack (← api)
- `js/store.js` — global `App` state, preferences, inventory loading (← event-bus, constants, api)

### Pure-logic modules (no DOM)
- `js/types.js` — JSDoc typedefs for shared data shapes (no runtime code)
- `js/csv-parser.js` — CSV parsing, BOM processing, CSV generation
- `js/part-keys.js` — part key builders, status icons/classes (← csv-parser)
- `js/matching.js` — BOM-to-inventory matching, value/package compatibility (← part-keys)
- `js/bom-row-data.js` — BOM row display data assembly (← part-keys)

### Panel modules (side-effect imports, self-initialise)
- `js/inventory-panel.js` — middle panel: grouped inventory view, search, render dispatch
- `js/inventory-modals.js` — adjustment + price modals with undo/redo
- `js/bom-comparison.js` — BOM comparison table: filtering, matching, confirm/unconfirm
- `js/bom-panel.js` — right panel: BOM file viewer, staging, consume
- `js/import-panel.js` — left panel: purchase CSV import
- `js/resize-panels.js` — drag-to-resize panels
- `js/part-preview.js` — hover tooltips
- `js/preferences-modal.js` — preferences sliders + Digikey login flow

### Entry point
- `js/app-init.js` — wires modals, global shortcuts, loads inventory, imports all panels

## Key Conventions

- **ES modules**: every dependency is an explicit `import` statement at the top of each file
- **Constants**: `UPPER_SNAKE_CASE` at module scope
- **Event names**: centralized in `Events` object in `event-bus.js`, used as `Events.X` everywhere
- **Status maps**: `STATUS_ICONS` and `STATUS_ROW_CLASS` in `part-keys.js`
- **Panels communicate**: via `EventBus.emit(Events.X)` / `EventBus.on(Events.X)`
- **`App` object** in `store.js` owns global state; properties declared upfront with ownership comments
- **Error policy**: prefer `AppLog.warn`/`AppLog.error` over silent catches. Throw errors rather than silently failing.
- **Test policy**: never use `pytest.skip`, `pytest.importorskip`, or `@pytest.mark.skip` to hide missing dependencies — add them to `requirements-dev.txt` instead. Tests must run, not be skipped.
- **UI clipping tests**: `sticky-buttons.spec.mjs` and `resize-visibility.spec.mjs` verify that action buttons (Adjust, Confirm, Link) are not clipped by panel overflow. Never weaken these tests (e.g., by relaxing tolerances, removing viewport sizes, or switching from individual-button checks to cell-level checks). If a CSS change causes these tests to fail, fix the CSS — the test is catching a real bug.
- **Window globals**: `window.closeModal` exposed for Python's `evaluate_js` (set in app-init.js)

## Testing & Linting

### Local (runs in seconds)

```bash
# JavaScript — Node.js installed on Windows via winget
npx eslint js/            # lint
npx tsc --noEmit          # type check
npx vitest run            # unit tests (~1s)

# Python
ruff check .              # lint all Python files
pytest tests/python/ -v   # unit tests (~30s)
```

### CI commit-message tags

Control which CI tiers run by adding a tag to your commit message:

    fix(api): handle empty CSV

    [ci: python]

| Tag | What runs | Wall clock |
|-----|-----------|------------|
| `[ci: all]` or no tag | Everything (safe default) | ~3 min |
| `[ci: lint]` | ESLint + tsc + ruff only (no tests) | ~8s |
| `[ci: js]` | Full JS: lint + types + vitest core + Playwright E2E | ~55s |
| `[ci: python]` | Full Python: ruff + fixture check + pytest | ~17s |
| `[ci: pnp-e2e]` | PnP same-machine E2E (both runners) | ~28s |
| `[ci: pnp-cross]` | PnP cross-compute E2E (depends on pnp-e2e) | ~56s |
| `[ci: quality]` | Visual/a11y: contrast, style-audit, accessibility E2E (warns, never blocks) | ~49s |

When unsure, omit the tag — all suites run. Use `[ci: lint]` only for docs/comments/CLAUDE.md changes.

### CI (via workflow_dispatch for selective runs)

```bash
gh workflow run ci.yml --ref <branch>                          # all suites
gh workflow run ci.yml --ref <branch> -f suite=lint            # lint only, no tests
gh workflow run ci.yml --ref <branch> -f suite=js
gh workflow run ci.yml --ref <branch> -f suite=python
gh workflow run ci.yml --ref <branch> -f suite=pnp-e2e
gh workflow run ci.yml --ref <branch> -f suite=pnp-cross
gh workflow run ci.yml --ref <branch> -f suite=quality
```

### PnP E2E Tests

Same-machine (CI default): `python tests/pnp-e2e/run_test.py`
Cross-compute: `python tests/pnp-e2e/run_test.py --remote-openpnp ux430`

The `--remote-openpnp` flag starts dubIS locally and launches OpenPnP on a remote
host via SSH. Uses `~/.ssh/config` for connection settings. Cross-compute tests
verify the real network path (dubIS ↔ OpenPnP over Tailscale).

### Inventory test safety

Adjustments have a `source` field (`"openpnp"`, `"test:<session_id>"`, etc.).
The headless test server (`dubis_headless.py --test-source <tag> --rollback-on-exit`)
tags all test adjustments and rolls them back on shutdown.

## Branch Workflow (for multi-Claude development)

- **Main branch** (`main`): protected, requires PR + passing CI
- **Branch naming**: `claude/<scope>-<description>` (e.g., `claude/refactor-tests`, `claude/feature-bom-export`)
- **Squash-merge** PRs to keep linear history
- **Each Claude instance** works in a separate git worktree
- **Coordination**: via GitHub Issues (labels: `feature`, `refactor`)
- **Before creating a PR**: ensure lint, type check, and tests pass (see Testing & Linting section above)
- **Push via `scripts/push-pr.sh`**: Always use this script to push and create PRs. It automatically detects if your branch's PR was already merged and creates a new branch if needed.
  ```bash
  bash scripts/push-pr.sh                          # PR title = last commit subject
  bash scripts/push-pr.sh --title "fix: the thing" # explicit title
  bash scripts/push-pr.sh --body "Fixes #123"      # explicit body
  ```
  If you forget and push to a merged branch directly, CI will fail with an error telling you to use this script.
- **Verify your worktree matches your task**: Before starting work, check that your current worktree/branch is relevant to the task at hand. If you're on an unrelated branch (e.g., leftover from a previous task), create a new worktree and branch for your current work instead of reusing it.
- **PR your work and watch CI**: When your work is complete, push your branch and create a PR. Then monitor CI (`gh pr checks <number>`) — if any checks fail, diagnose and fix the issues, push again, and keep iterating until all checks pass and the PR is ready to merge. Do not abandon a PR with failing CI.
