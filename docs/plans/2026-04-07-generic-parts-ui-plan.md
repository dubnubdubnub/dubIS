# Generic Parts UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify categories and generic parts — auto-generate passive groups, add groups mode toggle to section headers, render group rows with faceted filter chips, and integrate with BOM matching and linking.

**Architecture:** Backend adds a `source` column to `generic_parts` and auto-generates groups for passives at rebuild time. Frontend adds a per-section "Groups" toggle that renders generic part headers with collapsible filter chips derived from member specs. Linking extends to target group headers and filtered sub-groups.

**Tech Stack:** Python/SQLite (backend), vanilla JS ES modules (frontend), CSS custom properties

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `cache_db.py` | Modify | Schema v3→v4: add `source` column to `generic_parts` |
| `generic_parts.py` | Modify | `auto_generate_passive_groups()`, `source` param on create, member spec extraction for filter chips |
| `inventory_api.py` | Modify | Call auto-generation at rebuild, return `source` + member specs in `list_generic_parts` |
| `js/inventory/inv-state.js` | Modify | Add `groupsSections` Set for groups mode toggle |
| `js/inventory/inventory-panel.js` | Modify | Groups button on headers, render group rows + filter rows, link-target wiring |
| `js/inventory/inventory-renderer.js` | Modify | Add `renderGroupHeader()`, `renderFilterRow()`, `renderGroupedParts()` |
| `js/inventory/inventory-logic.js` | Modify | Add `groupPartsByGeneric()`, `computeFilterDimensions()`, `applyChipFilters()` |
| `css/panels/inventory.css` | Modify | Group header, filter row, chip, dynamic name styles |
| `css/components/linking.css` | Modify | Filter name zone as link-target |
| `tests/python/test_generic_parts.py` | Modify | Tests for auto-generation, source column |
| `tests/js/test-inventory-logic.js` | Modify | Tests for grouping + filter logic |

---

### Task 1: Schema migration — add `source` to `generic_parts`

**Files:**
- Modify: `cache_db.py:19` (SCHEMA_VERSION), `cache_db.py:72-78` (CREATE TABLE generic_parts)
- Test: `tests/python/test_cache_db.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/python/test_cache_db.py`:

```python
class TestSchemaV4:
    def test_generic_parts_has_source_column(self, db):
        """generic_parts table should have a source column."""
        row = db.execute("PRAGMA table_info(generic_parts)").fetchall()
        col_names = [r["name"] for r in row]
        assert "source" in col_names

    def test_source_defaults_to_manual(self, db):
        """source should default to 'manual' for user-created groups."""
        db.execute(
            "INSERT INTO parts (part_id) VALUES ('test1')"
        )
        db.execute(
            "INSERT INTO generic_parts (generic_part_id, name, part_type) VALUES ('gp1', 'Test', 'other')"
        )
        db.commit()
        row = db.execute("SELECT source FROM generic_parts WHERE generic_part_id='gp1'").fetchone()
        assert row["source"] == "manual"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/python/test_cache_db.py::TestSchemaV4 -v`
Expected: FAIL — `source` column does not exist.

- [ ] **Step 3: Update schema**

In `cache_db.py`, change `SCHEMA_VERSION` from `"3"` to `"4"`, and add `source` column to the `generic_parts` CREATE TABLE:

```python
SCHEMA_VERSION = "4"
```

```sql
CREATE TABLE IF NOT EXISTS generic_parts (
    generic_part_id  TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    part_type        TEXT NOT NULL,
    spec_json        TEXT NOT NULL DEFAULT '{}',
    strictness_json  TEXT NOT NULL DEFAULT '{}',
    source           TEXT NOT NULL DEFAULT 'manual'
);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/python/test_cache_db.py::TestSchemaV4 -v`
Expected: PASS

- [ ] **Step 5: Run full cache_db test suite**

Run: `pytest tests/python/test_cache_db.py -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add cache_db.py tests/python/test_cache_db.py
git commit -m "feat: add source column to generic_parts (schema v3→v4)"
```

---

### Task 2: Auto-generate passive generic parts at rebuild time

**Files:**
- Modify: `generic_parts.py`
- Test: `tests/python/test_generic_parts.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/python/test_generic_parts.py`:

