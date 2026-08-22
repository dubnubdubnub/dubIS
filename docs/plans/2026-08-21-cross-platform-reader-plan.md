# Cross-platform picture/PDF reader — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development.
> Steps use checkbox (`- [ ]`) syntax. Each task: write the failing test FIRST, run it,
> watch it fail, implement, re-run, commit.

**Spec:** `docs/plans/2026-08-21-cross-platform-reader-design.md`

**Goal:** the "Install Tesseract" button becomes "Install picture/PDF reader" —
cross-platform, on-demand download with a progress bar/percentage/status text,
plus an uninstall button; a `reader_mode` preference (`off`/`local`/`remote`/`auto`)
mirroring `server_url`; and y740's llamacpp becomes a real fleet node so the
remote leg is discovered rather than hardcoded.

**Two repos.** Tasks 1-12 + 14 are dubIS. Task 13 is `github.com/dubnubdubnub/infra`
and ships as its own PR.

---

## File Structure

| File | Responsibility | New/Modify |
|---|---|---|
| `reader_memory.py` | The seven memory probes; `detect_budget() -> BudgetInfo \| None` | Create |
| `reader_tiers.py` | Pinned model table; `choose_tier(budget) -> Tier \| None` | Create |
| `reader_install.py` | Download + sha256 + atomic rename + progress callback; uninstall | Create |
| `reader_runtime.py` | llama.cpp release URL/sha table per platform; spawn/health/stop | Create |
| `reader_jobs.py` | Install job registry + status state machine | Create |
| `fleet_client.py` | `GET /fleet?need_caps=vision`, `POST /leases`, renew, release | Create |
| `vlm_extract.py` | Accept explicit endpoint + bearer token, not just env | Modify |
| `ocr_layout.py` | Stop gating the VLM behind `require_tesseract()` | Modify |
| `domain/api_scan.py` | Drop `install_tesseract`; reader status/verify | Modify |
| `client_shell.py` | `start_reader_install`, `get_reader_install_status`, `uninstall_reader`, `get_reader_status` | Modify |
| `inventory_api.py` | Facade wiring | Modify |
| `server/routes/preferences.py` + `server/models.py` | `reader_mode` / `reader_url` | Modify |
| `js/api.js` | Replace `installTesseract` with the reader methods | Modify |
| `js/reader/reader-progress-logic.js` | **Pure**: phase -> label, byte formatting, pct clamping | Create |
| `js/reader/reader-panel.js` | Preferences UI: mode select, Install + progress, Uninstall + confirm | Create |
| `js/import/import-renderer.js` | Notice text: reader, not winget | Modify |
| `js/import/import-panel.js` | Point the in-zone button at the reader flow | Modify |
| `css/components/reader.css` | Progress bar + status text | Create |
| `tests/python/test_reader_memory.py` … `test_fleet_client.py` | Backend tests | Create |
| `tests/python/test_api_surface.py` | Frozen shell surface — must be updated | Modify |
| `tests/js/reader-progress-logic.test.js` | Pure-logic unit tests | Create |
| `tests/js/e2e/reader-install.spec.mjs` | E2E with a stubbed backend | Create |

---

## Task 1: memory probes

- [ ] Failing test `tests/python/test_reader_memory.py`: monkeypatch `subprocess.run`
      and `sys.platform` per case. Assert (a) nvidia-smi total+used parsed;
      (b) **largest** adapter wins when two are present, not the first;
      (c) `Win32_VideoController.AdapterRAM` is never consulted;
      (d) every probe returns `None` on `OSError` / non-zero exit / garbage;
      (e) Apple path applies the wireable fraction and honours a non-zero
      `iogpu.wired_limit_mb`.
- [ ] Implement `reader_memory.py`. Start from `git show ca07608 -- vlm_extract.py`
      for the nvidia-smi probe; it is the same code, restored.
- [ ] Run: `.venv/bin/python -m pytest tests/python/test_reader_memory.py -q`

**Fixture values to use verbatim (measured, not invented):** 3090 total
`25769803776`, `Dedicated Usage` 20.1 GiB, the broken `AdapterRAM`
`4293918720`, AMD iGPU `536870912`, `hw.memsize` `137438953472`.

## Task 2: tier selection

- [ ] Failing test `test_reader_tiers.py`: budget -> tier table from the spec
      (<5 GiB None; >=5 3B; >=10 7B); free-not-total is what is passed in; every
      tier entry has repo, filename, mmproj, both sha256s, pinned revision, ctx.
- [ ] Implement `reader_tiers.py`. **Verify every sha256 and filename against the
      live HF API before writing it down** — a wrong hash fails only at install time.
- [ ] Run: `.venv/bin/python -m pytest tests/python/test_reader_tiers.py -q`

## Task 3: download + verify + uninstall

- [ ] Failing test `test_reader_install.py`: serve bytes from a local
      `http.server`; assert progress callback monotonic and terminal `pct == 100`;
      checksum mismatch raises and leaves **no** file at the final path; a
      `.part` from a previous run is not mistaken for a complete file; an
      already-correct file is skipped (idempotent, like the init container).
