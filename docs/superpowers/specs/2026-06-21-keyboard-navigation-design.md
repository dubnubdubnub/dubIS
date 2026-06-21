# Full Keyboard Navigability — Design

Date: 2026-06-21
Status: Approved (pending spec review)

## Goal

Make the entire dubIS desktop app usable without a mouse: every interactive
element is reachable from the keyboard, row action buttons navigate as a 2D grid
via arrow keys, and every scrollable region scrolls from the keyboard. A clear
focus indicator appears during keyboard navigation only.

## Behavior summary (locked decisions)

- **Row button groups (2D roving grid):** Tab moves focus into a list's grid as
  a single tab stop. `←/→` move between focusable cells within a row; `↑/↓` move
  to the cell at the **same column index** in the adjacent row, clamped to that
  row's available cells. The focused cell is auto-scrolled into view.
- **Grid cells include row content:** a row's "cells" are all interactive/focusable
  elements in DOM order — MPN, distributor-PN badges, qty/price-warn button,
  group badge, near-miss badge, Adjust, Link, etc. Not only the action buttons.
- **Scroll regions:** each `overflow:auto/scroll` container becomes a tab stop;
  when focused, `↑/↓/PageUp/PageDown/Home/End` scroll it.
- **Focus ring:** `:focus-visible` only (keyboard), never on mouse click.
- **Scope:** the whole UI — all panels, modals, header, import, BOM, flyouts.
- **Delivery:** full pass, executed via subagent-driven dispatch.

## Architecture

### New utility layer — `js/a11y/`

Five small, independently testable modules.

