# Inventory Column Headers, Per-Part Price, and Scope-Cycling Sort

## Goal

Add a sticky column-header row to the main inventory view that:

1. Names every column in the part row (currently unlabeled).
2. Adds a new **Unit Price** column adjacent to **Total Value**.
3. Lets each header act as a sort/group control with a scope-expanding click cycle.
4. Has a **Reset (↺)** button at the right edge.
5. Persists its state across sessions via `preferences.json`.

Out of scope: BOM comparison mode (already has its own `<thead>`), Groups (◆) flyout view (keeps its preferred-first ordering).

## Column layout

The header row is rendered above the inventory body and aligned 1:1 with the columns of `.inv-part-row` (which uses flexbox, not a `<table>`).

| # | Header | Width source | Click behavior |
|---|--------|--------------|----------------|
| 1 | **Group** | drag-handle + width of section-chip when flat | 3-state toggle (see below) |
| 2 | **Part #** | matches `.part-ids` (100px) | scope-cycling vendor-grouping |
| 3 | **MPN** | matches `.part-mpn` (160px) | scope-cycling sort, A→Z |
| 4 | **Unit $** *(new)* | new column, ~70px right-aligned | scope-cycling sort, $ desc |
| 5 | **Total $** | matches `.part-value` (70px) | scope-cycling sort, $ desc |
| 6 | **Qty** | matches `.part-qty` (60px) | scope-cycling sort, # desc |
| 7 | **Description** | matches `.part-desc` (flex) | scope-cycling sort, A→Z |
| 8 | **↺ Reset** | matches `.part-actions` | clears everything |

Each column has a single natural sort direction (no direction toggle). Numeric columns sort descending; text columns sort ascending.

## The Group toggle (column 1, 3-state)

| State | Meaning | What renders |
|-------|---------|--------------|
| `0` (default) | Full hierarchy | Parent section headers + subsection headers + parts (current behavior) |
| `1` | Sections only | Top-level section headers only; subsection groupings folded up so all parts within a section appear flat under the section header |
| `2` | Flat | No section/subsection headers at all; one flat list of parts. Each row gets a small section chip in column 1 so the category is still visible |

Click the Group header to advance: `0 → 1 → 2 → 0`.

The header indicator visually fills 0, 1, or 2 dots to show the current state.

## Scope-cycling click model (columns 2–7)

Each click on a sortable header expands the **scope** of the sort (or vendor-grouping for column 2). The available scopes depend on the current Group state:

| Group state | Click sequence | Final click |
|-------------|----------------|-------------|
| `0` (full) | subsection → section → global | reset |
| `1` (sections) | section → global | reset |
| `2` (flat) | global | reset |

**Subsection scope**: sort within each subsection. Section/subsection headers stay in place; only the parts inside each subsection are reordered.

**Section scope**: sort within each top-level section. Subsection groupings are temporarily ignored for this column's sort (parts from all subsections of a section are merged then sorted). Subsection headers are hidden while a section-scope sort is active.

**Global scope**: sort across the entire inventory. All section/subsection headers are hidden while a global-scope sort is active (Group state visually equivalent to `2` for this render). When Group state is `2`, this is the only available scope.

For sections that have no subsections, the subsection step is skipped (clicking from default takes you straight to section scope).

**Header visual indicator** (sort columns): a tiny scope dot stack alongside the natural-direction arrow — e.g. `▼·` (subsection), `▼··` (section), `▼···` (global). Inactive columns show a faint placeholder. Same pattern for the Part # column with a vendor-group glyph in place of the arrow.

**Mutual exclusion**: only one of {column-3..7 sort, column-2 vendor-group} can be active at a time. Activating one clears the other.

## Part # vendor-grouping (column 2)

Same scope cycle as sort, but the operation groups parts by vendor (LCSC / Digikey / Mouser / Pololu / Other) instead of sorting. At each scope, vendor groups are rendered as inline subheaders within that scope:

- **Subsection scope**: inside each subsection, parts split into vendor piles.
- **Section scope**: inside each section, parts split into vendor piles (subsections collapsed for the duration of this view).
- **Global scope**: one big set of vendor piles across all parts (sections collapsed for the duration of this view).

This is the only column that *ignores* the Group toggle's flat (`2`) state — vendor-grouping always renders vendor subheaders regardless of Group state. The Group state still controls which scopes are reachable in the cycle.

## Reset (↺, column 8)

The Reset button:

