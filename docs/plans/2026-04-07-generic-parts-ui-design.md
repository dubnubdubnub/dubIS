# Generic Parts UI — Design Spec

**Date:** 2026-04-07
**Goal:** Unify categories and generic parts so that section headers act as group containers, passive generic parts auto-generate from value+package, manual groups handle non-passives, and the entire system integrates with BOM matching and linking.

---

## Core Concepts

### Categories own the tree, generic groups are an overlay

The inventory panel's section hierarchy (from `categorize.py`) remains the primary browse structure. Generic part groups appear **on demand** via a "◇ Groups" toggle button on each section/subsection header. When groups mode is off, the inventory looks exactly like today.

### Two kinds of generic groups

1. **Auto-generated (passives):** For each leaf-level passive category (e.g., "MLCC", "Chip Resistors"), the system auto-generates one generic part per unique value+package combination. "100nF 0402", "10µF 0805", "4.7kΩ 0402", etc. These are created at cache rebuild time from `spec_extractor` output — no user action required.

2. **Manual (everything else):** For non-passives (LEDs, connectors, ICs), users create groups by hand and link parts into them. Useful when descriptions/MPNs don't encode interchangeability (e.g., multiple green 0805 LEDs from different vendors).

### Generic group as a first-class row

When groups mode is active on a section, each generic group renders as a collapsible row with:
- Purple left edge and `◇` icon
- Name (e.g., "100nF 0402")
- Part count and aggregate quantity
- Expand/collapse chevron

Collapsed groups are scannable like regular part rows. Expanding a group reveals its members and a **filter row**.

---

## Filter Row — Faceted Spec Chips

Below each expanded group header is a filter row with two zones:

### Chip zone (right side)
Displays one chip per distinct value of each varying spec dimension across the group's members. For a "100nF 0402" group:

```
[Diel.] C0G ³  X5R ²  X7R ¹   [Tol.] 5% ²  10% ⁴
```

- Chips are independent per-dimension toggles (AND across dimensions)
- Clicking a chip filters the member list and updates counts on other chips to reflect the narrowed subset
- Unselected chips for values excluded by other active filters are dimmed
- Chips are derived at render time from member specs — no extra DB rows

### Name zone (left side)
- **Empty when no chips are active** — the parent group header is the identity
- **Builds dynamically as chips are clicked:** parent name + active chip values
  - Click C0G → `100nF 0402 C0G`
  - Click C0G + 10% → `100nF 0402 C0G 10%`
- The name zone is a **matchable entity** — BOM resolution can target it
- Separated from chip zone by a vertical border

### Filter dimensions by part type

| Part type | Dimensions |
|-----------|-----------|
| Capacitor | dielectric, tolerance, voltage |
| Resistor | tolerance, power |
| Inductor | tolerance, current |
| LED (manual) | derived from member spec variance (color, wavelength, etc.) |
| Other (manual) | derived from member spec variance |

---

## Groups Mode Toggle

Each section/subsection header gets a `◇ Groups` button:
- **Off (default):** Flat part list sorted by sort_key, same as today
- **On:** Parts re-sort under their generic group headers. Ungrouped parts appear dimmed below the groups. A `+ Create group` row allows manual group creation.

The toggle state is per-section and persists in the UI session (not saved to preferences — lightweight).

---

## BOM Matching Integration

### Generic match display

When a BOM row resolves via a generic part:
- Match label shows "Generic" (purple badge)
- Part cell shows `◇ group name` with "via C1525 (preferred)" subtitle
- "Have" column shows **aggregate group quantity** in purple
- If BOM specifies extra detail (e.g., "100nF C0G"), the match shows the base group with a `+C0G` annotation and "Have" reflects only the filtered subset

### Linking to generic groups

When in reverse-link mode (BOM→inventory) with groups visible:

1. **Group header** is a link target — clicking it links the BOM row to the entire group
2. **Individual parts** are link targets — same as today, links to a specific part
3. **Filter row name zone** becomes a link target when chips are active — clicking it links to the narrowed sub-group

