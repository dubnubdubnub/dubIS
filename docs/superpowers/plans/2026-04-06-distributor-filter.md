# Distributor Filter Buttons — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add distributor filter buttons (LCSC, Digikey, Mouser, Pololu, Other) to the inventory panel header so users can filter parts by source.

**Architecture:** Pure filter logic in `inventory-logic.js`, button wiring in `inventory-panel.js`, HTML in `index.html`, CSS in `inventory.css`. Filter state is a simple string variable (`null` or distributor name) that feeds into the existing `filterByQuery` pipeline.

**Tech Stack:** Vanilla JS (ES modules), CSS custom properties, vitest for tests.

---

### Task 1: Add distributor inference and counting logic

**Files:**
- Modify: `js/inventory/inventory-logic.js` (add two new exported functions after `filterByQuery`)
- Test: `tests/js/inventory-logic.test.js` (add new describe blocks)

- [ ] **Step 1: Write failing tests for `inferDistributor`**

Add to `tests/js/inventory-logic.test.js`:

```javascript
import {
  groupBySection,
  filterByQuery,
  computeMatchedInvKeys,
  computeCollapsedState,
  sortBomRows,
  buildRowMap,
  BOM_STATUS_SORT_ORDER,
  inferDistributor,
  countByDistributor,
  filterByDistributor,
} from '../../js/inventory/inventory-logic.js';

// ... (existing tests stay unchanged) ...

// ── inferDistributor tests ──

describe('inferDistributor', () => {
  it('returns "lcsc" when lcsc field is populated', () => {
    expect(inferDistributor({ lcsc: 'C12345', digikey: 'DK1' })).toBe('lcsc');
  });

  it('returns "digikey" when only digikey is populated', () => {
    expect(inferDistributor({ lcsc: '', digikey: 'DK1', mouser: '', pololu: '' })).toBe('digikey');
  });

  it('returns "mouser" when only mouser is populated', () => {
    expect(inferDistributor({ mouser: 'MSR1' })).toBe('mouser');
  });

  it('returns "pololu" when only pololu is populated', () => {
    expect(inferDistributor({ pololu: 'POL1' })).toBe('pololu');
  });

  it('returns "other" when no distributor PN is populated', () => {
    expect(inferDistributor({ lcsc: '', digikey: '', mouser: '', pololu: '' })).toBe('other');
  });

  it('returns "other" when all fields are undefined', () => {
    expect(inferDistributor({})).toBe('other');
  });

  it('respects priority order: lcsc > digikey > mouser > pololu', () => {
    expect(inferDistributor({ lcsc: 'C1', digikey: 'DK1', mouser: 'M1', pololu: 'P1' })).toBe('lcsc');
    expect(inferDistributor({ lcsc: '', digikey: 'DK1', mouser: 'M1', pololu: 'P1' })).toBe('digikey');
    expect(inferDistributor({ lcsc: '', digikey: '', mouser: 'M1', pololu: 'P1' })).toBe('mouser');
  });
});

// ── countByDistributor tests ──

describe('countByDistributor', () => {
  var inventory = [
    { lcsc: 'C1' },
    { lcsc: 'C2' },
    { digikey: 'DK1' },
    { mouser: 'M1' },
    { pololu: 'P1' },
    { pololu: 'P2' },
    {},
  ];

  it('counts parts per distributor', () => {
    var counts = countByDistributor(inventory);
    expect(counts.lcsc).toBe(2);
    expect(counts.digikey).toBe(1);
    expect(counts.mouser).toBe(1);
    expect(counts.pololu).toBe(2);
    expect(counts.other).toBe(1);
  });

  it('returns all zeros for empty inventory', () => {
    var counts = countByDistributor([]);
    expect(counts.lcsc).toBe(0);
    expect(counts.digikey).toBe(0);
    expect(counts.mouser).toBe(0);
    expect(counts.pololu).toBe(0);
    expect(counts.other).toBe(0);
  });
});

// ── filterByDistributor tests ──

describe('filterByDistributor', () => {
  var parts = [
    { lcsc: 'C1', mpn: 'R1' },
    { digikey: 'DK1', mpn: 'R2' },
    { mouser: 'M1', mpn: 'R3' },
    { pololu: 'P1', mpn: 'R4' },
    { mpn: 'R5' },
  ];

  it('returns all parts when filter is null', () => {
    expect(filterByDistributor(parts, null)).toHaveLength(5);
  });

  it('filters to lcsc parts only', () => {
    var result = filterByDistributor(parts, 'lcsc');
    expect(result).toHaveLength(1);
    expect(result[0].mpn).toBe('R1');
  });

  it('filters to digikey parts only', () => {
    var result = filterByDistributor(parts, 'digikey');
    expect(result).toHaveLength(1);
    expect(result[0].mpn).toBe('R2');
  });

  it('filters to other parts only', () => {
    var result = filterByDistributor(parts, 'other');
    expect(result).toHaveLength(1);
    expect(result[0].mpn).toBe('R5');
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `export PATH="/c/Program Files/nodejs:$PATH" && npx vitest run tests/js/inventory-logic.test.js`
Expected: FAIL — `inferDistributor`, `countByDistributor`, `filterByDistributor` are not exported.

- [ ] **Step 3: Implement the three functions**

Add to `js/inventory/inventory-logic.js` after the `filterByQuery` function:

```javascript
// ── Distributor inference ──

