# CI Reference

## Suite selection

CI auto-detects which suites to run based on changed files in PRs:

| Changed files | Suites triggered |
|---------------|-----------------|
| `js/`, `css/`, `index.html`, test fixtures | JS + Quality |
| `*.py`, `requirements*.txt`, `tests/python/` | Python |
| `pnp_server.py`, `openpnp/`, `tests/pnp-e2e/` | PnP E2E + Python |
| `.github/` | All (CI config changes) |
| `data/pnp_part_map.json` | PnP + Python |
| `data/*.json` (config) | JS + Python |
| Docs only (`*.md`, data CSVs, config files) | Lint only |
| Unrecognized files | All (safe fallback) |

Lint (eslint + tsc + ruff) always runs. Push to main always runs all suites.

Superseded PR runs are automatically cancelled (concurrency groups).

### Required checks & single-box legs

Branch protection requires three contexts, all of which are aggregate gate jobs running on GitHub-hosted ubuntu: **JS Lint & Test (ubuntu)** (aggregates js + js-e2e + js-live + js-hosted), **Python Lint & Test (ubuntu)** (aggregates python + python-hosted), and **PnP E2E (required)** (aggregates pnp-e2e). Skipped suites satisfy the gates, so e.g. a docs-only PR is still mergeable.

Single-physical-machine legs are **advisory** (`continue-on-error`): the macos (m4-air) legs of js/js-e2e/python/pnp-e2e, js-windows (win11 VM), and vlm-gpu. Their failures annotate the run red but never block merges — a laptop outage must not stall the queue.

### Persistent-runner hygiene (m4-air, win11)

Advisory is not the same as ignorable: an advisory leg that fails for environmental reasons trains everyone to stop reading it. Two properties of the m4-air legs used to produce exactly that, and both now have a guard.

**Reused workspace.** The macos legs of `js`, `js-e2e`, `python` and `quality` check out with `clean: false`. That does *not* skip a clone — `actions/checkout` reuses the existing `.git` either way; `clean: true` only adds `git clean -ffdx` + `git reset --hard`. What `clean: false` genuinely buys is the warm `node_modules`. What it also preserved, unintentionally, was every other untracked/ignored file: `test-results/` from the previous run, `cache.db`, the gitignored `data/*.csv` and `data/*.json`, and leftovers from whatever branch was last checked out there. The ubuntu legs have none of that, so the two legs were not running the same test.

`scripts/ci-scrub-workspace.sh` runs immediately after checkout on every leg of those four jobs. It is a no-op on the clean legs. On a reused workspace it:

- lists, then removes, every untracked/ignored path a fresh checkout would not have — keeping only `node_modules`, `.venv`, `.claude`, and `scripts/ci_watcher` (whose SQLite state is m4-air *production* state, not test residue);
- asserts no **tracked** file has drifted. `git checkout --force` should guarantee that even without `clean`, but if it ever doesn't, the committed-and-generated files (`js/api-map.js`, `docs/openapi-v1.json`, `js/inventory-record.d.ts`, `docs/code-map.md`) would be stale *and* the four staleness guards would be comparing against the wrong bytes. It fails the leg with a labelled error instead.

The count of removed paths goes into the job summary, so a workspace that has been quietly accumulating is visible rather than inferred.

**Browser cache.** Every leg that runs `playwright test` now installs browsers unconditionally (the old `install-pw: false` on macos is gone — installing is a fast no-op when the pinned revision is present, so skipping it only ever bought risk) *and* then runs `node scripts/check-playwright-browsers.mjs --repair`.

The verify step exists because `playwright install` decides "already installed" from the mere **existence** of the revision directory. An interrupted download — a killed job, a full disk, a truncated `actions/cache` restore — leaves that directory in place with a fraction of its files, and every later `playwright install` is a silent no-op, so on a runner whose cache survives between jobs the browser stays broken forever. A leg in that state does not report "my browser is broken"; it reports whatever the specs happen to fail on, which reads like a code defect.

The verdict is a real `chromium.launch()` plus a render, and nothing else:

