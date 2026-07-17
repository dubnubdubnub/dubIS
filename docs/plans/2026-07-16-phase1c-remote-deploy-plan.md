# Phase 1c — Remote Deployment, Auth, Docker — Implementation Plan (PR 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** auth middleware + identity stamping, data-dir lockfile, error-contract fixes, Dockerfile, `deploy/` kustomize manifests, GHCR build workflow, remote desktop mode, deploy runbook.

**Architecture:** per `docs/plans/2026-07-16-phase1c-remote-deploy-design.md` (binding). Mirror retirement is PR 2 — NOT this plan.

## Global Constraints

- Worktree `D:/gehub/dubIS/.claude/worktrees/platform-phase1c-remote`, branch `claude/platform-phase1c-remote`.
- TDD failing-first. Gates per task: focused pytest; before each commit `python -m pytest tests/python/ -q` + `ruff check .`; final task runs `bash scripts/verify.sh`. Full-log capture + explicit exit codes; never pipe through tail for gating.
- Error contract everywhere: `{error, code, detail}`. No pytest.skip. Auth `off` mode must stay byte-identical to today (frozen-surface tests + E2E must pass untouched).
- Tests use the real-server harness (`start_live_server` / `python -m server`), no HTTP mocks.

---

### Task 1: Data-dir lockfile

**Files:** Create `server/lockfile.py`, `tests/python/server/test_lockfile.py`; Modify `server/run.py` (acquire in `start_server` next to `.v1_port`, release in `stop_server`), `server/__main__.py`, `app.pyw` (user-visible dialog on `DataDirLockedError`), `dubis_errors.py` (new typed error).

- `acquire_lock(data_dir) -> LockHandle`: exclusive OS lock on `<data_dir>/.dubis_lock` (Windows `msvcrt.locking`, POSIX `fcntl.flock`), file content = `{pid, port}` JSON updated after bind. Never stale (OS releases on death). `DataDirLockedError` includes the other process's pid/port read from file content.
- Tests: second acquire on same dir raises with pid in message; released handle allows re-acquire; crashed-process file (content present, no lock held) acquires fine. Two live servers on one tmp data dir: second `python -m server` exits non-zero with the error on stderr.

Commit `feat(server): data-dir lockfile — fail fast on second server (1c)`.

### Task 2: Auth middleware + identity stamping

**Files:** Create `server/auth.py`, `tests/python/server/test_auth.py`; Modify `server/app.py` (install middleware from env), `server/mutations.py` or route layer (source stamping), `docs/` per design §1.

- Env contract per design table (`DUBIS_AUTH_MODE`, `DUBIS_TOKENS`, `DUBIS_TAILNET_ALLOWLIST`, `DUBIS_TRUST_TAILSCALE_HEADER`). Resolution order: loopback → bearer → tailscale header → 401. `/v1/health` exempt. Cookie fallback: `POST /v1/auth/session` with a valid bearer sets an HttpOnly cookie for browser static/API access (design §7), enabled only in `on` mode.
- Identity on `request.state.identity`; non-local identities appended to mutation `source` (`{client_source}@{identity}`, plain identity when client sends none). `off` mode: middleware not installed at all — zero behavior change (prove: full existing `tests/python/server/` suite green untouched).
- Tests (live server with env set): each resolution path, 401 shape `{error, code:"unauthorized"}`, header ignored when trust flag unset (spoof test), source stamping visible in adjustments via `/history`, health exempt, cookie session flow.

Commit `feat(server): auth — bearer tokens + tailnet allowlist + identity-stamped source (1c)`.

### Task 3: Error-contract carry-overs

**Files:** Modify `server/app.py`/`server/errors.py` (404 + 422 handlers), `server/routes/*` for `/v1/import/parse` loopback gate; Tests in `tests/python/server/test_v1_surface.py` area + new cases.

- Unknown route → `{error, code:"not_found", detail}` 404; validation error → `{error, code:"validation_error", detail:<field errors>}` 422. `/v1/import/parse` (and audit: any other route accepting server filesystem paths) → 403 `{code:"loopback_only"}` for non-local identities even when authed. Audit note recorded in the test file.
- Regen `docs/openapi-v1.json` if route metadata changes (`python scripts/gen-openapi.py`), regen api-map if needed.

Commit `fix(server): uniform error contract for framework 404/422 + loopback-only import paths (1c)`.

### Task 4: Dockerfile + smoke script

**Files:** Create `Dockerfile`, `.dockerignore`, `scripts/smoke-remote.sh`; Modify `docs/` mentions.

