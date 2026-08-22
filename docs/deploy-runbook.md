# dubis-server deploy runbook (Phase 1c)

Deploying the always-on `dubis-server` to the k3s cluster, so humans reach the
web UI at `https://dubis-server.<tailnet>.ts.net` and agents/OpenPnP reach
`/v1` with a bearer token or tailnet identity. Written for a reader who has
cluster context (kubectl, Argo, Longhorn, Tailscale operator) but not dubIS
internals — see `CLAUDE.md` and `docs/plans/2026-07-16-phase1c-remote-deploy-design.md`
for the code-side design.

This is **PR 1's** deploy (code + manifests already merged to `main`). The
local inventory mirror daemon is retired in a **separate, later** PR — do not
touch it as part of this runbook (step 9).

## 0. Prereqs

- `kubectl` access via `C:/Users/isaac/.kube/dubcluster-vip.yaml` (the cluster
  VIP kubeconfig — no SSH hop to a control-plane host needed):
  ```bash
  export KUBECONFIG=C:/Users/isaac/.kube/dubcluster-vip.yaml
  kubectl get ns
  ```
- The container image must already be built and pushed. This happens
  automatically: `.github/workflows/build-image.yml` builds
  `ghcr.io/dubnubdubnub/dubis-server:<sha>` on every push to `main` that
  touches backend/frontend/`Dockerfile`/`deploy/`, then commits the resulting
  sha back into `deploy/kustomization.yaml`'s `images[].newTag`. Confirm the
  tag isn't still the placeholder before applying anything:
  ```bash
  grep newTag deploy/kustomization.yaml   # must NOT read "sha-PLACEHOLDER"
  ```
  If it does, the workflow hasn't run yet (check
  `gh run list --workflow=build-image.yml`) — wait for it, don't hand-edit the
  tag.

## 1. Namespace

`deploy/namespace.yaml` creates the `dubis` namespace labeled
`ghcr-pull=enabled`. Apply it by hand — it is deliberately **not** in the
kustomization, because every AppProject on this cluster runs with
`clusterResourceWhitelist: []` and no Argo app manages cluster-scoped
objects. That makes this step the owner of the label, not Argo:
```bash
kubectl apply -f deploy/namespace.yaml
```
Then confirm the label triggered Kyverno's
pull-secret clone into the namespace (this is what lets the `dubis-server`
Pod actually pull the private `ghcr.io/dubnubdubnub/*` image):
```bash
kubectl get ns dubis --show-labels
kubectl get secret -n dubis | grep -i ghcr
```
If no `ghcr`-named secret shows up after a minute, check the Kyverno policy
logs before proceeding — the Deployment will otherwise sit in `ImagePullBackOff`.

## 2. Create the `dubis-server-auth` Secret (out-of-band, from stdin)

**Never** put tokens in git or in shell argv history — pipe them via stdin so
they never appear in `kubectl` command-line args (visible via `ps`/audit log)
or in the manifest tree:

```bash
kubectl create secret generic dubis-server-auth \
  --namespace dubis \
  --from-literal=DUBIS_AUTH_MODE=on \
  --from-literal=DUBIS_TOKENS='ci:<generate-a-long-random-token>,openpnp:<generate-a-long-random-token>' \
  --from-literal=DUBIS_TAILNET_ALLOWLIST='' \
  --from-literal=DUBIS_TRUST_TAILSCALE_HEADER=0 \
  --dry-run=client -o yaml | kubectl apply -f -
```

Every env the Deployment's `envFrom` expects (see `server/auth.py` and
`deploy/deployment.yaml`):

| Key | Set to, initially |
|---|---|
| `DUBIS_AUTH_MODE` | `on` |
| `DUBIS_TOKENS` | `name:token,name2:token2` — one entry per bearer client (CI, OpenPnP, MCP) |
| `DUBIS_TAILNET_ALLOWLIST` | leave empty for now |
| `DUBIS_TRUST_TAILSCALE_HEADER` | `0` — **leave unset/0 until step 6 confirms** the tailscale operator ingress actually injects the identity header. Flipping this on before verifying just trusts a header nobody is proving. |
| `DUBIS_TRUSTED_PROXY_IPS` | leave empty for now — see step 5's source-IP gate note before ever setting `DUBIS_TRUST_TAILSCALE_HEADER=1` |