- [ ] Uninstall tests: deletes only `<data_dir>/reader/`; refuses a caller-supplied
      path; does not follow a symlink out of the tree; idempotent on a missing
      dir; reports reclaimed bytes.
- [ ] Implement `reader_install.py`.
- [ ] Run: `.venv/bin/python -m pytest tests/python/test_reader_install.py -q`

## Task 4: llama.cpp runtime acquisition + supervision

- [ ] Failing test `test_reader_runtime.py`: the platform->release table covers
      darwin-arm64, win-cuda, win-cpu, linux-cuda, linux-cpu, each with a pinned
      tag and sha256; an unknown platform returns a typed error, not a crash.
      Spawn/health/stop against a fake executable; port is chosen free; stop is
      idempotent; the child does not outlive the app.
- [ ] Implement `reader_runtime.py`.
- [ ] Run: `.venv/bin/python -m pytest tests/python/test_reader_runtime.py -q`

## Task 5: job/status state machine

- [ ] Failing test `test_reader_jobs.py`: phases advance
      detect->runtime->weights->projector->start->verify->done; an exception
      lands in `error` with the message preserved; status is pollable
      concurrently; a second `start` while one runs returns the same job id
      rather than starting a second download.
- [ ] Implement `reader_jobs.py`.
- [ ] Run: `.venv/bin/python -m pytest tests/python/test_reader_jobs.py -q`

## Task 6: unhook the VLM from tesseract

- [ ] Failing test in `test_ocr_layout_backend.py`: with tesseract absent and a
      VLM reachable, `extract_pages` returns VLM rows instead of raising
      `TesseractMissingError`. This fails today because
      `ocr_layout.extract_pages` calls `require_tesseract()` at line ~72, before
      rasterising and before the VLM is ever consulted — the VLM is currently
      **unreachable without tesseract installed**.
- [ ] Move the gate so tesseract is required only when word boxes or the
      tesseract fallback are actually needed. Overlay word/line tokens still come
      from tesseract when present; when absent, return VLM rows with empty
      `words`/`lines` and let the frontend render without highlight.
- [ ] Run: `.venv/bin/python -m pytest tests/python/test_ocr_layout_backend.py tests/python/test_ocr_layout.py -q`

## Task 7: fleet client

- [ ] Failing test `test_fleet_client.py`: mocked HTTP. `GET /fleet?need_caps=vision`
      picks the top-ranked entry; `stale`/`unhealthy` are absent from the response
      and so are never chosen; a `409` from `POST /leases` surfaces the named
      holder; renew and release are called; a lease is released on shutdown; the
      registry being unreachable returns `None` rather than raising.
- [ ] Implement `fleet_client.py`. Registry `https://fleet.miku-parore.ts.net`
      (tailnet) or `http://fleet-registry.fleet.svc.cluster.local` (in-cluster).
- [ ] Run: `.venv/bin/python -m pytest tests/python/test_fleet_client.py -q`

**Leases are cooperative hints, not a mutex** (`fleet-registry/README.md`) — a
registry restart drops them. Do not build anything that assumes exclusivity.

## Task 8: vlm_extract takes an endpoint + token

- [ ] Failing test: `extract_line_items(..., endpoint=..., token=...)` targets that
      base URL and sends `Authorization: Bearer`; env still works when no
      endpoint is passed (regression); no token means no header.
- [ ] Implement. Keep `DUBIS_VLM_URL` / `DUBIS_VLM_MODEL` / `DUBIS_VLM_DISABLE`.
- [ ] Run: `.venv/bin/python -m pytest tests/python/test_vlm_extract.py -q`

## Task 9: preferences

- [ ] Failing test `test_preferences_reader.py`: default `reader_mode == "off"`;
      the four values round-trip; an invalid value is rejected; `reader_url`
      round-trips; existing prefs without the keys load unchanged.
- [ ] Implement, mirroring `server_url` from `a060a1a` (including restart-to-apply
      semantics). Touch `server/routes/preferences.py` + `server/models.py`.
- [ ] Run: `.venv/bin/python -m pytest tests/python/test_preferences_reader.py -q`
- [ ] Regenerate `docs/openapi-v1.json` and `js/api-map.js` if the route shape changed.

## Task 10: client shell + facade

- [ ] Update `tests/python/test_api_surface.py` FIRST — it freezes the surface, so
      it fails until the new methods exist and `install_tesseract` is gone.
- [ ] Add `start_reader_install`, `get_reader_install_status`, `uninstall_reader`,
      `get_reader_status` to `client_shell.py` + `inventory_api.py`; delete
      `install_tesseract` from `domain/api_scan.py`, `inventory_api.py`,
      `client_shell.py`.
- [ ] Run: `.venv/bin/python -m pytest tests/python/test_api_surface.py -q`

## Task 11: pure frontend logic

- [ ] Failing test `tests/js/reader-progress-logic.test.js`: phase -> human label
      for all seven phases; byte formatting (0, 999, 1 KiB, 4.7 GiB);
      pct clamped to 0-100 and `null` when `bytes_total` is unknown; an
      indeterminate phase (`detect`, `start`) reports no percentage rather than 0.
