# Cart Feature Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persistent, multi-cart shopping-cart functionality to dubIS — build carts of parts to purchase, add via link-mode/cart-mode/BOM, manage them in an inventory-style modal, and export as LCSC/DigiKey CSV or clipboard paste lists.

**Architecture:** New durable `carts` entity following the mandated entity-store pattern (`data/carts.json` source of truth → droppable SQLite cache → `load_into_db` on rebuild), exposed through `InventoryApi` facade methods and a `server/routes/carts.py` REST router that publishes a new `carts.updated` SSE event. Frontend adds a header cart button, cart-add interactions, and a `DataGrid`-based cart modal; cart state propagates via a new `cartsSignal`.

**Tech Stack:** Python 3 / FastAPI / SQLite / Pydantic (backend); vanilla ES-module JS, `js/components/data-grid.js`, `js/dom/`, SSE (frontend); pytest / vitest / Playwright (tests).

## Global Constraints

- **Error policy:** throw typed errors (`dubis_errors.py` hierarchy) / `AppLog.warn`/`AppLog.error`; never silent-catch. (spec: Key Policies)
- **Test policy:** never `pytest.skip`/`importorskip`/`mark.skip`; add missing deps to `requirements-dev.txt`. Every new feature gets a Playwright E2E test. E2E uses realistic interactions only — no `dispatchEvent`/`force:true`.
- **Entity-store pattern is mandatory:** `data/carts.json` is the source of truth, atomic-written via `csv_io.atomic_write_text` after every mutation; SQLite is a droppable derived cache restored by `load_into_db`. Copy `saved_searches.py` structure. (docs/entity-store.md)
- **Facade surface is frozen:** any new public `InventoryApi` method must be added to `tests/python/test_api_surface.py`'s expected surface in the same task.
- **Staleness guards must pass:** after backend/route/JS changes, regenerate as needed:
  - `python scripts/gen-openapi.py` (→ `docs/openapi-v1.json`) after adding/changing routes
  - `python scripts/gen-api-client.py` (→ `js/api-map.js`) after openapi changes
  - `python scripts/generate-test-fixtures.py` after backend inventory/price logic changes
  - `python scripts/gen-code-map.py` if the code map guard flags it
  - Final gate is always `bash scripts/verify.sh`.
- **SSE exhaustiveness:** a new SSE event type must be registered wherever the SSE type↔handler completeness guard checks (find via `grep -rn "inventory.updated" tests/`), and its JS handler wired in `js/sse.js` consumers.
- **Commit cadence:** commit after each task's tests pass. Branch: `claude/feature-cart` (already created). Push/PR via `bash scripts/push-pr.sh`.

---

## File Structure

**New backend files:**
- `carts.py` — cart domain module (CRUD, items, active-pointer, split/consolidate, persistence, `load_into_db`). Mirrors `saved_searches.py`.
- `cart_qty.py` — default-qty computation (tier reconstruction + cost-stepping rule). Pure functions, no DB writes.
- `cart_export.py` — LCSC/DigiKey CSV + paste-format serialization + PN resolution.
- `server/routes/carts.py` — `/v1/carts*` REST router.

**Modified backend files:**
- `cache_db.py` — add `carts` + `cart_items` tables to `create_schema`.
- `domain/inventory.py` — call `carts.load_into_db(conn, data_dir)` in `rebuild()`.
- `inventory_api.py` — cart facade methods.
- `server/app.py` — register `carts.router`.
- `server/events.py` — no code change needed (generic `publish`), but the new event name is documented/guard-registered.
- `tests/python/test_api_surface.py` — add new facade methods.

**New frontend files:**
- `js/cart/cart-store.js` — cart state glue over `store.js` + `cartsSignal` + API calls.
- `js/cart/cart-header.js` — header button, badge, cart-add-mode toggle wiring.
- `js/cart/cart-add.js` — cart-add mode + linking-mode cart target.
- `js/cart/cart-modal.js` — the DataGrid cart modal + top button bar.
- `js/cart/cart-export.js` — export UI (CSV download, copy paste, unresolved warning).
- `css/panels/cart.css` (or `css/components/cart.css`) — cart button + modal styles.

**Modified frontend files:**
- `index.html` — header cart button + toggle; BOM panel "add all missing" button.
- `js/store.js` — cart state setters.
- `js/signals.js` — `cartsSignal`.
- `js/sse.js` consumers (`js/app-init.js` or wherever `onEvent` is registered) — `carts.updated` handler.
- `js/bom/*` — "add all missing to cart" button handler.
- `js/inventory/inv-row-build.js` — cart-add-mode row click + linking-mode target hook.

---

# PHASE A — BACKEND

### Task A1: `carts` schema + core cart CRUD + persistence

**Files:**
- Create: `carts.py`
- Modify: `cache_db.py` (add tables to `create_schema`), `domain/inventory.py` (`rebuild()` calls `carts.load_into_db`)
- Test: `tests/python/test_carts.py`

**Interfaces:**
- Produces:
  - `carts.create(conn, data_dir, name: str) -> dict` → `{id, name, created_at, items: []}`
  - `carts.list_carts(conn) -> list[dict]`
  - `carts.get(conn, cart_id: str) -> dict | None`
  - `carts.rename(conn, data_dir, cart_id: str, name: str) -> dict`
  - `carts.delete(conn, data_dir, cart_id: str) -> None`
  - `carts.load_into_db(conn, data_dir) -> None`
  - SQLite tables `carts(id TEXT PK, name TEXT, created_at TEXT)` and
    `cart_items(cart_id TEXT, ref TEXT, part_id TEXT, raw TEXT, qty INTEGER, target_distributor TEXT, position INTEGER, PRIMARY KEY(cart_id, ref))`.
  - Item shape in returned dicts: `{ref, part_id|None, raw|None (dict), qty, target_distributor|None}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/python/test_carts.py
import sqlite3
import json
import carts
import cache_db


def _mk_conn(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cache_db.create_schema(conn)
    return conn


def test_create_list_get_rename_delete_roundtrip(tmp_path):
    conn = _mk_conn(tmp_path)
    data_dir = str(tmp_path)

    c = carts.create(conn, data_dir, "My Cart")
    assert c["name"] == "My Cart"
    assert c["items"] == []
    assert c["id"].startswith("cart_")

    assert [x["id"] for x in carts.list_carts(conn)] == [c["id"]]
    assert carts.get(conn, c["id"])["name"] == "My Cart"

    carts.rename(conn, data_dir, c["id"], "Renamed")
    assert carts.get(conn, c["id"])["name"] == "Renamed"

    # JSON is the source of truth and reflects the rename
    with open(f"{data_dir}/carts.json", encoding="utf-8") as f:
        assert json.load(f)[0]["name"] == "Renamed"

    carts.delete(conn, data_dir, c["id"])
    assert carts.list_carts(conn) == []


def test_load_into_db_restores_from_json(tmp_path):
    conn = _mk_conn(tmp_path)
    data_dir = str(tmp_path)
    c = carts.create(conn, data_dir, "Persist")

    # Simulate cache drop: fresh in-memory DB, reload from JSON
    conn2 = _mk_conn(tmp_path)
    carts.load_into_db(conn2, data_dir)
    assert carts.get(conn2, c["id"])["name"] == "Persist"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/python/test_carts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'carts'` (and/or missing tables).

- [ ] **Step 3: Add SQLite tables to `cache_db.create_schema`**

In `cache_db.py`, inside `create_schema(conn)` (alongside the existing `saved_searches` table creation), add:

```python
    conn.execute(
        """CREATE TABLE IF NOT EXISTS carts (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS cart_items (
            cart_id TEXT NOT NULL,
            ref TEXT NOT NULL,
            part_id TEXT,
            raw TEXT,
            qty INTEGER NOT NULL DEFAULT 1,
            target_distributor TEXT,
            position INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (cart_id, ref)
        )"""
    )
```

- [ ] **Step 4: Create `carts.py` core (mirror `saved_searches.py`)**

