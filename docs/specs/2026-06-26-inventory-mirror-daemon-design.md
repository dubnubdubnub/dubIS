# Inventory Mirror Daemon — Design

**Date:** 2026-06-26
**Status:** Approved (brainstorming complete)
**Branch/worktree:** `claude/feature-inventory-mirror`

## Problem

dubIS exposes inventory over a loopback-only "poll API" (`poll_api.py`) that lives
*inside* the dubIS process. It is therefore only readable while dubIS is running, and
only from the same machine. The user wants the current inventory state to be readable
**over Tailscale** even **when dubIS is not running**, while:

- using **minimal system resources** (idle CPU ≈ 0%, small RAM footprint),
- being **secure**,
- being **off by default** (not auto-bundled / not auto-started),
- being **one-click enable/disable from Preferences**,
- working on **every OS dubIS supports** (Windows, macOS, Linux).

## Solution overview

A standalone, always-on **mirror daemon** co-located with dubIS. While dubIS runs it
**pushes** the current inventory snapshot to the daemon (over loopback). The daemon
holds the last snapshot in memory + on disk and **serves** it — locally on loopback and
over Tailscale via `tailscale serve` — independent of dubIS's lifecycle. dubIS becomes
**push-only**; the daemon is the read surface.

```
dubIS (while running)                 inventory_mirror daemon (always on, while box up)
─────────────────────                 ───────────────────────────────────────────────
INVENTORY_UPDATED ─┐                   ┌─ 127.0.0.1:7892  (push, loopback only)
startup ───────────┼─► POST snapshot ─►┤
shutdown ──────────┘   (+ shared token)├─ 127.0.0.1:7893  (read, loopback only)
                                       │       ▲
                                       │       │ tailscale serve (TLS + identity header)
                                       └─ data/inventory_mirror.json  (atomic, persists)
                                               ▲
                           tailnet reader ─────┘ https://<host>.<tailnet>.ts.net/api/...
```

### Why Python (not Go/Rust/C)

For an idle snapshot server the workload is I/O-bound and idle ~100% of the time
(blocked on `socket.accept()`), so **idle CPU ≈ 0% in every language**; per-request CPU
differences are irrelevant at this request volume. The only real differentiator is idle
RAM (~15–25 MB Python vs ~5–10 MB Go vs ~1–5 MB Rust/C). Because dubIS is a cross-platform
Python app that **already ships a Python interpreter on every OS**, a Python daemon needs
**zero extra binaries** on any platform and can **reuse `poll_api`'s serialization**. A
compiled language would require committing/shipping ~5 platform/arch binaries and
reimplementing serialization — a recurring cost that outweighs ~15 MB of idle RAM on an
always-on box. C/C++ additionally introduce memory-unsafe network parsing, which fights
the "secure" requirement. **Decision: Python, stdlib only.**

## Components

| Unit | Responsibility | Depends on |
|------|----------------|-----------|
| `inventory_mirror.py` (new daemon, standalone entry point) | Run two loopback HTTP servers (push 7892, read 7893); hold snapshot in memory; persist to `data/inventory_mirror.json` atomically; enforce write token + read identity | stdlib only + shared serialization module |
| `mirror_serialize.py` (new shared module) | `INVENTORY_CSV_FIELDS`, `inventory_to_csv()`, `inventory_stats()` lifted from `poll_api.py` so daemon + push client + (legacy) poll API share one format | stdlib |
| `mirror_push.py` (new, in dubIS) | Build push payload; POST fire-and-forget on a short-timeout daemon thread; debounce `INVENTORY_UPDATED`; push on startup + shutdown; only active when feature enabled | `mirror_serialize`, `urllib` |
| `mirror_install/` (new package) | Per-OS autostart backends behind a common `Installer` interface: `windows.py` (schtasks), `macos.py` (launchd LaunchAgent), `linux.py` (systemd --user). `select()` dispatches on `sys.platform`. Also wraps `tailscale serve` enable/disable | stdlib `subprocess` |
| API methods (in `domain/api_preferences.py` or new `domain/api_mirror.py`) | `enable_inventory_mirror()`, `disable_inventory_mirror()`, `get_inventory_mirror_info()` | `mirror_install`, prefs |
| Preferences UI (JS) | Toggle in preferences modal; shows enabled/running state, last-push age, serve URL | existing prefs modal |

**Design for isolation:** the daemon never imports dubIS app code — it only depends on
`mirror_serialize` (pure functions) and stdlib, so it can run as a bare `python
inventory_mirror.py` process. The push client and installer are the only dubIS-side glue.

## Data flow & payload

**Push payload (dubIS → daemon, POST `/push` on 7892), JSON:**
```json
{
  "inventory": [ { ...inventory record... } ],
  "csv_fields": ["section","lcsc","mpn", ...],
  "pushed_at": "2026-06-26T12:34:56Z",
  "source": "dubis",
  "dubis_running": true,
  "token": "<shared secret>"
}
```
- `csv_fields` keeps the CSV column order as the Python SSOT so the daemon renders CSV
  without duplicating the field list.
- The **shutdown** push sends `"dubis_running": false`.
- The daemon stamps its own `received_at` when it accepts a push.

**Read endpoints (daemon, on 7893; mirrored from poll API + freshness):**
- `GET /api/health` → `{ ok, has_snapshot, age_seconds }`
- `GET /api/inventory` → `{ ok, count, inventory, freshness }`
- `GET /api/inventory.csv` → CSV attachment (rendered from snapshot + `csv_fields`)
- `GET /api/stats` → `{ ok, part_count, total_qty, section_counts, freshness }`
- `GET /` or `/api` → endpoint list