- [ ] Implement `js/reader/reader-progress-logic.js` — pure, no DOM, no store.
- [ ] Run: `npx vitest run tests/js/reader-progress-logic.test.js`

## Task 12: Preferences UI + import-zone notice

- [ ] `js/reader/reader-panel.js`: mode select, Install button, progress bar +
      percentage + status line, Uninstall button behind a confirm that names the
      directory and the reclaimed bytes. Poll `get_reader_install_status` on a
      timer; stop polling on `done`/`error`; survive a mid-install panel close.
- [ ] `css/components/reader.css`. **Layout dims go in `css/tokens.css` as
      `--reader-*`** or `scripts/check-layout-tokens.py` fails. **Never author bare
      `vh`/`vw`** — use `calc(N * var(--vh))` from `css/tokens/scale.css`.
- [ ] Retire `TESSERACT_WINGET_COMMAND` and the "approve the Windows prompt"
      button text in `js/import/import-renderer.js` / `import-panel.js`.
- [ ] Run: `npx eslint js/ && npx tsc --noEmit && npx vitest run`
- [ ] Run: `npx playwright test reader-install sticky-buttons resize-visibility`

**Header trap:** `.header` is `flex-wrap: wrap`; a new control adds a ~28px row at
narrow widths and can shove panels off an 800x600 viewport. Put reader controls in
the Preferences modal, not the header.

## Task 13: y740 becomes a fleet node — **infra repo, separate PR**

Repo `github.com/dubnubdubnub/infra`, path `win-runners/`. Read `CLAUDE.md`,
`docs/adding-a-model-node.md` §5, and `win-runners/gpu/README.md` first.

- [ ] Nodeagent for llamacpp — its own Deployment (or sidecar) with `node.yaml`:
      `platform: linux`, `location: in-cluster`, `path: cluster`,
      `endpoint: http://llamacpp.win-runners.svc.cluster.local:8080`,
      `swaps: false`, and the model entry carrying **`capabilities: [vision]`**.
- [ ] **Bench the baseline.** Do NOT copy or invent a curve — the repo has two
      documented incidents of copied curves lying in both directions (wmauler's
      IQ3 understated by ~60%; a stale curve "quietly produces false verdicts in
      both directions"). A model with no baseline reads `unknown`, which is
      honest; a wrong one is not.
- [ ] Additive NetworkPolicy `win-runners/46-allow-dubis-llamacpp.yaml`: ns
      `dubis` -> `app=llamacpp` port 8080 only. Mirror
      `45-allow-monitoring.yaml`'s shape and comment style. Do **not** widen
      `restrict-broker-vlm-ingress`.
- [ ] **Fix the missing `ephemeral-storage` request/limit on llamacpp.** Its own
      README mandates one for anything on y740 ("the eviction order is decided by
      requests you did or did not declare") and the Deployment declares none —
      on the node that already had a DiskPressure outage on 2026-08-06.
- [ ] Register new files in `win-runners/kustomization.yaml`.
- [ ] Any new ServiceMonitor needs `labels: { release: kps }` or it is **silently
      ignored**.
- [ ] Update `win-runners/gpu/README.md` and `docs/inventory.md`
      (`scripts/gen_docs.py`) — `docs-check` fails a PR that skips the regen.

## Task 14: docs

- [ ] `docs/install.md` — replace the tesseract-first framing; **fix the existing
      contradiction** (install.md says the 7B is "~6 GB VRAM at 4-bit";
      `vlm_extract.py:48` says "~9 GB". The second is closer).
- [ ] `README.md:24`, `CLAUDE.md` (traps + the client-shell method count).
- [ ] `python scripts/gen-code-map.py` for the new modules.

---

## Final verification (all gates before PR)

```bash
bash scripts/verify.sh          # fixtures/code-map/manifests/layout-tokens guards + ruff + pytest + eslint + tsc + vitest
npx playwright test
```

Then `bash scripts/push-pr.sh` and watch CI to green.

## Notes / gotchas

- **The VLM is unreachable without tesseract today** (Task 6). Any manual test of
  the local reader on a tesseract-less Mac fails at `require_tesseract()` before
  the model is consulted — fix Task 6 before concluding the reader is broken.
- **`--mmproj` is not optional.** Without it llama-server loads text-only and
  every image is silently ignored, with no error.
- **Regenerate fixtures after backend changes** or vitest fails with confusing
  value mismatches: `python scripts/generate-test-fixtures.py`.
- **Never `pytest.skip` / `importorskip`** — add to `requirements-dev.txt`. The
  `gpu` marker is the right mechanism for live-server tests.
- **Do not commit model weights or `data/signal-*.jpeg`** (PII). Tests build
  images synthetically with Pillow.
- **`_MAX_EDGE` is shared.** Lowering it for the VLM must not change the
  tesseract/grid path, whose thresholds are tuned for ~2600px.
- Uninstall deletes GiBs of the user's disk. Confirm first, name the directory,
  never accept a caller-supplied path.
