# Phase 2 Frontend Completion — Implementation Plan

**Goal:** Wire the existing Phase 2a (price history) and 2b (generic parts) backend
into the frontend so the features are actually usable. Backend code is complete and
tested; this plan closes the frontend gaps.

**Prerequisite for:** Phase 3 (feeder tracking relies on the cache layer being fully
active and the generic-parts matching pipeline being live).

---

## Current State

**Phase 2a (Price History):** Backend complete — `price_history.py` records
observations, `prices` cache table aggregates them, `inventory_api.py` exposes
`record_fetched_prices()` and `get_price_summary()`. No frontend calls either method.
Distributor tooltip fetches don't record prices. `data/events/` dir is created
lazily on first write (already handled).

**Phase 2b (Generic Parts):** Backend complete — `spec_extractor.py` extracts specs,
`generic_parts.py` handles CRUD/auto-matching/resolution, `inventory_api.py` exposes
`create_generic_part()`, `resolve_bom_spec()`, `list_generic_parts()`.
`js/matching.js` has step 5.5 generic resolution code. But:
- `matchBOM()` always receives `genericParts = []` (nothing loads them)
- `matchType: "generic"` falls to "—" in the match label (line 55 of `bom-row-data.js`)
- `computeRows()` treats generic like LCSC/MPN (ok/short) — no dedicated status
- `countStatuses()` has no generic counter
- Filter bar has no Generic button
- No member expansion UI (like alt rows but for generic group members)
- `add_member`/`remove_member`/`set_preferred` not exposed in `inventory_api.py`
- No generic part creation or management UI

---

## Part I — Price History Frontend

### Task 1: Record prices from distributor fetches

`record_fetched_prices()` exists on the API but no frontend code calls it.

**Files:**
- Modify: `js/part-preview.js`

- [ ] **Step 1:** After a successful Digikey/LCSC/Pololu/Mouser tooltip fetch that
  returns price tier data, call:
  ```js
  api("record_fetched_prices", partKey, distributor, priceTiers)
  ```
  Fire-and-forget — don't await, don't block the tooltip render.
- [ ] **Step 2:** Normalize price tier shape across distributors so they all pass
  `[{ price: number, qty: number }, ...]` to the API.
- [ ] **Step 3:** Vitest test: mock api call, trigger tooltip fetch, verify
  `record_fetched_prices` was called with correct arguments.

### Task 2: Show price history in part preview tooltip

**Files:**
- Modify: `js/part-preview.js`

- [ ] **Step 1:** After fetching distributor data, also call
  `api("get_price_summary", partKey)`.
- [ ] **Step 2:** If price history exists, append a "Price History" section below the
  live prices in the tooltip:
  ```
  ── Price History ────────────────
  Distributor │  Latest │  Avg  │ #
  LCSC        │  $0.012 │ $0.011│ 4
  Digikey     │  $0.035 │ $0.031│ 2
  ```
- [ ] **Step 3:** Style with existing `.tooltip-table` CSS. Muted header, compact rows.
- [ ] **Step 4:** If no price history yet, don't render the section (no empty state).

---

## Part II — Generic Parts: Plumbing

### Task 3: Wire generic parts into BOM matching

**Files:**
- Modify: `js/store.js`
- Modify: `js/bom/bom-panel.js` (match call site)
- Modify: `js/event-bus.js`

- [ ] **Step 1:** In `store.js`, add `App.genericParts = []` to the global state
  declarations.
- [ ] **Step 2:** After `INVENTORY_LOADED`, call `api("list_generic_parts")` and store
  the result in `App.genericParts`. Emit `Events.GENERIC_PARTS_LOADED`.
- [ ] **Step 3:** Add `GENERIC_PARTS_LOADED: "generic-parts-loaded"` to `Events` in
  `event-bus.js`.
- [ ] **Step 4:** In the BOM matching call site (where `matchBOM()` is invoked), pass
  `App.genericParts` as the fifth argument.
- [ ] **Step 5:** Vitest test: mock `api("list_generic_parts")` returning a set of
  generic parts with members, verify `matchBOM()` resolves a BOM row via
  `matchType: "generic"` with a valid `genericPartId`.

### Task 4: Expose generic part membership management in API

