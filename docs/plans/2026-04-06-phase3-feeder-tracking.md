# Phase 3: Feeder Tracking + OpenPnP Integration — Implementation Plan

**Goal:** Track which parts are loaded in which feeders, monitor feeder levels
during PnP jobs in real time, and give the operator a dashboard for job setup,
live monitoring, and teardown — all without leaving dubIS.

**Architecture:** Feeder state lives in the SQLite cache (`feeders` table).
Feeder mutations are recorded in `adjustments.csv` with a `feeder_id` column
(backward-compatible — empty for non-feeder ops). The OpenPnP HTTP bridge
already exposes live feeder data (`/api/feeders`); dubIS polls it and
reconciles with its own feeder records. The PnP server gains
`/api/feeders` CRUD endpoints so the bridge and future hardware (ESP32,
smart racks) can push state.

**Depends on:** Phase 1 (SQLite cache, merged), Phase 2b (generic parts,
backend done). Phase 2 frontend completion plan should land first so
generic-part BOM resolution is live before feeder setup references it.

---

## UX Design

### Design Principles

1. **The operator is standing at the machine.** During a PnP job the user is
   not sitting at a desk browsing BOMs — they're watching the machine,
   swapping tape reels, and glancing at a screen. UI must be glanceable:
   large status indicators, high-contrast colors, minimal reading.

2. **Feeder setup is a deliberate act.** Loading feeders is slow and
   physical. The UI should make it easy to declare what's loaded, but never
   auto-modify feeder state — the human is the source of truth for what's
   physically in the machine.

3. **Live data flows one way: OpenPnP → dubIS.** dubIS doesn't command the
   machine. It observes placements (via consume events) and feeder state
   (via the bridge). The operator uses OpenPnP to run jobs; dubIS is the
   inventory/feeder bookkeeper.

4. **Normal mode is not affected.** Users who don't use a PnP machine
   should never see feeder UI. Everything is behind a "PnP Mode" toggle.

---

### Application Modes

dubIS gains a **mode toggle** in the header:

```
┌──────────────────────────────────────────────────────────┐
│ [Preferences]   [Undo] [Redo]   ●───○ PnP Mode   42 parts │
│                                  ↑                         │
│                            toggle switch                   │
└──────────────────────────────────────────────────────────┘
```

| Mode | Left Panel | Middle Panel | Right Panel |
|------|-----------|-------------|-------------|
| **Normal** (default) | Purchase Import | Inventory | BOM Comparison |
| **PnP** | Feeder Dashboard | Inventory (annotated) | Job Monitor |

The toggle emits `Events.MODE_CHANGED` with `{ mode: "normal" | "pnp" }`.
Each panel listens and swaps its content. Panel headers update. The toggle
state persists in `preferences.json` under `pnp_mode: false`.

When PnP Mode is off, the PnP server still runs (OpenPnP doesn't care about
the UI), and consume events still decrement stock. The dashboard is just a
visibility toggle.

---

### Panel A: Feeder Dashboard (left panel in PnP Mode)

Replaces the Import panel. Shows all known feeders grouped by status.

```
┌─ Feeders ──────────────────────────┐
│ [Setup Job ▾]          [Sync ↻]   │
│                                    │
│ ▼ Active (3)           ← section  │
│ ┌──────────────────────────────┐  │
│ │ 🟢 Feeder-01                 │  │
│ │ C1525 · 100nF 0402          │  │
│ │ ████████████░░░ 38/50        │  │
│ │ 12 placed                    │  │
│ └──────────────────────────────┘  │
│ ┌──────────────────────────────┐  │
│ │ 🟡 Feeder-04                 │  │
│ │ C25076 · 10uF 0805          │  │
│ │ ███░░░░░░░░░░░░ 5/50         │  │
│ │ ⚠ Low — 5 remaining         │  │
│ │ 45 placed                    │  │
│ └──────────────────────────────┘  │
│ ┌──────────────────────────────┐  │
│ │ 🔴 Feeder-07                 │  │
│ │ C14663 · 100R 0402           │  │
│ │ ░░░░░░░░░░░░░░░ 0/100        │  │
│ │ ⛔ DEPLETED                   │  │
│ │ 100 placed                   │  │
│ └──────────────────────────────┘  │
│                                    │
│ ▶ Idle (2)             ← collapsed│
│ ▶ Empty (5)                       │
│                                    │
│ ──────────────────────────────── │
│ Total loaded: 3 feeders, 43 parts │
│ Low: 1 · Depleted: 1             │
└────────────────────────────────────┘
```