Generate tokens with e.g. `openssl rand -hex 32`. Re-running the same
`create ... --dry-run=client -o yaml | kubectl apply -f -` command later
(e.g. to rotate a token or add a new client) updates the Secret in place —
the Deployment needs a rollout restart to pick up new env values
(`kubectl rollout restart deployment/dubis-server -n dubis`), since
`envFrom` env vars aren't live-reloaded.

## 3. Apply the Argo AppProject, then the Application

The `default` AppProject on this cluster is locked down and permits neither
this repo nor the `dubis` namespace — an Application on `default` sits at
`sync=Unknown` with an `InvalidSpecError`. Every service here gets its own
project, so apply ours first:

```bash
kubectl apply -f deploy/argocd-appproject.yaml
kubectl apply -f deploy/argocd-application.yaml
```

If the namespace was ever reconciled by `kubectl apply -k deploy` before
handing it to Argo, delete the leftover bootstrap Job first — its Argo hook
annotations are inert to kubectl, so kubectl leaves a normal immutable Job
that the PostSync hook then collides with:

```bash
kubectl -n dubis delete job dubis-secret-bootstrap --ignore-not-found
```

This points Argo at this repo's `deploy/` path on `main` and lets it manage
`pvc.yaml`, `deployment.yaml`, `service.yaml`, `ingress.yaml` (plus the
`secret-bootstrap.yaml` PostSync hook). Per `deploy/argocd-application.yaml`: `prune: false` and
`selfHeal: false` initially (conservatism during first rollout) — watch the
first sync in Argo UI/CLI, confirm nothing unexpected got created or is
pending deletion, then flip both to `true` in that file and commit, once
you're satisfied the first sync was clean.

The Deployment will not become `Ready` yet — it depends on the
`dubis-server-auth` Secret (created in step 2, already done) and an
already-populated PVC won't matter for boot (empty `/data` is fine, see next
step), but it WILL crash-loop if the Secret is missing, so do step 2 before
this if you haven't already.

## 4. First-deploy data seeding

The PVC (`dubis-server-data`) starts empty. Two options:

