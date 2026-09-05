> ## ⚠️ SUPERSEDED 2026-09-04 — the win11 KubeVirt VM is being retired
>
> Windows CI moved to GitHub-hosted `windows-latest` (`ci.yml` js-windows +
> `windows-nightly.yml`), so none of the tuning below is worth doing: it optimises a
> VM that should not exist. The VM served ~15.3 h/month of CI at ~1% utilisation
> while holding 10.1 GiB of standing RAM on y740, the only GPU node. 14 of the 41
> scheduled nightly runs in this workflow's history were cancelled at GitHub's 24 h
> queue ceiling waiting for it, and as of 2026-09-04 there are ZERO runners carrying
> the `win11` label, so Windows CI had stopped entirely. dubIS is a public repo, so
> hosted Windows minutes are free.
>
> Kept as the record of why the VM behaved as it did, and because decommissioning
> the KubeVirt + CDI + autoscaler + golden-image + in-cluster-registry apparatus in
> the infra repo has not happened yet. Do not action anything below.

# win11 CI infra follow-up — `disk-v4` bake + more cores

**Status:** proposed (out-of-repo cluster work; not part of the repo PR)
**Where:** `mauler@blhx370` (libvirt master + KubeVirt host); containerDisk built on `ux430`
**Context:** the repo-side speedup (windows Playwright project + node_modules cache +
nightly full run) lands the PR-path win11 job at ~3–4 min. This runbook trims the remaining
install/setup floor and stabilizes variance by pre-baking dependencies into the golden image
and giving the VM more cores.

> This is deliberately **not** executed by the repo PR — it is a cluster runbook. The repo
> changes are correct and complete on their own; the node_modules cache simply becomes a
> harmless no-op once deps are baked in.

## Background

The win11 CI runners are KubeVirt VMs booted from the `win11-golden` containerDisk
(`10.1.1.2:30500/win11-golden:disk-vN`, currently disk-v3), 4 cores / 8 GiB, SATA + per-VM
copy-on-write overlay (fully ephemeral — resets to golden on every restart). A `disk-v4` bake
is already pending for true per-job disk reset (the in-guest `self-register-loop.ps1` now
shuts the VM down after every job, inert until baked). Piggy-back the dependency bake and the
core bump onto that same bake. Interactive bake steps live in
`mauler@blhx370:~/pool-keeper/README.md`.

## What to bake into `disk-v4`

Prepare these in the libvirt master VM `win11-ci` (the mutable golden source) for user
`ciadmin`, then bake to the containerDisk:

1. **node_modules** — clone the repo, `npm ci`, leave `node_modules` in a known path the CI
   checkout can reuse (or just warm the caches — see note). Pin to the Node version the image
   ships so the cached native binaries match.
2. **Playwright browser store** — `npx playwright install chromium` so
   `~\AppData\Local\ms-playwright` is populated.
3. **npm download store** — `~\AppData\Local\npm-cache` warmed by the `npm ci` above.

Because CI does a fresh `actions/checkout` into a job workspace, the most robust form is to
warm the **caches** (`npm-cache`, `ms-playwright`) and, for node_modules, rely on the GitHub
`actions/cache` restore keyed on `package-lock.json` (already wired in ci.yml). Baking a warm
GitHub-cache is not possible; baking the *stores* means the first post-lock-change job on a
fresh VM still pays a one-time cost, but the steady state is instant. If a truly zero-network
steady state is wanted, bake a repo checkout with `node_modules` at a fixed path and have CI
symlink/copy it on a cache miss — heavier, only if the cache proves insufficient.

## Core bump

The live KubeVirt `VirtualMachine` spec (ns `win-runners`) is 4 cores / 8 GiB. Bump to
**6 cores** (keep 8 GiB unless E2E shows memory pressure) to widen Playwright's default worker
count (cores/2) and absorb host contention. **Edit the live object, not the stale
`~/win11-vm.yaml` on disk** — per the runner-pool notes the on-disk YAMLs have drifted
(node-pinning replaced by `podAntiAffinity` on `runner-pool: win11`); re-applying them would
regress. Use `kubectl -n win-runners edit vm win11` (and the pool members `win11-2`/`win11-3`)
or patch `spec.template.spec.domain.cpu.cores`.

## Verification after bake

- Boot a fresh VM from `disk-v4`; confirm `~\AppData\Local\ms-playwright` and
  `~\AppData\Local\npm-cache` are populated before any job runs.
- Trigger a `js-windows` run; confirm the `Install deps` / `Install Playwright browsers`
  steps are near-instant (cache hits) and total job time drops toward the E2E floor (~2.5 min
  subset).
- Confirm `pool-keeper` still recognizes the new disk tag (it auto-creates missing members
  from the live golden image).

## Rollback

containerDisk tags are immutable and versioned — if `disk-v4` regresses, repoint the VM specs
back to `win11-golden:disk-v3`. The repo-side changes are independent and need no rollback.