**Feeder card anatomy:**

| Element | Content | Style |
|---------|---------|-------|
| Status dot | 🟢 green (>25%), 🟡 yellow (5–25%), 🔴 red (0 or <5) | 10px circle, `--color-green/yellow/red` |
| Name | Feeder ID/name from OpenPnP | Bold, `--text-bright` |
| Part line | Part key + description excerpt | `--text-secondary`, truncated |
| Progress bar | Remaining / loaded | 6px bar, color matches status dot |
| Feed count | "N placed" | `--text-muted`, small |
| Alert banner | "Low — N remaining" or "DEPLETED" | Yellow/red bg strip |

**Feeder groups:**
- **Active**: `loaded_qty > 0` and the part is in the current BOM (or job is running)
- **Idle**: `loaded_qty > 0` but part is NOT in the current BOM
- **Empty**: `loaded_qty == 0` (unloaded or never loaded)

**Interactions:**
- Click feeder card → expand to show full part details + action buttons:
  - **Unload** → returns remaining to stock (`feeder_unload` adjustment)
  - **Edit Qty** → correct loaded count (typo fix, partial unload)
  - **View Part** → scrolls inventory panel to that part
- "Sync ↻" button → polls OpenPnP bridge `/api/feeders`, reconciles state
- "Setup Job" button → opens Feeder Setup modal (see below)

**Low-feeder alert:** When any active feeder drops below the threshold
(default: 5 parts, configurable in preferences), a yellow banner appears
at the top of the dashboard:

```
⚠ Feeder-04 low: 5 remaining (10uF 0805)
```

If multiple feeders are low, the banner stacks. The banner is also pushed
to the console log.

---

### Panel B: Inventory (middle panel — annotated in PnP Mode)

The inventory panel is largely unchanged. In PnP mode it gains **feeder
badges** on parts that have loaded feeders:

```
C1525  100nF  0402  [F-01: 38]  qty: 212
                     ↑
              feeder badge (teal chip)
```

The badge is a small `.chip.teal` showing feeder name + loaded qty. If a
part is on multiple feeders, show multiple badges. Clicking a badge scrolls
the feeder dashboard to that feeder card.

**During active consumption:** the qty value animates briefly (flash green
→ normal) when a PnP consume event decrements it, so the operator can see
the count ticking down in real time.

---

### Panel C: Job Monitor (right panel in PnP Mode)

Replaces the BOM Comparison panel. Shows live job progress.

```
┌─ Job Monitor ──────────────────────┐
│                                    │
│ Status: ● RUNNING     00:14:32    │
│                                    │
│ ┌────────────────────────────────┐│
│ │ Board 1 of 3                   ││
│ │ ████████████████░░░░ 42/58     ││
│ │ 72% complete                   ││
│ └────────────────────────────────┘│
│                                    │
│ Overall: 42 / 174 placements      │
│ Rate: ~3.1 parts/min              │
│ Est. remaining: ~42 min            │
│                                    │
│ ── Recent Placements ──────────── │
│ 14:32:07  C1525  Feeder-01  1x   │
│ 14:32:04  C25076 Feeder-04  1x   │
│ 14:31:58  C14663 Feeder-07  1x   │
│ 14:31:55  C1525  Feeder-01  1x   │
│ ...                                │
│                                    │
│ ── Feeder Coverage ────────────── │
│ ✔ 12/12 BOM parts have feeders   │
│ or                                 │
│ ⚠ 2 parts missing feeders:       │
│   C49876 — 22pF 0402              │
│   C132487 — ESP32-S3              │
│                                    │
└────────────────────────────────────┘
```

**Data sources:**

- **Job status + board count:** polled from OpenPnP bridge `GET /api/job`
  (already implemented in `HttpBridge.py`). Shows running state, board
  count, placement list per board.
- **Placement rate:** computed client-side from consume event timestamps.
- **Recent placements:** live-appended from `window._pnpConsume()` callbacks
  (already fires on every consume).
- **Feeder coverage:** cross-reference loaded feeders against BOM parts.

**When no job is running**, the panel shows a summary:

