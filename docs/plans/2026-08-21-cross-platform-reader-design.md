# Cross-platform picture/PDF reader — Design

**Date:** 2026-08-21
**Status:** design, not yet implemented
**Supersedes:** the Windows-only `install_tesseract` affordance

## Problem

Image/PDF import is non-functional for this operator today, in three
independent ways, all measured this session:

1. **The desktop app runs against the remote backend.** `data/preferences.json`
   sets `server_url: https://dubis-server.miku-parore.ts.net`, and
   `ocr_engine_available`, `ocr_overlay_b64` and `parse_source_file_b64` are all
   HTTP-mapped — so OCR executes in the container, which by design ships no
   tesseract. Remote answers `{"available": false}`.
2. **The install button cannot work off Windows.** `domain/api_scan.py:84`
   returns `INSTALL_HINT` for any `sys.platform != "win32"`, and the hint plus
   `TESSERACT_WINGET_COMMAND` name a winget package. On macOS the click is a
   no-op that toasts a Windows command.
3. **The VLM path was dead even where a server existed.** Two bugs (markdown-
   fenced JSON; `model_name()` reporting an unloaded model) meant every
   extraction silently fell back to tesseract. Fixed 2026-08-21 — see
   `vlm_extract._strip_code_fence`. Neither was caught because the only live
   test asserted `available()` and never called `extract_line_items`.

## Decision

Replace tesseract-as-the-install-target with a **VLM reader** that can run in
two places, chosen by an explicit preference:

| `reader_mode` | Where the model runs | How it is reached |
|---|---|---|
| `off` | nowhere | image/PDF import degrades to the existing tesseract/flat path |
| `local` | this machine | bundled-on-demand llama.cpp on loopback |
| `remote` | a fleet node | fleet discovery, or an explicit `reader_url` |
| `auto` | prefer local, else fleet | probe local first |

`auto` is the default for a fresh install only after a successful local install;
a clean install starts at `off` so nothing downloads without a click.

### Why the fleet, and not a bespoke endpoint

The infra repo already has a purpose-built subsystem for this
(`fleet-registry` / `fleet-nodeagent`, ns `fleet`,
`https://fleet.miku-parore.ts.net`) whose stated purpose is that *any tailnet
agent discovers + leases capacity*. `docs/adding-a-model-node.md` is normative
and explicitly documents making an in-cluster GPU pod a fleet node. Critically:

- **`vision` is already a first-class capability.** The registry filters on it
  (`ranking.py:51`); `GET /fleet?need_caps=vision` is tested
  (`test_ranking.py:67`, `test_server.py:61`).
- **A fleet node's endpoint stays in-cluster** (`http://<svc>.<ns>.svc.cluster.local:<port>`),
  so y740 needs **no tailnet exposure** — no Ingress, no `--api-key`, no hole in
  `restrict-broker-vlm-ingress`.
- **`dubis-server` is itself in-cluster**, and in remote-backend mode the server
  is already where OCR runs. The remote leg is the dubis pod dialling a
  ClusterIP.

So the only network grant needed is a **narrow additive NetworkPolicy admitting
ns `dubis` to `app=llamacpp:8080`**, mirroring `45-allow-monitoring.yaml`. One
namespace, one port, nothing tailnet-facing.

A bespoke tailscale Ingress on y740 was considered and **rejected**: it would
have required puncturing a NetworkPolicy whose own comment explains it exists
because llama.cpp's `/v1` *"will happily burn the node's only GPU for any caller
that can reach it"*, and it would have created a second undocumented path
alongside the sanctioned one.

### Model tiers by detected memory

Sizes are weights + vision projector; add KV cache at the chosen context and the
CUDA/Metal context and compute buffers.

| Budget | Model | Weights + mmproj |
|---|---|---|
| < 5 GiB | none — `off`, keep tesseract | — |
| >= 5 GiB | Qwen2.5-VL-3B Q4_K_M | 1.80 + 1.25 = 3.05 GiB |
| >= 10 GiB | Qwen2.5-VL-7B Q4_K_M | ~4.7 + ~1.3 = ~6.0 GiB |

Measured test matrix:

| Host | Silicon | Budget | Tier |
|---|---|---|---|
| `bool` (this Mac) | M4 Max, 128 GiB unified, `iogpu.wired_limit_mb: 0` -> 89.6 GiB budget | ample | 7B |
| `mauler` | RTX 3090 24576 MiB; desktop holds ~3.2 GiB -> ~20.3 GiB usable. Also an AMD iGPU at 0.5 GiB | ample | 7B |
| y740 (in-cluster) | RTX 2060 6144 MiB | ~4 of 6 GiB | 3B only — `gpu/README.md` states the 7B is not an option on this card |

`pdf_raster._MAX_EDGE = 2600` was tuned for tesseract's kernels. A 2600px page
is thousands of Qwen2.5-VL vision tokens and inflates KV cache materially, so
the VLM path takes its own, lower cap. Unmeasured; bench before pinning.

