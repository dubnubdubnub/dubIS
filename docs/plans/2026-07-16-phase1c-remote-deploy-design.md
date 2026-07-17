# Phase 1c — Remote Deployment, Auth, Docker — Design

**Date:** 2026-07-16
**Parent:** `docs/plans/2026-07-15-platform-architecture-design.md` (Phase 1c). Builds on 1a/1b/2 (PRs #359/#360/#361).
**Cluster inputs (binding):** answers from the cluster-context session, 2026-07-16 — k3s + Argo GitOps, `longhorn-r2` StorageClass + `app-data` backup group, GHCR pulls via `ghcr-pull=enabled` namespace label (Kyverno), Tailscale k8s operator with ingress class `tailscale`, sha-pinned images with CI write-back to `deploy/kustomization.yaml`, no `:latest`, secrets = k8s Secrets created out-of-band, CI smoke via cluster DNS (`http://dubis-server.<ns>.svc`), cluster-internal traffic carries NO Tailscale identity.
**Standing decisions (owner):** auth = loopback trusted + tailnet allowlist + bearer tokens, identity stamped in `source`; docker → ghcr.io (Isaac provisions cluster-side); mirror retired after parity; OOTB desktop stays zero-config.

## Purpose

One always-on `dubis-server` on the tailnet becomes the authoritative inventory server. Humans reach the web UI at `https://dubis-server.<tailnet>.ts.net`; agents/OpenPnP reach `/v1` with a bearer token or tailnet identity; the desktop app can point at it instead of spawning locally. The mirror daemon (read-only, bespoke) is retired once the deployed server reaches parity.

## Scope split — two PRs

- **PR 1 (this repo, all code):** auth layer, lockfile, error-contract carry-overs, Dockerfile + compose example, `deploy/` kustomize manifests, `build-image.yml` CI workflow, remote-mode desktop client, docs + runbook.
- **PR 2 (after deployment verified):** mirror retirement (`inventory_mirror.py`, `mirror_install/`, `domain/api_mirror.py`, their tests, CLAUDE.md rows). Gated on the deployed server answering on the tailnet — parity per the standing decision. Isaac uninstalls the local mirror task himself (runbook step).

## 1. Auth layer (`server/auth.py`)

Starlette middleware on the FastAPI app, configured entirely by env (12-factor; container-friendly):

| Env | Meaning |
|---|---|
| `DUBIS_AUTH_MODE` | `off` (default — today's behavior, loopback deployments) or `on` (remote mode) |
| `DUBIS_TOKENS` | comma-separated `name:token` pairs, e.g. `ci:abc123,openpnp:xyz` — bearer tokens with a stable identity name |
| `DUBIS_TAILNET_ALLOWLIST` | comma-separated tailnet logins trusted via `Tailscale-User-Login` header (mirror's exact pattern) |
| `DUBIS_TRUST_TAILSCALE_HEADER` | `1` only when a tailscale proxy (operator ingress / `tailscale serve`) fronts the server; otherwise the header is ignored (spoofable) |

Resolution order per request, when mode is `on`:
1. Loopback peer (`request.client.host` in `127.0.0.0/8`, `::1`) → identity `local`, allowed. (In-container the tailscale/k8s proxies do NOT arrive as loopback, so this only trusts true same-host callers.)
2. `Authorization: Bearer <token>` matching `DUBIS_TOKENS` → identity = the token's name.
3. `Tailscale-User-Login` header, when `DUBIS_TRUST_TAILSCALE_HEADER=1` and login ∈ allowlist → identity = login.
4. Otherwise → `401 {error, code:"unauthorized", detail}` (the standard error contract).

**Identity → source stamping:** middleware stores identity on `request.state.identity`. Mutation routes that accept a `source` field compose it: client-supplied source preserved, identity appended when the request is non-local — e.g. `mcp@ci`, `openpnp@isaac@github`. Local/`off`-mode behavior is byte-identical to today (no suffix), so desktop ledger rows don't change. `/v1/health` stays unauthenticated (probes, k8s liveness). Static frontend assets are served only to authenticated identities in `on` mode (the UI is useless without API access anyway; one gate for everything).

**Verification-first note:** whether the operator ingress injects `Tailscale-User-Login` is UNVERIFIED on this cluster. Bearer tokens are therefore the baseline that everything works with; tailnet identity is an enhancement toggled by env after the header is confirmed (runbook includes the curl header-dump check).

## 2. Data-dir lockfile (carry-over from Phase 2)

`<data_dir>/.dubis_lock` acquired at server startup (Windows: `msvcrt.locking`; POSIX: `fcntl.flock` — held for process lifetime, auto-released on death, never goes stale). Second server on the same data dir fails fast with a clear error naming the PID/port from the lockfile content. `server/run.py` owns it next to the `.v1_port` write. The MCP spawn fallback then can't race the desktop app. Lock failure in `app.pyw` → user-visible error dialog (server already running), not a silent broken window.

## 3. Error-contract carry-overs (from 1a/1b reviews)

- Framework-generated 404 (unknown route) and 422 (validation) responses get exception handlers so they emit `{error, code, detail}` like every hand-written error. Contract test added to `test_v1_surface.py` territory.
- `/v1/import/parse` (accepts a filesystem path) becomes **loopback-only regardless of auth** — a remote bearer identity must not read server-local files. 403 with a clear detail. Same for any other path-accepting route found by audit (`open_source_file` is ClientShell-side, not /v1 — audit confirms).

## 4. Container image

`Dockerfile` (repo root): `python:3.12-slim`, install `requirements.txt`, copy backend + frontend (`index.html`, `js/`, `css/`, domain/, server/, the `*.py` modules), entrypoint `python -m server --data-dir /data --host 0.0.0.0 --port 8080 --static-dir /app`. `VOLUME /data`; healthcheck hits `/v1/health`. Distributor scraping that needs WebView2/CDP (DigiKey) simply won't function in-container — those API methods already fail with typed errors; documented as desktop-only features. `.dockerignore` keeps data/, tests/, node_modules out.

Image: `ghcr.io/dubnubdubnub/dubis-server:<git sha>` (+ `:main` moving tag for humans; Kyverno only bans `:latest`... **verify**: if `:main` is also banned by policy just drop it).

## 5. `deploy/` manifests (kustomize, in this repo)

Per cluster answers, mirroring the onboard/minecraft pattern:
- `deploy/kustomization.yaml` — images sha-pin written back by CI.
- `namespace.yaml` (`dubis`, labeled `ghcr-pull=enabled`), `deployment.yaml` (replicas 1, `strategy: Recreate`, envFrom secret `dubis-server-auth`, PVC mount at `/data`, liveness+readiness on `/v1/health`), `pvc.yaml` (`longhorn-r2`, RWO, 1Gi, labeled into Longhorn RecurringJob group `app-data` per `backup/README.md` in the infra repo), `service.yaml` (ClusterIP :80→8080), `ingress.yaml` (ingressClassName `tailscale`, host `dubis-server`).
- `deploy/argocd-application.yaml` — Argo Application pointing at this repo's `deploy/`, `prune: false` initially. Applied by Isaac (runbook).
- Kyverno guardrails exist cluster-side; manifests must satisfy them (no `:latest`, resource requests/limits set).

## 6. CI: `build-image.yml`

Modeled on the infra repo's `docs/templates/build.yml` (per cluster answers) as closely as reproducible from this side: on push to `main` (paths: backend/frontend/Dockerfile/deploy), build + push `ghcr.io/dubnubdubnub/dubis-server:<sha>` with `GITHUB_TOKEN` (`packages: write`), then commit the sha into `deploy/kustomization.yaml` back to main (`[skip ci]`, guarded against loops). Runs on `ubuntu-latest` (GH-hosted; ARC pods lack docker-in-docker unless the infra template says otherwise — template copy is the source of truth; if the template turns out to require a specific runner label, note it in the PR for Isaac).

Also: an **in-repo smoke test** (regular pytest, not cluster-dependent): build the image locally in CI? No — docker-build-in-PR-CI is slow; instead the deployed-cluster smoke lives in the runbook (curl `http://dubis-server.dubis.svc` from an ARC pod) and a `scripts/smoke-remote.sh` helper is provided. PR CI keeps testing `python -m server` directly, which is the same code path minus the container.

## 7. Remote desktop client

`preferences.json` (client-side prefs) gains `server_url` (empty = default local behavior). When set (or env `DUBIS_URL`): `app.pyw` skips server spawn, splash health-checks the remote `/v1/health`, navigates the webview to the remote URL. Bearer token for the desktop comes from `DUBIS_TOKEN` env or a `data/server_token` file (never in preferences.json — that file gets committed to backups; token file pattern matches `data/mirror_token` precedent). Frontend attaches `Authorization` header — **but** navigation/static loads can't set headers → token delivered via `?token=` once and stored by the served page? NO — simpler: when the tailscale ingress fronts the server, humans are identified by tailnet identity (no token needed in the browser); the desktop remote mode uses the same tailnet path. Bearer tokens are for headless API clients (CI, OpenPnP, MCP with DUBIS_URL). This keeps the browser story header-free. If the identity header turns out not to be injected (open question), interim: `DUBIS_TAILNET_ALLOWLIST` unused, humans use a `?token=` bootstrap that sets a cookie; implement the cookie fallback only if the header check fails (runbook decision point) — code ships with header path + cookie fallback both, selected by env, since we cannot verify before merge.

MCP server: `DUBIS_URL` discovery already exists; v1client gains `Authorization` header from `DUBIS_TOKEN` env when set — small, in-scope.

## 8. Runbook (`docs/deploy-runbook.md`)

Step-by-step for Isaac/cluster-Claude: create namespace via Argo or label it, create the `dubis-server-auth` secret from stdin (kubectl create secret … --dry-run=client | apply), apply the Argo Application, first-deploy data seeding (kubectl cp the CSVs into the PVC via a temp pod — the server is authoritative from then on), the identity-header verification curl, smoke checks (ARC pod curl by cluster DNS + tailnet curl), Longhorn backup-group attachment check, and mirror uninstall (`mirror_install` teardown) once parity confirmed.

## Out of scope

Multi-user write arbitration beyond single-server (SQLite/CSV single-writer is the model); TLS termination (tailscale provides it); user management UI; CSV sync between desktop and remote instances (desktop remote mode = thin client, one data dir, the server's).

## Risks

- **Identity header unverified** → dual-path auth (bearer baseline + header behind env flag + cookie fallback), runbook settles it post-deploy.
- **CI write-back loops** → `[skip ci]` + path guard + concurrency group on the workflow.
- **In-container feature gaps** (DigiKey CDP, file dialogs, OCR deps size) → documented desktop-only list in the runbook; OCR system deps (tesseract) NOT baked in v1 image (import endpoints that need it return typed errors; revisit on demand — keeps the image small).
- **Data seeding is a one-time manual step** — runbook owns it; the lockfile prevents accidental double-writer during migration.