```
┌─ Job Monitor ──────────────────────┐
│                                    │
│ Status: ● IDLE                    │
│ Last job: 2026-04-06 14:45        │
│ Placed: 174 parts across 3 boards │
│                                    │
│ ── Loaded Feeders ─────────────── │
│ 5 feeders loaded (3 active, 2 idle)│
│                                    │
│ Load a BOM and set up feeders to  │
│ start tracking a job.             │
│                                    │
└────────────────────────────────────┘
```

---

### Feeder Setup Modal

Opened from "Setup Job" in the feeder dashboard (or "Setup Feeders" button
in the BOM panel header when a BOM is loaded). This is the pre-job workflow
where the operator declares feeder assignments.

```
┌──────────────────────────────────────────────────────┐
│ Setup Feeders for: project-v2.csv (×3)               │
│                                                      │
│ ┌────┬──────────┬──────────┬───────────┬──────────┐ │
│ │ ✔  │ Part     │ Needed   │ Feeder    │ Loaded   │ │
│ ├────┼──────────┼──────────┼───────────┼──────────┤ │
│ │ ✔  │ C1525    │ 36       │ Feeder-01 │ 50       │ │
│ │    │ 100nF    │          │ ▾         │          │ │
│ │ ✔  │ C25076   │ 12       │ Feeder-04 │ 50       │ │
│ │    │ 10uF     │          │ ▾         │          │ │
│ │ ⚠  │ C49876   │ 9        │ — none —  │          │ │
│ │    │ 22pF     │          │ ▾         │          │ │
│ │ ✔  │ C14663   │ 30       │ Feeder-07 │ 100      │ │
│ │    │ 100R     │          │ ▾         │          │ │
│ └────┴──────────┴──────────┴───────────┴──────────┘ │
│                                                      │
│ ✔ 3/4 parts assigned · ⚠ 1 unassigned              │
│                                                      │
│ [Cancel]                              [Save & Load]  │
└──────────────────────────────────────────────────────┘
```

**Columns:**
- **✔/⚠**: Green check if feeder assigned and loaded qty >= needed; yellow
  warning if unassigned or insufficient
- **Part**: Part key + value (from BOM matching results)
- **Needed**: Total qty required for the BOM × multiplier
- **Feeder**: Dropdown of available feeders (from OpenPnP bridge sync).
  Pre-populated if a feeder already has this part loaded.
- **Loaded**: Editable number — how many pieces the operator loaded into the
  feeder. Pre-populated from current `loaded_qty` if feeder was already loaded.

**Auto-suggest logic:**
1. If a feeder is already loaded with this part → pre-select it
2. If OpenPnP reports a feeder assigned to this part → suggest it
3. If no match → leave "— none —" and show ⚠

**"Save & Load" action:**
- For each row with a feeder assignment:
  - If feeder was not previously loaded with this part: append `feeder_load`
    adjustment, set `feeders.part_id` and `feeders.loaded_qty`
  - If feeder was already loaded but qty changed: append adjustment for delta
  - If feeder assignment changed (different part): `feeder_unload` old part,
    `feeder_load` new part
- Emit `Events.FEEDERS_UPDATED`
- Close modal, feeder dashboard refreshes

---

### Feeder Load/Unload Quick Actions

Outside of the full "Setup Job" flow, the operator can manage individual
feeders from the feeder dashboard:

**Load Feeder (from expanded feeder card):**
```
┌─ Load Feeder-01 ──────────────────┐
│                                    │
│ Part: [search / select ▾]         │
│ Qty:  [50        ]                │
│                                    │
│ [Cancel]              [Load]       │
└────────────────────────────────────┘
```

Part selector: type-ahead search against inventory (same as the existing
inventory search). Shows part key + description + current stock.

**Unload Feeder:**
Confirmation: "Return 38 remaining C1525 to general stock?"
→ `feeder_unload` adjustment, `feeders.loaded_qty = 0`, `feeders.part_id = NULL`

---

### Preferences Additions

Add to the Preferences modal under a "PnP" section:

```
── PnP ──────────────────────────────
Low feeder threshold: [5    ] parts
OpenPnP bridge host:  [pnp-machine:8899]
Poll interval:        [3    ] seconds
```

- **Low feeder threshold**: qty below which a feeder shows yellow warning
  (default: 5)