**`freshness` block** (on inventory/stats/health):
```json
{ "pushed_at": "...", "received_at": "...", "age_seconds": 12, "dubis_running": true, "source": "dubis" }
```
`dubis_running` is **best-effort**: true since the last push unless a clean shutdown
flipped it to false. An ungraceful dubIS crash leaves it stale-true — documented caveat,
not a correctness bug (readers should treat it as a hint and rely on `age_seconds`).

## Security

- **Write path (7892):** binds **loopback only** (never exposed to the tailnet) + a
  **shared token** stored in `data/mirror_token` (created on enable, user-readable perms).
  dubIS sends it on every push; the daemon rejects pushes with a missing/wrong token
  (HTTP 403). Loopback is the primary gate; the token stops other local processes from
  poisoning the mirror.
- **Read path (7893):** binds **loopback only**. `tailscale serve` terminates TLS and
  proxies the tailnet to `127.0.0.1:7893`, injecting the authenticated
  `Tailscale-User-Login` header. Daemon rule:
  - **Header present** → request came via `tailscale serve`; the login **must be in the
    allow-list** (default: the machine owner's login; configurable). Else HTTP 403.
  - **Header absent** → direct local loopback read (e.g. Claude polling); **allowed
    unauthenticated** (loopback is trusted).
  External traffic can only arrive through `tailscale serve`, which sets the header
  authentically, so the loopback-bypass cannot be reached from the tailnet.

## Lifecycle (preference-driven, off by default)

- New preference `inventoryMirror.enabled`, default **false**. dubIS pushes nothing and
  starts no daemon unless enabled.
- **`enable_inventory_mirror()`**: write token → register the per-OS autostart service
  (schtasks/launchd/systemd-user, with restart-on-failure) → start the daemon now →
  `tailscale serve` for 7893 → flip the pref. Returns status incl. the serve URL.
  **Throws an actionable error** if `tailscale` is missing or not logged in, or if the
  service can't be registered.
- **`disable_inventory_mirror()`**: stop `tailscale serve` → stop + unregister the service
  → flip the pref. Snapshot file is left in place (harmless).
- **`get_inventory_mirror_info()`**: `{ enabled, running, serve_url, last_push_age, owner_allowlist }`
  for the Preferences toggle/status.
- **No lazy-start** from dubIS — it would fight explicit on/off. The autostart service +
  restart-on-failure keeps the daemon alive across reboots while enabled.

## Error handling

- **Push is fire-and-forget**: failures (connection refused, timeout) `AppLog.warn` and
  never block or crash dubIS — a failed mirror push is non-fatal to the app.
- **Atomic snapshot writes**: write temp + `os.replace`. A corrupt/missing snapshot at
  daemon startup logs a warning and starts empty (no crash).
- **Port bind failure throws** — the daemon's whole purpose is to be reachable.
- **Debounce** coalesces `INVENTORY_UPDATED` bursts (~500 ms) to avoid push storms.

## Cross-platform

The daemon core, push client, `mirror_serialize`, and `tailscale serve` are **write-once**
and portable. The **only** per-OS code is the installer backend:

| OS | Autostart mechanism |
|----|---------------------|
| Windows | `schtasks` logon task, restart-on-failure |
| macOS | `launchd` LaunchAgent plist in `~/Library/LaunchAgents`, `launchctl load/unload` |
| Linux | `systemd --user` unit, `systemctl --user enable --now` |

CI already runs Python on ubuntu + macos + windows, so all three are exercised.

## Testing

**Unit (pytest):**
- Daemon: push stores snapshot; reads serve it; freshness math; write-token enforced
  (403 on missing/wrong); read identity allow-list enforced when header present;
  loopback bypass when header absent; persistence across restart (load from file);
  atomic-write + corruption recovery.
- Push client: correct payload shape; connection-refused doesn't raise; debounce
  coalesces bursts; shutdown push sets `dubis_running:false`.
- Installer: each backend's register/unregister command construction (mocked
  `subprocess`); `sys.platform` dispatch picks the right backend; missing/not-logged-in
  `tailscale` raises an actionable error.

**E2E (Playwright):**
- Preferences toggle: enabling shows running state + serve URL; disabling clears it.
  (Daemon/tailscale interactions stubbed at the API boundary so E2E stays hermetic — no
  live tailnet in CI; simulate the identity header for the read-auth path.)

No live tailscale in CI. The read-auth path is tested by simulating the
`Tailscale-User-Login` header.

## Phasing / PR plan

- **PR 1 — core + Windows (testable):** `inventory_mirror.py`, `mirror_serialize.py`
  (extracted from `poll_api.py`, poll API updated to import it), `mirror_push.py` wired
  into dubIS startup/shutdown/INVENTORY_UPDATED, **Windows** installer backend, the three
  API methods, Preferences toggle, unit + E2E tests. Poll API still runs (overlap/fallback).
- **PR 2 — macOS + Linux installer backends (separate):** `launchd` + `systemd --user`
  backends behind the same `Installer` interface, with tests. No daemon changes.
- **PR 3 — Phase 2, retire poll API (later):** remove `start_poll_server` /
  `restart_poll_server`, the `app.pyw` startup call, and `get_poll_api_info` /
  `set_poll_api_port`; keep `mirror_serialize`; repoint local consumers to
  `127.0.0.1:7893`; update `test_api_surface.py` and `test_poll_api.py`.

## Open questions / decisions recorded

- Ports: **7892 push / 7893 read** (confirmed).
- Shared write token: **kept** (defense-in-depth; loopback is primary gate).
- Build all 3 installer backends, **Windows first** (PR 1), mac+Linux in PR 2.
- Execution: **subagent-driven**; PRs pushed via `scripts/push-pr.sh`, CI watched to green,
  user notified on each merge.