**Files:**
- Modify: `inventory_api.py`
- Modify: `tests/python/test_inventory_api.py`

- [ ] **Step 1:** Add `add_generic_member(generic_part_id, part_id)`:
  - Calls `generic_parts.add_member(conn, generic_part_id, part_id)`
  - Returns updated member list
- [ ] **Step 2:** Add `remove_generic_member(generic_part_id, part_id)`:
  - Calls `generic_parts.remove_member(conn, generic_part_id, part_id)`
  - Returns updated member list
- [ ] **Step 3:** Add `set_preferred_member(generic_part_id, part_id)`:
  - Calls `generic_parts.set_preferred(conn, generic_part_id, part_id)`
  - Returns updated member list
- [ ] **Step 4:** Add `update_generic_part(generic_part_id, name, spec_json, strictness_json)`:
  - Updates spec fields and re-runs auto-matching
  - Returns updated generic part with members
- [ ] **Step 5:** Pytest tests for all four new API methods.

---

## Part III — Generic Parts: BOM Table Integration

### UX Design: Generic Matches in BOM Comparison

When a BOM row matches through a generic part (e.g., BOM says "100nF 0402" and
generic part "100nF 0402 MLCC" resolves it to inventory part C1525):

```
┌──────────┬───┬──────────┬──────────┬──────┬──────┬──────────────┬─────────┬────────────┐
│ Refs     │ ◆ │ C1525    │ CL05B104│ 12   │ 200  │ 100nF ±10%   │ Generic │ [Members ▾]│
│ C1,C2... │   │ LCSC     │          │      │      │ 0402 C0G     │         │ [Adjust]   │
│          │   │          │          │      │      │              │         │ [Link]     │
├──────────┼───┼──────────┼──────────┼──────┼──────┼──────────────┼─────────┼────────────┤
│          │   │  Generic group: 100nF 0402 MLCC                  │         │            │
│          │ ★ │ C1525    │ CL05B104 │      │ 200  │ 100nF 0402   │ Preferred            │
│          │   │ C315236  │ GCM155.. │      │  50  │ 100nF 0402   │ [Use] [Adjust]       │
│          │   │ C105620  │ CC0402.. │      │   0  │ 100nF 0402   │ [Use] [Adjust]       │
└──────────┴───┴──────────┴──────────┴──────┴──────┴──────────────┴─────────┴────────────┘
```

**Key design decisions:**

1. **Color: purple** (`--color-purple: #a371f7`). Generic matches get their own
   visual identity, distinct from green (direct match), orange (possible), pink
   (manual), and teal (confirmed). This signals "matched via group, not by exact
   part number" — the user should notice and can inspect/override.

2. **Icon: ◆** (filled diamond). Conveys "resolved through a group" without
   implying uncertainty (like "?" for possible). `◆~` for generic-short.

3. **Status flows as ok/short underneath.** A generic match with sufficient stock
   is `generic` (purple, ◆). A generic match with insufficient stock is
   `generic-short` (purple with short indicator, ◆~). This parallels how manual
   and confirmed each have a `-short` variant.

4. **Members button** replaces the Confirm button position. Clicking expands the
   generic group members below the row (same pattern as alt-row expansion).
   Member rows show part key, MPN, stock qty, preferred badge (★), and a
   "Use" button to select a different member.

5. **"Use" on a member** confirms the match with that specific real part. The
   match becomes `confirmed` (teal) with the chosen part. This is a deliberate
   act — generic matches are auto-accepted for consumption, but the user can
   lock in a specific member if they care.

6. **Consumption:** Generic matches ARE included in `prepareConsumption()` — this
   already works because the code excludes only `value` and `fuzzy` (line 126 of
   `bom-logic.js`). No change needed.

7. **Generic group name** shown as a subtitle in the description cell:
   `description + " · via 100nF 0402 MLCC"` in muted text.

### Task 5: Add generic status to the pipeline

**Files:**
- Modify: `js/part-keys.js` — add status entries
- Modify: `js/bom/bom-logic.js` — add `generic` status computation
- Modify: `js/bom-row-data.js` — add match label, icon, row class, qty class
- Modify: `js/inventory/inventory-renderer.js` — add filter button, count
- Modify: `css/tables.css` or `css/panels/bom.css` — add purple row styles