- **Recommended: seed the CSVs via `kubectl cp`.** The CSVs are the source of
  truth (`CLAUDE.md`'s Data Flow), so start the remote server with real data
  rather than an empty inventory. Pattern: spin up a short-lived pod that
  mounts the same PVC, `kubectl cp` the files in, delete the pod.

  ```bash
  # 1. Launch a throwaway pod with the PVC mounted at /data
  kubectl run dubis-seed --image=busybox -n dubis --restart=Never \
    --overrides='{"spec":{"containers":[{"name":"dubis-seed","image":"busybox","command":["sleep","3600"],"volumeMounts":[{"name":"data","mountPath":"/data"}]}],"volumes":[{"name":"data","persistentVolumeClaim":{"claimName":"dubis-server-data"}}]}}'
  kubectl wait -n dubis --for=condition=Ready pod/dubis-seed --timeout=60s

  # 2. Copy the source-of-truth files in (run from the repo root)
  kubectl cp data/inventory.csv dubis/dubis-seed:/data/inventory.csv
  kubectl cp data/purchase_ledger.csv dubis/dubis-seed:/data/purchase_ledger.csv
  kubectl cp data/adjustments.csv dubis/dubis-seed:/data/adjustments.csv
  kubectl cp events/ dubis/dubis-seed:/data/events
  kubectl cp data/preferences.json dubis/dubis-seed:/data/preferences.json
  kubectl cp data/constants.json dubis/dubis-seed:/data/constants.json
  kubectl cp data/pnp_part_map.json dubis/dubis-seed:/data/pnp_part_map.json
  kubectl cp data/part_registry.json dubis/dubis-seed:/data/part_registry.json
  kubectl cp data/generic_parts.json dubis/dubis-seed:/data/generic_parts.json
  kubectl cp data/saved_searches.json dubis/dubis-seed:/data/saved_searches.json

  # 3. IMPORTANT: chown everything to the container's uid (10001) — the
  #    pod above ran as root (busybox default), so files land root-owned.
  #    The dubis-server container runs as uid 10001 (Dockerfile) and cannot
  #    write to root-owned files, which breaks the very next adjustment/import.
  kubectl exec -n dubis dubis-seed -- chown -R 10001:10001 /data

  # 4. Clean up the throwaway pod
  kubectl delete pod -n dubis dubis-seed
  ```

  Do NOT skip step 3 — a fresh Longhorn volume mount is root-owned by
  default even with `fsGroup: 10001` set on the real Deployment (`fsGroup`
  only fixes *group* ownership at mount time for the app's own Pod, not for
  files a different, root-running seed Pod already wrote).

  After seeding, do a `kubectl rollout restart deployment/dubis-server -n
  dubis` (or just let the Deployment start naturally if it hasn't yet) so it
  picks up the seeded CSVs on first boot / cache rebuild.

- **Alternative: start clean.** Skip seeding entirely — the server boots fine
  against an empty `/data` (empty inventory) and you import purchases /
  build up inventory through the app afterward. Only use this if you
  deliberately want the remote deployment to start from zero rather than
  inheriting the desktop's existing inventory.

The `kubectl cp` seeding path is recommended since the CSVs already ARE the
source of truth — there's no reason to throw that history away.

## 5. Identity-header verification (decision point)

This is the open question from the design doc (§7, §1's "Verification-first
note"): does the Tailscale k8s operator's `ingress` class actually inject a
`Tailscale-User-Login` header for browser traffic? This determines whether
humans can authenticate to the web UI by tailnet identity, or need a bearer
token / the cookie-session fallback.

Once the Service/Ingress are up and the tailnet hostname resolves
(`dubis-server.<tailnet>.ts.net` per `deploy/ingress.yaml`), from a machine on
the tailnet:

```bash
curl -sS -D - -o /dev/null https://dubis-server.<tailnet>.ts.net/v1/health
```

`/v1/health` is unauthenticated so this always returns 200 regardless of the
header question — the point is to **dump the request headers the server
actually received**. Since `/v1/health` doesn't echo headers back, instead
hit an authed route without a token and inspect the 401 detail, or
temporarily add a debug log line / use `kubectl logs` on the Pod while
curling a real route:

```bash
curl -sS -D - -o /dev/null https://dubis-server.<tailnet>.ts.net/v1/parts
kubectl logs -n dubis deployment/dubis-server --tail=20
```

Cross-reference against the raw request the operator's sidecar forwards —
the simplest reliable check is to temporarily exec into the running Pod and
inspect what arrives at the app (or add a short-lived echo route / rely on
the 401 `detail` if `server/auth.py` is extended to include the header
presence in its unauthorized response during this check).

**Decision:**
- **Header IS present and trustworthy** (comes only from the operator's
  in-cluster sidecar, never client-forgeable) → update the Secret. As of the
  source-IP gate (`server/auth.py`), `DUBIS_TRUST_TAILSCALE_HEADER=1` on its
  own is no longer enough — the pod is still reachable via ClusterIP, so any
  other in-cluster pod that discovers a valid tailnet login name could
  otherwise forge the header directly, bypassing the proxy entirely. You
  **must** also set `DUBIS_TRUSTED_PROXY_IPS` to the tailscale operator
  proxy's pod IP (find it with `kubectl get pods -n tailscale -o wide`, or
  whatever namespace the operator's proxy pod runs in on this cluster —
  currently `10.42.2.176`, but **this churns**: it's a pod IP, and the pod
  gets a new one on every restart/reschedule):
  ```bash
  kubectl create secret generic dubis-server-auth --namespace dubis \
    --from-literal=DUBIS_AUTH_MODE=on \
    --from-literal=DUBIS_TOKENS='ci:<token>,openpnp:<token>' \
    --from-literal=DUBIS_TAILNET_ALLOWLIST='isaac@github' \
    --from-literal=DUBIS_TRUST_TAILSCALE_HEADER=1 \
    --from-literal=DUBIS_TRUSTED_PROXY_IPS='10.42.2.176' \
    --dry-run=client -o yaml | kubectl apply -f -
  kubectl rollout restart deployment/dubis-server -n dubis
  ```
  Fail-safe: if `DUBIS_TRUST_TAILSCALE_HEADER=1` is set but
  `DUBIS_TRUSTED_PROXY_IPS` is left empty, the header is ignored entirely
  (one warning logged, not a crash) — so a stale/missing proxy IP degrades to
  "tailnet-header login doesn't work", never to "the gate silently opens."
  Humans then get transparent browser access via tailnet identity — no token
  needed in the browser (see `app.pyw`'s remote-mode navigation comment,
  §7 of the design doc).

  Because the proxy pod IP churns on restart, treat the IP allowlist as a
  belt, not the only belt: the robust complement is a **NetworkPolicy**
  restricting ingress to the `dubis-server` pod to the `tailscale` namespace
  (or wherever the operator's proxy pods live), so even a same-cluster pod
  that happens to spoof or reuse the current trusted IP still can't reach
  `dubis-server` on the network layer at all. Add/verify that NetworkPolicy
  alongside this change rather than relying on the pod-IP allowlist alone.
- **Header is ABSENT or unverifiable** → leave `DUBIS_TRUST_TAILSCALE_HEADER`
  at `0` (and `DUBIS_TRUSTED_PROXY_IPS` empty). Humans and headless clients
  alike use bearer tokens; the browser falls back to the `POST
  /v1/auth/session` cookie route (`server/auth.py`) for a persistent browser
  session instead of a header-based one.

Do not guess here — this is exactly the kind of assumption the design doc
flags as UNVERIFIED against this specific cluster's Tailscale operator
config. Confirm it empirically before flipping the trust flag.

## 6. Smoke tests

`scripts/smoke-remote.sh` (already in the repo) checks: `/v1/health` is 200,
`/v1/parts` without a token is 401 (proves auth is actually enforcing, not
just configured), and `/v1/parts` with a token is 200.

- **From an ARC pod (or any in-cluster pod)** — cluster DNS, no tailnet
  needed:
  ```bash
  bash scripts/smoke-remote.sh http://dubis-server.dubis.svc:80 <ci-token>
  ```
- **From the tailnet** (proves the ingress + identity story end to end):
  ```bash
  bash scripts/smoke-remote.sh https://dubis-server.<tailnet>.ts.net <ci-token>
  ```

Both must print `SMOKE PASS` and exit 0. If the no-token check doesn't come
back 401, `DUBIS_AUTH_MODE` isn't actually `on` in the running Pod — check
the Secret and that the Deployment picked it up (`kubectl rollout restart`
after any Secret edit; `envFrom` isn't live-reloaded).

**CLI footgun:** if you point the desktop at this remote instance (`DUBIS_URL`
env or `data/preferences.json`'s `server_url`), export the same `DUBIS_URL`
(and `DUBIS_TOKEN`) for `tools/dubis-cli` too. Otherwise the desktop shows the
remote's inventory while the CLI, seeing no `DUBIS_URL`, falls back to the
local port file and reads whichever server is running against the local CSVs —
giving agents a stale/divergent view of the inventory the user is actually
looking at. The CLI will not silently start a server of its own (that is
`dubis serve`, explicitly), so the failure mode is a wrong-but-live answer from
a local instance, not a second writer appearing behind your back.

## 7. Longhorn backup-group confirmation

`deploy/pvc.yaml` labels the PVC
`recurring-job-group.longhorn.io/app-data: enabled` to opt it into the
cluster's `app-data` Longhorn RecurringJob group for scheduled
snapshots/backups. **This label key is UNVERIFIED against this specific
cluster** — it follows the general Longhorn RecurringJob-group convention,
but confirm it against the live cluster's actual RecurringJob objects and the
infra repo's `backup/README.md` before trusting it for backup coverage:

```bash
kubectl get pvc dubis-server-data -n dubis --show-labels
kubectl get recurringjobs.longhorn.io -n longhorn-system   # or wherever Longhorn lives on this cluster
```

Confirm the PVC actually shows up as a member of the `app-data` group's
target set (Longhorn UI → RecurringJob → Groups, or the CRD's selector), not
just that the label exists on the PVC — a typo'd label key silently backs up
nothing.

## 8. Mirror retirement — NOT part of this deploy

Once the deployed server is answering on the tailnet and has reached parity
with the desktop's local inventory (steps 1-7 above, done and smoke-tested),
the local inventory mirror daemon becomes redundant. **Do not retire it as
part of this runbook.** That's a separate follow-up PR (per the design doc's
"Scope split — two PRs" section): it removes `inventory_mirror.py`,
`mirror_install/`, `domain/api_mirror.py`, their tests, and the corresponding
`CLAUDE.md` rows, and Isaac uninstalls the local mirror scheduled task
himself as part of that PR's own runbook step. Flag parity confirmed here;
action the retirement there.