The filter row is split so chips remain interactive for filtering even during link mode:
- **Name zone (left):** becomes `link-target` (dashed purple outline, `← click to link` hint)
- **Chip zone (right):** always filters, never creates links

### Linking result

After linking a BOM row to a generic group:
- BOM resolves to best available member (preferred ★ first, then highest stock)
- "Have" shows aggregate quantity of the linked group (or filtered sub-group)
- User can still expand the match to see all members

---

## Auto-Generation of Passive Generic Parts

At cache rebuild time (in `cache_db.py` or `generic_parts.py`):

1. For each part in a passive section ("Passives - Resistors > Chip Resistors", "Passives - Capacitors > MLCC", etc.):
   - Extract spec via `spec_extractor.extract_spec(description, package)`
   - Build a key: `(part_type, value, package)` — e.g., `("capacitor", 1e-7, "0402")`
2. Group parts by this key
3. For each group with ≥1 member, ensure a `generic_parts` row exists:
   - `generic_part_id`: stable ID from type+value+package (e.g., `cap_100nf_0402`)
   - `name`: human-readable (e.g., "100nF 0402")
   - `spec_json`: `{"type":"capacitor","value":1e-7,"package":"0402"}`
   - `strictness_json`: `{"required":["value","package"]}`
   - `source`: `"auto"` — distinguishes from manual groups
4. Auto-match members via existing `_auto_match()` logic
5. Do not delete or modify manually-created generic parts

### Source field

Add a `source` column to `generic_parts` table: `"auto"` or `"manual"`. Auto-generated groups can be regenerated on rebuild; manual groups are preserved. The UI shows "auto"/"manual" label on group headers.

---

## Manual Group Creation

Entry points:
1. **`+ Create group` row** at the bottom of a section in groups mode — opens the existing `generic-parts-modal.js` in create mode
2. **"Group" button** on missing/possible BOM rows — same as today, pre-fills from BOM spec
3. **From inventory part context** — right-click or button on an ungrouped part to start a new group with that part as the first member

The create modal works the same as today: name, type, spec fields, strictness toggles, live member preview.

---

## Data Model Changes

### `generic_parts` table — add `source` column

```sql
ALTER TABLE generic_parts ADD COLUMN source TEXT NOT NULL DEFAULT 'manual';
```

Values: `"auto"` (generated from passive categorization) or `"manual"` (user-created).

Note: `generic_part_members` already has a `source` column (tracking whether each member was auto-matched or manually added). This new column is on the parent `generic_parts` table to track whether the group itself was auto-generated or user-created.

### No other schema changes needed

The existing `generic_parts`, `generic_part_members`, and `spec_json`/`strictness_json` fields support everything in this design. Filter chips are derived at render time from member specs — no storage needed.

---

## Files to Modify

| File | Changes |
|------|---------|
| `cache_db.py` | Schema v3→v4 migration: add `source` column to `generic_parts` |
| `generic_parts.py` | Auto-generation logic for passives at rebuild time; `source` parameter on create |
| `inventory_api.py` | Pass `source` through create API; expose auto-generation |
| `js/inventory/inventory-panel.js` | Groups mode toggle, render generic group rows, filter row |
| `js/inventory/inventory-renderer.js` | Generic group header row builder, filter row builder |
| `js/generic-parts-modal.js` | Minor: pass `source` on create |
| `js/store.js` | Groups mode toggle state per section |
| `js/matching.js` | Handle filtered sub-group matching |
| `js/bom-row-data.js` | Generic match display with aggregate qty, filter annotation |
| `css/panels/inventory.css` | Group header, filter row, chip styles |
| `css/components/linking.css` | Filter row name zone as link-target |
| `index.html` | No changes expected (groups rendered dynamically) |

---

## Out of Scope

- Popularity scoring / ranking within groups (future)
- Bulk auto-create confirmation dialog (auto-generation is silent)
- Generic part deletion UI
- Drag-and-drop reordering of members
- Persisting groups mode toggle across sessions