```python
class TestAutoGeneratePassiveGroups:
    def test_generates_groups_for_capacitors(self, db, events_dir):
        _seed_parts(db)
        groups = generic_parts.auto_generate_passive_groups(db, events_dir)
        # Should create groups for 100nF 0402 (C1525 + C9999) and 10µF 0805 (C19702)
        assert len(groups) >= 2
        names = {g["name"] for g in groups}
        assert any("100nF" in n and "0402" in n for n in names)
        assert any("10µF" in n and "0805" in n for n in names)

    def test_auto_groups_have_source_auto(self, db, events_dir):
        _seed_parts(db)
        generic_parts.auto_generate_passive_groups(db, events_dir)
        rows = db.execute("SELECT source FROM generic_parts").fetchall()
        assert all(r["source"] == "auto" for r in rows)

    def test_auto_groups_have_correct_members(self, db, events_dir):
        _seed_parts(db)
        generic_parts.auto_generate_passive_groups(db, events_dir)
        # Find the 100nF 0402 group
        gp = db.execute(
            "SELECT generic_part_id FROM generic_parts WHERE name LIKE '%100nF%0402%'"
        ).fetchone()
        assert gp is not None
        members = db.execute(
            "SELECT part_id FROM generic_part_members WHERE generic_part_id=?",
            (gp["generic_part_id"],),
        ).fetchall()
        member_ids = {m["part_id"] for m in members}
        assert "C1525" in member_ids
        assert "C9999" in member_ids
        assert "C19702" not in member_ids

    def test_does_not_clobber_manual_groups(self, db, events_dir):
        _seed_parts(db)
        # Create a manual group first
        db.execute(
            "INSERT INTO generic_parts (generic_part_id, name, part_type, source) VALUES ('manual_1', 'My Group', 'other', 'manual')"
        )
        db.commit()
        generic_parts.auto_generate_passive_groups(db, events_dir)
        row = db.execute("SELECT source FROM generic_parts WHERE generic_part_id='manual_1'").fetchone()
        assert row["source"] == "manual"

    def test_idempotent(self, db, events_dir):
        _seed_parts(db)
        generic_parts.auto_generate_passive_groups(db, events_dir)
        count1 = db.execute("SELECT COUNT(*) as c FROM generic_parts WHERE source='auto'").fetchone()["c"]
        generic_parts.auto_generate_passive_groups(db, events_dir)
        count2 = db.execute("SELECT COUNT(*) as c FROM generic_parts WHERE source='auto'").fetchone()["c"]
        assert count1 == count2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/python/test_generic_parts.py::TestAutoGeneratePassiveGroups -v`
Expected: FAIL — `auto_generate_passive_groups` does not exist.

- [ ] **Step 3: Implement `auto_generate_passive_groups`**

Add to `generic_parts.py`:

```python
PASSIVE_SECTIONS = {"Passives - Capacitors", "Passives - Resistors", "Passives - Inductors"}


def auto_generate_passive_groups(
    conn: Any,
    events_dir: str,
) -> list[dict[str, Any]]:
    """Auto-generate generic parts for passives, grouped by value+package.

    Scans all parts in passive sections, extracts specs, and creates one
    generic part per unique (type, value, package) combination.
    Idempotent: uses INSERT OR REPLACE keyed on the generated ID.
    Does not modify manually-created groups (source='manual').
    """
    # Delete old auto-generated groups (they'll be recreated)
    conn.execute("DELETE FROM generic_part_members WHERE generic_part_id IN "
                 "(SELECT generic_part_id FROM generic_parts WHERE source='auto')")
    conn.execute("DELETE FROM generic_parts WHERE source='auto'")

    # Scan passive parts and group by (type, value, package)
    parts = conn.execute(
        "SELECT part_id, description, package, section FROM parts"
    ).fetchall()

    groups: dict[str, dict] = {}  # generic_part_id -> {name, type, spec, members}
    for part in parts:
        section = part["section"] or ""
        # Check if this is a passive section (match prefix)
        if not any(section.startswith(ps) for ps in PASSIVE_SECTIONS):
            continue
        part_spec = spec_extractor.extract_spec(part["description"], part["package"])
        if part_spec["type"] == "other" or "value" not in part_spec:
            continue
        gp_id = spec_extractor.generate_generic_id(part_spec["type"], {
            "value": part_spec.get("value_display", ""),
            "package": part_spec.get("package", ""),
        })
        if gp_id not in groups:
            name_parts = []
            if part_spec.get("value_display"):
                name_parts.append(part_spec["value_display"])
            if part_spec.get("package"):
                name_parts.append(part_spec["package"])
            groups[gp_id] = {
                "name": " ".join(name_parts) or gp_id,
                "part_type": part_spec["type"],
                "spec": {
                    "type": part_spec["type"],
                    "value": part_spec.get("value"),
                    "package": part_spec.get("package", ""),
                },
                "strictness": {"required": ["value", "package"]},
                "members": [],
            }
        groups[gp_id]["members"].append(part["part_id"])

    # Insert groups and members
    created = []
    for gp_id, g in groups.items():
        conn.execute(
            """INSERT OR REPLACE INTO generic_parts
               (generic_part_id, name, part_type, spec_json, strictness_json, source)
               VALUES (?,?,?,?,?,?)""",
            (gp_id, g["name"], g["part_type"],
             json.dumps(g["spec"]), json.dumps(g["strictness"]), "auto"),
        )
        for part_id in g["members"]:
            conn.execute(
                """INSERT OR IGNORE INTO generic_part_members
                   (generic_part_id, part_id, source, preferred)
                   VALUES (?, ?, 'auto', 0)""",
                (gp_id, part_id),
            )
        created.append({
            "generic_part_id": gp_id,
            "name": g["name"],
            "part_type": g["part_type"],
            "spec": g["spec"],
            "strictness": g["strictness"],
        })

    conn.commit()
    return created
```