- Sits in the column-header area directly above the per-row Adjust/Groups buttons.
- Is always visible (not greyed out) — clicking when nothing is active is a no-op.
- On click: clears the active sort or vendor-group, and resets Group state to `0`.

## Per-part Unit Price column

A new column placed immediately before Total Value. Format:

- `$X.XX` when unit_price ≥ $0.01
- `$X.XXXX` when unit_price < $0.01 and > 0
- `—` when unit_price is 0/null/missing

Right-aligned, monospace. Existing `.price-warn-btn` (the ⚠ on the qty cell when price is missing) stays where it is.

## Sticky positioning

```
top: 0,   z: 6  → column header
top: H₁,  z: 4  → parent header   (H₁ = column header height)
top: H₁+H₂, z: 3 → subsection header (H₂ = parent header height)
```

Heights are fixed in CSS (single line of small text, predictable) — use a CSS custom property `--inv-col-header-h` so all three sticky offsets stay in sync.

When section/subsection headers are hidden by an active sort/group operation, no offset adjustment is needed (they're not in the DOM).

## State and persistence

New session state lives in `inv-state.js`:

```
state.groupLevel        // 0 | 1 | 2
state.sortColumn        // null | "mpn" | "unit_price" | "value" | "qty" | "desc"
state.sortScope         // null | "subsection" | "section" | "global"
state.vendorGroupScope  // null | "subsection" | "section" | "global"
```

Mirrored to `preferences.json` under a new `inventory_view` key:

```json
{
  "inventory_view": {
    "group_level": 0,
    "sort_column": null,
    "sort_scope": null,
    "vendor_group_scope": null
  }
}
```

Loaded into `inv-state.js` by `app-init.js` after `store.preferences` resolves.
Saved (debounced ~300ms) on any change via the existing `api("save_preferences", ...)` path.

## Architecture notes

- New pure module `js/inventory/inv-sort-group.js` exporting:
  - `applySortAndGroup(parts, opts)` — given the flat list of parts in a scope and the sort/vendor-group spec, returns the ordered array (with optional vendor-pile boundaries).
  - `nextScope(groupLevel, currentScope)` — pure cycle stepper.
- `inventory-panel.js` consults `inv-state.js` to decide how to render: it picks the right "scope unit" (subsection / section / global) and feeds each unit's parts through `applySortAndGroup`. The existing per-section/per-subsection render helpers stay; only the *contents* and which headers render change.
- Column header row built by a new render function `renderInvColHeader()` in `inventory-renderer.js`. Wiring (click handlers) lives in `inv-events.js`.

## Behavior matrix (worked example)

Inventory has Resistors → 0603, Resistors → 0805, Capacitors (no subsections), Connectors.

| Group | Sort col | Sort scope | Render outcome |
|-------|----------|------------|----------------|
| 0 | (none) | — | Parent headers + subsection headers + parts in default order (today's behavior) |
| 0 | Qty | subsection | Same headers; parts inside `0603`, `0805`, `Capacitors`, `Connectors` each sorted by qty desc |
| 0 | Qty | section | Parent headers visible; subsections hidden; all Resistors merged then sorted by qty desc |
| 0 | Qty | global | All headers hidden; flat list sorted by qty desc |
| 1 | (none) | — | Parent headers visible; subsections folded up; default order within each section |
| 2 | (none) | — | Flat list, no headers; section chip in column 1 of each row |
| 0 | (Part # vendor) | section | Within each section: LCSC pile, DK pile, Mouser pile, Pololu pile, Other pile (subheaders); each pile in default order |

## Test plan

- Vitest: pure-function tests on `applySortAndGroup` and `nextScope` covering the matrix above.
- Playwright E2E: `inv-col-header.spec.mjs`
  - Header row is sticky (still visible after scrolling 500px).
  - Click each sortable column → row order changes; click cycles through expected scopes.
  - Group toggle cycles 0 → 1 → 2 → 0 with correct header visibility.
  - Part # column groups by vendor and ignores Group=2 flat mode.
  - Reset clears all state.
  - State survives a page reload (preferences round-trip).
- No weakening of existing `sticky-buttons.spec.mjs` or `resize-visibility.spec.mjs`.

## Risks / things to watch

- **Width alignment**: header cells must match part-row cells exactly even as the description column flexes. Use the same width rules in CSS (and the same auto-hide threshold for description).
- **Sticky stacking**: get `--inv-col-header-h` right or section headers will overlap or leave a gap.
- **Performance**: scope-expanding sort across thousands of parts must stay snappy. Re-sort on click only; cache the default-ordered arrays.