- only launching exercises the binary the specs use — since Playwright 1.49 a headless `chromium.launch()` resolves to the separate `chromium_headless_shell` download, not the path `executablePath()` names;
- file-size heuristics on the executable are actively wrong. On macOS the file Playwright names as the executable is `Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing`, a ~52 KB **stub launcher by design** (the code lives in the bundle's `Frameworks/`). The first version of this check used a size floor, condemned the m4-air's perfectly healthy cache, and deleted it to "repair" it. The size of the *download directory* (a real install is a few hundred files and >100 MB) is reported alongside a failure as a diagnostic — never as a verdict.

A failure annotates the job with an explicit "this is an ENVIRONMENT failure, do not debug the specs" error and the download inventory (e.g. `download: truncated (39 files, 431 KB)`). The launch is retried once after a pause before that verdict, because several jobs share the m4-air's cache and a launch can lose a transient race. `--repair` then deletes the pinned revision (both downloads) and re-fetches it, so the runner heals without hands-on access. The check also runs on `js-live`, `js-windows` (whose cache is restored by `actions/cache` — same truncation hazard) and `update-visual-baselines` (a golden image generated by a half-installed browser would be committed and then enforced).

Both scripts are covered by `tests/python/test_ci_workspace_hygiene.py` (behaviour against a synthetic git repo, plus assertions that `ci.yml` still wires them in and still keeps the macos legs advisory) and `tests/js/ci-playwright-check.test.js` (the pure helpers in `scripts/playwright-health-lib.mjs`), because a mistake in CI-only glue is otherwise only discoverable by a full CI round trip on an advisory leg — as the size-floor regression above demonstrated.

`pnp-e2e`'s macos leg needs neither guard: it takes a default (clean) checkout and uses no browser.

**Fork PRs**: all self-hosted jobs are skipped for security, and `JS Lint & Test (ubuntu)` deliberately **fails** so the PR can't show green without tests. A maintainer must push the branch into the repo to run CI.

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
| `[ci: quality]` | Visual/a11y: contrast, style-audit, accessibility E2E (warns, never blocks) | ~49s |
| `[ci: distributors]` | Real LCSC + Pololu fetches — network only, no secrets (advisory) | ~20s |

### `[ci: hosted]` — GitHub-hosted fallback (emergency escape hatch)

`[ci: hosted]` is a **modifier**, not a suite: it can appear alone or alongside a
suite tag (e.g. `[ci: python] [ci: hosted]`). Use it when the self-hosted runners
(the home k3s cluster) are down and the merge queue is frozen because the required
checks can never start. With the tag present on the PR's head commit:

- All self-hosted jobs are **skipped** (js, js-e2e, js-live, js-windows, python,
  vlm-gpu, pnp-e2e, quality — nothing queues on a dead runner).
- The blocking lint/unit legs rerun on GitHub-hosted `ubuntu-latest` instead:
  `js-hosted` (eslint + tsc + vitest core) and `python-hosted` (ruff + staleness
  guards + pytest, with tesseract installed via apt so the OCR tests still run).
- The required gate contexts (`JS Lint & Test (ubuntu)`, `Python Lint & Test
  (ubuntu)`, `PnP E2E (required)`) aggregate the hosted jobs and go green/red on
  their results.

The trade-off is deliberate: hosted mode has **no E2E coverage** (Playwright
functional/live, win11, PnP). It exists so lint + unit verified work can still
merge during an infra outage — don't use it routinely. Untagged runs are
completely unaffected (no double-running, no added latency). It only applies to
PR runs: pushes to main and fork PRs always ignore it.

## Live distributor test tier

The `live` pytest marker gates every test that hits a real distributor endpoint. Two further markers say *what else* a live test needs, so a run can ask for a subset instead of all-or-nothing:

| Marker | Means | Tests |
|--------|-------|-------|
| `live` | Hits a real endpoint. Deselected by default via `addopts` in `pyproject.toml`. | all of the below |
| `browser` | Additionally needs the shared Chrome named by `DUBIS_CDP_URL`. | `tests/python/test_distributor_browser.py` |
| `credentials` | Additionally needs a secret that lives only on Isaac's machine. | `test_mouser_live` (API key), `test_digikey_session_live` (cookies), the two Windows-only DigiKey session tests |

`browser` and `credentials` never appear alone, so `pytest -m live` still means exactly what it always did — everything. The split exists so a run can ask for the part it can actually satisfy: CI runs `-m "live and not credentials and not browser"`, which is LCSC and Pololu.

**Run locally** when touching distributor clients, normalizers, `browser_page.py`, or `scripts/capture-distributor-fixtures.py`, and before merging that work:

```bash
pytest -m live   # requires network, cached DigiKey cookies (data/digikey_cookies.json),
                 # and a Mouser API key (data/mouser_credentials.json)
                 # missing credentials → actionable failure, not a skip
DUBIS_CDP_URL=http://browser-x.browser.svc.cluster.local:9222 pytest -m browser
```

An unset `DUBIS_CDP_URL` in a `browser` run is a **failure**, not a skip: the marker is the opt-in, so by the time one of these is selected the operator has already asked for a real browser fetch, and a green skip would report success for work that never happened.

Latency is recorded and printed; there are no threshold assertions. If live runs reveal upstream API drift, refresh the committed fixtures:

```bash
python scripts/capture-distributor-fixtures.py
git add tests/fixtures/generated/distributor-scrapes.json
```

The capture → commit → replay flow: `scripts/capture-distributor-fixtures.py` writes `tests/fixtures/generated/distributor-scrapes.json`; `tests/python/test_normalizers.py` replays it offline. Five blocks, not four — Mouser is captured twice: `mouser` is Search API JSON (needs a key), `mouser_product` is the normalized product a *deployed* dubIS server returns from `GET /v1/distributors/mouser/product/{code}` (needs an API token, no browser here). See `distributor_fixtures.CAPTURE_BLOCKS`. Design doc: `docs/plans/2026-05-31-live-distributor-test-tier-design.md`.

### Why nothing in CI speaks CDP

`mouser_client`'s keyless path needs a browser, and the only browser available is the cluster's shared Chrome (`browser-x` in namespace `browser`). Reaching it from a runner would mean labelling `arc-runners` with `browser-client=enabled`, because a `default-deny-ingress` plus `allow-browser-clients` policy admits port 9222 only from labelled namespaces.

That label was **considered and rejected**. CDP is unauthenticated, so it lets anything in the namespace read `contexts[0]`'s cookies and act as every identity the profile holds — including a live X account. CI executes branch code, and while fork PRs already cannot run self-hosted CI (see parse-tags step 0), that only closes the anonymous path, not the "a branch I pushed does something I did not read" one.

A second, dedicated Chrome with no personal logins does **not** solve it either, and it is worth writing down why so nobody re-proposes it. Cluster egress is a single NAT with no per-namespace SNAT, so a second browser has the same public IP. The same image gives it the same UA, Chrome version, TLS fingerprint, canvas/WebGL and fonts. The only real difference is the cookie jar, and that difference runs the wrong way: a virgin profile has no anti-bot history, which is itself a mild bot signal. Sharing the aged profile is the better position for the app, so the app keeps it and CI gets nothing.

**What CI does instead** splits capture from parsing, because they need different trust levels and only one of them needs a browser:

| Job | Runs | Needs | Catches |
|-----|------|-------|---------|
| Offline replay (`test_normalizers.py`, in the normal Python job) | every PR | nothing | our code breaking against committed bytes |
| `distributors` | PRs touching distributor code, pushes to main | a network | the LCSC/Pololu clients' own fetch code rotting |
| `refresh-fixtures.yml` weekly | schedule | a dubIS API token | Mouser/LCSC/Pololu changing upstream |

Routing capture through the deployed server would be wrong for a parser test — it tests deployed code, not the branch's — but it is exactly right for capture, whose whole point is recording what *upstream* returns.

### The `distributors` CI job

`ci.yml`'s `distributors` job runs `pytest -m "live and not credentials and not browser"` on `arc-dubis` — LCSC and Pololu over plain HTTP. No secrets, no grants, no cluster access. It is selected by a `[ci: distributors]` tag, by `-f suite=distributors`, by a push to main, or automatically when a PR touches a distributor client, normalizer, `browser_page.py`, `distributor_fixtures.py`, or the capture script.

What it covers that nothing else does is the **clients' own fetch code** — headers, timeouts, error handling, `_fetch_raw`. The offline replay tests exercise the normalizers against committed bytes and never call a client, and the capture script has its own duplicate fetch implementations, so without this job `lcsc_client._fetch_raw` could rot untouched with every suite green.

It is **advisory** (`continue-on-error`, in no required gate) for the same reason the single-box legs are: two third-party websites, and an LCSC outage must not block every merge.

### Scheduled fixture auto-refresh (`refresh-fixtures.yml`)

A scheduled workflow keeps the credential-free fixtures fresh: LCSC and Pololu over plain HTTP, and `mouser_product` by asking the deployed dubIS server. It runs weekly (Mondays 06:00 UTC) and on manual `workflow_dispatch`, invoking `capture-distributor-fixtures.py --refresh-if-stale` twice — once `--public-only`, once `--server-only` — on the same internet-connected self-hosted runner as the Python job. Per-block `captured_at` timestamps drive it: the script re-captures only blocks older than 30 days (via `distributor_fixtures.stale_distributors`), so most weekly runs are a no-op.

The Mouser step is the only thing here that needs a credential, and it is a dubIS API token rather than a distributor secret. **Needs a human, once:** a repository secret `DUBIS_CI_TOKEN` whose value also appears in the `dubis-server-auth` Secret's `DUBIS_TOKENS` (the server runs `DUBIS_AUTH_MODE=on`). Nothing in this repo creates it. Until it exists the request 401s, the step is `continue-on-error`, and the LCSC/Pololu refresh proceeds untouched. The `dubis` namespace has no NetworkPolicy, so no label is involved in reaching its ClusterIP.

The `mouser` (API key) and `digikey` (cookies) blocks are deliberately skipped and stay local-only (`pytest -m live`). The script's own guard leaves an existing block untouched whenever it cannot capture a replacement — including when the server answers but every part comes back challenged — so no failure here can turn into a deleted fixture. When a fixture does change, the job does **not** push to protected `main`; it force-updates the `automation/refresh-fixtures` branch and opens (or reuses) a PR, so the normal offline replay tests validate the captured data before a human merges. Design doc: `docs/plans/2026-05-31-live-distributor-test-tier-design.md`.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| "Branch already merged" error on push | Pushing to a branch whose PR was squash-merged | Run `bash scripts/push-pr.sh` — it auto-creates a new branch |
| JS test fixture mismatch | Backend changed but fixtures not regenerated | `python scripts/generate-test-fixtures.py && git add tests/fixtures/generated/` |
| "Playwright browser unhealthy" error on a leg | That runner's cached browser is truncated/corrupt (a `playwright install` no-op — see Persistent-runner hygiene) | The `--repair` step already tried to re-download it; if it still fails, the runner's disk or network is the problem. Every E2E failure in that job is environmental — don't debug the specs |
| "Reused workspace has drifted" error on a macos leg | Tracked files in the m4-air workspace differ from the checked-out commit, so the staleness guards are checking the wrong bytes | The listed files show what drifted. Re-run; if it recurs, flip that leg's `checkout-clean` to `true` (costs a cold `npm install`, nothing more) |
| Playwright tests fail on one OS only | Genuine cross-platform rendering difference, or a spec measuring across more than one layout generation | Browser installs are no longer per-leg optional, so this is not a missing-browser problem. Check the failing spec for sequential measurements that assume no re-render between them |
| Visual snapshot "doesn't exist" / fails only on ubuntu | Golden-image baselines are per-platform; CI Linux runner lacks `-linux.png` | Regenerate on the runner — see [visual-testing.md](visual-testing.md) |
| `distributors` job red | LCSC or Pololu is down, slow, or changed shape | The job is advisory and blocks nothing. If the shape changed, the fixtures need a refresh too |
| refresh-fixtures: "HTTP 401 from the server" on the Mouser step | The `DUBIS_CI_TOKEN` repository secret is missing, or its value is not in the server's `DUBIS_TOKENS` | Human step, see above. Everything else in that run still refreshed |
| `pytest -m browser`: endpoint tests pass, every Mouser one fails | Whatever `DUBIS_CDP_URL` points at is a freshly launched or headless browser. Mouser reads the browser, not the address: a `--headless=new` Chrome gets an empty search page and a bot-blocked product page | Point it at a long-lived headful profile that also sees human traffic — the shared `browser-x`. This is not a code regression |
| Quality suite "fails" | Quality tier uses `continue-on-error: true` | These are warnings, not blockers — quality failures don't block merge |
| ruff or eslint fails after refactor | New file not covered by existing config | Check `pyproject.toml` excludes and `eslint.config.mjs` includes |

## Manual CI triggers (workflow_dispatch)

```bash
gh workflow run ci.yml --ref <branch>                          # all suites
gh workflow run ci.yml --ref <branch> -f suite=lint            # lint only, no tests
gh workflow run ci.yml --ref <branch> -f suite=js
gh workflow run ci.yml --ref <branch> -f suite=python
gh workflow run ci.yml --ref <branch> -f suite=pnp-e2e
gh workflow run ci.yml --ref <branch> -f suite=quality
```

## PnP E2E Tests

Same-machine (CI default): `python tests/pnp-e2e/run_test.py`
Cross-compute (local only): `python tests/pnp-e2e/run_test.py --remote-openpnp ux430`

The `--remote-openpnp` flag starts dubIS locally and launches OpenPnP on a remote
host via SSH. Uses `~/.ssh/config` for connection settings. Cross-compute tests
verify the real network path (dubIS ↔ OpenPnP over Tailscale). The cross-compute
CI jobs (`if: false` since 2026-06-13, regressed during the OpenPnP→handheld
migration) were deleted in 2026-07 — recover them from git history if the
cross-compute path is ever fixed and worth re-gating.