- [ ] **Step 4: Update `create_generic_part` to accept `source` param**

In `generic_parts.py`, update `create_generic_part` (line 36-65) — add `source` parameter:

```python
def create_generic_part(
    conn: Any,
    events_dir: str,
    name: str,
    part_type: str,
    spec: dict,
    strictness: dict,
    source: str = "manual",
) -> dict[str, Any]:
    """Create a generic part and auto-match existing real parts."""
    generic_part_id = spec_extractor.generate_generic_id(part_type, spec)

    conn.execute(
        """INSERT OR REPLACE INTO generic_parts
           (generic_part_id, name, part_type, spec_json, strictness_json, source)
           VALUES (?,?,?,?,?,?)""",
        (generic_part_id, name, part_type, json.dumps(spec), json.dumps(strictness), source),
    )
    # ... rest unchanged
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/python/test_generic_parts.py -v`
Expected: All pass (old + new)

- [ ] **Step 6: Commit**

```bash
git add generic_parts.py tests/python/test_generic_parts.py
git commit -m "feat: auto-generate passive generic parts at rebuild time"
```

---

### Task 3: Wire auto-generation into inventory rebuild + return member specs

**Files:**
- Modify: `inventory_api.py:687-708` (list_generic_parts), `inventory_api.py` (rebuild_inventory)
- Test: `tests/python/test_generic_parts.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/python/test_generic_parts.py`:

```python
class TestListGenericPartsWithSpecs:
    def test_list_includes_source(self, db, events_dir):
        _seed_parts(db)
        generic_parts.auto_generate_passive_groups(db, events_dir)
        gps = generic_parts.list_generic_parts_with_member_specs(db)
        assert len(gps) >= 1
        assert all("source" in gp for gp in gps)
        assert all(gp["source"] == "auto" for gp in gps)

    def test_list_includes_member_specs(self, db, events_dir):
        _seed_parts(db)
        generic_parts.auto_generate_passive_groups(db, events_dir)
        gps = generic_parts.list_generic_parts_with_member_specs(db)
        gp = next(g for g in gps if "100nF" in g["name"])
        assert len(gp["members"]) >= 2
        # Each member should have extracted spec fields
        for m in gp["members"]:
            assert "spec" in m
            assert "type" in m["spec"]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/python/test_generic_parts.py::TestListGenericPartsWithSpecs -v`
Expected: FAIL — function does not exist.

- [ ] **Step 3: Implement `list_generic_parts_with_member_specs`**

Add to `generic_parts.py`:

```python
def list_generic_parts_with_member_specs(conn: Any) -> list[dict[str, Any]]:
    """List all generic parts with members and their extracted specs.

    Returns each generic part with 'source' field and each member with
    an extracted 'spec' dict (for rendering filter chips).
    """
    gps = conn.execute("SELECT * FROM generic_parts").fetchall()
    result = []
    for gp in gps:
        members = conn.execute(
            """SELECT gm.part_id, gm.source, gm.preferred, s.quantity,
                      p.description, p.package
               FROM generic_part_members gm
               JOIN stock s USING (part_id)
               JOIN parts p USING (part_id)
               WHERE gm.generic_part_id = ?
               ORDER BY gm.preferred DESC, s.quantity DESC""",
            (gp["generic_part_id"],),
        ).fetchall()
        member_list = []
        for m in members:
            member_spec = spec_extractor.extract_spec(m["description"] or "", m["package"] or "")
            member_list.append({
                "part_id": m["part_id"],
                "source": m["source"],
                "preferred": m["preferred"],
                "quantity": m["quantity"],
                "description": m["description"] or "",
                "package": m["package"] or "",
                "spec": member_spec,
            })
        result.append({
            "generic_part_id": gp["generic_part_id"],
            "name": gp["name"],
            "part_type": gp["part_type"],
            "source": gp["source"],
            "spec": json.loads(gp["spec_json"]),
            "strictness": json.loads(gp["strictness_json"]),
            "members": member_list,
        })
    return result
```

- [ ] **Step 4: Update `inventory_api.py` to call auto-generation and use new list function**

In `inventory_api.py`, find the `rebuild_inventory` method. After `populate_full` is called (and after price history is rebuilt), add:

```python
# Auto-generate passive generic parts
import generic_parts as gp_mod
gp_mod.auto_generate_passive_groups(conn, self.events_dir)
```

Update `list_generic_parts` method to use the new function:

```python
def list_generic_parts(self) -> list[dict[str, Any]]:
    """List all generic parts with their members and specs."""
    import generic_parts
    conn = self._get_cache()
    return generic_parts.list_generic_parts_with_member_specs(conn)
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/python/test_generic_parts.py -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add generic_parts.py inventory_api.py tests/python/test_generic_parts.py
git commit -m "feat: wire auto-generation into rebuild, return member specs"
```

---

### Task 4: Frontend — groups mode toggle state + button on section headers

**Files:**
- Modify: `js/inventory/inv-state.js:3-17`
- Modify: `js/inventory/inventory-panel.js:135-157` (renderSubSection)
- Modify: `css/panels/inventory.css`

