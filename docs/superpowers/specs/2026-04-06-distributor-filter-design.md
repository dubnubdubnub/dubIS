# Distributor Filter Buttons — Design Spec

## Summary

Add distributor filter buttons to the inventory panel header, allowing users to filter parts by source distributor (LCSC, Digikey, Mouser, Pololu, Other). Buttons use a tab-style accent design with vendor brand colors.

## Layout

The inventory panel header, left to right:

```
Inventory | Rebuild Inventory | Clear Filters | LCSC (n) · Digikey (n) · Mouser (n) · Pololu (n) · Other (n) | [Search parts...]
```

- **"Inventory"** title stays at left edge
- **"Rebuild Inventory"** button stays in its current position (after title)
- **"Clear Filters"** button left of the distributor buttons
- **Distributor buttons** in a flex row with 4px gap
- **Search input** stays at right edge (pushed right with `margin-left: auto`)

## Button Style — Tab-style Accent (Option C)

**Inactive state:**
- `background: transparent`
- `border: 1px solid var(--border-default)` (grey `#30363d`)
- `border-bottom: 2px solid transparent`
- `border-radius: 6px 6px 2px 2px`
- `color: var(--text-secondary)` (grey `#8b949e`)
- `font-size: 11px; font-weight: 600`

**Active state** — vendor color on bottom border + text + subtle bg tint:
- LCSC: `border-bottom-color: var(--vendor-lcsc)`, `color: var(--vendor-lcsc)`, `background: #1166dd10`
- Digikey: `border-bottom-color: var(--vendor-digikey)`, `color: var(--vendor-digikey)`, `background: #ee282110`
- Mouser: `border-bottom-color: var(--vendor-mouser)`, `color: #6a9fd8` (lightened for contrast), `background: #004A9910`
- Pololu: `border-bottom-color: #5a6fd6` (lightened), `color: #5a6fd6`, `background: #1e2f9410`
- Other: `border-bottom-color: var(--color-gray-muted)`, `color: var(--text-secondary)`, `background: #636c7610`

**Hover (inactive):** text brightens to `var(--text-primary)`

### Contrast Notes

Mouser (`#004A99`) and Pololu (`#1e2f94`) are too dark against `--bg-surface` (`#161b22`). Use lightened variants for text/border:
- Mouser text: `#6a9fd8`
- Pololu text/border: `#5a6fd6`

The original brand colors remain in CSS variables for other uses (icons, etc).

## "Clear Filters" Button

**No filter active (greyed out):**
- `color: var(--text-muted)` (`#484f58`)
- `cursor: default`, non-interactive
- `border: 1px solid transparent`

**Filter active (red):**
- `color: var(--color-red)` (`#f85149`)
- `border: 1px solid #f8514930`
- `cursor: pointer`
- Hover: `background: #f8514915`, `border-color: var(--color-red)`

Style: `font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.3px`

## Behaviour

### Filtering
- **Single-select**: clicking a distributor button activates it and shows only parts from that distributor. Clicking a different button switches to that filter.
- **Toggle off**: clicking the active button deselects it (returns to showing all).
- **Clear Filters**: resets to showing all parts. Only clickable when a filter is active.
- **Combines with search**: distributor filter AND text search both apply. Changing either re-renders.

### Part Counts
Each button shows the count of parts matching that distributor in parentheses: `LCSC (142)`. Counts reflect the full inventory (not filtered by search). Counts update when inventory data changes.

### Distributor Inference
A part's distributor is determined by which distributor PN field is populated, checked in priority order:
1. `lcsc` field populated → LCSC
2. `digikey` field populated → Digikey
3. `mouser` field populated → Mouser
4. `pololu` field populated → Pololu
5. None of the above → Other

This matches the existing `_infer_distributor()` logic in `inventory_api.py`. The JS frontend should replicate this logic (or the backend can provide a `distributor` field).

### State Persistence
The active distributor filter resets on page load (no persistence needed). It clears when switching between normal and BOM comparison mode.

## Files to Modify

| File | Change |
|------|--------|
| `index.html` | Add "Clear Filters" button and distributor button container to inventory panel header |
| `css/panels/inventory.css` | New `.dist-filter-btn`, `.dist-filter-btn.active`, `.clear-filters-btn` styles |
| `js/inventory/inventory-panel.js` | Wire button click handlers, manage active filter state, integrate with render |
| `js/inventory/inventory-logic.js` | Add distributor filter to the existing search/filter pipeline, add distributor inference + counting |

## Testing

- **Unit tests** (vitest): distributor inference logic, filter function with various inventory shapes
- **Lint + typecheck**: eslint, tsc --noEmit
- Contrast: vendor text colors should meet 4.5:1 against `--bg-surface` (#161b22) — verify Mouser/Pololu lightened values