- **OpenPnP bridge host**: hostname:port for the HTTP bridge
  (default: auto-detected from `pnp_server.py` config, or `localhost:8899`)
- **Poll interval**: how often to poll `/api/feeders` and `/api/job` during
  PnP mode (default: 3s, min: 1s, max: 30s)

Stored in `preferences.json` under:
```json
{
  "pnp_low_feeder_threshold": 5,
  "pnp_bridge_host": "pnp-machine:8899",
  "pnp_poll_interval_s": 3
}
```

---

## Backend Implementation

### Task 1: Schema — add `feeders` table (cache_db v3→v4)

**Files:**
- Modify: `cache_db.py`
- Modify: `tests/python/test_cache_db.py`

- [ ] **Step 1:** Bump `SCHEMA_VERSION` to 4.
- [ ] **Step 2:** Add `feeders` table to `create_schema()`:
  ```sql
  CREATE TABLE IF NOT EXISTS feeders (
      feeder_id   TEXT PRIMARY KEY,
      part_id     TEXT REFERENCES parts(part_id),
      loaded_qty  INTEGER NOT NULL DEFAULT 0,
      loaded_at   TEXT,
      note        TEXT
  );
  ```
- [ ] **Step 3:** Add v3→v4 migration in `_migrate()`.
- [ ] **Step 4:** Add `feeder_set()`, `feeder_clear()`, `feeder_get()`,
  `feeder_list()`, `feeder_update_qty()` functions to `cache_db.py`.
- [ ] **Step 5:** Tests: schema creation includes feeders table, migration
  from v3 works, CRUD operations on feeders table.

### Task 2: Extend adjustments.csv with feeder_id column

**Files:**
- Modify: `inventory_ops.py` (adjustment writing)
- Modify: `cache_db.py` (adjustment replay)
- Modify: `tests/python/test_cache_db.py`

- [ ] **Step 1:** Add optional `feeder_id` column to adjustments CSV.
  Existing rows have empty feeder_id (backward compatible).
- [ ] **Step 2:** New adjustment types: `feeder_load`, `feeder_unload`,
  `feeder_consume`. These are recorded alongside the stock mutation.
- [ ] **Step 3:** On cache rebuild, replay feeder adjustments to reconstruct
  `feeders` table state.
- [ ] **Step 4:** Tests: write feeder adjustments, rebuild cache, verify
  feeders table matches expected state.

### Task 3: Feeder operations in inventory_api.py

**Files:**
- Modify: `inventory_api.py`
- Modify: `tests/python/test_inventory_api.py`

- [ ] **Step 1:** `load_feeder(feeder_id, part_key, qty, note=None)`:
  - Validate part exists in inventory
  - Append `feeder_load` to adjustments.csv (stock qty unchanged)
  - `INSERT/UPDATE feeders` in cache
  - Return updated inventory
- [ ] **Step 2:** `unload_feeder(feeder_id)`:
  - Append `feeder_unload` to adjustments.csv
  - Clear feeder in cache (`loaded_qty=0, part_id=NULL`)
  - Return updated inventory
- [ ] **Step 3:** `update_feeder_qty(feeder_id, new_qty)`:
  - For correcting loaded qty without load/unload cycle
  - Append adjustment with delta
  - Update cache
- [ ] **Step 4:** `list_feeders()`:
  - Return all feeders from cache with part details
- [ ] **Step 5:** `get_feeder(feeder_id)`:
  - Return single feeder with part details
- [ ] **Step 6:** Modify `adjust_part()` to accept optional `feeder_id`:
  - When consuming via PnP with a feeder_id, also decrement
    `feeders.loaded_qty`
  - Append `feeder_consume` adjustment type
- [ ] **Step 7:** Tests for all operations, including the stock invariant:
  `sum(feeders.loaded_qty for part) <= stock.quantity`

### Task 4: PnP server — feeder API endpoints

**Files:**
- Modify: `pnp_server.py`
- Modify: `tests/python/test_pnp_server.py`

- [ ] **Step 1:** `GET /api/feeders` → `api.list_feeders()`
- [ ] **Step 2:** `POST /api/feeders` → `api.load_feeder(feeder_id, part_id, qty)`
  ```json
  { "feeder_id": "Feeder-01", "part_id": "C1525", "qty": 50 }
  ```