- [ ] **Step 1: Add groupsSections to inv-state.js**

In `js/inventory/inv-state.js`, add to the state object:

```javascript
var state = {
  body: null,
  searchInput: null,

  collapsedSections: new Set(),
  groupsSections: new Set(),   // sections with groups mode active
  bomData: null,
  activeFilter: "all",
  expandedAlts: new Set(),
  expandedMembers: new Set(),
  expandedGroups: new Set(),   // expanded generic group IDs
  groupFilters: {},            // { genericPartId: { dielectric: "C0G", tolerance: "10%" } }
  rowMap: new Map(),

  DESC_HIDE_WIDTH: 680,
  hideDescs: true,
};
```

- [ ] **Step 2: Add Groups button to subsection headers**

In `js/inventory/inventory-panel.js`, update `renderSubSection` (around line 135) to add a Groups button to the header:

```javascript
function renderSubSection(container, displayName, fullKey, parts) {
  var sub = document.createElement("div");
  sub.className = "inv-subsection";

  var isCollapsed = state.collapsedSections.has(fullKey);
  var isGroupsActive = state.groupsSections.has(fullKey);
  var header = document.createElement("div");
  header.className = "inv-subsection-header" + (isCollapsed ? " collapsed" : "");
  header.innerHTML = '<span class="chevron">\u25BE</span> ' + escHtml(displayName) +
    ' <span class="inv-section-count">(' + parts.length + ')</span>' +
    '<button class="groups-btn' + (isGroupsActive ? ' active' : '') + '" data-section="' + escHtml(fullKey) + '">\u25C6 Groups</button>';

  header.querySelector(".chevron").parentElement.addEventListener("click", function (e) {
    if (e.target.closest(".groups-btn")) return;
    if (state.collapsedSections.has(fullKey)) state.collapsedSections.delete(fullKey);
    else state.collapsedSections.add(fullKey);
    render();
  });
  header.querySelector(".groups-btn").addEventListener("click", function (e) {
    e.stopPropagation();
    if (state.groupsSections.has(fullKey)) state.groupsSections.delete(fullKey);
    else state.groupsSections.add(fullKey);
    render();
  });
  sub.appendChild(header);

  if (!isCollapsed) {
    if (isGroupsActive) {
      renderGroupedView(sub, fullKey, parts);
    } else {
      for (var k = 0; k < parts.length; k++) {
        sub.appendChild(createPartRow(parts[k], fullKey));
      }
    }
  }

  container.appendChild(sub);
}
```

Also do the same for `renderSection` (flat sections, around line 258).

- [ ] **Step 3: Add CSS for the Groups button**

Append to `css/panels/inventory.css`:

```css
/* ── Groups Mode Button ─────────────────────────────── */
.groups-btn {
  margin-left: auto; font-size: 10px; padding: 2px 8px; border-radius: 4px;
  background: #a371f718; color: var(--color-purple-light); border: 1px solid #a371f730;
  cursor: pointer; font-weight: 500; white-space: nowrap;
}
.groups-btn:hover { background: #a371f730; }
.groups-btn.active { background: #a371f740; border-color: #a371f760; }
```

- [ ] **Step 4: Add stub `renderGroupedView` function**

In `inventory-panel.js`, add a temporary stub after `createPartRow`:

```javascript
function renderGroupedView(container, sectionKey, parts) {
  // Stub — will be implemented in Task 5
  var stub = document.createElement("div");
  stub.style.cssText = "padding:12px 24px;color:#8b949e;font-size:11px;";
  stub.textContent = "Groups mode active for " + sectionKey + " (" + parts.length + " parts)";
  container.appendChild(stub);
}
```

- [ ] **Step 5: Verify visually**

Run the app (`python app.py`) and check:
- Each subsection header shows a "◆ Groups" button on the right
- Clicking it toggles the active state (purple highlight)
- When active, the stub text appears instead of the flat part list
- Collapse/expand still works independently

- [ ] **Step 6: Commit**

```bash
git add js/inventory/inv-state.js js/inventory/inventory-panel.js css/panels/inventory.css
git commit -m "feat: add groups mode toggle to section headers"
```

---

### Task 5: Frontend — group header rows and grouped part rendering

**Files:**
- Modify: `js/inventory/inventory-logic.js`
- Modify: `js/inventory/inventory-panel.js`
- Modify: `css/panels/inventory.css`

- [ ] **Step 1: Add `groupPartsByGeneric` to inventory-logic.js**