/**
 * Infer which distributor a part comes from based on populated PN fields.
 * Priority: lcsc > digikey > mouser > pololu > other.
 * @param {Object} item - inventory item
 * @returns {"lcsc"|"digikey"|"mouser"|"pololu"|"other"}
 */
export function inferDistributor(item) {
  if (item.lcsc) return "lcsc";
  if (item.digikey) return "digikey";
  if (item.mouser) return "mouser";
  if (item.pololu) return "pololu";
  return "other";
}

/**
 * Count inventory items per distributor.
 * @param {Array<Object>} inventory
 * @returns {{lcsc: number, digikey: number, mouser: number, pololu: number, other: number}}
 */
export function countByDistributor(inventory) {
  var counts = { lcsc: 0, digikey: 0, mouser: 0, pololu: 0, other: 0 };
  for (var i = 0; i < inventory.length; i++) {
    counts[inferDistributor(inventory[i])]++;
  }
  return counts;
}

/**
 * Filter parts by distributor. Returns all parts when filter is null.
 * @param {Array<Object>} parts
 * @param {string|null} distributor - "lcsc", "digikey", "mouser", "pololu", "other", or null
 * @returns {Array<Object>}
 */
export function filterByDistributor(parts, distributor) {
  if (!distributor) return parts;
  return parts.filter(function (item) {
    return inferDistributor(item) === distributor;
  });
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `export PATH="/c/Program Files/nodejs:$PATH" && npx vitest run tests/js/inventory-logic.test.js`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add js/inventory/inventory-logic.js tests/js/inventory-logic.test.js
git commit -m "feat: add distributor inference, counting, and filter logic"
```

---

### Task 2: Add HTML structure and CSS styles for filter buttons

**Files:**
- Modify: `index.html:53-58` (inventory panel header)
- Modify: `css/panels/inventory.css` (add new styles at bottom)

- [ ] **Step 1: Add distributor filter buttons to `index.html`**

Replace the inventory panel header (lines 54-58) with:

```html
    <div class="panel-header">
      Inventory
      <button class="rebuild-btn" id="rebuild-inv" title="Rebuild Inventory">Rebuild Inventory</button>
      <button class="clear-filters-btn" id="clear-dist-filter" disabled>Clear Filters</button>
      <div class="dist-filter-bar" id="dist-filter-bar">
        <button class="dist-filter-btn" data-distributor="lcsc">LCSC</button>
        <button class="dist-filter-btn" data-distributor="digikey">Digikey</button>
        <button class="dist-filter-btn" data-distributor="mouser">Mouser</button>
        <button class="dist-filter-btn" data-distributor="pololu">Pololu</button>
        <button class="dist-filter-btn" data-distributor="other">Other</button>
      </div>
      <input type="text" class="search-input" id="inv-search" placeholder="Search parts...">
    </div>
```

- [ ] **Step 2: Add CSS styles to `css/panels/inventory.css`**

Append to end of file:

```css
/* ── Distributor Filter Buttons ─────────────────────── */
.dist-filter-bar {
  display: flex;
  gap: 4px;
  align-items: center;
}
.dist-filter-btn {
  padding: 3px 10px;
  border-radius: 6px 6px 2px 2px;
  border: 1px solid var(--border-default);
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--text-secondary);
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
}
.dist-filter-btn:hover { color: var(--text-primary); }
.dist-filter-btn.active[data-distributor="lcsc"]    { border-bottom-color: var(--vendor-lcsc);    color: var(--vendor-lcsc);    background: #1166dd10; }
.dist-filter-btn.active[data-distributor="digikey"] { border-bottom-color: var(--vendor-digikey); color: var(--vendor-digikey); background: #ee282110; }
.dist-filter-btn.active[data-distributor="mouser"]  { border-bottom-color: #6a9fd8;              color: #6a9fd8;              background: #004A9910; }
.dist-filter-btn.active[data-distributor="pololu"]  { border-bottom-color: #5a6fd6;              color: #5a6fd6;              background: #1e2f9410; }
.dist-filter-btn.active[data-distributor="other"]   { border-bottom-color: var(--color-gray-muted); color: var(--text-secondary); background: #636c7610; }

/* ── Clear Filters Button ───────────────────────────── */
.clear-filters-btn {
  padding: 3px 8px;
  border-radius: 6px;
  border: 1px solid transparent;
  background: transparent;
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.3px;
  white-space: nowrap;
  transition: all 0.15s;
}
.clear-filters-btn:disabled {
  color: var(--text-muted);
  cursor: default;
}
.clear-filters-btn:not(:disabled) {
  color: var(--color-red);
  border-color: #f8514930;
  cursor: pointer;
}
.clear-filters-btn:not(:disabled):hover {
  background: #f8514915;
  border-color: var(--color-red);
}
```

- [ ] **Step 3: Run lint and typecheck**

Run: `export PATH="/c/Program Files/nodejs:$PATH" && npx eslint js/ && npx tsc --noEmit`
Expected: No errors.

- [ ] **Step 4: Commit**

```bash
git add index.html css/panels/inventory.css
git commit -m "feat: add distributor filter button HTML and CSS"
```

---

### Task 3: Wire up filter buttons in the inventory panel

**Files:**
- Modify: `js/inventory/inventory-panel.js` (add state, button handlers, integrate filter into render)

- [ ] **Step 1: Add imports and state variable**

In `js/inventory/inventory-panel.js`, update the import from `inventory-logic.js` (line 14-21) to include the new functions:

```javascript
import {
  groupBySection,
  filterByQuery,
  filterByDistributor,
  countByDistributor,
  computeMatchedInvKeys,
  sortBomRows,
  buildRowMap,
  bomRowDisplayData,
} from './inventory-logic.js';
```

Add a new state variable after `var activeFilter = "all";` (line 41):

```javascript
var activeDistributor = null;  // null = show all, or "lcsc"|"digikey"|"mouser"|"pololu"|"other"
```

Add DOM references after the existing ones (after line 35):

```javascript
var clearFilterBtn = document.getElementById("clear-dist-filter");
var distFilterBar = document.getElementById("dist-filter-bar");
```

- [ ] **Step 2: Add button wiring in `init()`**

Inside the `init()` function, after the search input listener (after line 67), add:

```javascript
  // ── Distributor filter buttons ──
  distFilterBar.addEventListener("click", function (e) {
    var btn = e.target.closest(".dist-filter-btn");
    if (!btn) return;
    var dist = btn.dataset.distributor;
    activeDistributor = (activeDistributor === dist) ? null : dist;
    updateDistFilterUI();
    render();
  });

  clearFilterBtn.addEventListener("click", function () {
    if (activeDistributor === null) return;
    activeDistributor = null;
    updateDistFilterUI();
    render();
  });
```

- [ ] **Step 3: Add the `updateDistFilterUI` helper and `updateDistCounts` helper**

Add after the `init()` function (after line 96):

```javascript
// ── Distributor filter UI state ──

function updateDistFilterUI() {
  var btns = distFilterBar.querySelectorAll(".dist-filter-btn");
  for (var i = 0; i < btns.length; i++) {
    btns[i].classList.toggle("active", btns[i].dataset.distributor === activeDistributor);
  }
  clearFilterBtn.disabled = (activeDistributor === null);
}

function updateDistCounts() {
  var counts = countByDistributor(App.inventory);
  var btns = distFilterBar.querySelectorAll(".dist-filter-btn");
  for (var i = 0; i < btns.length; i++) {
    var dist = btns[i].dataset.distributor;
    btns[i].textContent = dist.charAt(0).toUpperCase() + dist.slice(1) + " (" + counts[dist] + ")";
  }
}
```

- [ ] **Step 4: Integrate distributor filter into `renderNormalInventory`**

In `renderNormalInventory()` (line 133), add `updateDistCounts()` call and use `filterByDistributor` inside the filter pipeline. Replace the function:

```javascript
function renderNormalInventory() {
  updateDistCounts();
  var query = (searchInput.value || "").toLowerCase();
  var sections = groupBySection(App.inventory);

  for (var i = 0; i < SECTION_HIERARCHY.length; i++) {
    var entry = SECTION_HIERARCHY[i];
    if (!entry.children) {
      var filtered = filterByDistributor(filterByQuery(sections[entry.name] || [], query), activeDistributor);
      if (filtered.length > 0) renderSection(entry.name, filtered);
    } else {
      renderHierarchySection(entry, sections, query);
    }
  }
}
```

- [ ] **Step 5: Integrate distributor filter into `renderHierarchySection`**

Update `renderHierarchySection` to apply `filterByDistributor` after `filterByQuery`:

```javascript
function renderHierarchySection(entry, sections, query) {
  var parentParts = filterByDistributor(filterByQuery(sections[entry.name] || [], query), activeDistributor);
  var childData = [];
  var totalCount = parentParts.length;
  for (var i = 0; i < entry.children.length; i++) {
    var fullKey = entry.name + " > " + entry.children[i];
    var filtered = filterByDistributor(filterByQuery(sections[fullKey] || [], query), activeDistributor);
    totalCount += filtered.length;
    childData.push({ name: entry.children[i], fullKey: fullKey, parts: filtered });
  }
  if (totalCount === 0) return;

  var container = document.createElement("div");
  container.className = "inv-section";

  var isParentCollapsed = collapsedSections.has(entry.name);
  var header = document.createElement("div");
  header.className = "inv-parent-header" + (isParentCollapsed ? " collapsed" : "");
  header.innerHTML = '<span class="chevron">\u25BE</span> ' + escHtml(entry.name) + ' <span class="inv-section-count">(' + totalCount + ')</span>';
  header.addEventListener("click", function () {
    if (collapsedSections.has(entry.name)) collapsedSections.delete(entry.name);
    else collapsedSections.add(entry.name);
    render();
  });
  container.appendChild(header);

  if (!isParentCollapsed) {
    if (parentParts.length > 0) {
      renderSubSection(container, "Ungrouped", entry.name, parentParts);
    }
    for (var j = 0; j < childData.length; j++) {
      if (childData[j].parts.length > 0) {
        renderSubSection(container, childData[j].name, childData[j].fullKey, childData[j].parts);
      }
    }
  }

  body.appendChild(container);
}
```

- [ ] **Step 6: Integrate distributor filter into remaining inventory sections**

Update `renderRemainingNormalSections` to apply the distributor filter. In the loop body at line 300, update the `filterByQuery` calls:

```javascript
function renderRemainingNormalSections(otherParts, query) {
  var hasAny = FLAT_SECTIONS.some(function (s) { return !!otherParts[s]; });
  if (hasAny) {
    var divider = document.createElement("div");
    divider.className = "inv-section-header inv-other-divider";
    divider.textContent = "Other Inventory";
    body.appendChild(divider);

    for (var i = 0; i < SECTION_HIERARCHY.length; i++) {
      var entry = SECTION_HIERARCHY[i];
      if (!entry.children) {
        var filtered = filterByDistributor(filterByQuery(otherParts[entry.name] || [], query), activeDistributor);
        if (filtered.length > 0) renderSection(entry.name, filtered);
      } else {
        renderHierarchySection(entry, otherParts, query);
      }
    }
  }
}
```

- [ ] **Step 7: Reset distributor filter on BOM clear**

In the `BOM_CLEARED` event handler (line 79), add `activeDistributor = null;` and `updateDistFilterUI();`:

```javascript
  EventBus.on(Events.BOM_CLEARED, function () {
    bomData = null;
    activeFilter = "all";
    activeDistributor = null;
    updateDistFilterUI();
    expandedAlts = new Set();
    expandedMembers = new Set();
    App.links.clearAll();
    render();
  });
```

- [ ] **Step 8: Run lint, typecheck, and all tests**

Run: `export PATH="/c/Program Files/nodejs:$PATH" && npx eslint js/ && npx tsc --noEmit && npx vitest run`
Expected: All pass.

- [ ] **Step 9: Commit**

```bash
git add js/inventory/inventory-panel.js
git commit -m "feat: wire distributor filter buttons into inventory panel"
```

---

### Task 4: Add contrast test entries for new filter button colors

**Files:**
- Modify: `tests/js/contrast.test.js` (add new color pairs)

- [ ] **Step 1: Add contrast pairs for lightened vendor colors on surface**

In `tests/js/contrast.test.js`, add new entries to the `ALL_PAIRS` array, after the existing vendor color entries (after the `'Mouser blue on base'` entry around line 107):

```javascript
  // Distributor filter button text colors (lightened for contrast on surface)
  { fg: '#6a9fd8', bg: '--bg-surface', min: 3.0, label: 'Mouser filter text on surface' },
  { fg: '#5a6fd6', bg: '--bg-surface', min: 3.0, label: 'Pololu filter text on surface' },
  { fg: '--vendor-lcsc',    bg: '--bg-surface', min: 3.0, label: 'LCSC filter text on surface' },
  { fg: '--vendor-digikey', bg: '--bg-surface', min: 3.0, label: 'Digikey filter text on surface' },
```

- [ ] **Step 2: Run contrast tests**

Run: `export PATH="/c/Program Files/nodejs:$PATH" && npx vitest run tests/js/contrast.test.js`
Expected: All pass. If the lightened Mouser/Pololu colors don't meet 3:1, adjust the hex values in `css/panels/inventory.css` and re-run.

- [ ] **Step 3: Commit**

```bash
git add tests/js/contrast.test.js
git commit -m "test: add contrast audit entries for distributor filter buttons"
```

---

### Task 5: Final lint, full test run, and push

**Files:** None — verification only.

- [ ] **Step 1: Run full lint and type check**

Run: `export PATH="/c/Program Files/nodejs:$PATH" && npx eslint js/ && npx tsc --noEmit`
Expected: No errors.

- [ ] **Step 2: Run full test suite**

Run: `export PATH="/c/Program Files/nodejs:$PATH" && npx vitest run`
Expected: All tests pass.

- [ ] **Step 3: Push and create PR**

```bash
bash scripts/push-pr.sh --title "feat: distributor filter buttons in inventory panel"
```

- [ ] **Step 4: Watch CI**

Run: `gh pr checks <PR_NUMBER> --watch`
Expected: All checks pass. If any fail, diagnose and fix.