1. **`roving-grid.js`** — `RovingGrid(container, { rowSelector, cellSelector, rowKey })`
   - Maintains a single tab stop into the grid: exactly one cell has
     `tabindex="0"`, all others `tabindex="-1"`. Non-focused state lives in the
     DOM, so re-renders are handled by re-deriving the grid.
   - Keydown (delegated on `container`): `←/→` move within the current row's cells;
     `↑/↓` move to the same column index in the previous/next row (clamped to that
     row's cell count); `Home/End` jump to first/last cell in the row. Calls
     `el.focus()` + `scrollIntoView({ block: "nearest" })` and updates the rover.
   - `focusin` on a grid cell promotes it to the rover (so mouse focus and
     programmatic focus stay consistent).
   - `refresh()` re-derives rows/cells after a re-render and re-establishes the
     rover, preferring the previously focused row by `rowKey` (e.g. `data-part-id`),
     falling back to the first cell. Idempotent and safe to call repeatedly.

2. **`scrollable.js`** — `makeScrollable(el)`
   - Idempotent: adds `tabindex="0"`, `role="region"`, a `data-kbd-scroll` marker,
     and a keydown handler. `↑/↓` scroll by a line, `PageUp/PageDown` by ~90% of
     the client height, `Home/End` to top/bottom. No-op if the element can't
     scroll. Does **not** intercept keys when the event target is itself a control
     inside the region that needs those keys (inputs, the roving grid) — it only
     acts when the scroll region itself is the focused element.

3. **`focus-trap.js`** — `trap(modalEl)` / `release()`
   - Confines Tab/Shift+Tab to focusable elements within the modal, records the
     previously focused element, sets initial focus (first `[autofocus]`, else the
     first focusable control, else the modal), and restores focus to the trigger
     on `release()`. Single active trap at a time.

4. **`activate-on-key.js`** — `activateOnKey(el)`
   - Adds `tabindex="0"`, `role="button"` (if not already a button), and an
     Enter/Space → `el.click()` handler so existing click logic is reused, not
     duplicated. Used for the clickable `<div>`/`<td>` elements below.

5. **`keyboard-nav.js`** — init module imported by `app-init.js`
   - Wires a `RovingGrid` to the inventory list container and the BOM `tbody`.
   - Applies `makeScrollable` to the known scroll regions (panel bodies, prefs
     sliders, vendors list/detail, BOM table wrap, flyout members, refs cells,
     console entries, label/import previews, scan-grouping list, OCR grid pane).
   - Re-applies grids/scrollables after re-render via existing EventBus events
     (`INVENTORY_LOADED`, `INVENTORY_UPDATED`, `BOM_LOADED`, `BOM_CLEARED`) by
     calling `grid.refresh()` and re-marking scroll regions (idempotent).

### Making non-button elements reachable

The surface map found clickable `<div>`/`<td>` elements Tab can't reach. Each is
wrapped with `activateOnKey` (or added to the roving grid as a cell where it lives
inside a row):

| Element | Location | Treatment |
|---|---|---|
| `.inv-section-header` / `.inv-parent-header` / `.inv-subsection-header` | inventory | `activateOnKey` (collapse/expand on Enter/Space) |
| `.inv-part-row` (linking-mode click target) | inventory | row participates in grid; Enter/Space activates the link when in linking mode |
| `.row-delete` (`<td>`) | BOM/import tables | becomes a grid cell / `activateOnKey` |
| `.label-po-row` | import PO picker | `activateOnKey` |
| flyout members | group flyout | `activateOnKey` (or local roving within the flyout) |

### Focus styling — `css/`

Add one global `:focus-visible` rule (new `css/a11y.css`, imported last so it wins):

```css
:focus-visible {
  outline: 2px solid var(--color-blue);
  outline-offset: 2px;
  border-radius: 2px;
}
```

Inputs keep their existing blue-border `:focus` treatment and additionally get the
ring under `:focus-visible`. Existing `outline: none` rules remain for mouse
`:focus` but are overridden under `:focus-visible`. Verify the ring is visible
against dark panels and inside modals.

### Modal integration

Wire `trap()` / `release()` into the existing `Modal()` factory in `ui-helpers.js`
so all seven modals get focus trapping, initial focus, and focus restoration for
free. The existing Escape-to-close and backdrop-click behavior is preserved.

## Testing

New Playwright specs (realistic key presses only — real `keyboard.press`, no
`dispatchEvent`/`force`):

- `keyboard-nav.spec.mjs` — Tab into the inventory grid; `←/→` move within a row;
  `↑/↓` preserve column index and clamp on shorter rows; rover survives a
  re-render; same for BOM grid.
- `scroll-keyboard.spec.mjs` — focus a panel body / prefs sliders; `↓` and
  `PageDown` change `scrollTop`; `Home/End` jump to extremes.
- `modal-focus-trap.spec.mjs` — opening a modal moves focus inside; Tab cycles
  within; Escape closes and focus returns to the trigger.
- `focus-ring.spec.mjs` — keyboard focus shows the ring; a mouse click does not.

Unit tests (vitest) for the pure logic in `roving-grid.js` (next-cell computation,
clamping) and `scrollable.js` (scroll-delta math) against jsdom.

**Guardrail:** `sticky-buttons.spec.mjs` and `resize-visibility.spec.mjs` must keep
passing untouched (per CLAUDE.md — they catch real clipping bugs; do not weaken).

## Non-goals

- No ARIA live-region / screen-reader announcement work beyond roles already added.
- No redesign of existing components; only additive focus/keyboard behavior.
- No change to mouse behavior or existing shortcuts (Ctrl+Z/Redo/Ctrl+F/Escape).

## Execution plan

Dispatched via subagent-driven development:

1. Utility layer (`js/a11y/*` + unit tests) — foundation, no UI wiring.
2. Focus styling (`css/a11y.css` + import) — independent.
3. Inventory roving grid wiring + `activateOnKey` for inventory divs + spec.
4. BOM roving grid wiring + `.row-delete` + spec.
5. Scroll regions wiring + spec.
6. Modal focus trap integration + spec.
7. Reconverge: full `eslint` / `tsc` / `vitest` / `playwright`, regenerate
   fixtures if backend untouched (none expected), verify guardrail specs green.