```javascript
/**
 * Group parts by their generic part membership.
 * @param {Array<Object>} parts - inventory items in this section
 * @param {Array<Object>} genericParts - from App.genericParts
 * @param {string} sectionKey - current section key
 * @returns {{ groups: Array<{ gp: Object, members: Array<Object> }>, ungrouped: Array<Object> }}
 */
export function groupPartsByGeneric(parts, genericParts, sectionKey) {
  // Build lookup: part_id -> generic part
  var partToGroup = {};
  var gpMap = {};
  for (var i = 0; i < genericParts.length; i++) {
    var gp = genericParts[i];
    gpMap[gp.generic_part_id] = gp;
    var members = gp.members || [];
    for (var j = 0; j < members.length; j++) {
      var pid = members[j].part_id;
      if (!partToGroup[pid]) partToGroup[pid] = [];
      partToGroup[pid].push(gp.generic_part_id);
    }
  }

  var grouped = {};   // gpId -> [part items]
  var ungrouped = [];
  var usedGpIds = new Set();

  for (var k = 0; k < parts.length; k++) {
    var item = parts[k];
    var pid2 = (item.lcsc || item.mpn || item.digikey || item.pololu || item.mouser || "").toUpperCase();
    var gpIds = partToGroup[pid2] || partToGroup[(item.lcsc || "")] || partToGroup[(item.mpn || "")];
    if (gpIds && gpIds.length > 0) {
      var gpId = gpIds[0]; // primary group
      if (!grouped[gpId]) grouped[gpId] = [];
      grouped[gpId].push(item);
      usedGpIds.add(gpId);
    } else {
      ungrouped.push(item);
    }
  }

  var groups = [];
  usedGpIds.forEach(function (gpId) {
    if (gpMap[gpId]) {
      groups.push({ gp: gpMap[gpId], parts: grouped[gpId] || [] });
    }
  });
  // Sort groups by the first member's sort position (maintains value ordering)
  groups.sort(function (a, b) {
    var aIdx = parts.indexOf(a.parts[0]);
    var bIdx = parts.indexOf(b.parts[0]);
    return aIdx - bIdx;
  });

  return { groups: groups, ungrouped: ungrouped };
}
```

- [ ] **Step 2: Implement `renderGroupedView` in inventory-panel.js**

Replace the stub with the real implementation:

```javascript
function renderGroupedView(container, sectionKey, parts) {
  var result = groupPartsByGeneric(parts, App.genericParts, sectionKey);

  for (var i = 0; i < result.groups.length; i++) {
    var g = result.groups[i];
    var gp = g.gp;
    var isExpanded = state.expandedGroups.has(gp.generic_part_id);
    var totalQty = 0;
    for (var q = 0; q < g.parts.length; q++) totalQty += (g.parts[q].qty || 0);

    // Group header row
    var header = document.createElement("div");
    header.className = "generic-group-header";
    header.dataset.gpId = gp.generic_part_id;
    header.innerHTML =
      '<span class="chevron">' + (isExpanded ? '\u25BE' : '\u25B8') + '</span>' +
      '<span class="gp-icon">\u25C6</span>' +
      '<span class="gp-name">' + escHtml(gp.name) + '</span>' +
      '<span class="gp-source">' + escHtml(gp.source || "auto") + '</span>' +
      '<span class="gp-stats">' + g.parts.length + ' parts</span>' +
      '<span class="gp-total-qty">' + totalQty + '</span>' +
      '<span class="gp-edit"><button class="group-edit-btn">Edit</button></span>';

    header.addEventListener("click", function (gpId) {
      return function (e) {
        if (e.target.closest(".group-edit-btn")) {
          e.stopPropagation();
          openGenericEdit(gpId);
          return;
        }
        if (state.expandedGroups.has(gpId)) state.expandedGroups.delete(gpId);
        else state.expandedGroups.add(gpId);
        render();
      };
    }(gp.generic_part_id));

    // Link target wiring (reverse link mode)
    if (App.links.linkingMode && App.links.linkingBomRow) {
      header.classList.add("link-target");
      header.addEventListener("click", function (gpItem) {
        return function () {
          // Link to the preferred or first member of the group
          var preferred = (gpItem.gp.members || []).find(function (m) { return m.preferred; });
          var best = preferred || (gpItem.gp.members || [])[0];
          if (best) createReverseLink({ lcsc: best.part_id, mpn: best.part_id });
        };
      }(g));
    }

    container.appendChild(header);

    // Expanded: show filter row + member parts
    if (isExpanded) {
      var filterRow = renderFilterRow(gp, g.parts);
      container.appendChild(filterRow);

      var filteredParts = applyGroupFilters(gp.generic_part_id, g.parts, gp);
      for (var p = 0; p < filteredParts.length; p++) {
        var partRow = createPartRow(filteredParts[p], sectionKey);
        partRow.classList.add("grouped-part-row");
        container.appendChild(partRow);
      }
    }
  }

  // Ungrouped parts (dimmed)
  for (var u = 0; u < result.ungrouped.length; u++) {
    var uRow = createPartRow(result.ungrouped[u], sectionKey);
    uRow.classList.add("ungrouped-part-row");
    container.appendChild(uRow);
  }
}
```

- [ ] **Step 3: Add imports**

At the top of `inventory-panel.js`, add `groupPartsByGeneric` to the import from `inventory-logic.js`:

```javascript
import {
  groupBySection,
  filterByQuery,
  computeMatchedInvKeys,
  sortBomRows,
  buildRowMap,
  bomRowDisplayData,
  groupPartsByGeneric,
} from './inventory-logic.js';
```