- Per design §4: python:3.12-slim, requirements.txt, backend+frontend copied, `python -m server --data-dir /data --host 0.0.0.0 --port 8080 --static-dir /app`, HEALTHCHECK /v1/health, VOLUME /data. `.dockerignore`: data/, tests/, node_modules/, .git, docs/, .claude*.
- If docker is available locally, build + run + curl /v1/health `{ok:true}` and record output in the task report; if unavailable, hadolint-style self-review + the CI build becomes the verification (note it explicitly — no false-green claims).
- `smoke-remote.sh <base_url> [token]`: checks health, authed parts list, 401 without token; used by runbook.

Commit `feat(deploy): dubis-server container image (1c)`.

### Task 5: deploy/ manifests + Argo Application

**Files:** Create `deploy/kustomization.yaml`, `deploy/namespace.yaml`, `deploy/deployment.yaml`, `deploy/pvc.yaml`, `deploy/service.yaml`, `deploy/ingress.yaml`, `deploy/argocd-application.yaml`.

- Per design §5 + cluster answers: replicas 1, Recreate, envFrom secret `dubis-server-auth`, longhorn-r2 RWO 1Gi PVC in Longhorn group `app-data` (recurring-job label convention: `recurring-job-group.longhorn.io/app-data: enabled` — verify exact key against Longhorn docs), liveness/readiness `/v1/health`, resources requests/limits set (Kyverno), namespace labeled `ghcr-pull=enabled`, tailscale ingress host `dubis-server`, image `ghcr.io/dubnubdubnub/dubis-server:PLACEHOLDER_SHA` pinned via kustomization `images:`.
- Validate: `kubectl kustomize deploy/` renders (kubectl available? else `kustomize` via npx/none — fall back to python yaml parse asserting required fields in a pytest `tests/python/test_deploy_manifests.py`: replicas==1, strategy Recreate, no :latest, resources present, storageClassName longhorn-r2). The pytest guard is required either way (keeps manifests honest in CI).

Commit `feat(deploy): kustomize manifests + Argo application for k3s (1c)`.

### Task 6: build-image.yml CI workflow

**Files:** Create `.github/workflows/build-image.yml`.

- On push to main (paths: *.py, js/, css/, index.html, requirements.txt, Dockerfile, deploy/): docker/build-push-action → `ghcr.io/dubnubdubnub/dubis-server:<sha>` + `:main`, `permissions: packages: write, contents: write`; then sed the sha into `deploy/kustomization.yaml` and push with `[skip ci]` + `paths-ignore` self-guard + concurrency group. ubuntu-latest.
- Static validation only in this PR (actionlint if available / YAML parse pytest); the workflow exercises for real on merge — watch it then.

Commit `ci: build + push dubis-server image to GHCR with sha write-back (1c)`.

### Task 7: Remote desktop mode + MCP token

**Files:** Modify `app.pyw`, `client_shell.py` (if needed), `splash.html` (remote health poll URL), `tools/dubis-mcp/v1client.py` (+ its tests); Create `tests/python/test_remote_mode.py`.

- `DUBIS_URL` env or `server_url` in preferences.json → skip spawn + lockfile, splash polls `<url>/v1/health`, navigate there. Unset → today's path byte-identical.
- v1client: `DUBIS_TOKEN` env → `Authorization: Bearer` header on every request.
- Tests: remote mode against a locally-started `python -m server` with auth on (token via env) — window-free where possible (unit-test the URL-resolution + spawn-skip logic; the splash poll logic is already E2E-covered, extend the existing pattern only if cheap). MCP client test: authed server + DUBIS_TOKEN roundtrip; 401 without.

Commit `feat(app): remote server mode — DUBIS_URL/preferences, MCP bearer token (1c)`.

### Task 8: Runbook + docs + verify + PR

**Files:** Create `docs/deploy-runbook.md` (design §8: secret from stdin, Argo apply, PVC data seeding via temp pod, identity-header curl check + decision point, ARC smoke via cluster DNS, Longhorn group check, mirror uninstall AFTER parity); Modify `CLAUDE.md` (deploy/ row, auth envs, remote mode trap if any), regen code-map.

- `bash scripts/verify.sh` full PASS (full log + explicit exit).
- `bash scripts/push-pr.sh --title "feat(deploy): remote dubis-server — auth, docker, k3s manifests, remote desktop mode (Phase 1c)"`; watch CI to green; controller merges via merge-prs.sh.

Commit `docs(deploy): runbook + CLAUDE.md for remote deployment (1c)`.