- [ ] **Step 1:** Add to `STATUS_ICONS` in `part-keys.js`:
  ```js
  "generic": "◆",
  "generic-short": "◆~",
  ```

- [ ] **Step 2:** Add to `STATUS_ROW_CLASS` in `part-keys.js`:
  ```js
  "generic": "row-purple",
  "generic-short": "row-purple-short",
  ```

- [ ] **Step 3:** In `computeRows()` (`bom-logic.js` line 66), add a branch for
  generic before the ok/short fallthrough:
  ```js
  } else if (r.matchType === "generic") {
    status = r.bom.qty * mult > r.inv.qty ? "generic-short" : "generic";
  } else if (r.bom.qty * mult <= r.inv.qty) {
  ```

- [ ] **Step 4:** In `countStatuses()` (`part-keys.js`), add a `generic` counter.
  Count both `generic` and `generic-short` into it.

- [ ] **Step 5:** In `bomRowDisplayData()` (`bom-row-data.js` line 49), add:
  ```js
  : r.matchType === "generic" ? "Generic"
  ```
  to the matchLabel chain (before the final `"\u2014"`).

- [ ] **Step 6:** Add qty classes for generic:
  ```js
  : st === "generic" ? "qty-generic"
  : st === "generic-short" ? "qty-generic-short"
  ```

- [ ] **Step 7:** Add filter support — in the filter condition (line 12-17), add:
  ```js
  (activeFilter === "generic" && st === "generic-short")
  ```

- [ ] **Step 8:** In `renderFilterBarHtml()` (`inventory-renderer.js`), add a Generic
  filter button (purple) when `c.generic > 0`, between Confirmed and In Stock.

- [ ] **Step 9:** Add CSS for purple rows in `css/tables.css` or `css/panels/bom.css`:
  ```css
  .row-purple       { background: #a371f720; }
  .row-purple-short { background: #a371f715; }
  .row-purple td:first-child { border-left: 3px solid var(--color-purple); }
  .qty-generic       { color: var(--color-purple-light); }
  .qty-generic-short { color: var(--color-purple); }
  ```

- [ ] **Step 10:** Vitest tests:
  - `computeRows` with a generic matchType returns `generic` / `generic-short`
  - `bomRowDisplayData` returns matchLabel "Generic", icon "◆", rowClass "row-purple"
  - `countStatuses` counts generic matches
  - Filter with `activeFilter === "generic"` shows generic and generic-short rows

### Task 6: Generic group member expansion

The member expansion follows the same pattern as alt-row expansion but shows the
generic group's members instead of value-matched alternatives.

**Files:**
- Modify: `js/bom-row-data.js` — add `memberBadge` to display data
- Modify: `js/inventory/inventory-renderer.js` — add member rows renderer
- Modify: `js/inventory/inventory-panel.js` — add member expand/collapse + Use handler
- Modify: `css/panels/bom.css` — member row styles

- [ ] **Step 1:** Extend match result to carry generic members. In `matchBOM()`
  (`matching.js`), when `matchType === "generic"`, also store the full generic
  part object (name, members list) on the result:
  ```js
  results.push({
    bom, inv, status, matchType, alts,
    genericPartId: gp.generic_part_id,
    genericPartName: gp.name,              // NEW
    genericMembers: gp.members,            // NEW — [{part_id, preferred, quantity}]
  });
  ```

- [ ] **Step 2:** In `bomRowDisplayData()`, add `memberBadge` (analogous to `altBadge`):
  ```js
  var memberBadge = null;
  if (r.matchType === "generic" && r.genericMembers && r.genericMembers.length > 1) {
    memberBadge = {
      groupName: r.genericPartName,
      memberCount: r.genericMembers.length,
      expanded: expandedMembers.has(partKey),  // new Set, like expandedAlts
    };
  }
  ```
  Add `expandedMembers` parameter to the function signature (passed from panel).

- [ ] **Step 3:** In `createBomRowElement()`, render the member badge in the Have cell
  (below invQty), similar to alt badge:
  ```html
  <span class="member-badge" data-part-key="...">
    <span class="chevron">▸</span> 3 members (100nF 0402 MLCC)
  </span>
  ```
  Style: purple text, `--color-purple-light` on hover.