- [ ] **Step 4: Add CSS for group headers**

Append to `css/panels/inventory.css`:

```css
/* ── Generic Group Headers ──────────────────────────── */
.generic-group-header {
  display: flex; align-items: center; padding: 4px 12px 4px 24px;
  gap: 6px; font-size: 11px; font-weight: 600;
  background: #a371f70a; border-bottom: 1px solid #a371f718;
  border-left: 3px solid var(--color-purple); color: var(--color-purple-light);
  cursor: pointer; user-select: none;
}
.generic-group-header:hover { background: #a371f714; }
.generic-group-header .chevron { font-size: 9px; color: var(--text-muted); }
.generic-group-header .gp-icon { color: var(--color-purple); font-size: 12px; }
.generic-group-header .gp-name { white-space: nowrap; }
.generic-group-header .gp-source {
  font-size: 9px; color: var(--text-muted); opacity: 0.6;
}
.generic-group-header .gp-stats {
  font-weight: 400; color: var(--text-muted); font-size: 10px;
}
.generic-group-header .gp-total-qty {
  margin-left: auto; font-weight: 700; color: var(--color-purple);
  font-size: 12px;
}
.generic-group-header .group-edit-btn {
  font-size: 9px; padding: 1px 6px; border-radius: 3px;
  background: transparent; color: var(--text-muted); border: 1px solid var(--border-default);
  cursor: pointer;
}
.generic-group-header .group-edit-btn:hover {
  color: var(--color-purple-light); border-color: #a371f750;
}
.grouped-part-row { border-left: 3px solid #a371f730; }
.ungrouped-part-row { opacity: 0.5; }
```

- [ ] **Step 5: Add stub filter functions**

In `inventory-panel.js`, add temporary stubs for `renderFilterRow` and `applyGroupFilters`:

```javascript
function renderFilterRow(gp, parts) {
  var row = document.createElement("div");
  row.className = "generic-filter-row";
  row.style.cssText = "padding:4px 12px 4px 36px;border-bottom:1px solid #a371f715;background:#a371f706;border-left:3px solid #a371f750;font-size:10px;color:#8b949e;";
  row.textContent = "Filter chips coming in Task 6...";
  return row;
}

function applyGroupFilters(gpId, parts, gp) {
  // No filtering yet — return all parts
  return parts;
}
```

- [ ] **Step 6: Verify visually**

Run the app, click "◆ Groups" on a passive section (e.g., MLCC), confirm:
- Generic group headers appear with purple left edge, name, part count, total qty
- Clicking a header expands/collapses it
- Expanded groups show member parts indented with purple border
- Ungrouped parts appear dimmed below
- Edit button opens the existing generic parts modal

- [ ] **Step 7: Commit**

```bash
git add js/inventory/inventory-logic.js js/inventory/inventory-panel.js css/panels/inventory.css
git commit -m "feat: render generic group headers with expand/collapse"
```

---

### Task 6: Frontend — faceted filter chips with dynamic name

**Files:**
- Modify: `js/inventory/inventory-logic.js`
- Modify: `js/inventory/inventory-panel.js`
- Modify: `css/panels/inventory.css`

- [ ] **Step 1: Add `computeFilterDimensions` to inventory-logic.js**

```javascript
/**
 * Compute filter chip dimensions from member specs.
 * Returns dimensions with counts, e.g.:
 * [{ label: "Diel.", field: "dielectric", values: [{ value: "C0G", count: 3 }, ...] }, ...]
 *
 * @param {Array<Object>} members - from gp.members, each with .spec
 * @param {string} partType - capacitor, resistor, inductor
 * @returns {Array<{ label: string, field: string, values: Array<{ value: string, count: number }> }>}
 */
export function computeFilterDimensions(members, partType) {
  var dimensionFields = {
    capacitor: [
      { label: "Diel.", field: "dielectric" },
      { label: "Tol.", field: "tolerance" },
      { label: "Voltage", field: "voltage" },
    ],
    resistor: [
      { label: "Tol.", field: "tolerance" },
      { label: "Power", field: "power" },
    ],
    inductor: [
      { label: "Tol.", field: "tolerance" },
      { label: "Current", field: "current" },
    ],
  };
  var fields = dimensionFields[partType] || [];
  var result = [];

  for (var i = 0; i < fields.length; i++) {
    var f = fields[i];
    var valueCounts = {};
    for (var j = 0; j < members.length; j++) {
      var spec = members[j].spec || {};
      var val = spec[f.field];
      if (val !== undefined && val !== null && val !== "") {
        var display = typeof val === "number" ? val + (f.field === "voltage" ? "V" : "") : String(val);
        valueCounts[display] = (valueCounts[display] || 0) + 1;
      }
    }
    var values = Object.keys(valueCounts).map(function (v) {
      return { value: v, count: valueCounts[v] };
    });
    // Only include dimensions with 2+ distinct values
    if (values.length >= 2) {
      values.sort(function (a, b) { return b.count - a.count; });
      result.push({ label: f.label, field: f.field, values: values });
    }
  }
  return result;
}


/**
 * Filter members based on active chip selections.
 * @param {Array<Object>} members - with .spec
 * @param {Object} activeFilters - { dielectric: "C0G", tolerance: "10%" }
 * @returns {Array<Object>} filtered members
 */
export function filterMembersByChips(members, activeFilters) {
  if (!activeFilters || Object.keys(activeFilters).length === 0) return members;
  return members.filter(function (m) {
    var spec = m.spec || {};
    for (var field in activeFilters) {
      var expected = activeFilters[field];
      var actual = spec[field];
      if (actual === undefined || actual === null) return false;
      var actualStr = typeof actual === "number" ? actual + (field === "voltage" ? "V" : "") : String(actual);
      if (actualStr !== expected) return false;
    }
    return true;
  });
}
```