## Memory detection

Seven probes, each returning `None` on any failure, best-effort and dependency-free.

| Target | Probe | Verified |
|---|---|---|
| NVIDIA | `nvidia-smi --query-gpu=memory.total,memory.used` | yes — reinstates the approach deleted in `ca819c3`, originally added in `ca07608` |
| Windows, vendor-neutral capacity | registry `HardwareInformation.qwMemorySize` | yes — `25769803776` = exactly 24 GiB on mauler's 3090 |
| Windows, vendor-neutral usage | perf counter `\GPU Adapter Memory(*)\Dedicated Usage` | yes — 20.1 GiB, matching nvidia-smi |
| Apple unified | `sysctl hw.memsize` x 0.70; `iogpu.wired_limit_mb` when set non-zero | yes — 128 GiB -> 89.6 GiB on `bool` |
| AMD/Linux | `rocm-smi --showmeminfo vram` | no |
| Linux system RAM | `/proc/meminfo` `MemTotal` | no |
| Windows system RAM | `Win32_ComputerSystem.TotalPhysicalMemory` | yes |

> **Do NOT use `Win32_VideoController.AdapterRAM`.** Measured on mauler it
> reports `4293918720` (~4.0 GiB) for a 24 GiB 3090 — a 32-bit DWORD that
> saturates at 4 GB. It is the first API most code reaches for and it is wrong.

Two policy rules the deleted `ca07608` tiering got wrong:

1. **Use free memory, not total.** `_MODEL_TIERS` keyed off total VRAM. On mauler
   with the 27B resident that would have said "24 GiB, serve the 32B" while
   ~4 GiB was actually free.
2. **Pick the largest adapter, never the first.** mauler enumerates a 3090 *and*
   an AMD iGPU at 0.5 GiB; index-0 logic concludes nothing fits.

## Install / uninstall

On-demand download on click, with visible progress. Nothing is bundled: the app
stays small, and the pinned-and-checksummed pattern from
`win-runners/gpu/llamacpp.yaml`'s init container is the model to copy — exact
quant, exact revision, sha256-verified, so the bytes are auditable.

Phases, each reporting `{phase, message, bytes_done, bytes_total, pct}`:

1. `detect` — memory probe, tier choice
2. `runtime` — llama.cpp release binary for this platform, sha256-verified
3. `weights` — the GGUF, sha256-verified, atomic rename from `.part`
4. `projector` — the mmproj GGUF (**without it llama-server is text-only and
   every image is silently ignored** — `docs/install.md` already warns this)
5. `start` — spawn on a free loopback port, poll `/health`
6. `verify` — one real `extract_line_items` against a synthetic page
7. `done` / `error`

Everything lands under a single managed directory, `<data_dir>/reader/`.

**Uninstall** stops the running server, then deletes that directory and nothing
else. Hard constraints: it never accepts a path from the caller, never follows
symlinks out, is idempotent (uninstalling nothing succeeds), reports reclaimed
bytes, and requires an explicit confirm showing what will be removed and how
much space it frees — this is multiple GiB of the user's disk. Uninstall affects
only the **local** reader; `remote` stays functional, and `local`/`auto` fall
back rather than erroring.

## Transport: why the client shell, not `/v1`

The local reader must install and run on the **client** machine. In remote-backend
mode `app.pyw` skips the local server boot entirely, so there is no local `/v1`
to carry this — and the remote `/v1` is the wrong machine. That leaves the
pywebview client shell, which is the correct home anyway: `CLAUDE.md` scopes it
to *"OS-only concerns … that have no HTTP-y shape"*, and spawning processes and
writing binaries to the client's disk is exactly that.

pywebview cannot stream, so progress is **polled**: `start_reader_install()`
returns a job id immediately; the frontend polls `get_reader_install_status(id)`
on a timer. This grows the deliberately-minimal ~9-method shell — accepted, and
`tests/python/test_api_surface.py` freezes that surface, so it must be updated
in the same change.

## Open questions

- The 3B's bboxes came back as `[10,100,100,110]`-style garbage on the 0-1000
  grid; `_parse_bbox` accepts them and highlight positioning will be wrong but
  plausible. Whether the 7B localises usefully is **unmeasured**.
- The VLM still extracts no `unit_price`, `manufacturer` or `package`
  (`vlm_extract.py` hardcodes `0.0` / `""`), so the flat `distributor_profiles`
  parse cannot be retired. Extending the prompt is out of scope here.
- The VLM reads **page 1 only** (`ocr_layout.py:88-91`). Multi-page is out of
  scope here.
- `gpu-runner` on y740 is in CrashLoopBackOff (59 restarts / 4d21h), so the
  `vlm-gpu` CI leg has had no runner for ~5 days. Independent of this work but
  it gates the live test actually running.