- [ ] **Step 3:** `DELETE /api/feeders/:feeder_id` → `api.unload_feeder(feeder_id)`
- [ ] **Step 4:** `PATCH /api/feeders/:feeder_id` → `api.update_feeder_qty(feeder_id, qty)`
- [ ] **Step 5:** Extend `POST /api/consume` to accept optional `feeder_id`:
  ```json
  { "part_id": "C1525", "qty": 1, "feeder_id": "Feeder-01" }
  ```
  Pass `feeder_id` through to `adjust_part()`.
- [ ] **Step 6:** Extend `window._pnpConsume()` callback payload to include
  `feeder_id` and `feeder_remaining` so the frontend can update the
  dashboard without re-polling.
- [ ] **Step 7:** Tests: feeder CRUD via HTTP, consume with feeder_id
  decrements both stock and feeder qty.

### Task 5: OpenPnP bridge sync endpoint

**Files:**
- Modify: `inventory_api.py`

- [ ] **Step 1:** `sync_feeders_from_bridge(bridge_feeders)`:
  - Accepts the JSON array from OpenPnP bridge `GET /api/feeders`
  - For each bridge feeder with a part_id:
    - If dubIS has no record for this feeder_id: create it with
      `loaded_qty=0` (operator must declare qty manually)
    - If dubIS has a record: update `feed_count` from bridge data
  - Returns list of new/updated feeders for the frontend
- [ ] **Step 2:** `get_bridge_feeders(host)`:
  - HTTP GET to `{host}/api/feeders`
  - Returns parsed JSON
  - Timeout: 3s, graceful error handling
- [ ] **Step 3:** Tests with mocked HTTP responses.

---

## Frontend Implementation

### Task 6: PnP Mode toggle + panel swapping

**Files:**
- Modify: `index.html` (add toggle to header, add hidden PnP panel containers)
- Modify: `js/event-bus.js` (add `MODE_CHANGED`, `FEEDERS_UPDATED`,
  `PNP_CONSUME` events)
- Create: `js/pnp/pnp-mode.js` (mode toggle logic)
- Create: `css/panels/pnp.css` (feeder dashboard + job monitor styles)

- [ ] **Step 1:** Add toggle switch to header-right:
  ```html
  <label class="pnp-toggle">
    <input type="checkbox" id="pnp-mode-toggle">
    <span class="pnp-toggle-label">PnP Mode</span>
  </label>
  ```
  Style: small toggle switch using `--color-teal` when active.

- [ ] **Step 2:** Add hidden panel containers to `index.html`:
  ```html
  <div class="panel panel-feeders hidden" id="panel-feeders">
    <div class="panel-header">Feeders
      <button class="setup-btn" id="setup-job-btn">Setup Job</button>
      <button class="sync-btn" id="sync-feeders-btn" title="Sync from OpenPnP">↻</button>
    </div>
    <div class="panel-body" id="feeders-body"></div>
  </div>

  <div class="panel panel-job-monitor hidden" id="panel-job-monitor">
    <div class="panel-header">Job Monitor</div>
    <div class="panel-body" id="job-monitor-body"></div>
  </div>
  ```

- [ ] **Step 3:** `pnp-mode.js` — toggle handler:
  - On toggle ON: hide `panel-import` + `panel-bom`, show
    `panel-feeders` + `panel-job-monitor`, emit `MODE_CHANGED`
  - On toggle OFF: reverse
  - Persist to preferences
  - On load: restore from preferences

- [ ] **Step 4:** Add events to event-bus:
  ```js
  MODE_CHANGED:     "mode-changed",
  FEEDERS_UPDATED:  "feeders-updated",
  PNP_CONSUME:      "pnp-consume",
  ```

- [ ] **Step 5:** Playwright test: toggle PnP mode, verify panel visibility
  swaps correctly.

### Task 7: Feeder Dashboard panel

**Files:**
- Create: `js/pnp/feeder-dashboard.js`
- Create: `js/pnp/feeder-renderer.js`
- Modify: `css/panels/pnp.css`

- [ ] **Step 1:** `feeder-dashboard.js` — panel logic:
  - On `FEEDERS_UPDATED`: re-render
  - On `PNP_CONSUME`: update specific feeder card qty + progress bar
    (incremental DOM update, not full re-render)
  - On `MODE_CHANGED` to PnP: fetch feeders, render
  - Polling: start interval on mode enter, stop on mode exit