- [ ] **Step 2: Replace `renderFilterRow` stub in inventory-panel.js**

```javascript
function renderFilterRow(gp, parts) {
  var gpId = gp.generic_part_id;
  var members = gp.members || [];
  var activeFilters = state.groupFilters[gpId] || {};
  var filteredMembers = filterMembersByChips(members, activeFilters);
  var dims = computeFilterDimensions(filteredMembers.length < members.length ? members : members, gp.part_type);

  // Recompute dims counts based on current filter (cross-dimension)
  // For each dimension, counts should reflect members matching OTHER active filters
  var dimsWithCounts = [];
  for (var i = 0; i < dims.length; i++) {
    var dim = dims[i];
    // Filter by all OTHER active filters (not this dimension)
    var otherFilters = {};
    for (var key in activeFilters) {
      if (key !== dim.field) otherFilters[key] = activeFilters[key];
    }
    var subset = filterMembersByChips(members, otherFilters);
    var recounted = {};
    for (var j = 0; j < subset.length; j++) {
      var spec = subset[j].spec || {};
      var val = spec[dim.field];
      if (val !== undefined && val !== null && val !== "") {
        var display = typeof val === "number" ? val + (dim.field === "voltage" ? "V" : "") : String(val);
        recounted[display] = (recounted[display] || 0) + 1;
      }
    }
    var values = dim.values.map(function (v) {
      return { value: v.value, count: recounted[v.value] || 0 };
    });
    dimsWithCounts.push({ label: dim.label, field: dim.field, values: values });
  }

  // Build dynamic name from parent name + active chip values
  var nameParts = [];
  for (var fk in activeFilters) {
    nameParts.push(activeFilters[fk]);
  }
  var dynamicName = nameParts.length > 0 ? gp.name + " " + nameParts.join(" ") : "";

  var row = document.createElement("div");
  row.className = "generic-filter-row";

  var html = '';
  // Name zone (left side)
  if (dynamicName) {
    html += '<div class="filter-name-zone" data-gp-id="' + escHtml(gpId) + '">';
    html += '<span class="filter-dynamic-name">' + escHtml(dynamicName) + '</span>';
    if (App.links.linkingMode && App.links.linkingBomRow) {
      html += '<span class="filter-link-hint">\u2190 click to link</span>';
    }
    html += '</div>';
    html += '<span class="filter-sep">\u007C</span>';
  }

  // Chip zone (right side)
  html += '<div class="filter-chip-zone">';
  for (var d = 0; d < dimsWithCounts.length; d++) {
    var dm = dimsWithCounts[d];
    html += '<div class="filter-dim"><span class="filter-dim-label">' + escHtml(dm.label) + '</span>';
    for (var v = 0; v < dm.values.length; v++) {
      var chip = dm.values[v];
      var isActive = activeFilters[dm.field] === chip.value;
      var isDim = chip.count === 0;
      html += '<span class="filter-chip' + (isActive ? ' active' : '') + (isDim ? ' dim' : '') + '"' +
        ' data-gp-id="' + escHtml(gpId) + '"' +
        ' data-field="' + escHtml(dm.field) + '"' +
        ' data-value="' + escHtml(chip.value) + '">' +
        escHtml(chip.value) + '<span class="chip-count">' + chip.count + '</span></span>';
    }
    html += '</div>';
  }
  html += '</div>';

  row.innerHTML = html;

  // Chip click handler
  row.querySelectorAll(".filter-chip").forEach(function (chip) {
    chip.addEventListener("click", function (e) {
      e.stopPropagation();
      var field = chip.dataset.field;
      var value = chip.dataset.value;
      var gpId2 = chip.dataset.gpId;
      if (!state.groupFilters[gpId2]) state.groupFilters[gpId2] = {};
      if (state.groupFilters[gpId2][field] === value) {
        delete state.groupFilters[gpId2][field];
      } else {
        state.groupFilters[gpId2][field] = value;
      }
      if (Object.keys(state.groupFilters[gpId2]).length === 0) delete state.groupFilters[gpId2];
      render();
    });
  });

  // Name zone link target
  var nameZone = row.querySelector(".filter-name-zone");
  if (nameZone && App.links.linkingMode && App.links.linkingBomRow) {
    nameZone.classList.add("link-target");
    nameZone.addEventListener("click", function (e) {
      e.stopPropagation();
      // Same as linking to group header — use preferred/first member
      var preferred = (gp.members || []).find(function (m) { return m.preferred; });
      var best = preferred || (gp.members || [])[0];
      if (best) createReverseLink({ lcsc: best.part_id, mpn: best.part_id });
    });
  }

  return row;
}
```

