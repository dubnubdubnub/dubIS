# Phase 0 — Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stable part identity (registry with aliases), durable persistence for generic parts (fixing the cache-delete data-loss bug), part_events demotion to audit trail, and agent-hygiene riders (CLAUDE.md guard + fact fixes, code-map comment false-positive fix, dead PREFS_CHANGED removal).

**Architecture:** A new `domain/part_registry.py` makes `get_part_key()` registry-aware (canonical key = first-ever derived key; later-added PNs become aliases instead of changing identity). `domain/generic_parts.py` gains a `saved_searches.py`-style durable JSON overlay (`data/generic_parts.json` + `load_into_db` called from `rebuild()`), so manual groups/members/exclusions/preferred survive cache deletion and schema bumps. Riders harden the agent-facing docs/tooling.

**Tech Stack:** Python 3 stdlib (json, csv, sqlite3), existing `csv_io.atomic_write_text`, pytest; no new dependencies.

**Parent spec:** `docs/plans/2026-07-15-platform-architecture-design.md` (Phase 0 section).

## Global Constraints

- Throw errors rather than silently failing (`dubis_errors.DubISError` hierarchy; note the exact casing: `DubISError`).
- Never skip tests; add missing deps to `requirements-dev.txt` (there are none expected).
- After backend changes run `python scripts/generate-test-fixtures.py`; before PR run `bash scripts/verify.sh`.
- All work in worktree `D:/gehub/dubIS/.claude/worktrees/platform-phase0`, branch `claude/platform-phase0-foundations`.
- Registry must be **optional everywhere** (param default `None` → today's derived behavior) so unthreaded call sites and existing tests keep working; deleting `data/part_registry.json` must never break loading (it self-heals on next rebuild).

---

### Task 1: Part registry module

**Files:**
- Create: `domain/part_registry.py`
- Modify: `dubis_errors.py` (add exception)
- Test: `tests/python/domain/test_part_registry.py`

**Interfaces:**
- Produces: `PartRegistry` class (`.parts: dict[str, list[str]]`, `.alias_index: dict[str, str]`, `.dirty: bool`); functions `derive_key(row) -> str`, `load(data_dir) -> PartRegistry`, `save(data_dir, registry) -> None`, `canonical_for_row(registry, row) -> str` (returns `""` when no alias matches; raises `PartRegistryCollisionError` when a row's PNs map to two different canonicals), `register_row(registry, row) -> str`. File format: `data/part_registry.json` = `{"version": 1, "parts": {"<canonical>": ["<alias>", ...]}}`.
- Consumes: `csv_io.atomic_write_text(path, text, *, encoding)`; `dubis_errors.DubISError`.

- [ ] **Step 1: Add the exception to `dubis_errors.py`**

Append after the existing `CacheError` class:

```python
class PartRegistryCollisionError(DubISError):
    """A ledger row's part numbers map to two different registered parts."""
```

- [ ] **Step 2: Write the failing tests**

Create `tests/python/domain/test_part_registry.py`:

```python
"""Tests for domain.part_registry — stable part identity via alias registry."""

import json
import os

import pytest

from domain import part_registry
from dubis_errors import PartRegistryCollisionError


def _row(**kw):
    base = {
        "LCSC Part Number": "", "Manufacture Part Number": "",
        "Digikey Part Number": "", "Pololu Part Number": "", "Mouser Part Number": "",
    }
    base.update(kw)
    return base


class TestDeriveKey:
    def test_precedence_lcsc_over_mpn(self):
        row = _row(**{"LCSC Part Number": "C1234", "Manufacture Part Number": "MPN1"})
        assert part_registry.derive_key(row) == "C1234"

    def test_non_c_prefixed_lcsc_falls_through_to_mpn(self):
        row = _row(**{"LCSC Part Number": "1234", "Manufacture Part Number": "MPN1"})
        assert part_registry.derive_key(row) == "MPN1"

    def test_empty_row_returns_empty(self):
        assert part_registry.derive_key(_row()) == ""


class TestRegistry:
    def test_load_missing_file_returns_empty_registry(self, tmp_path):
        reg = part_registry.load(str(tmp_path))
        assert reg.parts == {}
        assert reg.dirty is False

    def test_register_new_row_uses_derived_key_and_records_aliases(self, tmp_path):
        reg = part_registry.load(str(tmp_path))
        row = _row(**{"LCSC Part Number": "C77", "Manufacture Part Number": "STM32F405"})
        key = part_registry.register_row(reg, row)
        assert key == "C77"
        assert set(reg.parts["C77"]) == {"C77", "STM32F405"}
        assert reg.dirty is True

    def test_enrichment_does_not_change_identity(self, tmp_path):
        """The core bug this module fixes: adding a higher-precedence PN later
        must NOT flip the part's identity."""
        reg = part_registry.load(str(tmp_path))
        # Part first appears with only an MPN → canonical key is the MPN.
        key1 = part_registry.register_row(reg, _row(**{"Manufacture Part Number": "STM32F405"}))
        assert key1 == "STM32F405"
        # Later the same row is enriched with an LCSC number.
        enriched = _row(**{"LCSC Part Number": "C99", "Manufacture Part Number": "STM32F405"})
        key2 = part_registry.register_row(reg, enriched)
        assert key2 == "STM32F405"          # identity is stable
        assert reg.alias_index["C99"] == "STM32F405"  # new PN becomes an alias
        # A row later matching only by the new LCSC number still resolves.
        assert part_registry.canonical_for_row(reg, _row(**{"LCSC Part Number": "C99"})) == "STM32F405"

    def test_collision_raises(self, tmp_path):
        reg = part_registry.load(str(tmp_path))
        part_registry.register_row(reg, _row(**{"Manufacture Part Number": "MPN_A"}))
        part_registry.register_row(reg, _row(**{"LCSC Part Number": "C1"}))
        # One row claiming PNs of two different registered parts → hard fail.
        bad = _row(**{"LCSC Part Number": "C1", "Manufacture Part Number": "MPN_A"})
        with pytest.raises(PartRegistryCollisionError):
            part_registry.canonical_for_row(reg, bad)

    def test_save_load_roundtrip(self, tmp_path):
        reg = part_registry.load(str(tmp_path))
        part_registry.register_row(reg, _row(**{"LCSC Part Number": "C77", "Manufacture Part Number": "M1"}))
        part_registry.save(str(tmp_path), reg)
        path = os.path.join(str(tmp_path), "part_registry.json")
        assert json.load(open(path, encoding="utf-8"))["version"] == 1
        reg2 = part_registry.load(str(tmp_path))
        assert reg2.parts == reg.parts
        assert reg2.alias_index["M1"] == "C77"
        assert reg2.dirty is False

    def test_register_row_with_no_pns_returns_empty(self, tmp_path):
        reg = part_registry.load(str(tmp_path))
        assert part_registry.register_row(reg, _row()) == ""
        assert reg.dirty is False
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/python/domain/test_part_registry.py -v` (from the worktree root)
Expected: FAIL / ERROR with `ModuleNotFoundError: No module named 'domain.part_registry'` (and `ImportError` for the exception until Step 1 lands).

- [ ] **Step 4: Implement `domain/part_registry.py`**

```python
"""Stable part identity — canonical key + distributor-PN alias registry.

The canonical key for a part is the FIRST key ever derived for it (via the
LCSC > MPN > DigiKey > Pololu > Mouser precedence).  When a part later gains
a higher-precedence PN (enrichment), that PN becomes an *alias* pointing at
the original canonical key instead of silently changing the part's identity —
which would orphan adjustments, price observations, group memberships, and
PnP/feeder references.

data/part_registry.json is the durable store:
    {"version": 1, "parts": {"<canonical>": ["<alias>", ...]}}
The file is additive and self-healing: if deleted, the next rebuild
re-registers every part from the ledger (identities revert to derived keys,
which is exactly today's behavior).
"""

from __future__ import annotations

import json
import os

import csv_io
from dubis_errors import PartRegistryCollisionError

_JSON_FILE = "part_registry.json"

# Ledger columns holding part numbers, in identity-precedence order.
PN_COLUMNS = (
    "LCSC Part Number",
    "Manufacture Part Number",
    "Digikey Part Number",
    "Pololu Part Number",
    "Mouser Part Number",
)


def derive_key(row: dict[str, str]) -> str:
    """Best unique identifier: LCSC (C-prefixed) > MPN > Digikey > Pololu > Mouser."""
    lcsc = (row.get("LCSC Part Number") or "").strip()
    if lcsc and lcsc.upper().startswith("C"):
        return lcsc
    for col in PN_COLUMNS[1:]:
        val = (row.get(col) or "").strip()
        if val:
            return val
    return ""


def _present_pns(row: dict[str, str]) -> list[str]:
    """All non-empty PNs in the row (a non-C LCSC value is not a usable PN)."""
    pns = []
    lcsc = (row.get("LCSC Part Number") or "").strip()
    if lcsc and lcsc.upper().startswith("C"):
        pns.append(lcsc)
    for col in PN_COLUMNS[1:]:
        val = (row.get(col) or "").strip()
        if val:
            pns.append(val)
    return pns


class PartRegistry:
    def __init__(self, parts: dict[str, list[str]] | None = None):
        self.parts: dict[str, list[str]] = parts or {}
        self.alias_index: dict[str, str] = {
            alias: canonical
            for canonical, aliases in self.parts.items()
            for alias in aliases
        }
        self.dirty = False


def _json_path(data_dir: str) -> str:
    return os.path.join(data_dir, _JSON_FILE)


def load(data_dir: str) -> PartRegistry:
    """Load the registry; missing file → empty registry (self-healing)."""
    path = _json_path(data_dir)
    if not os.path.exists(path):
        return PartRegistry()
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return PartRegistry(data.get("parts", {}))


def save(data_dir: str, registry: PartRegistry) -> None:
    os.makedirs(data_dir, exist_ok=True)
    csv_io.atomic_write_text(
        _json_path(data_dir),
        json.dumps({"version": 1, "parts": registry.parts},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    registry.dirty = False


def canonical_for_row(registry: PartRegistry, row: dict[str, str]) -> str:
    """Canonical key for a row via alias lookup; "" if no PN is registered.

    Raises PartRegistryCollisionError if the row's PNs map to two different
    registered parts (data corruption must fail loudly, not warn-and-drop).
    """
    canonicals = {
        registry.alias_index[pn]
        for pn in _present_pns(row)
        if pn in registry.alias_index
    }
    if len(canonicals) > 1:
        raise PartRegistryCollisionError(
            f"Row part numbers {_present_pns(row)!r} map to multiple "
            f"registered parts: {sorted(canonicals)!r}"
        )
    return next(iter(canonicals)) if canonicals else ""


def register_row(registry: PartRegistry, row: dict[str, str]) -> str:
    """Resolve a row to its canonical key, registering new PNs as aliases.

    Returns "" for rows with no usable PN (matches derive_key behavior).
    """
    canonical = canonical_for_row(registry, row)
    if not canonical:
        canonical = derive_key(row)
        if not canonical:
            return ""
    aliases = registry.parts.setdefault(canonical, [])
    if canonical not in aliases:
        aliases.append(canonical)
        registry.alias_index[canonical] = canonical
        registry.dirty = True
    for pn in _present_pns(row):
        if pn not in registry.alias_index:
            aliases.append(pn)
            registry.alias_index[pn] = canonical
            registry.dirty = True
    return canonical
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/python/domain/test_part_registry.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add dubis_errors.py domain/part_registry.py tests/python/domain/test_part_registry.py
git commit -m "feat(identity): part registry — stable canonical keys with PN aliases"
```

---

### Task 2: Thread the registry through merge, cache population, and row-matching

**Files:**
- Modify: `inventory_ops.py` (`get_part_key`, `read_and_merge`)
- Modify: `cache_db.py` (`populate_full` — the three `get_part_key(...)` calls at lines 178, 230, 261)
- Modify: `domain/inventory.py` (`rebuild()` at line 49; the four `inventory_ops.get_part_key(row)` call sites at lines 173, 396, 474, 614)
- Test: `tests/python/domain/test_part_registry_integration.py`

**Interfaces:**
- Consumes: Task 1's `domain.part_registry` (`load`, `save`, `register_row`, `canonical_for_row`, `derive_key`, `PartRegistry`).
- Produces: `inventory_ops.get_part_key(row, registry=None)` — same return as today when `registry is None`; `inventory_ops.read_and_merge(purchase_csv, fieldnames, registry=None)`; `cache_db.populate_full(..., registry=None)` keyword arg. `rebuild()` loads the registry from `base_dir`, threads it through merge + populate, and saves it if dirty.

**Design note (scope boundary):** registry-aware resolution is threaded through the rebuild pipeline and the ledger row-matching loops in `domain/inventory.py` (where enrichment-induced key flips would corrupt data). Read-only helpers that scan the ledger with derived keys (`last_po_quantity`, `domain/pricing.get_sourced_distributors`) keep today's behavior — they were consistent-by-derivation before and remain so; they get registry-aware in Phase 1 when the service layer gives them a shared registry handle. Do not thread the registry anywhere not listed here.

- [ ] **Step 1: Write the failing integration test**

Create `tests/python/domain/test_part_registry_integration.py`:

```python
"""End-to-end: enriching a part with a higher-precedence PN keeps its identity."""

import csv
import os
import sqlite3

import cache_db
import inventory_ops
from domain import part_registry

LEDGER_FIELDS = [
    "LCSC Part Number", "Manufacture Part Number", "Digikey Part Number",
    "Pololu Part Number", "Mouser Part Number", "Manufacturer", "Description",
    "Package", "RoHS", "Quantity", "Unit Price($)", "Ext.Price($)",
    "Date Code / Lot No.", "po_id",
]


def _write_ledger(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in LEDGER_FIELDS})


def test_merge_key_stable_across_enrichment(tmp_path):
    ledger = os.path.join(str(tmp_path), "purchase_ledger.csv")
    _write_ledger(ledger, [{
        "Manufacture Part Number": "STM32F405", "Description": "MCU",
        "Quantity": "10", "Unit Price($)": "5.00", "Ext.Price($)": "50.00",
    }])

    # First merge: part registers under its MPN.
    reg = part_registry.load(str(tmp_path))
    _, merged1 = inventory_ops.read_and_merge(ledger, LEDGER_FIELDS, registry=reg)
    assert "STM32F405" in merged1
    part_registry.save(str(tmp_path), reg)

    # Enrichment: the same row gains an LCSC number (higher precedence).
    _write_ledger(ledger, [{
        "LCSC Part Number": "C99", "Manufacture Part Number": "STM32F405",
        "Description": "MCU", "Quantity": "10",
        "Unit Price($)": "5.00", "Ext.Price($)": "50.00",
    }])
    reg2 = part_registry.load(str(tmp_path))
    _, merged2 = inventory_ops.read_and_merge(ledger, LEDGER_FIELDS, registry=reg2)

    # Identity did NOT flip to C99.
    assert "STM32F405" in merged2
    assert "C99" not in merged2


def test_merge_without_registry_matches_today(tmp_path):
    ledger = os.path.join(str(tmp_path), "purchase_ledger.csv")
    _write_ledger(ledger, [{
        "LCSC Part Number": "C99", "Manufacture Part Number": "STM32F405",
        "Description": "MCU", "Quantity": "10",
        "Unit Price($)": "5.00", "Ext.Price($)": "50.00",
    }])
    _, merged = inventory_ops.read_and_merge(ledger, LEDGER_FIELDS)
    assert "C99" in merged  # derived-precedence behavior unchanged


def test_populate_full_uses_registry_keys(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cache_db.create_schema(conn)

    reg = part_registry.PartRegistry({"STM32F405": ["STM32F405", "C99"]})
    part = {
        "LCSC Part Number": "C99", "Manufacture Part Number": "STM32F405",
        "Description": "MCU", "Package": "LQFP64", "Quantity": "10",
        "Unit Price($)": "5.00", "Ext.Price($)": "50.00",
    }
    cache_db.populate_full(conn, {"STM32F405": part}, {"ICs": [part]}, registry=reg)
    row = conn.execute("SELECT part_id FROM parts").fetchone()
    assert row["part_id"] == "STM32F405"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/python/domain/test_part_registry_integration.py -v`
Expected: FAIL with `TypeError: read_and_merge() got an unexpected keyword argument 'registry'`.

- [ ] **Step 3: Make `get_part_key` and `read_and_merge` registry-aware**

In `inventory_ops.py`, replace the `get_part_key` function body (lines 23-40) with delegation, and add the `registry` param to `read_and_merge`:

```python
def get_part_key(row: dict[str, str], registry=None) -> str:
    """Return the part's canonical identity.

    With a registry: alias lookup first (stable across enrichment), falling
    back to derived precedence.  Without: derived precedence, as always.
    """
    from domain import part_registry  # local import — domain imports inventory_ops

    if registry is not None:
        canonical = part_registry.canonical_for_row(registry, row)
        if canonical:
            return canonical
    return part_registry.derive_key(row)
```

In `read_and_merge`, change the signature to
`def read_and_merge(purchase_csv, fieldnames, registry=None):`
and change the merge-loop key line (line 65) from `pn = get_part_key(r)` to:

```python
        if registry is not None:
            from domain import part_registry
            pn = part_registry.register_row(registry, r)
        else:
            pn = get_part_key(r)
```

- [ ] **Step 4: Make `populate_full` registry-aware**

In `cache_db.py`, add `registry=None` to `populate_full`'s keyword-only params (after `vendors_json_path`), and change the three call sites:
- line 178: `part_id = get_part_key(part, registry)`
- line 230: `pk = get_part_key(row, registry)`
- line 261: `pk = get_part_key(part, registry)`

- [ ] **Step 5: Thread the registry through `rebuild()` and the ledger row-match loops**

In `domain/inventory.py` `rebuild()` (line 49), after the `migrate_to_vendors` line insert, and pass through:

```python
    from domain import part_registry as _preg  # noqa: PLC0415
    registry = _preg.load(base_dir)
    file_fieldnames, merged = inventory_ops.read_and_merge(input_csv, fieldnames, registry=registry)
    inventory_ops.apply_adjustments(merged, adjustments_csv, file_fieldnames)
    categorized = inventory_ops.categorize_and_sort(list(merged.values()))
    cache_db.populate_full(
        conn, merged, categorized,
        ledger_path=input_csv,
        po_csv_path=os.path.join(base_dir, "purchase_orders.csv"),
        vendors_json_path=vendors_json,
        registry=registry,
    )
    if registry.dirty:
        _preg.save(base_dir, registry)
```

Then make the four ledger row-match loops registry-aware. Each enclosing function already receives `base_dir`; at the top of each function's ledger-scanning block add `registry = _preg.load(base_dir)` (import `from domain import part_registry as _preg` at module top of `domain/inventory.py`), and change:
- line 173: `part_key = inventory_ops.get_part_key(row)` → `inventory_ops.get_part_key(row, registry)`
- line 396: `pk = inventory_ops.get_part_key(row)` → `inventory_ops.get_part_key(row, registry)`
- line 474 (in `update_part_fields`): same change
- line 614: same change

(Line numbers are pre-edit anchors; locate by the grep pattern `inventory_ops.get_part_key(row)`.)

- [ ] **Step 6: Run the new tests and the full Python suite**

Run: `python -m pytest tests/python/domain/test_part_registry_integration.py tests/python/test_inventory_ops.py tests/python/test_inventory_api_loading.py -v`
Expected: PASS (existing `get_part_key` tests exercise the `registry=None` path unchanged).

Run: `python -m pytest tests/python/ -q`
Expected: PASS.

- [ ] **Step 7: Regenerate fixtures and commit**

```bash
python scripts/generate-test-fixtures.py
git add -A
git commit -m "feat(identity): thread part registry through merge, cache population, and row matching"
```

---

### Task 3: Durable persistence for generic parts (fix the cache-delete data-loss bug)

**Files:**
- Modify: `domain/generic_parts.py` (add `_persist` + `load_into_db`; add `data_dir` param to mutating functions)
- Modify: `domain/api_generic_parts.py` (pass `self._api.base_dir` through)
- Modify: `domain/inventory.py` (`rebuild()` — call `load_into_db` after `auto_generate_passive_groups`)
- Modify: `domain/inventory.py` `delete_part` flow if it calls `remove_member` (pass `data_dir`)
- Test: extend `tests/python/domain/test_generic_parts.py`

**Interfaces:**
- Consumes: `csv_io.atomic_write_text`; existing SQLite tables `generic_parts`, `generic_part_members`.
- Produces: `data/generic_parts.json` = `{"version": 1, "groups": [{generic_part_id, name, part_type, spec, strictness}], "members": [{generic_part_id, part_id, source}], "preferred": [{generic_part_id, part_id}]}` where `groups` holds `source='manual'` groups only and `members` holds `source IN ('manual','excluded')` rows only. New signatures (data_dir appended after events_dir): `create_generic_part(conn, events_dir, data_dir, name, part_type, spec, strictness, source="manual")`, `add_member(conn, events_dir, data_dir, generic_part_id, part_id, source="manual")`, `remove_member(conn, events_dir, data_dir, generic_part_id, part_id)`, `exclude_member(conn, events_dir, data_dir, generic_part_id, part_id)`, `set_preferred(conn, events_dir, data_dir, generic_part_id, part_id)`, same pattern for `create_generic_part_api`/`update_generic_part_api`/`add_member_api`/`remove_member_api`/`set_preferred_api`. Plus `load_into_db(conn, data_dir) -> None`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/python/domain/test_generic_parts.py` (adapt fixture names to the file's existing fixtures — it already has a `db` SQLite fixture and `events_dir`; add a `data_dir` tmp fixture alongside):

```python
class TestDurablePersistence:
    def test_manual_group_survives_cache_wipe(self, db, events_dir, tmp_path):
        data_dir = str(tmp_path / "data")
        _insert_part(db, "C1", "100nF cap", "0402")  # use the file's existing part-insert helper
        create_generic_part(db, events_dir, data_dir, "My Group", "capacitor",
                            {"value": "100nF", "package": "0402"},
                            {"required": ["value"]})
        add_member(db, events_dir, data_dir, "cap_100nf_0402", "C1")

        # Simulate cache deletion: fresh empty DB, then reload from JSON.
        db2 = _fresh_db()  # use/extract the file's existing schema-creating helper
        _insert_part(db2, "C1", "100nF cap", "0402")
        load_into_db(db2, data_dir)

        names = [r["name"] for r in
                 db2.execute("SELECT name FROM generic_parts WHERE source='manual'").fetchall()]
        assert "My Group" in names
        member_sources = {r["source"] for r in db2.execute(
            "SELECT source FROM generic_part_members WHERE part_id='C1'").fetchall()}
        assert "manual" in member_sources

    def test_exclusion_and_preferred_survive_reload(self, db, events_dir, tmp_path):
        data_dir = str(tmp_path / "data")
        _insert_part(db, "C1", "100nF cap", "0402")
        _insert_part(db, "C2", "100nF cap", "0402")
        create_generic_part(db, events_dir, data_dir, "G", "capacitor",
                            {"value": "100nF", "package": "0402"}, {"required": ["value"]})
        gid = db.execute("SELECT generic_part_id FROM generic_parts").fetchone()["generic_part_id"]
        exclude_member(db, events_dir, data_dir, gid, "C2")
        add_member(db, events_dir, data_dir, gid, "C1")
        set_preferred(db, events_dir, data_dir, gid, "C1")

        db2 = _fresh_db()
        _insert_part(db2, "C1", "100nF cap", "0402")
        _insert_part(db2, "C2", "100nF cap", "0402")
        load_into_db(db2, data_dir)

        rows = {r["part_id"]: r for r in db2.execute(
            "SELECT part_id, source, preferred FROM generic_part_members "
            "WHERE generic_part_id=?", (gid,)).fetchall()}
        assert rows["C2"]["source"] == "excluded"
        assert rows["C1"]["preferred"] == 1

    def test_load_into_db_missing_file_is_noop(self, db, tmp_path):
        load_into_db(db, str(tmp_path / "nowhere"))  # must not raise

    def test_load_bootstraps_json_from_existing_db_state(self, db, events_dir, tmp_path):
        """Migration path: users with manual groups already in SQLite but no
        JSON file yet — load_into_db must write the file from DB state."""
        data_dir = str(tmp_path / "data")
        _insert_part(db, "C1", "cap", "0402")
        # Manual group created BEFORE the overlay existed (no data_dir write):
        db.execute("INSERT INTO generic_parts (generic_part_id, name, part_type, "
                   "spec_json, strictness_json, source) "
                   "VALUES ('g1','Legacy','capacitor','{}','{}','manual')")
        db.execute("INSERT INTO generic_part_members (generic_part_id, part_id, "
                   "source, preferred) VALUES ('g1','C1','manual',0)")
        db.commit()
        load_into_db(db, data_dir)
        assert os.path.exists(os.path.join(data_dir, "generic_parts.json"))
```

Import `load_into_db` in the test file's import block. Where the plan says `_insert_part` / `_fresh_db`, reuse the helpers the test file already defines for its existing tests (it creates parts and a schema'd DB for `auto_generate_passive_groups` tests); extract them into module-level helpers if they're currently inline.

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/python/domain/test_generic_parts.py -v -k Durable`
Expected: FAIL with `ImportError`/`TypeError` (no `load_into_db`; new `data_dir` arg).

- [ ] **Step 3: Implement `_persist` and `load_into_db` in `domain/generic_parts.py`**

Add near the top (after `_record_event`):

```python
_JSON_FILE = "generic_parts.json"


def _json_path(data_dir: str) -> str:
    return os.path.join(data_dir, _JSON_FILE)


def _persist(conn: Any, data_dir: str) -> None:
    """Write all manual generic-part state from SQLite to the durable JSON file.

    Manual groups, manual/excluded memberships, and preferred flags are
    user-created state; SQLite is a deletable cache, so this file is their
    source of truth (same pattern as saved_searches.json).
    """
    import csv_io  # noqa: PLC0415

    groups = [
        {
            "generic_part_id": r["generic_part_id"],
            "name": r["name"],
            "part_type": r["part_type"],
            "spec": json.loads(r["spec_json"]),
            "strictness": json.loads(r["strictness_json"]),
        }
        for r in conn.execute(
            "SELECT * FROM generic_parts WHERE source='manual' ORDER BY generic_part_id"
        ).fetchall()
    ]
    members = [
        {"generic_part_id": r["generic_part_id"], "part_id": r["part_id"],
         "source": r["source"]}
        for r in conn.execute(
            "SELECT generic_part_id, part_id, source FROM generic_part_members "
            "WHERE source IN ('manual','excluded') ORDER BY generic_part_id, part_id"
        ).fetchall()
    ]
    preferred = [
        {"generic_part_id": r["generic_part_id"], "part_id": r["part_id"]}
        for r in conn.execute(
            "SELECT generic_part_id, part_id FROM generic_part_members "
            "WHERE preferred=1 ORDER BY generic_part_id, part_id"
        ).fetchall()
    ]
    os.makedirs(data_dir, exist_ok=True)
    csv_io.atomic_write_text(
        _json_path(data_dir),
        json.dumps({"version": 1, "groups": groups, "members": members,
                    "preferred": preferred}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def load_into_db(conn: Any, data_dir: str) -> None:
    """Restore manual generic-part state from generic_parts.json into SQLite.

    Called during rebuild AFTER auto_generate_passive_groups.  Idempotent.
    If the JSON file is missing but SQLite already holds manual state
    (pre-overlay users), bootstraps the file from the DB instead.
    """
    import logging  # noqa: PLC0415
    logger = logging.getLogger(__name__)

    path = _json_path(data_dir)
    if not os.path.exists(path):
        has_manual = conn.execute(
            "SELECT 1 FROM generic_parts WHERE source='manual' LIMIT 1"
        ).fetchone()
        if has_manual:
            _persist(conn, data_dir)
        return

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    known_parts = {r["part_id"] for r in conn.execute("SELECT part_id FROM parts").fetchall()}

    for g in data.get("groups", []):
        conn.execute(
            """INSERT OR REPLACE INTO generic_parts
               (generic_part_id, name, part_type, spec_json, strictness_json, source)
               VALUES (?,?,?,?,?,'manual')""",
            (g["generic_part_id"], g["name"], g["part_type"],
             json.dumps(g["spec"]), json.dumps(g["strictness"])),
        )
        _auto_match(conn, g["generic_part_id"], g["part_type"], g["spec"], g["strictness"])

    for m in data.get("members", []):
        if m["part_id"] not in known_parts:
            # Part was deleted from the ledger after this membership was saved;
            # inserting would violate the FK.  Warn (visible), keep the record
            # in JSON (the part may return), skip the DB row.
            logger.warning("generic_parts.json member %s references unknown part %s — skipped",
                           m["generic_part_id"], m["part_id"])
            continue
        conn.execute(
            """INSERT OR REPLACE INTO generic_part_members
               (generic_part_id, part_id, source, preferred) VALUES (?,?,?,0)""",
            (m["generic_part_id"], m["part_id"], m["source"]),
        )

    for p in data.get("preferred", []):
        conn.execute(
            "UPDATE generic_part_members SET preferred=1 "
            "WHERE generic_part_id=? AND part_id=?",
            (p["generic_part_id"], p["part_id"]),
        )
    conn.commit()
    logger.info("Loaded %d manual generic groups from %s", len(data.get("groups", [])), path)
```

- [ ] **Step 4: Add `data_dir` to every mutating function and call `_persist`**

In `domain/generic_parts.py`, change signatures (insert `data_dir: str` after `events_dir`) and add `_persist(conn, data_dir)` immediately after each function's `conn.commit()`:
- `create_generic_part(conn, events_dir, data_dir, name, part_type, spec, strictness, source="manual")`
- `add_member(conn, events_dir, data_dir, generic_part_id, part_id, source="manual")`
- `remove_member(conn, events_dir, data_dir, generic_part_id, part_id)`
- `exclude_member(conn, events_dir, data_dir, generic_part_id, part_id)`
- `set_preferred(conn, events_dir, data_dir, generic_part_id, part_id)`
- `update_generic_part_api(conn, events_dir, data_dir, generic_part_id, name, spec_json, strictness_json)` — add `_persist(conn, data_dir)` after the `_auto_match(...)` call (its writes commit inside `_auto_match`; add an explicit `conn.commit()` before `_persist` if none follows).
- API wrappers `create_generic_part_api`, `add_member_api`, `remove_member_api`, `set_preferred_api` gain `data_dir` and pass it through.

Update the callers:
- `domain/api_generic_parts.py`: every call to the functions above adds `self._api.base_dir` as the `data_dir` argument (the facade already uses `self._api.base_dir` for saved searches at line 85).
- `domain/inventory.py`: `delete_part`'s generic-group detach calls `remove_member` (added in PR #355) — pass `base_dir` as `data_dir`.
- Any other caller found by `grep -rn "add_member(\|remove_member(\|exclude_member(\|set_preferred(\|create_generic_part(" --include=*.py` — update tests in `tests/python/domain/test_generic_parts.py` and any facade call sites the grep reveals.

- [ ] **Step 5: Call `load_into_db` from `rebuild()`**

In `domain/inventory.py` `rebuild()` (lines 79-83), after `_gp.auto_generate_passive_groups(conn, events_dir)` and before the saved_searches load, insert:

```python
    _gp.load_into_db(conn, base_dir)
```

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/python/domain/test_generic_parts.py tests/python/test_inventory_api_generic_parts.py -v`
Expected: PASS (existing tests updated for the new `data_dir` params in Step 4).

Run: `python -m pytest tests/python/ -q`
Expected: PASS.

- [ ] **Step 7: Regenerate fixtures, commit**

```bash
python scripts/generate-test-fixtures.py
git add -A
git commit -m "fix(generic-parts): durable JSON overlay — manual groups survive cache deletion"
```

---

### Task 4: Demote part_events.csv to audit trail + entity-store convention doc

**Files:**
- Modify: `domain/generic_parts.py` (`_record_event` docstring)
- Create: `docs/entity-store.md`
- Modify: `CLAUDE.md` (Data row of the architecture table — one-line pointer)

**Interfaces:** documentation only; no code behavior changes.

- [ ] **Step 1: Update `_record_event`'s docstring**

Replace the one-line docstring at `domain/generic_parts.py:20` with:

```python
    """Append an event to part_events.csv (append-only AUDIT TRAIL).

    This log is never replayed — it is not event-sourcing.  Durable state
    lives in data/generic_parts.json (see load_into_db / docs/entity-store.md).
    """
```

- [ ] **Step 2: Write `docs/entity-store.md`**

```markdown
# Entity-store convention

Every first-class user-created entity in dubIS follows one persistence
pattern. SQLite (`cache.db`) is a deletable materialized view — an entity
stored only in SQLite WILL be lost on cache deletion or schema bump.

The pattern (reference implementations: `saved_searches.py`,
`domain/generic_parts.py` `_persist`/`load_into_db`):

1. **Durable file** in `data/` (JSON for structured records, CSV for
   append-only logs) written with `csv_io.atomic_write_text` /
   `csv_io.atomic_write_rows` after every mutation.
2. **`load_into_db(conn, data_dir)`** — idempotent restore into SQLite,
   called from `domain/inventory.py:rebuild()`.
3. **SQLite table** in `cache_db.create_schema` — derived cache only. It may
   be dropped on `SCHEMA_VERSION` bumps precisely because of rule 1+2.
4. **Schema entry** in `domain/schema.py` if the entity's fields reach the
   frontend (then regenerate types: `python scripts/gen-inventory-types.py`).

Existing entities and their durable stores:

| Entity | Durable store | Restored by |
|---|---|---|
| Purchase history | `data/purchase_ledger.csv` | full rebuild (merge) |
| Adjustments | `data/adjustments.csv` | full rebuild / catch_up |
| Vendors | `data/vendors.json` | `populate_full` |
| Purchase orders | `data/purchase_orders.csv` | `populate_full` |
| Saved searches | `data/saved_searches.json` | `saved_searches.load_into_db` |
| Generic parts (manual state) | `data/generic_parts.json` | `domain/generic_parts.load_into_db` |
| Part identity registry | `data/part_registry.json` | loaded each rebuild (`domain/part_registry.py`) |
| Price observations | `events/price_observations.csv` | `populate_prices_cache` |

**Audit trails (never replayed):** `events/part_events.csv` records generic-part
mutations for forensics only. Do not build restore logic on it.

New entities (BOMs, boards, feeders, part maps) MUST follow this pattern —
copy `saved_searches.py`, not a SQLite-only design.
```

- [ ] **Step 3: Add the pointer in CLAUDE.md**

In the architecture table's **Data** row, after the `cache.db` mention, append: `; entity persistence rules: docs/entity-store.md`. Also fix the data-flow diagram footnote `generic_parts (populated by generic_parts.py, stored directly in SQLite)` → `generic_parts (manual state durable in data/generic_parts.json; auto groups regenerated)`.

- [ ] **Step 4: Commit**

```bash
git add domain/generic_parts.py docs/entity-store.md CLAUDE.md
git commit -m "docs: entity-store convention; demote part_events.csv to audit trail"
```

---

### Task 5: Fix comment false-positives in gen-code-map.py and devtools event_trace

**Files:**
- Modify: `scripts/gen-code-map.py` (`scan_eventbus_refs`, line 128)
- Modify: `tools/dev-tools-mcp/matchers.py` (`find_event_emitters_listeners`, line 109)
- Test: extend `tests/python/test_dev_tools_mcp.py`
- Regenerate: `docs/code-map.md`

**Interfaces:** no signature changes; both scanners must ignore `//` and `/* */` comments. Known false positive to eliminate: `js/store.js:39` (comment reading `Replaces the EventBus.emit(Events.PREFS_CHANGED) pattern`) is currently reported as an emitter in `docs/code-map.md`.

- [ ] **Step 1: Write the failing test**

Add to `tests/python/test_dev_tools_mcp.py` (follow the file's existing pattern for invoking `find_event_emitters_listeners` — it imports from `matchers`):

```python
def test_event_trace_ignores_comments(tmp_path):
    js = tmp_path / "js"
    js.mkdir()
    (js / "a.js").write_text(
        "// Replaces the EventBus.emit(Events.FOO_EVENT) pattern\n"
        "/* EventBus.on(Events.FOO_EVENT, x) */\n"
        "EventBus.emit(Events.FOO_EVENT, data);\n",
        encoding="utf-8",
    )
    result = find_event_emitters_listeners("FOO_EVENT", str(tmp_path))
    assert len(result["emitters"]) == 1
    assert result["emitters"][0]["line"] == 3
    assert result["listeners"] == []
```

(Adapt the call to `find_event_emitters_listeners`'s actual signature — read it first; it takes the event name plus a root/js-dir argument and returns dicts with line info per the docstring at `matchers.py:109-124`.)

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/python/test_dev_tools_mcp.py -v -k comments`
Expected: FAIL — emitter count is 2 (or 3) because comment lines match.

- [ ] **Step 3: Add a shared comment-blanking helper and use it in both scanners**

In `tools/dev-tools-mcp/matchers.py`, add:

```python
def _blank_js_comments(text: str) -> str:
    """Replace JS comment contents with spaces, preserving line structure
    so line numbers of real code stay valid."""
    text = re.sub(r"/\*.*?\*/",
                  lambda m: re.sub(r"[^\n]", " ", m.group(0)),
                  text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    return text
```

In `find_event_emitters_listeners`, run each file's text through `_blank_js_comments` before line-splitting/matching (keep reporting line numbers against the blanked text — they are identical to the original's).

In `scripts/gen-code-map.py` `scan_eventbus_refs` (line 128), apply the same fix — after reading `text`, insert the exact comment-strip lines already used by the import scanner at lines 97-100:

```python
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
```

- [ ] **Step 4: Run tests, regenerate the code map**

Run: `python -m pytest tests/python/test_dev_tools_mcp.py -v`
Expected: PASS.

Run: `python scripts/gen-code-map.py`
Expected: `docs/code-map.md` diff removes `PREFS_CHANGED` from `js/store.js` emitters (verify with `git diff docs/code-map.md | grep PREFS`).

- [ ] **Step 5: Commit**

```bash
git add scripts/gen-code-map.py tools/dev-tools-mcp/matchers.py tests/python/test_dev_tools_mcp.py docs/code-map.md
git commit -m "fix(tooling): EventBus scanners ignore JS comments (code-map + event_trace false positives)"
```

---

### Task 6: Remove dead PREFS_CHANGED, fix CLAUDE.md drift, add check-claude-md guard

**Files:**
- Modify: `js/event-bus.js` (remove lines 11 and 29)
- Modify: `js/inventory/_README.md` (line 11 — drop `PREFS_CHANGED` from the events list)
- Modify: `CLAUDE.md` (EventBus table, backend/frontend architecture rows, signals rule)
- Create: `scripts/check-claude-md.py`
- Modify: `scripts/verify.sh` (new guard step)
- Test: `tests/python/test_check_claude_md.py`

**Interfaces:**
- Produces: `scripts/check-claude-md.py` — exit 0 when every repo path referenced in backticks in CLAUDE.md exists, exit 1 listing missing paths. Checkable token = backtick content matching `^[A-Za-z0-9_\-./]+$` that contains `/` or ends with a known source extension; tokens under `data/`, `events/`, `memory/`, `~` are skipped (runtime/user files).

- [ ] **Step 1: Remove the dead event**

- `js/event-bus.js`: delete line 11 (`* PREFS_CHANGED: ...`) and line 29 (`PREFS_CHANGED: "preferences-changed",`).
- `js/inventory/_README.md` line 11: change `` `INVENTORY_LOADED` / `INVENTORY_UPDATED` / `PREFS_CHANGED` — `inv-events.js:setupEvents`; re-renders `` to `` `INVENTORY_LOADED` / `INVENTORY_UPDATED` — `inv-events.js:setupEvents`; re-renders (preferences via `preferencesSignal`) ``.
- Verify nothing references the enum member: `grep -rn "PREFS_CHANGED" js/` must return only the comment at `js/store.js:39` — rewrite that comment to `* Signal holding the preferences object (replaced the old PREFS_CHANGED EventBus event).`, then the grep must return nothing.

- [ ] **Step 2: Fix CLAUDE.md drift**

In `CLAUDE.md`:
1. Backend row: remove `` `price_ops.py` + `price_history.py` (pricing) `` and the root `` `generic_parts.py` (generic part CRUD + BOM resolution) `` entries (this logic lives in `domain/pricing.py` and `domain/generic_parts.py`, already listed in the `domain/` entry); change `inventory_api.py` description from "API facade, 74 methods" to "API facade (composition root; surface frozen by tests/python/test_api_surface.py)"; add `domain/part_registry.py` to the `domain/` list.
2. Frontend row: replace `` `css/styles.css`, 73 JS ES modules `` with `` `css/` (split stylesheets: tokens/, components/, panels/, buttons.css, tables.css, modals.css), JS ES modules ``.
3. EventBus table: delete the `PREFS_CHANGED` row. After the table's "Non-emitting setters" paragraph add: `**Signals vs EventBus:** preferences propagate via `preferencesSignal` in store.js (see js/signals.js), not EventBus. Rule: new cross-panel *state* uses signals; EventBus remains for discrete UI *events*.` (Verify the signals module's actual path with `ls js/signals.js` first; if it lives elsewhere, reference that path.)
4. Data row: add `part_registry.json`, `generic_parts.json`, `saved_searches.json` to the config list.

- [ ] **Step 3: Write the failing guard test**

Create `tests/python/test_check_claude_md.py`:

```python
"""check-claude-md.py — CLAUDE.md path references must exist."""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check-claude-md.py"


def _run(md_text, tmp_path):
    md = tmp_path / "CLAUDE.md"
    md.write_text(md_text, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--file", str(md), "--root", str(REPO)],
        capture_output=True, text=True,
    )


def test_existing_path_passes(tmp_path):
    assert _run("see `inventory_api.py` and `domain/inventory.py`", tmp_path).returncode == 0


def test_missing_path_fails(tmp_path):
    r = _run("see `css/styles.css`", tmp_path)
    assert r.returncode == 1
    assert "css/styles.css" in r.stdout


def test_runtime_paths_skipped(tmp_path):
    assert _run("see `data/digikey_cookies.json` and `events/part_events.csv`",
                tmp_path).returncode == 0


def test_non_path_tokens_skipped(tmp_path):
    assert _run("run `bash scripts/verify.sh --e2e` or set `DUBIS_WEBVIEW_PROFILE`",
                tmp_path).returncode == 0


def test_real_claude_md_passes():
    r = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True,
                       text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stdout + r.stderr
```

- [ ] **Step 4: Run to verify failure**

Run: `python -m pytest tests/python/test_check_claude_md.py -v`
Expected: FAIL — script does not exist.

- [ ] **Step 5: Implement `scripts/check-claude-md.py`**

```python
#!/usr/bin/env python
"""Guard: every repo path referenced in backticks in CLAUDE.md must exist.

CLAUDE.md is the first document every agent reads; a stale path sends agents
chasing files that no longer exist. Machine-generated docs have staleness
guards — this gives the hand-written one the same protection.

Usage: check-claude-md.py [--file CLAUDE.md] [--root .]
Exit 0 = all referenced paths exist; exit 1 = lists missing paths.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_TOKEN_RE = re.compile(r"`([^`\n]+)`")
_PATHISH_RE = re.compile(r"^[A-Za-z0-9_\-./]+$")
_SRC_EXTS = (".py", ".pyw", ".js", ".mjs", ".ts", ".css", ".html", ".json",
             ".sh", ".md", ".csv", ".yml", ".yaml")
_SKIP_PREFIXES = ("data/", "events/", "memory/", "~")


def _is_checkable(token: str) -> bool:
    if not _PATHISH_RE.match(token):
        return False  # commands, flags, code snippets
    if token.startswith(_SKIP_PREFIXES):
        return False  # runtime/user files not in the checkout
    if "/" in token:
        return True
    return token.endswith(_SRC_EXTS)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="CLAUDE.md")
    ap.add_argument("--root", default=".")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    text = Path(args.file).read_text(encoding="utf-8")

    missing = []
    for token in _TOKEN_RE.findall(text):
        token = token.strip()
        if not _is_checkable(token):
            continue
        if not (root / token.rstrip("/")).exists():
            missing.append(token)

    if missing:
        print("CLAUDE.md references paths that do not exist:")
        for t in sorted(set(missing)):
            print(f"  {t}")
        print("Fix CLAUDE.md (or extend the skip rules in scripts/check-claude-md.py).")
        return 1
    print("check-claude-md: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Iterate on the real CLAUDE.md: run `python scripts/check-claude-md.py` and fix any remaining stale references it finds (or, for legitimately-skipped classes of token, extend the skip rules) until it exits 0. Do not weaken `_is_checkable` to the point of vacuousness — `test_missing_path_fails` pins the core behavior.

- [ ] **Step 6: Wire into verify.sh**

In `scripts/verify.sh`, after the manifests step (line 75), add:

```bash
# 4b. claude-md
run_step "claude-md" "$PY" scripts/check-claude-md.py
```

- [ ] **Step 7: Run tests + JS gates**

Run: `python -m pytest tests/python/test_check_claude_md.py -v`
Expected: PASS.

Run: `npx eslint js/ && npx tsc --noEmit && npx vitest run --project core`
Expected: PASS (nothing referenced the removed enum member; if tsc/eslint flag a leftover reference, fix that call site to use `preferencesSignal`).

Run: `python scripts/gen-code-map.py && git diff --stat docs/code-map.md`
Expected: possibly-updated code map (commit it if changed).

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "chore(agent-hygiene): remove dead PREFS_CHANGED, fix CLAUDE.md drift, add check-claude-md guard"
```

---

### Task 7: Full verification and PR

**Files:** none new.

- [ ] **Step 1: Run the full gate**

Run: `bash scripts/verify.sh`
Expected: `PASS` summary — all steps green including the new `claude-md` step. Fix anything red before proceeding.

- [ ] **Step 2: Push and open the PR**

```bash
bash scripts/push-pr.sh --title "feat(foundations): part registry, durable generic parts, agent-hygiene guards (Phase 0)" --body "Phase 0 of docs/plans/2026-07-15-platform-architecture-design.md.

- Stable part identity: domain/part_registry.py — canonical key + PN aliases; enrichment no longer changes part identity
- Durable generic-parts overlay: data/generic_parts.json + load_into_db — manual groups/exclusions/preferred survive cache deletion and schema bumps
- part_events.csv demoted to audit trail; entity-store convention documented (docs/entity-store.md)
- EventBus scanners ignore JS comments (code-map/event_trace false positive fixed)
- Dead PREFS_CHANGED removed; CLAUDE.md drift fixed; new check-claude-md guard in verify.sh"
```

- [ ] **Step 3: Watch CI to green**

Run: `gh pr checks <number> --watch`
Expected: all required checks pass. If any fail, diagnose, fix, push, repeat — do not abandon with failing CI.