- [ ] **Step 2:** `feeder-renderer.js` — pure rendering:
  - `renderFeederDashboard(feeders, bomParts)` → HTML string
  - Group feeders into Active/Idle/Empty based on BOM state
  - Render feeder cards with progress bars
  - Collapsible sections (reuse chevron pattern from inventory panel)

- [ ] **Step 3:** Feeder card styles in `pnp.css`:
  - `.feeder-card` — `--bg-surface` background, `--border-default` border,
    8px border-radius, 10px padding
  - `.feeder-status-dot` — 10px circle, positioned top-left of card
  - `.feeder-progress-bar` — 6px height, `--bg-raised` track,
    colored fill matching status
  - `.feeder-alert` — yellow/red strip with ⚠/⛔ icon
  - `.feeder-card.expanded` — shows action buttons

- [ ] **Step 4:** Feeder card interactions:
  - Click card → toggle expanded state (show Unload / Edit Qty / View Part)
  - "Unload" → confirmation toast → `api("unload_feeder", feederId)`
  - "Edit Qty" → inline number input → `api("update_feeder_qty", feederId, qty)`
  - "View Part" → `EventBus.emit(Events.INVENTORY_SCROLL_TO, partKey)` (new event)

- [ ] **Step 5:** Low-feeder alert banner:
  - Rendered at top of feeders-body when any active feeder is below threshold
  - Yellow background (`--color-yellow` at 15% opacity), ⚠ icon
  - Lists all low feeders with part key and remaining qty
  - Also fires `AppLog.warn()` to console

- [ ] **Step 6:** Sync button:
  - `api("get_bridge_feeders", host)` → `api("sync_feeders_from_bridge", result)`
  - Show toast on success: "Synced N feeders from OpenPnP"
  - Show toast on error: "Could not reach OpenPnP bridge at {host}"

- [ ] **Step 7:** Vitest tests for renderer (pure functions).
  Playwright test: enter PnP mode, verify feeder cards render.

### Task 8: Feeder Setup modal

**Files:**
- Create: `js/pnp/feeder-setup-modal.js`
- Modify: `index.html` (add modal HTML)
- Modify: `css/modals.css` (wide modal variant)

- [ ] **Step 1:** Add modal HTML to `index.html`:
  ```html
  <div class="modal-overlay hidden" id="feeder-setup-modal">
    <div class="modal modal-feeder-setup">
      <div class="modal-title" id="feeder-setup-title">Setup Feeders</div>
      <div class="modal-subtitle" id="feeder-setup-subtitle"></div>
      <div class="feeder-setup-table-wrap" id="feeder-setup-table-wrap"></div>
      <div class="modal-divider"></div>
      <div class="feeder-setup-summary" id="feeder-setup-summary"></div>
      <div class="modal-actions">
        <button class="btn btn-cancel" id="feeder-setup-cancel">Cancel</button>
        <button class="btn btn-apply" id="feeder-setup-save">Save & Load</button>
      </div>
    </div>
  </div>
  ```

- [ ] **Step 2:** `.modal-feeder-setup` style:
  - `min-width: 560px; max-width: 720px` (wider than standard modal)
  - Table scrolls vertically if many parts (max-height: 60vh)

- [ ] **Step 3:** `feeder-setup-modal.js`:
  - `open(bomMatches, multiplier, existingFeeders, bridgeFeeders)`:
    - Build table rows from BOM matches
    - Pre-populate feeder assignments from existing loaded feeders
    - Suggest from bridge feeders if not yet assigned
  - Feeder dropdown per row: lists bridge feeders + "— none —"
  - Loaded qty input per row (editable number, default from existing or blank)
  - Status column: ✔ if assigned + qty >= needed, ⚠ otherwise
  - Summary line: "N/M parts assigned · K unassigned"

- [ ] **Step 4:** "Save & Load" handler:
  - For each changed row: call `api("load_feeder", ...)` or
    `api("unload_feeder", ...)` as needed
  - Batch operations, show progress
  - Emit `Events.FEEDERS_UPDATED` when done
  - Close modal

- [ ] **Step 5:** Playwright test: open setup modal, assign feeders, verify
  feeder dashboard updates.

### Task 9: Job Monitor panel