- [ ] **Step 3: Replace `applyGroupFilters` stub**

```javascript
function applyGroupFilters(gpId, parts, gp) {
  var activeFilters = state.groupFilters[gpId] || {};
  if (Object.keys(activeFilters).length === 0) return parts;

  var members = gp.members || [];
  var filtered = filterMembersByChips(members, activeFilters);
  var filteredIds = new Set(filtered.map(function (m) { return m.part_id; }));

  return parts.filter(function (item) {
    var pid = (item.lcsc || item.mpn || item.digikey || item.pololu || item.mouser || "");
    return filteredIds.has(pid);
  });
}
```

- [ ] **Step 4: Add imports**

Add `computeFilterDimensions` and `filterMembersByChips` to the import from `inventory-logic.js` in `inventory-panel.js`.

- [ ] **Step 5: Add CSS for filter row and chips**

Append to `css/panels/inventory.css`:

```css
/* ── Generic Filter Row ─────────────────────────────── */
.generic-filter-row {
  display: flex; align-items: center; padding: 4px 12px 4px 36px;
  gap: 4px; border-bottom: 1px solid #a371f715; background: #a371f706;
  border-left: 3px solid #a371f750; flex-wrap: wrap; min-height: 26px;
}
.filter-name-zone {
  display: flex; align-items: center; padding-right: 8px;
  flex-shrink: 0; cursor: default;
}
.filter-name-zone.link-target { cursor: pointer; }
.filter-dynamic-name {
  font-size: 11px; font-weight: 600; color: var(--color-purple-light);
  font-family: "SFMono-Regular", Consolas, monospace; white-space: nowrap;
}
.filter-link-hint {
  font-size: 9px; color: var(--color-purple); margin-left: 6px; opacity: 0.7;
  white-space: nowrap;
}
.filter-sep { color: var(--border-default); margin: 0 4px; flex-shrink: 0; }
.filter-chip-zone {
  display: flex; align-items: center; gap: 3px; flex-wrap: wrap; flex: 1;
}
.filter-dim {
  display: inline-flex; align-items: center; gap: 3px; margin-right: 6px;
}
.filter-dim-label {
  font-size: 9px; color: var(--text-muted); text-transform: uppercase;
  letter-spacing: 0.5px; margin-right: 2px;
}
.filter-chip {
  font-size: 10px; padding: 1px 7px; border-radius: 10px;
  background: var(--bg-raised); color: var(--text-secondary);
  border: 1px solid var(--border-default); cursor: pointer;
  white-space: nowrap; transition: all 0.1s;
}
.filter-chip:hover {
  border-color: #a371f750; color: var(--color-purple-light);
  background: #a371f715;
}
.filter-chip.active {
  background: #a371f725; color: var(--color-purple-light);
  border-color: #a371f760;
}
.filter-chip.dim { opacity: 0.35; }
.chip-count {
  color: var(--text-muted); margin-left: 2px; font-size: 9px;
}
```

- [ ] **Step 6: Verify visually**

Run the app, expand a generic group:
- Filter chips appear per dimension (Diel., Tol., Voltage for capacitors)
- Clicking a chip filters the member list and updates counts
- Dynamic name appears on the left ("100nF 0402 C0G")
- Clicking a chip again deselects it

- [ ] **Step 7: Run lint + type check**

```bash
npx eslint js/ && npx tsc --noEmit
```

Fix any issues.

- [ ] **Step 8: Commit**

```bash
git add js/inventory/inventory-logic.js js/inventory/inventory-panel.js css/panels/inventory.css
git commit -m "feat: faceted filter chips with dynamic name on generic groups"
```

---

### Task 7: Run full test suites and fix issues

**Files:** Various — depends on what breaks

- [ ] **Step 1: Run Python tests**

```bash
pytest tests/python/ -v
```

Fix any failures.

- [ ] **Step 2: Run JS lint + type check**

```bash
npx eslint js/ && npx tsc --noEmit
```

Fix any issues.

- [ ] **Step 3: Run JS unit tests**

```bash
npx vitest run
```

Fix any failures.

- [ ] **Step 4: Run Python lint**

```bash
ruff check .
```

Fix any issues.

- [ ] **Step 5: Commit fixes if any**

```bash
git add -A
git commit -m "fix: resolve test and lint issues from generic parts UI"
```

---

### Task 8: Push and create PR

- [ ] **Step 1: Push and create PR**

```bash
bash scripts/push-pr.sh --title "feat: generic parts UI — groups mode, filter chips, auto-generation"
```

- [ ] **Step 2: Monitor CI**

```bash
gh pr checks <number> --watch
```

Fix any CI failures, push again, repeat until green.