- [ ] **Step 4:** In the description cell, append generic group name as subtitle:
  ```html
  <span class="generic-via muted">via 100nF 0402 MLCC</span>
  ```

- [ ] **Step 5:** Add `renderMemberRows(members, partKey, resolvedPartId)` to
  `inventory-renderer.js`, parallel to `renderAltRows()`:
  ```
  ┌─────────────────────────────────────────────────────────────────────┐
  │        Generic group: 100nF 0402 MLCC                              │
  ├───┬───┬──────────┬──────────┬──────┬──────┬──────────────┬─────────┤
  │   │ ★ │ C1525    │ CL05B104 │      │ 200  │ 100nF 0402   │ Current │
  │   │   │ C315236  │ GCM155.. │      │  50  │ 100nF 0402   │ [Use]   │
  │   │   │ C105620  │ CC0402.. │      │   0  │ 100nF 0402   │ [Use]   │
  └───┴───┴──────────┴──────────┴──────┴──────┴──────────────┴─────────┘
  ```
  - First row: group header with generic part name (purple background strip)
  - Member rows: `<tr class="member-row">` styled like `.alt-row` but with
    purple left border
  - ★ badge on preferred member
  - "Current" label (muted) on the resolved member
  - "Use" button on other members
  - Stock qty with color: green if > 0, muted if 0
  - Members sorted: preferred first, then by qty descending

- [ ] **Step 6:** Wire expand/collapse in `inventory-panel.js`:
  - Add `expandedMembers = new Set()` alongside `expandedAlts`
  - On `.member-badge` click: toggle partKey in expandedMembers, re-render row
  - On `.use-member-btn` click:
    - Call `App.links.confirmMatch(bomKey, memberPartKey)` to lock in the member
    - Emit `Events.CONFIRMED_CHANGED`
    - Row transitions from `generic` (purple) to `confirmed` (teal)
    - This is the deliberate "I want this specific part" act
  - The member expansion collapses after confirming

- [ ] **Step 7:** CSS for member rows:
  ```css
  .member-row {
    background: #a371f70a;
    border-left: 3px solid var(--color-purple);
  }
  .member-row .preferred-badge {
    color: var(--color-yellow);
    font-size: 11px;
  }
  .member-badge {
    color: var(--color-purple-light);
    font-size: 11px;
    cursor: pointer;
  }
  .member-badge:hover { color: var(--color-purple); }
  .member-badge .chevron { transition: transform 0.15s; }
  .member-badge.expanded .chevron { transform: rotate(90deg); }
  .generic-via { font-size: 11px; display: block; }
  .current-label { color: var(--text-muted); font-size: 11px; }
  ```

- [ ] **Step 8:** Vitest tests:
  - `bomRowDisplayData` with generic match returns memberBadge when > 1 member
  - `renderMemberRows` renders correct number of rows with preferred badge
  - Clicking "Use" triggers confirmMatch with the correct member part key

---

## Part IV — Generic Part Management

### UX Design: Creating and Managing Generic Parts

Generic parts should be discoverable but not intrusive. Two entry points:

**Entry point 1: From inventory panel (proactive).** A "Create Generic" button
appears on the right-click context menu (or inline button) for any part that
doesn't belong to a generic group. Opens the creation modal pre-filled with
that part's extracted spec.

**Entry point 2: From BOM comparison (reactive).** When a BOM row is "missing"
or "possible" and the user clicks Link → instead of linking to a specific part,
they can click "Create Generic" in the linking banner. This creates a generic
part from the BOM row's value/package/type, auto-matches existing inventory,
and resolves the BOM row.

**Entry point 3: From inventory section header (bulk).** A "⚡ Auto-create
Generics" action in the section header dropdown scans all parts in that
category, extracts specs, and suggests generic groups. User reviews and
confirms.

### Task 7: Generic Part Creation Modal

**Files:**
- Create: `js/generic-parts-modal.js`
- Modify: `index.html` — add modal HTML
- Modify: `css/modals.css` — modal styles