**Files:**
- Create: `js/pnp/job-monitor.js`
- Create: `js/pnp/job-renderer.js`
- Modify: `css/panels/pnp.css`

- [ ] **Step 1:** `job-monitor.js`:
  - Poll `api("get_bridge_job", host)` (new API wrapping bridge `/api/job`)
  - Listen to `PNP_CONSUME` events for real-time placement log
  - Compute placement rate (rolling window of last 20 events)
  - Compute ETA: remaining placements / rate

- [ ] **Step 2:** `job-renderer.js`:
  - `renderJobMonitor(jobState, placements, feeders, bomParts)`:
    - Status badge: RUNNING (green pulse), IDLE (gray), ERROR (red)
    - Board progress: "Board N of M" with progress bar
    - Overall progress: "N / total placements"
    - Rate + ETA
    - Recent placements table (last 20, newest at top):
      timestamp | part key | feeder | qty
    - Feeder coverage summary

- [ ] **Step 3:** When idle (no job running):
  - Show last job summary if available (stored in session state)
  - Show loaded feeder count
  - Prompt: "Load a BOM and set up feeders to start tracking a job"

- [ ] **Step 4:** Real-time update flow:
  - `window._pnpConsume` already fires on every placement
  - In PnP mode, additionally emit `Events.PNP_CONSUME` with full detail
  - Job monitor appends to placement log, updates progress bar
  - Feeder dashboard updates the specific feeder card

- [ ] **Step 5:** Styles in `pnp.css`:
  - `.job-status-badge` — inline status with pulsing dot for RUNNING
  - `.placement-log` — mono font, compact rows, scrollable
  - `.progress-overall` — large progress bar (12px height)
  - `.coverage-list` — green ✔ / yellow ⚠ per part

- [ ] **Step 6:** Playwright test: verify job monitor renders idle state.
  (Live job testing requires PnP E2E infrastructure.)

### Task 10: Inventory panel feeder annotations

**Files:**
- Modify: `js/inventory/inventory-renderer.js`
- Modify: `css/panels/inventory.css`

- [ ] **Step 1:** When PnP mode is active, add feeder badge to part rows:
  ```html
  <span class="chip teal feeder-badge" data-feeder="Feeder-01">F-01: 38</span>
  ```
  Positioned after the qty cell. Multiple badges if part is on multiple feeders.

- [ ] **Step 2:** On `PNP_CONSUME` event, flash the qty cell green briefly
  (CSS animation: `@keyframes qty-flash`, 300ms, green → normal).

- [ ] **Step 3:** On `FEEDERS_UPDATED`, re-render badges on affected parts
  (targeted DOM update, not full panel re-render).

- [ ] **Step 4:** Click feeder badge → emit `Events.FEEDER_SCROLL_TO` with
  feeder_id, feeder dashboard scrolls to that card.

---

## Testing Strategy

### Unit Tests (vitest)
- Feeder renderer: pure functions, snapshot tests for HTML output
- Job monitor: rate calculation, ETA computation, placement log management
- Feeder setup: auto-suggest logic, status computation

### Python Unit Tests (pytest)
- `test_cache_db.py`: feeders table CRUD, v3→v4 migration
- `test_inventory_api.py`: load/unload/consume with feeder_id, stock invariant
- `test_pnp_server.py`: feeder API endpoints, consume with feeder_id

### E2E Tests (Playwright)
- Toggle PnP mode: panels swap correctly
- Feeder dashboard: renders cards from mock data
- Feeder setup modal: assign feeders, save, verify dashboard updates
- Job monitor: idle state render

### PnP E2E Tests (real OpenPnP)
- Sync feeders from bridge
- Consume with feeder_id → feeder qty decrements
- Low-feeder alert triggers at threshold
- Full flow: setup → run job → feeders deplete → unload

---

## Future (not in this plan)

- **ESP32 / AprilTag integration**: `feeders` table gains `tag_type`,
  `tag_value`, `last_seen` columns. REST API already supports it.
- **Smart rack polling**: hardware pushes `POST /api/feeders` when
  feeder is physically inserted/removed.
- **Feeder history view**: timeline of load/unload/consume events per
  feeder, useful for optimizing feeder layout.
- **Auto-reorder suggestions**: when stock for a loaded part drops below
  reorder threshold, show a notification with distributor links.