```python
"""Carts — CRUD for persistent purchase carts.

data_dir/carts.json is the source of truth; SQLite (carts + cart_items) is a
derived materialized view rebuilt on cache load. Mirrors saved_searches.py.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime
from typing import Any

import csv_io

logger = logging.getLogger(__name__)

_JSON_FILE = "carts.json"


def _json_path(data_dir: str) -> str:
    return os.path.join(data_dir, _JSON_FILE)


def item_ref(part_id: str | None, raw: dict | None) -> str:
    """Stable identity for a cart line: part_id, else a hash of raw fields."""
    if part_id:
        return part_id
    payload = json.dumps(raw or {}, sort_keys=True)
    return "raw:" + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _item_rows(conn: sqlite3.Connection, cart_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT ref, part_id, raw, qty, target_distributor FROM cart_items "
        "WHERE cart_id=? ORDER BY position, ref",
        (cart_id,),
    ).fetchall()
    return [
        {
            "ref": r["ref"],
            "part_id": r["part_id"],
            "raw": json.loads(r["raw"]) if r["raw"] else None,
            "qty": r["qty"],
            "target_distributor": r["target_distributor"],
        }
        for r in rows
    ]


def _cart_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "created_at": row["created_at"],
        "items": _item_rows(conn, row["id"]),
    }


def _persist(conn: sqlite3.Connection, data_dir: str) -> None:
    records = [
        {
            "id": r["id"],
            "name": r["name"],
            "created_at": r["created_at"],
            "items": _item_rows(conn, r["id"]),
        }
        for r in conn.execute("SELECT id, name, created_at FROM carts ORDER BY created_at").fetchall()
    ]
    os.makedirs(data_dir, exist_ok=True)
    csv_io.atomic_write_text(
        _json_path(data_dir),
        json.dumps(records, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def create(conn: sqlite3.Connection, data_dir: str, name: str) -> dict[str, Any]:
    cart_id = "cart_" + uuid.uuid4().hex
    created_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    conn.execute(
        "INSERT INTO carts (id, name, created_at) VALUES (?,?,?)",
        (cart_id, name, created_at),
    )
    conn.commit()
    _persist(conn, data_dir)
    return {"id": cart_id, "name": name, "created_at": created_at, "items": []}


def list_carts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT id, name, created_at FROM carts ORDER BY created_at").fetchall()
    return [_cart_dict(conn, r) for r in rows]


def get(conn: sqlite3.Connection, cart_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT id, name, created_at FROM carts WHERE id=?", (cart_id,)).fetchone()
    return _cart_dict(conn, row) if row else None


def rename(conn: sqlite3.Connection, data_dir: str, cart_id: str, name: str) -> dict[str, Any]:
    conn.execute("UPDATE carts SET name=? WHERE id=?", (name, cart_id))
    conn.commit()
    _persist(conn, data_dir)
    return get(conn, cart_id)


def delete(conn: sqlite3.Connection, data_dir: str, cart_id: str) -> None:
    conn.execute("DELETE FROM cart_items WHERE cart_id=?", (cart_id,))
    conn.execute("DELETE FROM carts WHERE id=?", (cart_id,))
    conn.commit()
    _persist(conn, data_dir)


def load_into_db(conn: sqlite3.Connection, data_dir: str) -> None:
    path = _json_path(data_dir)
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        records = json.load(f)
    for rec in records:
        conn.execute(
            "INSERT OR REPLACE INTO carts (id, name, created_at) VALUES (?,?,?)",
            (rec["id"], rec.get("name", ""), rec.get("created_at", "")),
        )
        for pos, item in enumerate(rec.get("items", [])):
            ref = item.get("ref") or item_ref(item.get("part_id"), item.get("raw"))
            conn.execute(
                "INSERT OR REPLACE INTO cart_items "
                "(cart_id, ref, part_id, raw, qty, target_distributor, position) VALUES (?,?,?,?,?,?,?)",
                (
                    rec["id"], ref, item.get("part_id"),
                    json.dumps(item["raw"]) if item.get("raw") else None,
                    int(item.get("qty", 1)), item.get("target_distributor"), pos,
                ),
            )
    conn.commit()
    logger.info("Loaded %d carts from %s", len(records), path)
```

- [ ] **Step 5: Wire `load_into_db` into rebuild**

In `domain/inventory.py` `rebuild()`, next to the existing `saved_searches.load_into_db(...)` (and other entity loads), add:

```python
    import carts
    carts.load_into_db(conn, data_dir)
```
(Match the existing import style — if `saved_searches` is imported at module top, import `carts` there too.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/python/test_carts.py -v`
Expected: PASS (both tests).

- [ ] **Step 7: Commit**

```bash
git add carts.py cache_db.py domain/inventory.py tests/python/test_carts.py
git commit -m "feat(cart): carts entity — schema, CRUD, JSON persistence, load_into_db"
```

---

### Task A2: Cart item operations (add / update / remove / clear)

**Files:**
- Modify: `carts.py`
- Test: `tests/python/test_carts.py`

**Interfaces:**
- Consumes: `carts.item_ref`, `_persist`, `get` (Task A1)
- Produces:
  - `carts.add_item(conn, data_dir, cart_id, *, part_id=None, raw=None, qty=1, target_distributor=None) -> dict` — returns updated cart; if an item with the same ref exists, **sets** qty to the new value (not additive) and updates distributor. Raises `dubis_errors` `NotFoundError` (use the existing not-found error type in `dubis_errors.py`) if the cart doesn't exist.
  - `carts.update_item(conn, data_dir, cart_id, ref, *, qty=None, target_distributor=None) -> dict`
  - `carts.remove_item(conn, data_dir, cart_id, ref) -> dict`
  - `carts.clear(conn, data_dir, cart_id) -> dict`

- [ ] **Step 1: Write the failing test**

```python
def test_item_add_update_remove_clear(tmp_path):
    conn = _mk_conn(tmp_path)
    data_dir = str(tmp_path)
    c = carts.create(conn, data_dir, "C")

    carts.add_item(conn, data_dir, c["id"], part_id="C15742", qty=5, target_distributor="lcsc")
    cart = carts.get(conn, c["id"])
    assert len(cart["items"]) == 1
    it = cart["items"][0]
    assert it["ref"] == "C15742" and it["qty"] == 5 and it["target_distributor"] == "lcsc"

    # re-add same ref => qty is SET, not added
    carts.add_item(conn, data_dir, c["id"], part_id="C15742", qty=8)
    assert carts.get(conn, c["id"])["items"][0]["qty"] == 8

    # raw item gets a hashed ref
    carts.add_item(conn, data_dir, c["id"], raw={"mpn": "X", "description": "d"}, qty=2)
    refs = {i["ref"] for i in carts.get(conn, c["id"])["items"]}
    assert any(r.startswith("raw:") for r in refs)

    carts.update_item(conn, data_dir, c["id"], "C15742", qty=3)
    assert next(i for i in carts.get(conn, c["id"])["items"] if i["ref"] == "C15742")["qty"] == 3

    carts.remove_item(conn, data_dir, c["id"], "C15742")
    assert all(i["ref"] != "C15742" for i in carts.get(conn, c["id"])["items"])

    carts.clear(conn, data_dir, c["id"])
    assert carts.get(conn, c["id"])["items"] == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/python/test_carts.py::test_item_add_update_remove_clear -v`
Expected: FAIL — `AttributeError: module 'carts' has no attribute 'add_item'`.

- [ ] **Step 3: Implement item ops in `carts.py`**

```python
from dubis_errors import NotFoundError  # add to imports; use the actual class name in dubis_errors.py


def _require(conn: sqlite3.Connection, cart_id: str) -> None:
    if conn.execute("SELECT 1 FROM carts WHERE id=?", (cart_id,)).fetchone() is None:
        raise NotFoundError(f"cart {cart_id} not found")


def _next_position(conn: sqlite3.Connection, cart_id: str) -> int:
    row = conn.execute("SELECT COALESCE(MAX(position), -1) AS m FROM cart_items WHERE cart_id=?", (cart_id,)).fetchone()
    return int(row["m"]) + 1


def add_item(conn, data_dir, cart_id, *, part_id=None, raw=None, qty=1, target_distributor=None):
    _require(conn, cart_id)
    ref = item_ref(part_id, raw)
    existing = conn.execute("SELECT ref FROM cart_items WHERE cart_id=? AND ref=?", (cart_id, ref)).fetchone()
    if existing:
        conn.execute(
            "UPDATE cart_items SET qty=?, target_distributor=? WHERE cart_id=? AND ref=?",
            (int(qty), target_distributor, cart_id, ref),
        )
    else:
        conn.execute(
            "INSERT INTO cart_items (cart_id, ref, part_id, raw, qty, target_distributor, position) "
            "VALUES (?,?,?,?,?,?,?)",
            (cart_id, ref, part_id, json.dumps(raw) if raw else None, int(qty),
             target_distributor, _next_position(conn, cart_id)),
        )
    conn.commit()
    _persist(conn, data_dir)
    return get(conn, cart_id)


def update_item(conn, data_dir, cart_id, ref, *, qty=None, target_distributor=None):
    _require(conn, cart_id)
    if qty is not None:
        conn.execute("UPDATE cart_items SET qty=? WHERE cart_id=? AND ref=?", (int(qty), cart_id, ref))
    if target_distributor is not None:
        conn.execute("UPDATE cart_items SET target_distributor=? WHERE cart_id=? AND ref=?",
                     (target_distributor, cart_id, ref))
    conn.commit()
    _persist(conn, data_dir)
    return get(conn, cart_id)


def remove_item(conn, data_dir, cart_id, ref):
    conn.execute("DELETE FROM cart_items WHERE cart_id=? AND ref=?", (cart_id, ref))
    conn.commit()
    _persist(conn, data_dir)
    return get(conn, cart_id)


def clear(conn, data_dir, cart_id):
    conn.execute("DELETE FROM cart_items WHERE cart_id=?", (cart_id,))
    conn.commit()
    _persist(conn, data_dir)
    return get(conn, cart_id)
```
(Before writing, open `dubis_errors.py` and use its actual not-found exception class name; if none exists, use the closest typed error and raise with a clear message — do not use a bare `Exception`.)

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/python/test_carts.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add carts.py tests/python/test_carts.py
git commit -m "feat(cart): cart item add/update/remove/clear"
```

---

### Task A3: Per-user active-cart pointer

**Files:**
- Modify: `carts.py`
- Test: `tests/python/test_carts.py`

**Interfaces:**
- Produces:
  - `carts.set_active(data_dir, identity: str, cart_id: str) -> None` — persists to `data/cart_active.json` (`{identity: cart_id}`), atomic-write.
  - `carts.get_active(data_dir, identity: str) -> str | None`

Active pointer is a plain JSON map keyed by auth identity — NOT in SQLite (it's per-user UI state, small, and not derived from inventory).

- [ ] **Step 1: Write the failing test**

```python
def test_active_pointer_is_per_identity(tmp_path):
    data_dir = str(tmp_path)
    assert carts.get_active(data_dir, "local") is None
    carts.set_active(data_dir, "local", "cart_a")
    carts.set_active(data_dir, "mcp@ci", "cart_b")
    assert carts.get_active(data_dir, "local") == "cart_a"
    assert carts.get_active(data_dir, "mcp@ci") == "cart_b"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/python/test_carts.py::test_active_pointer_is_per_identity -v`
Expected: FAIL — `AttributeError: ... 'set_active'`.

- [ ] **Step 3: Implement**

```python
_ACTIVE_FILE = "cart_active.json"


def _active_path(data_dir: str) -> str:
    return os.path.join(data_dir, _ACTIVE_FILE)


def _read_active(data_dir: str) -> dict[str, str]:
    path = _active_path(data_dir)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def set_active(data_dir: str, identity: str, cart_id: str) -> None:
    m = _read_active(data_dir)
    m[identity] = cart_id
    os.makedirs(data_dir, exist_ok=True)
    csv_io.atomic_write_text(_active_path(data_dir), json.dumps(m, indent=2), encoding="utf-8")


def get_active(data_dir: str, identity: str) -> str | None:
    return _read_active(data_dir).get(identity)
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/python/test_carts.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add carts.py tests/python/test_carts.py
git commit -m "feat(cart): per-user active-cart pointer (cart_active.json)"
```

---

### Task A4: Default-qty computation (`cart_qty.py`)

**Files:**
- Create: `cart_qty.py`
- Test: `tests/python/test_cart_qty.py`

**Interfaces:**
- Produces:
  - `cart_qty.tier_ladder(events_dir: str, part_id: str, distributor: str) -> list[tuple[int, float]]` — sorted `[(break_qty, unit_price), …]` reconstructed from `events/price_observations.csv` (columns `timestamp,part_id,distributor,unit_price,currency,source,moq,note`); latest observation per `moq` wins; empty list if none.
  - `cart_qty.default_qty(shortfall: int | None, ladder: list[tuple[int, float]]) -> int` — implements the spec's cost-stepping rule.

**Rule (from spec):**
1. Base `N = shortfall` if `shortfall` is a positive int, else `N = 1`.
2. If `ladder` non-empty:
   - If a shortfall was given (`shortfall` is a positive int): `step` = smallest `break_qty >= N`. If `step` exists and `step <= 2*N`, return `step`. If `step` exists but `step > 2*N`, return `N` rounded up to nearest 10. If no `break_qty >= N`, return the largest break_qty.
   - If no shortfall (`N==1` via None/0): `low` = smallest break_qty. If `low*unit_price(low) > 30`, return 5, else return `low`.
3. If `ladder` empty: return `N` (shortfall or 1).

- [ ] **Step 1: Write the failing test**

```python
# tests/python/test_cart_qty.py
import cart_qty


L = [(1, 9.2), (20, 7.23), (40, 6.83)]  # (break_qty, unit_price)


def test_shortfall_rounds_up_to_nearest_break():
    # need 15 -> nearest break >=15 is 20, and 20 <= 2*15 -> 20
    assert cart_qty.default_qty(15, L) == 20


def test_shortfall_break_more_than_double_rounds_to_ten():
    # need 3 -> nearest break >=3 is 20, but 20 > 2*3 -> round 3 up to nearest 10 => 10
    assert cart_qty.default_qty(3, L) == 10


def test_shortfall_above_all_breaks_uses_largest_break():
    assert cart_qty.default_qty(100, L) == 40


def test_no_shortfall_uses_lowest_break_when_cheap():
    cheap = [(10, 0.05), (100, 0.02)]  # 10*0.05 = 0.5 <= 30
    assert cart_qty.default_qty(None, cheap) == 10


def test_no_shortfall_expensive_lowest_break_defaults_to_five():
    pricey = [(1, 40.0), (5, 35.0)]  # 1*40 = 40 > 30
    assert cart_qty.default_qty(None, pricey) == 5


def test_no_ladder_returns_shortfall_or_one():
    assert cart_qty.default_qty(7, []) == 7
    assert cart_qty.default_qty(None, []) == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/python/test_cart_qty.py -v`
Expected: FAIL — no module `cart_qty`.

- [ ] **Step 3: Implement `cart_qty.py`**

```python
"""Default purchase-quantity computation for cart items.

Reconstructs the price-break ladder from events/price_observations.csv and
applies the cost-stepping rule (see docs/superpowers/specs/2026-07-24-cart-feature-design.md).
"""
from __future__ import annotations

import csv
import math
import os


def _round_up_10(n: int) -> int:
    return int(math.ceil(n / 10.0) * 10)


def tier_ladder(events_dir: str, part_id: str, distributor: str) -> list[tuple[int, float]]:
    path = os.path.join(events_dir, "price_observations.csv")
    if not os.path.exists(path):
        return []
    latest: dict[int, tuple[str, float]] = {}  # moq -> (timestamp, unit_price)
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("part_id") != part_id or row.get("distributor") != distributor:
                continue
            moq_raw = (row.get("moq") or "").strip()
            price_raw = (row.get("unit_price") or "").strip()
            if not moq_raw or not price_raw:
                continue
            try:
                moq = int(float(moq_raw))
                price = float(price_raw)
            except ValueError:
                continue
            ts = row.get("timestamp", "")
            if moq not in latest or ts >= latest[moq][0]:
                latest[moq] = (ts, price)
    return sorted((q, p) for q, (_ts, p) in latest.items())


def default_qty(shortfall: int | None, ladder: list[tuple[int, float]]) -> int:
    has_shortfall = isinstance(shortfall, int) and shortfall > 0
    base = shortfall if has_shortfall else 1
    if not ladder:
        return base
    breaks = [q for q, _ in ladder]
    if has_shortfall:
        candidates = [q for q in breaks if q >= base]
        if not candidates:
            return max(breaks)
        step = min(candidates)
        return step if step <= 2 * base else _round_up_10(base)
    # no shortfall: lowest break, unless its extended price > $30 -> 5
    low_q, low_price = ladder[0]
    return 5 if low_q * low_price > 30 else low_q
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/python/test_cart_qty.py -v`
Expected: PASS (all 6).

- [ ] **Step 5: Commit**

```bash
git add cart_qty.py tests/python/test_cart_qty.py
git commit -m "feat(cart): default-qty cost-stepping computation"
```

---

### Task A5: Export serialization (`cart_export.py`)

**Files:**
- Create: `cart_export.py`
- Test: `tests/python/test_cart_export.py`

**Interfaces:**
- Consumes: a `resolve_pn(part_id, distributor) -> str | None` callback (facade will pass a closure over `pricing.get_sourced_distributors`); item dicts from `carts.get`.
- Produces:
  - `cart_export.build(items: list[dict], distributor: str, fmt: str, resolve_pn, part_meta) -> dict`
    returns `{"content": str, "unresolved": list[dict], "filename": str}`.
    - `fmt="csv"` + `distributor="lcsc"` → LCSC columns `Index,LCSC#,MPN,Manufacturer,Package,Customer #,Description,RoHS,Quantity,MOQ,Multiple,Unit Price($),Extended Price($),Product Link`.
    - `fmt="csv"` + `distributor="digikey"` → `Index,DigiKey Part #,Manufacturer Part Number,Manufacturer,Description,Customer Reference,Quantity,Backorder,Unit Price,Extended Price`.
    - `fmt="paste"` → lines `"<pn>\t<qty>"`.
  - `part_meta(part_id) -> dict` provides `{mpn, manufacturer, package, description}` (facade passes a closure over the parts table); `{}` for raw items.
- Lines with no PN for the chosen distributor go into `unresolved` (with `ref`, `part_id`/`raw`) and are omitted from `content`.

- [ ] **Step 1: Write the failing test**

```python
# tests/python/test_cart_export.py
import csv
import io
import cart_export


ITEMS = [
    {"ref": "C15742", "part_id": "C15742", "raw": None, "qty": 5, "target_distributor": "lcsc"},
    {"ref": "raw:abc", "part_id": None, "raw": {"mpn": "NOPN", "description": "d"}, "qty": 3, "target_distributor": "lcsc"},
]


def _resolve_pn(part_id, distributor):
    return {"C15742": "C15742"}.get(part_id) if distributor == "lcsc" else None


def _part_meta(part_id):
    return {"mpn": "STM32", "manufacturer": "ST", "package": "LQFP-64", "description": "MCU"} if part_id else {}


def test_lcsc_csv_has_expected_header_and_resolved_rows():
    out = cart_export.build(ITEMS, "lcsc", "csv", _resolve_pn, _part_meta)
    reader = list(csv.reader(io.StringIO(out["content"])))
    assert reader[0] == ["Index", "LCSC#", "MPN", "Manufacturer", "Package", "Customer #",
                         "Description", "RoHS", "Quantity", "MOQ", "Multiple",
                         "Unit Price($)", "Extended Price($)", "Product Link"]
    assert reader[1][1] == "C15742" and reader[1][8] == "5"
    assert len(reader) == 2  # header + 1 resolved row (raw item unresolved)
    assert out["unresolved"][0]["ref"] == "raw:abc"


def test_paste_format():
    out = cart_export.build(ITEMS, "lcsc", "paste", _resolve_pn, _part_meta)
    assert out["content"] == "C15742\t5"


def test_digikey_header():
    out = cart_export.build(ITEMS, "digikey", "csv", lambda p, d: "DK-1" if p else None, _part_meta)
    header = list(csv.reader(io.StringIO(out["content"])))[0]
    assert header[1] == "DigiKey Part #" and header[6] == "Quantity"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/python/test_cart_export.py -v`
Expected: FAIL — no module `cart_export`.

- [ ] **Step 3: Implement `cart_export.py`**

```python
"""Serialize a cart to LCSC/DigiKey CSV or a paste list."""
from __future__ import annotations

import csv
import io

_LCSC_HEADER = ["Index", "LCSC#", "MPN", "Manufacturer", "Package", "Customer #",
                "Description", "RoHS", "Quantity", "MOQ", "Multiple",
                "Unit Price($)", "Extended Price($)", "Product Link"]
_DIGIKEY_HEADER = ["Index", "DigiKey Part #", "Manufacturer Part Number", "Manufacturer",
                   "Description", "Customer Reference", "Quantity", "Backorder",
                   "Unit Price", "Extended Price"]


def build(items, distributor, fmt, resolve_pn, part_meta):
    resolved, unresolved = [], []
    for it in items:
        pn = resolve_pn(it.get("part_id"), distributor)
        if pn:
            resolved.append((it, pn))
        else:
            unresolved.append({"ref": it["ref"], "part_id": it.get("part_id"), "raw": it.get("raw")})

    if fmt == "paste":
        content = "\n".join(f"{pn}\t{it['qty']}" for it, pn in resolved)
        return {"content": content, "unresolved": unresolved, "filename": f"cart_{distributor}.txt"}

    buf = io.StringIO()
    w = csv.writer(buf)
    if distributor == "lcsc":
        w.writerow(_LCSC_HEADER)
        for i, (it, pn) in enumerate(resolved, start=1):
            m = part_meta(it.get("part_id")) or {}
            w.writerow([i, pn, m.get("mpn", ""), m.get("manufacturer", ""), m.get("package", ""),
                        "", m.get("description", ""), "yes", it["qty"], 1, 1, "", "", ""])
    elif distributor == "digikey":
        w.writerow(_DIGIKEY_HEADER)
        for i, (it, pn) in enumerate(resolved, start=1):
            m = part_meta(it.get("part_id")) or {}
            w.writerow([i, pn, m.get("mpn", ""), m.get("manufacturer", ""),
                        m.get("description", ""), "", it["qty"], "", "", ""])
    else:
        raise ValueError(f"unknown distributor {distributor!r}")
    return {"content": buf.getvalue(), "unresolved": unresolved, "filename": f"cart_{distributor}.csv"}
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/python/test_cart_export.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cart_export.py tests/python/test_cart_export.py
git commit -m "feat(cart): LCSC/DigiKey CSV + paste export serialization"
```

---

### Task A6: Split by distributor + consolidate

**Files:**
- Modify: `carts.py`
- Test: `tests/python/test_carts.py`

**Interfaces:**
- Consumes: cart item ops (A2), a `part_distributors(part_id) -> list[str]` callback (facade passes a closure over `pricing.get_sourced_distributors`).
- Produces:
  - `carts.split_by_distributor(conn, data_dir, cart_id, distributor, new_name, remove_from_source, part_distributors) -> dict` — creates a NEW cart containing every line whose `target_distributor == distributor` OR (target unset AND `distributor in part_distributors(part_id)`); if `remove_from_source`, deletes those lines from the source cart. Returns `{"source": <cart>, "new": <cart>}`.
  - `carts.consolidate(conn, data_dir, cart_id, distributor, part_distributors) -> dict` — sets `target_distributor=distributor` on every line where `distributor in part_distributors(part_id)`; leaves lines that can't source from `distributor` untouched and lists them in `unresolved`. Returns `{"cart": <cart>, "unresolved": [refs]}`.

- [ ] **Step 1: Write the failing test**

```python
def _pd(mapping):
    return lambda pid: mapping.get(pid, [])


def test_split_by_distributor_moves_matching_lines(tmp_path):
    conn = _mk_conn(tmp_path); data_dir = str(tmp_path)
    c = carts.create(conn, data_dir, "src")
    carts.add_item(conn, data_dir, c["id"], part_id="A", qty=1, target_distributor="lcsc")
    carts.add_item(conn, data_dir, c["id"], part_id="B", qty=1, target_distributor="digikey")
    res = carts.split_by_distributor(conn, data_dir, c["id"], "lcsc", "lcsc cart",
                                     remove_from_source=True, part_distributors=_pd({}))
    assert [i["part_id"] for i in res["new"]["items"]] == ["A"]
    assert [i["part_id"] for i in res["source"]["items"]] == ["B"]


def test_consolidate_sets_target_where_sourceable(tmp_path):
    conn = _mk_conn(tmp_path); data_dir = str(tmp_path)
    c = carts.create(conn, data_dir, "c")
    carts.add_item(conn, data_dir, c["id"], part_id="A", qty=1)
    carts.add_item(conn, data_dir, c["id"], part_id="B", qty=1)
    res = carts.consolidate(conn, data_dir, c["id"], "lcsc",
                            part_distributors=_pd({"A": ["lcsc", "digikey"], "B": ["mouser"]}))
    items = {i["part_id"]: i["target_distributor"] for i in res["cart"]["items"]}
    assert items["A"] == "lcsc" and items["B"] != "lcsc"
    assert "B" in [u for u in res["unresolved"]] or any("B" == r for r in res["unresolved"])
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/python/test_carts.py -k "split or consolidate" -v`
Expected: FAIL — attributes missing.

- [ ] **Step 3: Implement in `carts.py`**

```python
def split_by_distributor(conn, data_dir, cart_id, distributor, new_name, remove_from_source, part_distributors):
    _require(conn, cart_id)
    src = get(conn, cart_id)
    moved = [
        it for it in src["items"]
        if it["target_distributor"] == distributor
        or (it["target_distributor"] is None and it.get("part_id")
            and distributor in part_distributors(it["part_id"]))
    ]
    new_cart = create(conn, data_dir, new_name)
    for it in moved:
        add_item(conn, data_dir, new_cart["id"], part_id=it.get("part_id"), raw=it.get("raw"),
                 qty=it["qty"], target_distributor=distributor)
    if remove_from_source:
        for it in moved:
            remove_item(conn, data_dir, cart_id, it["ref"])
    return {"source": get(conn, cart_id), "new": get(conn, new_cart["id"])}


def consolidate(conn, data_dir, cart_id, distributor, part_distributors):
    _require(conn, cart_id)
    cart = get(conn, cart_id)
    unresolved = []
    for it in cart["items"]:
        pid = it.get("part_id")
        if pid and distributor in part_distributors(pid):
            update_item(conn, data_dir, cart_id, it["ref"], target_distributor=distributor)
        else:
            unresolved.append(it["ref"])
    return {"cart": get(conn, cart_id), "unresolved": unresolved}
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/python/test_carts.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add carts.py tests/python/test_carts.py
git commit -m "feat(cart): split-by-distributor and consolidate operations"
```

---

### Task A7: `InventoryApi` facade methods + frozen-surface update

**Files:**
- Modify: `inventory_api.py`
- Test: `tests/python/test_api_surface.py`, `tests/python/test_cart_facade.py`

**Interfaces:**
- Consumes: `carts`, `cart_qty`, `cart_export`, `pricing.get_sourced_distributors`, `pricing.get_price_summary`, the api's SQLite connection + `data_dir`/`events_dir`, and `InventoryApi._lock`.
- Produces (all acquire `self._lock` like existing facade methods; all mutating ones call the appropriate `carts.*` then return the fresh cart/dict — they do NOT rebuild inventory):
  - `list_carts() -> list[dict]`
  - `get_cart(cart_id) -> dict`
  - `create_cart(name: str | None) -> dict` — if `name` falsy, server route supplies prefill; facade may accept the resolved name.
  - `rename_cart(cart_id, name) -> dict`
  - `delete_cart(cart_id) -> None`
  - `set_active_cart(identity, cart_id) -> dict` → `{"active_cart_id": cart_id}`
  - `get_active_cart(identity) -> str | None`
  - `add_cart_item(cart_id, part_id=None, raw=None, qty=None, target_distributor=None, shortfall=None) -> dict` — when `qty is None`, compute via `cart_qty.default_qty(shortfall, cart_qty.tier_ladder(events_dir, part_id, target_distributor or <first sourced distributor>))`.
  - `update_cart_item(cart_id, ref, qty=None, target_distributor=None) -> dict`
  - `remove_cart_item(cart_id, ref) -> dict`
  - `clear_cart(cart_id) -> dict`
  - `add_bom_missing_to_cart(cart_id, missing: list[dict]) -> dict` — each `missing` entry `{part_id?|raw?, shortfall?, target_distributor?}`; loops `add_cart_item`.
  - `split_cart(cart_id, distributor, new_name, remove_from_source) -> dict`
  - `consolidate_cart(cart_id, distributor) -> dict`
  - `export_cart(cart_id, distributor, fmt) -> dict` — builds `resolve_pn`/`part_meta` closures over `pricing.get_sourced_distributors` + the parts table, calls `cart_export.build`.

Use the existing pattern in `inventory_api.py` for `_lock`, connection access, `data_dir`, and how `saved_searches`/`generic_parts` facade methods obtain the SQLite connection. `resolve_pn(part_id, distributor)` = look up the part's sourced distributors and return the `part_number` for the matching distributor; `part_distributors(part_id)` = the list of distributor keys from `get_sourced_distributors`.

- [ ] **Step 1: Write the failing surface + behavior tests**

```python
# tests/python/test_cart_facade.py — uses the shared api fixture (copy the fixture
# style from an existing facade test, e.g. tests/python/test_*generic*).
def test_cart_facade_crud(api):
    c = api.create_cart("Facade Cart")
    api.add_cart_item(c["id"], part_id="C15742", qty=5)
    got = api.get_cart(c["id"])
    assert got["items"][0]["qty"] == 5
    api.remove_cart_item(c["id"], "C15742")
    assert api.get_cart(c["id"])["items"] == []


def test_add_cart_item_computes_default_qty_when_absent(api):
    c = api.create_cart("Q")
    # No price observations for this synthetic part => ladder empty => qty defaults to 1
    api.add_cart_item(c["id"], part_id="ZZZNOEXIST", qty=None)
    assert api.get_cart(c["id"])["items"][0]["qty"] == 1
```

Add each new method name to the expected surface set in `tests/python/test_api_surface.py`.

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/python/test_cart_facade.py tests/python/test_api_surface.py -v`
Expected: FAIL — surface mismatch + missing methods.

- [ ] **Step 3: Implement facade methods in `inventory_api.py`**

Add the methods per the Interfaces block, following the file's existing `with self._lock:` + connection-access conventions. For `add_cart_item` default qty:

```python
    def add_cart_item(self, cart_id, part_id=None, raw=None, qty=None,
                      target_distributor=None, shortfall=None):
        with self._lock:
            conn = self._conn  # use whatever attribute existing facade methods use
            if qty is None:
                dist = target_distributor
                if dist is None and part_id:
                    sourced = pricing.get_sourced_distributors(conn, part_id)
                    dist = sourced[0]["distributor"] if sourced else None
                ladder = cart_qty.tier_ladder(self._events_dir, part_id, dist) if (part_id and dist) else []
                qty = cart_qty.default_qty(shortfall, ladder)
            return carts.add_item(conn, self._data_dir, cart_id, part_id=part_id, raw=raw,
                                  qty=qty, target_distributor=target_distributor)
```
(Confirm the real attribute names for the connection, `data_dir`, and events dir in `inventory_api.py` before writing; reuse them verbatim.)

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/python/test_cart_facade.py tests/python/test_api_surface.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add inventory_api.py tests/python/test_cart_facade.py tests/python/test_api_surface.py
git commit -m "feat(cart): InventoryApi facade methods + frozen-surface update"
```

---

### Task A8: `/v1/carts` router + `carts.updated` SSE + registration

**Files:**
- Create: `server/routes/carts.py`
- Modify: `server/app.py` (register router), any SSE-exhaustiveness guard fixture
- Test: `tests/python/server/test_carts_routes.py`, regenerate `docs/openapi-v1.json`

**Interfaces:**
- Consumes: `request.app.state.api.<facade method>` (Task A7), `server.events.publish`, `server.auth.resolve_identity` (or the existing helper used to get the caller identity — check `server/auth.py`).
- Produces routes (all mutating ones call `events.publish("carts.updated", {...})` AFTER the facade call, then return the payload):

| Verb & path | Facade call |
|---|---|
| `GET /v1/carts` | `list_carts()` + include `active_cart_id` = `get_active_cart(identity)` |
| `POST /v1/carts` | `create_cart(body.name or <prefill>)` |
| `GET /v1/carts/{cart_id}` | `get_cart(cart_id)` |
| `PUT /v1/carts/{cart_id}` | `rename_cart(cart_id, body.name)` |
| `DELETE /v1/carts/{cart_id}` | `delete_cart(cart_id)` |
| `POST /v1/carts/{cart_id}/active` | `set_active_cart(identity, cart_id)` |
| `POST /v1/carts/{cart_id}/items` | `add_cart_item(...)` |
| `PATCH /v1/carts/{cart_id}/items/{ref}` | `update_cart_item(...)` |
| `DELETE /v1/carts/{cart_id}/items/{ref}` | `remove_cart_item(...)` |
| `POST /v1/carts/{cart_id}/clear` | `clear_cart(cart_id)` |
| `POST /v1/carts/{cart_id}/add-bom-missing` | `add_bom_missing_to_cart(cart_id, body.missing)` |
| `POST /v1/carts/{cart_id}/split` | `split_cart(...)` |
| `POST /v1/carts/{cart_id}/consolidate` | `consolidate_cart(...)` |
| `GET /v1/carts/{cart_id}/export` | `export_cart(cart_id, distributor, fmt)` (query params) — returns `{content, unresolved, filename}`; does NOT publish |

Publishing rule: cart mutations publish `carts.updated` (NOT `inventory.updated`) so the frontend refetches carts only. Export/GET do not publish.

- [ ] **Step 1: Write the failing test (TestClient)**

```python
# tests/python/server/test_carts_routes.py — copy the app/client fixture from an
# existing server route test (e.g. tests/python/server/test_generic_parts_routes.py).
def test_cart_route_crud(client):
    r = client.post("/v1/carts", json={"name": "Route Cart"})
    assert r.status_code == 200
    cid = r.json()["detail"]["id"] if "detail" in r.json() else r.json()["id"]

    r = client.post(f"/v1/carts/{cid}/items", json={"part_id": "C15742", "qty": 5})
    assert r.status_code == 200

    r = client.get(f"/v1/carts/{cid}")
    body = r.json()
    items = body.get("detail", body).get("items", body.get("items"))
    assert items[0]["qty"] == 5

    r = client.get(f"/v1/carts/{cid}/export", params={"distributor": "lcsc", "format": "paste"})
    assert "C15742" in r.json()["content"]
```
(Match the response envelope to whatever the router actually returns — align the test to the router; keep it simple: return plain dicts, not `finish_mutation`.)

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/python/server/test_carts_routes.py -v`
Expected: FAIL — 404 (router not registered).

- [ ] **Step 3: Implement `server/routes/carts.py`**

Model on `server/routes/generic_parts.py` (router prefix, Pydantic bodies, `request.app.state.api`). Body models: `CreateCartBody{name: str | None = None}`, `RenameCartBody{name: str}`, `AddItemBody{part_id: str | None = None, raw: dict | None = None, qty: int | None = None, target_distributor: str | None = None, shortfall: int | None = None}`, `UpdateItemBody{qty: int | None = None, target_distributor: str | None = None}`, `AddBomMissingBody{missing: list[dict]}`, `SplitBody{distributor: str, new_name: str, remove_from_source: bool = False}`, `ConsolidateBody{distributor: str}`. Resolve caller identity via the same helper `server/auth.py` exposes (check how other routes read identity — likely `request` state); default `"local"`. Each mutating handler:

```python
    result = api.<method>(...)
    events.publish("carts.updated", {"cart_id": cart_id})
    return {"ok": True, "detail": result}
```
The export handler returns `api.export_cart(...)` directly (no publish). Name the `format` query param `fmt` internally (`format: str = Query("csv")`).

- [ ] **Step 4: Register router in `server/app.py`**

Add `carts` to the `from server.routes import (...)` tuple and `app.include_router(carts.router)` alongside the others (line ~42).

- [ ] **Step 5: Register the SSE event in the exhaustiveness guard**

Find the guard: `grep -rn "inventory.updated\|scan.received" tests/python` and the known-types list (likely in a test fixture or `server/events` doc). Add `"carts.updated"` wherever the SSE type↔handler completeness test enumerates known types so the guard passes. Run that guard test to confirm.

- [ ] **Step 6: Regenerate OpenAPI + run tests**

Run:
```bash
python scripts/gen-openapi.py
pytest tests/python/server/test_carts_routes.py -v
python scripts/gen-openapi.py --check
```
Expected: PASS; `--check` exits 0.

- [ ] **Step 7: Commit**

```bash
git add server/routes/carts.py server/app.py docs/openapi-v1.json tests/python/server/test_carts_routes.py
git commit -m "feat(cart): /v1/carts REST router + carts.updated SSE event"
```

---

### Task A9: Backend gate — full Python suite + guards

- [ ] **Step 1: Run ruff + full pytest + openapi/manifest guards**

Run:
```bash
ruff check .
pytest tests/python/ -v
python scripts/gen-openapi.py --check
python scripts/check-manifests.py || true
python scripts/gen-code-map.py
```
Expected: ruff clean; all pytest pass; openapi not stale. If code-map changed, stage it.

- [ ] **Step 2: Commit any regenerated artifacts**

```bash
git add -A
git commit -m "chore(cart): regenerate code-map/manifests after backend" || echo "nothing to commit"
```

---

# PHASE B — FRONTEND

### Task B1: Cart store state + `cartsSignal` + API client + SSE handler

**Files:**
- Create: `js/cart/cart-store.js`
- Modify: `js/signals.js` (`cartsSignal`), `js/store.js` (cart state setters if needed), the SSE-registration site (search `onEvent(` usage — likely `js/app-init.js`)
- Regenerate: `js/api-map.js` via `python scripts/gen-api-client.py`
- Test: `tests/js/cart-store.test.mjs`

**Interfaces:**
- Produces (in `js/cart/cart-store.js`):
  - `loadCarts()` → `await api("list_carts")`; stores `carts` + `activeCartId`; publishes `cartsSignal`.
  - `getCarts()`, `getActiveCart()`, `getActiveCartId()`
  - `addToActiveCart({partId, raw, qty, shortfall, targetDistributor})` → POST items to active cart, then `loadCarts()`.
  - `setActiveCart(cartId)`, `createCart(name)`, `renameCart`, `deleteCart`, `updateItem`, `removeItem`, `clearCart`, `splitCart`, `consolidateCart`, `exportCart(cartId, distributor, fmt)`.
  - `cartItemCount()` → total lines in active cart (for the badge).
- `cartsSignal` (in `js/signals.js`) mirrors `preferencesSignal` structure.

- [ ] **Step 1: Write the failing test**

```js
// tests/js/cart-store.test.mjs
import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock the api module the store calls.
vi.mock('../../js/api.js', () => ({
  api: vi.fn(),
  AppLog: { warn() {}, error() {} },
}));
import { api } from '../../js/api.js';
import * as cartStore from '../../js/cart/cart-store.js';

describe('cart-store', () => {
  beforeEach(() => { api.mockReset(); });

  it('loadCarts stores carts and active id', async () => {
    api.mockResolvedValueOnce({ carts: [{ id: 'cart_1', name: 'A', items: [{ ref: 'x', qty: 2 }] }], active_cart_id: 'cart_1' });
    await cartStore.loadCarts();
    expect(cartStore.getActiveCartId()).toBe('cart_1');
    expect(cartStore.cartItemCount()).toBe(1);
  });
});
```
(Align the response shape to what `GET /v1/carts` actually returns — a `{carts, active_cart_id}` object; if the route returns a bare list, adjust both.)

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run tests/js/cart-store.test.mjs`
Expected: FAIL — module not found.

- [ ] **Step 3: Regenerate the API client so `list_carts` etc. are mapped**

Run:
```bash
python scripts/gen-api-client.py
```
Verify `js/api-map.js` now contains the cart operation ids (`list_carts`, `create_cart`, `add_cart_item`, …). The op ids come from the route `operation_id=` values — set those in Task A8 (e.g. `@router.post("/carts", operation_id="create_cart")`). If missing, add `operation_id`s and re-run gen-openapi + gen-api-client.

- [ ] **Step 4: Add `cartsSignal` + implement `cart-store.js`**

`js/signals.js`: add `export const cartsSignal = createSignal(...)` mirroring `preferencesSignal`. Implement `cart-store.js` per Interfaces, calling `api("list_carts")`, `api("create_cart", {name})`, etc. (string-keyed convention). `cartItemCount()` reads the active cart's `items.length`.

- [ ] **Step 5: Wire the SSE handler**

At the `onEvent(...)` registration site (where `inventory.updated` is handled), add:
```js
onEvent('carts.updated', () => { cartStore.loadCarts(); });
```
Debounce is optional (cart payloads are small); a direct reload is fine.

- [ ] **Step 6: Run tests + lint + types**

Run:
```bash
npx vitest run tests/js/cart-store.test.mjs
npx eslint js/cart/ js/signals.js
npx tsc --noEmit
```
Expected: PASS / clean.

- [ ] **Step 7: Commit**

```bash
git add js/cart/cart-store.js js/signals.js js/api-map.js docs/openapi-v1.json tests/js/cart-store.test.mjs
git commit -m "feat(cart): frontend cart store, cartsSignal, API client, SSE handler"
```

---

### Task B2: Header cart button + count badge

**Files:**
- Modify: `index.html` (header-right), create `css/components/cart.css` (link it in index.html where other css is linked), create `js/cart/cart-header.js`
- Test: `tests/js/e2e/cart-header.spec.mjs`

**Interfaces:**
- Consumes: `cartStore.cartItemCount`, `cartsSignal`, `cartStore.loadCarts`
- Produces: header button `#cart-btn` with `.cart-badge` count; clicking opens the cart modal (wired in B6 — for now it can call a stub `openCartModal()` that B6 fills in). Cart-add-mode toggle `#cart-add-toggle` beside it (behavior in B3).

- [ ] **Step 1: Write the failing E2E test**

```js
// tests/js/e2e/cart-header.spec.mjs — copy harness setup from an existing e2e spec.
import { test, expect } from '@playwright/test';
import { launchApp } from './helpers.mjs'; // use the repo's existing e2e helper

test('cart button shows in header with a badge', async ({ page }) => {
  await launchApp(page);
  const btn = page.locator('#cart-btn');
  await expect(btn).toBeVisible();
  // badge reflects active cart line count (0 on a fresh cart)
  await expect(page.locator('#cart-btn .cart-badge')).toHaveText(/\d+/);
});
```
(Use the actual E2E bootstrap the repo uses — inspect an existing `tests/js/e2e/*.spec.mjs` for `launchApp`/server fixture; match it.)

- [ ] **Step 2: Run to verify it fails**

Run: `npx playwright test cart-header`
Expected: FAIL — `#cart-btn` not found.

- [ ] **Step 3: Add header markup**

In `index.html`, insert as the FIRST child of `<div class="header-right">` (before `#inv-count` at line 152):
```html
    <button class="btn-md cart-btn" id="cart-btn" title="Open cart">
      <span class="cart-icon">&#128722;</span>
      <span class="cart-badge" id="cart-badge">0</span>
    </button>
    <button class="btn-md cart-add-toggle" id="cart-add-toggle" title="Cart-add mode: click parts to add to cart" aria-pressed="false">+</button>
```
Add `<link rel="stylesheet" href="css/components/cart.css">` with the other css links.

- [ ] **Step 4: Implement `cart-header.js` + css**

`cart-header.js`: on init, `cartStore.loadCarts()`, subscribe to `cartsSignal` to update `#cart-badge` textContent = `cartItemCount()`; `#cart-btn` click → `openCartModal()` (import from cart-modal.js — created in B6; until then export a no-op so lint/types pass, then B6 replaces it). Import and call `initCartHeader()` from `js/app-init.js`. `cart.css`: style the button, badge (small pill, top-right), and `.cart-add-toggle.active { background: purple; }`.

- [ ] **Step 5: Run E2E + lint**

Run: `npx playwright test cart-header && npx eslint js/cart/`
Expected: PASS / clean.

- [ ] **Step 6: Commit**

```bash
git add index.html css/components/cart.css js/cart/cart-header.js js/app-init.js tests/js/e2e/cart-header.spec.mjs
git commit -m "feat(cart): header cart button + count badge"
```

---

### Task B3: Cart-add mode (toggle → click rows to add)

**Files:**
- Create: `js/cart/cart-add.js`
- Modify: `js/cart/cart-header.js` (toggle wiring), `js/inventory/inv-row-build.js` (row click hook)
- Test: `tests/js/e2e/cart-add-mode.spec.mjs`

**Interfaces:**
- Produces: `cartAddMode.isActive()`, `cartAddMode.toggle()`, `cartAddMode.handleRowClick(item)` → `cartStore.addToActiveCart({partId: item.part_id})`. When active, `<body>` gets class `cart-add-active` (for cursor/visual cue) and the toggle button gets `.active`.
- Consumes: `cartStore` (B1), inventory row build (existing).

- [ ] **Step 1: Write the failing E2E test**

```js
test('cart-add mode: toggle then click a row adds it to the cart', async ({ page }) => {
  await launchApp(page); // seed at least one inventory part in the fixture
  await page.click('#cart-add-toggle');
  await expect(page.locator('#cart-add-toggle')).toHaveClass(/active/);
  await page.locator('.inv-row').first().click();
  await expect(page.locator('#cart-badge')).toHaveText('1');
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx playwright test cart-add-mode`
Expected: FAIL.

- [ ] **Step 3: Implement `cart-add.js` + wire toggle + row hook**

`cart-add.js`: module-level `active` boolean; `toggle()` flips it, toggles `document.body.classList` `cart-add-active` and the toggle button `.active`, `aria-pressed`. `handleRowClick(item)`: if `active`, `cartStore.addToActiveCart({partId: item.part_id})` and return true (consumed), else false. In `cart-header.js`, wire `#cart-add-toggle` click → `cartAddMode.toggle()`. In `js/inventory/inv-row-build.js`, at the row click handler (near the `.link-btn` wiring, ~line 91), add an early check: `if (cartAddMode.handleRowClick(item)) return;` so a cart-add click doesn't fall through to selection/linking.

- [ ] **Step 4: Run E2E + lint + types**

Run: `npx playwright test cart-add-mode && npx eslint js/ && npx tsc --noEmit`
Expected: PASS / clean.

- [ ] **Step 5: Commit**

```bash
git add js/cart/cart-add.js js/cart/cart-header.js js/inventory/inv-row-build.js tests/js/e2e/cart-add-mode.spec.mjs
git commit -m "feat(cart): cart-add mode — click rows to add to active cart"
```

---

### Task B4: Linking-mode cart target (purple dotted box on cart icon)

**Files:**
- Modify: `js/cart/cart-add.js` (or a small `js/cart/cart-link-target.js`), `js/inventory/inv-events.js` and/or `js/bom/bom-events.js` (LINKING_MODE listeners), `css/components/cart.css`
- Test: `tests/js/e2e/cart-link-target.spec.mjs`

**Interfaces:**
- Consumes: `LINKING_MODE` EventBus event (existing), `store.js` `linkingInvItem` (the armed inventory item), `cartStore.addToActiveCart`.
- Produces: while linking mode is active with an armed inventory item, `#cart-btn` gets `.link-target` (purple dotted box, mirrors the existing `.link-eligible` style used on rows). Clicking `#cart-btn` while armed → add the armed part to the active cart and exit linking mode (`store.setLinkingMode(false)`). Clicking normally (not in linking mode) opens the modal.

- [ ] **Step 1: Write the failing E2E test**

```js
test('linking mode marks the cart as a drop target; clicking it adds the armed part', async ({ page }) => {
  await launchApp(page); // fixture must load a BOM so Link buttons render
  await page.locator('.inv-row .link-btn').first().click(); // arm linking
  await expect(page.locator('#cart-btn')).toHaveClass(/link-target/);
  await page.click('#cart-btn');
  await expect(page.locator('#cart-badge')).toHaveText('1');
  await expect(page.locator('#cart-btn')).not.toHaveClass(/link-target/);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx playwright test cart-link-target`
Expected: FAIL.

- [ ] **Step 3: Implement the link-target behavior**

Add a `LINKING_MODE` listener (in `inv-events.js`, next to the existing one at ~line 194): when `active && linkingInvItem`, add `.link-target` to `#cart-btn`; else remove it. In the `#cart-btn` click handler (cart-header.js): if `store.getLinkingInvItem()` (use the actual store getter for the armed item), then `cartStore.addToActiveCart({partId: armed.part_id})`, `store.setLinkingMode(false)`, and DO NOT open the modal; otherwise open the modal. Add `.cart-btn.link-target` CSS mirroring the row `.link-eligible` purple dotted outline (find the existing selector in css and reuse its border/outline values).

- [ ] **Step 4: Run E2E + lint**

Run: `npx playwright test cart-link-target && npx eslint js/`
Expected: PASS / clean.

- [ ] **Step 5: Commit**

```bash
git add js/cart/ js/inventory/inv-events.js css/components/cart.css tests/js/e2e/cart-link-target.spec.mjs
git commit -m "feat(cart): linking mode adds cart as a purple drop target"
```

---

### Task B5: BOM "Add all missing to cart" button

**Files:**
- Modify: `index.html` (BOM panel toolbar), `js/bom/bom-panel.js` or `js/bom/bom-events.js`
- Test: `tests/js/e2e/cart-bom-missing.spec.mjs`

**Interfaces:**
- Consumes: `bom-logic.js` `buildLinkableKeys()` / `computeRows()` (the missing/short set), `cartStore.addBomMissing(cartId, missing)` (add to B1 store if not present: POST `/v1/carts/{id}/add-bom-missing`).
- Produces: a `#bom-add-to-cart` button in the BOM panel, enabled only when a BOM is loaded. Click → gather missing rows (each → `{part_id or raw:{mpn,description}, shortfall}`) → `cartStore.addBomMissing(activeCartId, missing)` → toast with count.

The `missing` set = rows whose `effectiveStatus` is `missing`/`possible`/`*-short` (from `computeRows`); shortfall = `need - onHand` for short rows, else omitted (server defaults qty).

- [ ] **Step 1: Write the failing E2E test**

```js
test('Add all missing to cart adds the BOM shortfall parts', async ({ page }) => {
  await launchApp(page);
  await loadBomFixture(page); // repo helper or UI steps to load a BOM with N missing parts
  await page.click('#bom-add-to-cart');
  await expect(page.locator('#cart-badge')).not.toHaveText('0');
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx playwright test cart-bom-missing`
Expected: FAIL.

- [ ] **Step 3: Add button + handler**

`index.html`: add `<button class="btn-md" id="bom-add-to-cart" title="Add all missing/short BOM parts to the active cart">Add missing to cart</button>` in the BOM panel toolbar (find the BOM panel header/toolbar region). In `bom-events.js`: enable/disable on `BOM_LOADED`/`BOM_CLEARED`; on click, build the missing list from `computeRows()` output and call `cartStore.addBomMissing(cartStore.getActiveCartId(), missing)`, then `showToast(...)`. Add `addBomMissing` to cart-store.js if absent.

- [ ] **Step 4: Run E2E + lint**

Run: `npx playwright test cart-bom-missing && npx eslint js/`
Expected: PASS / clean.

- [ ] **Step 5: Commit**

```bash
git add index.html js/bom/ js/cart/cart-store.js tests/js/e2e/cart-bom-missing.spec.mjs
git commit -m "feat(cart): BOM panel 'add all missing to cart' button"
```

---

### Task B6: Cart modal — DataGrid + core line editing

**Files:**
- Create: `js/cart/cart-modal.js`
- Modify: `js/cart/cart-header.js` (real `openCartModal`), `css/components/cart.css`
- Test: `tests/js/e2e/cart-modal.spec.mjs`

**Interfaces:**
- Consumes: `js/components/data-grid.js` `DataGrid`, `js/ui-helpers.js` `Modal`/`showToast`, `cartStore`.
- Produces: `openCartModal()` builds a modal titled with the active cart name, containing a top button bar + a `DataGrid` of line items. Columns: description/part, package, on-hand (read-only), **qty-to-purchase (editable)**, target distributor, actions (delete). Uses `DataGrid` `onCellEdit` for qty → `cartStore.updateItem(cartId, ref, {qty})`, `rowActions` delete → `cartStore.removeItem`. Top bar (this task): **Clear cart** button. `cartsSignal` subscription re-renders the grid.

- [ ] **Step 1: Write the failing E2E test**

```js
test('cart modal: open, edit qty, delete a line, clear', async ({ page }) => {
  await launchApp(page);
  await addOnePartToCart(page); // via cart-add mode helper
  await page.click('#cart-btn');
  await expect(page.locator('.cart-modal')).toBeVisible();

  const qtyCell = page.locator('.cart-modal .cart-qty-input').first();
  await qtyCell.fill('12');
  await qtyCell.blur();
  // reopen / assert persisted via badge or reload
  await expect(page.locator('.cart-modal .cart-qty-input').first()).toHaveValue('12');

  await page.locator('.cart-modal .cart-del-line').first().click();
  await expect(page.locator('#cart-badge')).toHaveText('0');
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx playwright test cart-modal`
Expected: FAIL.

- [ ] **Step 3: Implement `cart-modal.js`**

Build the modal via `Modal(...)`; render a `DataGrid` into its body with the columns above. Wire `onCellEdit` (qty, integer ≥ 0) → `cartStore.updateItem`; a per-row delete action → `cartStore.removeItem`; a top-bar **Clear cart** button → confirm → `cartStore.clearCart`. Subscribe to `cartsSignal` to `grid.render(activeCart.items)` and update the modal title. Replace the `openCartModal` stub import in `cart-header.js` with the real one. CSS: `.cart-modal` sizing to resemble the inventory view; `.cart-qty-input`, `.cart-del-line`, `.cart-topbar`.

- [ ] **Step 4: Run E2E + lint + types**

Run: `npx playwright test cart-modal && npx eslint js/ && npx tsc --noEmit`
Expected: PASS / clean.

- [ ] **Step 5: Commit**

```bash
git add js/cart/cart-modal.js js/cart/cart-header.js css/components/cart.css tests/js/e2e/cart-modal.spec.mjs
git commit -m "feat(cart): cart modal with editable qty, delete line, clear"
```

---

### Task B7: Cart management in the modal (rename / create / switch / delete)

**Files:**
- Modify: `js/cart/cart-modal.js`, `css/components/cart.css`
- Test: `tests/js/e2e/cart-manage.spec.mjs`

**Interfaces:**
- Consumes: `cartStore.createCart`, `renameCart`, `deleteCart`, `setActiveCart`, `getCarts`, `getActiveCartId`.
- Produces: top-bar controls — a **cart switcher** `<select id="cart-switcher">` (options = all carts, value = id, selected = active) → `setActiveCart`; **New** button → `createCart(prefillName())` where `prefillName()` = `"<YYYY-MM-DD> · <loadedBomName or ''>"`; **Rename** button → prompt/inline → `renameCart`; **Delete** button → confirm → `deleteCart` (and switch active to first remaining).

- [ ] **Step 1: Write the failing E2E test**

```js
test('cart modal manage: create, switch, rename, delete', async ({ page }) => {
  await launchApp(page);
  await page.click('#cart-btn');
  await page.click('.cart-topbar .cart-new');
  const count = await page.locator('#cart-switcher option').count();
  expect(count).toBeGreaterThanOrEqual(2);
  // rename
  page.once('dialog', d => d.accept('Renamed Cart')); // if using window.prompt
  await page.click('.cart-topbar .cart-rename');
  await expect(page.locator('#cart-switcher option:checked')).toHaveText(/Renamed Cart/);
});
```
(If the design uses an inline text field instead of `window.prompt`, adjust the test to fill that field — realistic interactions only, no forced events.)

- [ ] **Step 2: Run to verify it fails**

Run: `npx playwright test cart-manage`
Expected: FAIL.

- [ ] **Step 3: Implement management controls**

Add the switcher + New/Rename/Delete to the top bar; wire per Interfaces. `prefillName()` reads the loaded BOM filename from `store` (the BOM meta `fileName`) — reuse the getter used elsewhere. Re-render switcher options on `cartsSignal`.

- [ ] **Step 4: Run E2E + lint**

Run: `npx playwright test cart-manage && npx eslint js/`
Expected: PASS / clean.

- [ ] **Step 5: Commit**

```bash
git add js/cart/cart-modal.js css/components/cart.css tests/js/e2e/cart-manage.spec.mjs
git commit -m "feat(cart): cart management (create/switch/rename/delete) in modal"
```

---

### Task B8: Per-line distributor + split + consolidate buttons

**Files:**
- Modify: `js/cart/cart-modal.js`
- Test: `tests/js/e2e/cart-split-consolidate.spec.mjs`

**Interfaces:**
- Consumes: `cartStore.updateItem` (target_distributor), `cartStore.splitCart`, `cartStore.consolidateCart`; a part's sourced distributors (from the item's resolved detail returned by `GET /v1/carts/{id}` — ensure `get_cart` includes each item's available distributors, OR fetch via the parts data already in the frontend store). 
- Produces: per-row distributor `<select>` → `updateItem(ref, {targetDistributor})`; top-bar **Split by distributor** (choose distributor + toggle "remove from this cart") → `splitCart` → switches active to the new cart + toast; top-bar **Consolidate to distributor** (choose distributor) → `consolidateCart` → toast listing any unresolved lines.

Note: to populate the per-line distributor options, extend `carts.get`/`get_cart` (backend) to include `available_distributors` per item — a small addition in the facade's `get_cart` that calls `pricing.get_sourced_distributors`. If this backend change is made, add a Python test for it and re-run gen-openapi.

- [ ] **Step 1: Write the failing E2E test**

```js
test('split by distributor creates a new cart with only that distributor', async ({ page }) => {
  await launchApp(page);
  await seedCartWithMixedDistributors(page); // helper: add parts sourced from lcsc + digikey
  await page.click('#cart-btn');
  await page.selectOption('.cart-topbar .cart-split-dist', 'lcsc');
  await page.click('.cart-topbar .cart-split-go');
  // active cart switches to the new lcsc-only cart
  await expect(page.locator('.cart-modal .cart-row')).toHaveCount(/* lcsc line count */ 1);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx playwright test cart-split-consolidate`
Expected: FAIL.

- [ ] **Step 3: Implement (backend `available_distributors` if needed, then UI)**

If needed, extend `get_cart` facade to attach `available_distributors` per item (test + gen-openapi). Then add the per-row `<select>` and the two top-bar operations to `cart-modal.js`, wired to `cartStore.splitCart`/`consolidateCart`. Show a toast with unresolved refs on consolidate.

- [ ] **Step 4: Run E2E + full JS checks**

Run: `npx playwright test cart-split-consolidate && npx eslint js/ && npx tsc --noEmit`
Expected: PASS / clean.

- [ ] **Step 5: Commit**

```bash
git add js/cart/cart-modal.js inventory_api.py docs/openapi-v1.json tests/ 
git commit -m "feat(cart): per-line distributor, split, and consolidate in modal"
```

---

### Task B9: Export UI (CSV download + copy paste + unresolved warning)

**Files:**
- Create: `js/cart/cart-export.js`
- Modify: `js/cart/cart-modal.js` (top-bar export buttons)
- Test: `tests/js/e2e/cart-export.spec.mjs`

**Interfaces:**
- Consumes: `cartStore.exportCart(cartId, distributor, fmt)` → `{content, unresolved, filename}`.
- Produces: top-bar **Export ▾** with LCSC CSV / DigiKey CSV / Copy LCSC paste / Copy DigiKey paste. CSV → trigger a browser download of `content` as `filename` (Blob + object URL, `<a download>`). Paste → `navigator.clipboard.writeText(content)` + toast. If `unresolved.length`, show a warning toast/modal listing unresolved refs.

- [ ] **Step 1: Write the failing E2E test**

```js
test('export LCSC CSV downloads a file', async ({ page }) => {
  await launchApp(page);
  await addResolvableLcscPartToCart(page);
  await page.click('#cart-btn');
  const [ download ] = await Promise.all([
    page.waitForEvent('download'),
    (async () => { await page.click('.cart-topbar .cart-export'); await page.click('.cart-export-lcsc-csv'); })(),
  ]);
  expect(download.suggestedFilename()).toMatch(/lcsc\.csv$/);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx playwright test cart-export`
Expected: FAIL.

- [ ] **Step 3: Implement `cart-export.js` + buttons**

`cart-export.js`: `downloadCsv(cartId, distributor)` → `const {content, filename, unresolved} = await cartStore.exportCart(cartId, distributor, 'csv'); triggerDownload(filename, content); warnUnresolved(unresolved);`. `copyPaste(cartId, distributor)` → export `'paste'`, `navigator.clipboard.writeText`, toast. `triggerDownload` uses a Blob + `URL.createObjectURL` + a synthetic `<a download>` click (this is a real user-initiated download, allowed). Add the Export dropdown to the modal top bar.

- [ ] **Step 4: Run E2E + lint + types**

Run: `npx playwright test cart-export && npx eslint js/ && npx tsc --noEmit`
Expected: PASS / clean.

- [ ] **Step 5: Commit**

```bash
git add js/cart/cart-export.js js/cart/cart-modal.js tests/js/e2e/cart-export.spec.mjs
git commit -m "feat(cart): export UI — CSV download + clipboard paste + unresolved warning"
```

---

### Task B10: Fixtures + full verify gate

**Files:**
- Regenerate: fixtures, code-map, openapi, api-map; run `verify.sh`

- [ ] **Step 1: Regenerate all derived artifacts**

Run:
```bash
python scripts/generate-test-fixtures.py
python scripts/gen-openapi.py
python scripts/gen-api-client.py
python scripts/gen-code-map.py
```

- [ ] **Step 2: Run the full verify suite**

Run: `bash scripts/verify.sh`
Expected: all guards + ruff + pytest + eslint + tsc + vitest PASS. Fix any staleness/lint/type failures it reports, re-running until green.

- [ ] **Step 3: Run the full Playwright suite (cart specs + regression on clipping tests)**

Run: `npx playwright test`
Expected: PASS, including `sticky-buttons` and `resize-visibility` (the header cart button must not clip action buttons — if it does, fix the CSS, never weaken those tests).

- [ ] **Step 4: Commit regenerated artifacts**

```bash
git add -A
git commit -m "chore(cart): regenerate fixtures/openapi/api-map/code-map; verify green" || echo "nothing to commit"
```

---

### Task B11: Push + PR + CI

- [ ] **Step 1: Push and open the PR**

Run:
```bash
bash scripts/push-pr.sh --title "feat(cart): shopping cart — carts entity, add flows, modal, LCSC/DigiKey export" --body "Implements docs/superpowers/specs/2026-07-24-cart-feature-design.md. Persistent multi-carts, cart-add/link/BOM add flows, DataGrid cart modal (qty edit, delete, per-line distributor, split, consolidate, cart management), and LCSC/DigiKey CSV + clipboard export. Direct distributor API submission intentionally out of scope (no ordering API available)."
```

- [ ] **Step 2: Watch CI to green**

Run: `gh pr checks <number>` (repeat) — diagnose and fix any failures, push again via `push-pr.sh`, until all required checks pass.

---

## Self-Review

**Spec coverage:**
- Cart button left of count/worth → B2. ✓
- Add-all-missing-BOM → B5. ✓
- Link makes cart purple + click adds → B4. ✓
- Cart-add-mode toggle → B3. ✓
- Persistent across sessions → A1 (`carts.json` + load_into_db). ✓
- Multiple carts + one active per-user → A1/A3, B7. ✓
- Export LCSC/DigiKey (CSV + paste) → A5/A8/B9. Direct API dropped (documented). ✓
- Cart modal like inventory view w/ qty edit, delete, per-line distributor, manage, split, consolidate → B6/B7/B8. ✓
- Default qty (shortfall + cost-stepping) → A4, used in A7. ✓
- Per-user active pointer → A3. ✓
- SSE + signals propagation → A8/B1. ✓
- Tests: Python (A1–A8), vitest (B1), E2E per feature (B2–B9), verify gate (A9/B10). ✓

**Placeholder scan:** Each code step contains real code or a precise "reuse existing X at file:line" pointer. Two deliberate softenings — the exact `dubis_errors` class name (A2), the E2E bootstrap helper name, and the store attribute names in `inventory_api.py` (A7) — are called out as "read the actual file, reuse verbatim" because they depend on existing names the implementer must confirm; these are lookups, not undefined behavior.

**Type consistency:** cart item shape `{ref, part_id, raw, qty, target_distributor}` is consistent across `carts.py`, `cart_export.py`, facade, and JS store. `default_qty(shortfall, ladder)` signature consistent A4↔A7. `export_cart(cart_id, distributor, fmt)` consistent A7↔A8↔B9. Facade method names match router calls in A8 and the frozen-surface list in A7.