- [ ] **Step 1:** Add modal HTML to `index.html`:
  ```html
  <div class="modal-overlay hidden" id="generic-modal">
    <div class="modal modal-wide">
      <div class="modal-title" id="generic-modal-title">Create Generic Part</div>
      <div class="modal-subtitle" id="generic-modal-subtitle"></div>
      <div class="modal-form">
        <label for="gp-name">Name:</label>
        <input type="text" id="gp-name" placeholder="e.g. 100nF 0402 MLCC">
      </div>
      <div class="modal-form">
        <label for="gp-type">Type:</label>
        <select id="gp-type">
          <option value="capacitor">Capacitor</option>
          <option value="resistor">Resistor</option>
          <option value="inductor">Inductor</option>
          <option value="ic">IC</option>
          <option value="other">Other</option>
        </select>
      </div>
      <div class="modal-divider"></div>
      <div class="modal-subtitle"><strong>Matching Spec</strong></div>
      <div id="gp-spec-fields"></div>
      <div class="modal-divider"></div>
      <div class="modal-subtitle"><strong>Auto-matched Members</strong> <span id="gp-member-count" class="muted"></span></div>
      <div id="gp-members-preview"></div>
      <div class="modal-actions">
        <button class="btn btn-cancel" id="gp-cancel">Cancel</button>
        <button class="btn btn-apply" id="gp-save">Create</button>
      </div>
    </div>
  </div>
  ```

- [ ] **Step 2:** Spec fields section — dynamically rendered based on type:
  ```
  ┌─ Matching Spec ───────────────────────────┐
  │                                            │
  │ Value:     [100nF     ] ☑ Required         │
  │ Package:   [0402      ] ☑ Required         │
  │ Voltage:   [16V       ] ☐ Optional         │
  │ Tolerance: [±10%      ] ☐ Optional         │
  │ Dielectric:[C0G       ] ☐ Optional         │
  │                                            │
  └────────────────────────────────────────────┘
  ```
  - Each spec field: text input + "Required" checkbox
  - Required = must match for auto-membership (maps to `strictness_json.required[]`)
  - Optional = stored but not enforced in auto-matching
  - Pre-populated from `api("extract_spec", partKey)` (new API wrapping
    `spec_extractor.extract_spec()`)
  - When spec fields change, trigger live preview of auto-matched members

- [ ] **Step 3:** Auto-matched members preview:
  ```
  ┌─ Auto-matched Members (3) ────────────────┐
  │ ✔ C1525    CL05B104  200 in stock         │
  │ ✔ C315236  GCM155..   50 in stock         │
  │ ✔ C105620  CC0402..    0 in stock         │
  └────────────────────────────────────────────┘
  ```
  - Fetched by calling `api("create_generic_part", name, type, spec, strictness)`
    in **preview mode** (dry-run) — or by adding a
    `api("preview_generic_matches", type, spec, strictness)` endpoint
  - Each member has a checkbox (pre-checked) to include/exclude
  - Shows part key, MPN, stock qty

- [ ] **Step 4:** "Create" button handler:
  - Call `api("create_generic_part", name, type, specJson, strictnessJson)`
  - Refresh `App.genericParts` from `api("list_generic_parts")`
  - Emit `Events.GENERIC_PARTS_LOADED`
  - If a BOM is loaded, re-run matching (the new generic part may resolve rows)
  - Close modal, toast: "Created generic part: 100nF 0402 MLCC (3 members)"

- [ ] **Step 5:** Add `extract_spec` to `inventory_api.py`:
  ```python
  def extract_spec(self, part_key: str) -> dict:
      """Extract component spec from a part's description/metadata."""
      part = self._find_part(part_key)
      return spec_extractor.extract_spec(
          part.get("description", ""), part.get("package", "")
      )
  ```

### Task 8: Generic Part Editing Modal

Reuses the creation modal with an "Edit" title and pre-populated fields.

**Files:**
- Modify: `js/generic-parts-modal.js`

- [ ] **Step 1:** `openEdit(genericPartId)` mode:
  - Fetches generic part details from `App.genericParts`
  - Pre-fills name, type, spec fields, strictness checkboxes
  - Shows current members with add/remove controls
  - Title: "Edit Generic Part"
  - Save button: "Update"

- [ ] **Step 2:** Member management within the edit modal:
  - Each member row has a "Remove" button (calls `api("remove_generic_member", ...)`)
  - "Add Member" row at bottom: type-ahead search against inventory
    (reuse inventory search pattern)
  - Star toggle on each member to set/unset preferred
    (calls `api("set_preferred_member", ...)`)

- [ ] **Step 3:** Spec changes trigger re-run of auto-matching:
  - Show diff: "Will add 2 members, remove 1"
  - User confirms before applying

- [ ] **Step 4:** "Update" handler:
  - Call `api("update_generic_part", ...)` for spec/name changes
  - Refresh `App.genericParts`
  - Re-run BOM matching if BOM loaded

### Task 9: Entry points for generic part creation

**Files:**
- Modify: `js/inventory/inventory-panel.js`
- Modify: `js/bom/bom-panel.js`

- [ ] **Step 1: From inventory part row.** Add a "Group" button to part rows
  (next to Adjust and Link buttons, only when no BOM is loaded or as a
  persistent action):
  - If part is NOT in a generic group: "Group" button → opens creation modal
    pre-filled from this part
  - If part IS in a generic group: "Group" button shows a purple badge with
    group name; click opens edit modal

- [ ] **Step 2: From BOM missing/possible row.** In the button group, add a
  "Create Group" button when:
  - Status is `missing` or `possible`
  - BOM row has value + package data but no part number
  - Click opens creation modal pre-filled from BOM row's extracted spec

- [ ] **Step 3: From linking banner.** When in reverse linking mode (BOM → inventory)
  for a missing row, add "or Create Generic" option in the linking banner.
  Click opens creation modal.

### Task 10: Inventory panel — generic group badges

**Files:**
- Modify: `js/inventory/inventory-renderer.js`
- Modify: `css/panels/inventory.css`

- [ ] **Step 1:** When rendering inventory part rows, check if the part belongs
  to any generic group (cross-reference `App.genericParts`).

- [ ] **Step 2:** If yes, show a purple badge after the part key:
  ```html
  <span class="chip purple generic-group-badge"
        data-generic-id="cap_100nf_0402"
        title="100nF 0402 MLCC">◆ 100nF 0402</span>
  ```

- [ ] **Step 3:** Click badge → opens generic part edit modal for that group.

- [ ] **Step 4:** Badge CSS:
  ```css
  .generic-group-badge {
    background: #a371f718;
    color: var(--color-purple-light);
    border: 1px solid #a371f730;
    font-size: 10px;
    padding: 1px 5px;
    border-radius: 4px;
    cursor: pointer;
  }
  .generic-group-badge:hover {
    background: #a371f730;
  }
  ```

---

## Part V — Testing Strategy

### Vitest (JS unit tests)
- `matching.test.js`: generic matching resolves BOM rows, sets matchType/genericPartId
- `bom-logic.test.js`: computeRows with generic → generic/generic-short status
- `bom-row-data.test.js`: display data for generic matches (label, icon, class, badge)
- `inventory-renderer.test.js`: member rows render, filter bar includes generic

### Pytest (Python unit tests)
- `test_inventory_api.py`: new API methods (add/remove/set_preferred/update/extract_spec)
- `test_spec_extractor.py`: already comprehensive, add edge cases if needed

### Playwright (E2E tests)
- Load BOM with generic-matchable rows → verify purple rows appear
- Expand member badge → verify member rows render with correct data
- Click "Use" on member → row transitions to confirmed (teal)
- Open create modal from inventory → pre-filled spec → create → verify row resolves
- Edit generic part → change strictness → verify member list updates

---

## Implementation Order

The tasks should be done in this order due to dependencies:

```
Task 3 (wire matching)     ← foundational, everything depends on this
    ↓
Task 4 (API methods)       ← needed for management UI
    ↓
Task 5 (status pipeline)   ← purple rows, icons, labels, filters
    ↓
Task 6 (member expansion)  ← depends on status pipeline + match data
    ↓
Task 7 (creation modal)    ← depends on API methods
    ↓
Task 8 (editing modal)     ← extends creation modal
    ↓
Task 9 (entry points)      ← wires modals into existing panels
    ↓
Task 10 (inventory badges) ← polish, can be done in parallel with 9
    ↓
Tasks 1-2 (price history)  ← independent, can be done at any point
```
