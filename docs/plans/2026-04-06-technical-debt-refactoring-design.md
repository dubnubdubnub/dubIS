# Technical Debt Refactoring — Design Spec

**Date:** 2026-04-06
**Goal:** Reduce technical debt across the dubIS codebase to improve AI-assisted development (context window efficiency + edit reliability) while minimizing merge conflicts with concurrent Claude work.

## Constraints

- **Data-architecture Claude** is actively editing: `inventory_api.py`, `cache_db.py`, `matching.js`, `spec_extractor.py`, `generic_parts.py`, and their tests.
- **Testing Claude** is actively editing: test files across `tests/python/` and `tests/js/`.
- Phases are ordered to avoid those files until their work merges.

## Current State Summary

| File | Lines | Key Problems |
|------|-------|-------------|
| digikey_client.py | 763 | Hand-rolled CDP, 165-line normalizer, inline JS, 39 broad try/except |
| inventory_api.py | 709 | God class: CSV + cache + price + distributors + generics + window |
| bom-panel.js | 553 | 9 module-level state vars, 290-line init(), 31 event listeners |
| inventory-panel.js | 497 | 7 state vars, deeply nested rendering, absorbed bom-comparison |
| cache_db.py | 393 | No transaction rollback, raw SQL scattered |
| store.js | 272 | Dual API (Proxy `App` + direct exports), unclear which to use |
| mouser_client.py | 241 | ~180 lines duplicated with pololu_client.py |
| pololu_client.py | 230 | ~180 lines duplicated with mouser_client.py |

Cross-cutting issues:
- Price calculation duplicated in 4 files
- No common distributor interface
- Implicit state machines (modal states as bare booleans)
- Minimal ESLint (2 rules), tsconfig strict: false
- CSS button base styles repeated 8+ times
- CI ubuntu/macos jobs nearly identical

---

## Phase 1 — New Files Only (Zero Conflict Risk)

### 1a. Distributor base class

**New file:** `base_client.py`

- Define `BaseProductClient` abstract base class with:
  - `fetch_product(identifier: str) -> ProductResult | None`
  - Common caching logic (in-memory dict with TTL)
- Define `ProductResult` as a TypedDict:
  ```python
  class ProductResult(TypedDict, total=False):
      title: str
      mpn: str
      brand: str
      unit_price: float | None
      currency: str
      stock: int | None
      image_url: str
      category: str
      subcategory: str
      attributes: dict[str, str]
      url: str
      distributor: str
  ```
- Each existing client adds `(BaseProductClient)` to its class definition and imports ProductResult.
- Minimal diff to existing clients — only the class declaration line and an import.

### 1b. Extract shared HTML parsing

**New file:** `html_product_parser.py`

Extract from mouser_client.py and pololu_client.py (~180 duplicated lines):
- `extract_jsonld(html: str) -> dict | None` — find and parse JSON-LD script tags
- `extract_title(html: str, jsonld: dict | None) -> str` — JSON-LD name, fallback to `<h1>`
- `extract_image_url(html: str, jsonld: dict | None) -> str` — JSON-LD image, fallback to og:image, fix protocol-relative URLs
- `extract_price(html: str, jsonld: dict | None) -> float | None` — JSON-LD offers.price, fallback to HTML patterns
- `extract_stock(html: str) -> int | None` — availability parsing
- `extract_breadcrumbs(html: str) -> tuple[str, str]` — category + subcategory
- `extract_attributes(html: str) -> dict[str, str]` — spec table rows

After extraction:
- `mouser_client._parse_product_page()` shrinks to ~30 lines (call shared + Mouser-specific overrides)
- `pololu_client._parse_product_page()` shrinks to ~30 lines (call shared + Pololu-specific overrides)

### 1c. Consolidate price calculation

**Expand:** `price_ops.py` (currently 27 lines)

Add:
```python
def derive_missing_price(
    unit_price: float | None,
    ext_price: float | None,
    qty: int,
) -> tuple[float | None, float | None]:
    """Fill in whichever of unit/ext is missing, given the other + qty."""
```

Replace the duplicated logic in:
- `inventory_api.py::update_part_price()` (lines 427-430)
- `inventory_ops.py::read_and_merge()` (lines 84-87)
- Any other call sites found during implementation

Surgical edits — only the calculation lines change, surrounding code stays.

---

## Phase 2 — Config & CSS (Near-Zero Conflict Risk)

### 2a. ESLint rule expansion

Add rules to `eslint.config.mjs`:
- `eqeqeq` (error) — prevent `==` / `!=`
- `prefer-const` (warn) — flag `let` that's never reassigned
- `no-var` (warn) — flag `var` usage
- `no-implicit-globals` (error)
- `no-throw-literal` (error)

Run `npx eslint js/ --fix` after adding rules, fix remaining issues manually. This may touch panel files but only mechanical changes (let→const, ==→===) that merge trivially.

### 2b. CSS button deduplication

**Edit:** `css/buttons.css`

Extract shared button base:
```css
.btn-base {
  padding: 3px 10px;
  border-radius: 4px;
  border: 1px solid transparent;
  cursor: pointer;
  font-size: 0.82rem;
  transition: background 0.15s, border-color 0.15s;
}
```

Apply `.btn-base` class to buttons in `index.html`, remove repeated declarations from individual button rules. Each specific button rule keeps only its unique color/sizing.

### 2c. CSS color variables

**Edit:** `css/variables.css`

Move hard-coded hex+opacity values from `buttons.css` into CSS variables:
```css
--btn-primary-bg: rgba(31, 111, 235, 0.25);
--btn-primary-hover: rgba(31, 111, 235, 0.35);
--btn-danger-bg: rgba(248, 81, 73, 0.25);
/* etc. */
```

### 2d. CI matrix DRY-up

**Edit:** `.github/workflows/ci.yml`

Replace duplicated `js-ubuntu` / `js-macos` and `python-ubuntu` / `python-macos` jobs with matrix strategy:
```yaml
js:
  strategy:
    matrix:
      runner: [ubuntu-latest, m4-air]
      include:
        - runner: ubuntu-latest
          run-e2e: true
        - runner: m4-air
          run-e2e: false
```

Same pattern for python jobs. Cuts ~80 lines of duplication.

### 2e. Data directory cleanup

- Remove `data-backup-2026-04-06/` (orphaned backup)
- Remove `inventory - Copy.csv` if confirmed stale
- Add `.gitignore` entries for `data-backup-*/` pattern
- Document purpose of remaining test CSVs in a `data/README.md` if needed

---

## Phase 3 — JS Panel Splits (Safe Zone)

### 3a. Split bom-panel.js (553 lines)

Split into 3 files under `js/bom/`:
- **`js/bom/bom-state.js`** (~60 lines) — module-level state variables (`bomRawRows`, `bomHeaders`, `bomCols`, `bomDirty`, `consumeArmed`, etc.) exported as a state object with getters/setters. Replaces 9 scattered `let` declarations.
- **`js/bom/bom-events.js`** (~150 lines) — all event listener setup, delegation, EventBus subscriptions. Currently the bulk of `init()`.
- **`js/bom/bom-render.js`** (~120 lines) — `reprocessAndRender()`, table building, DOM updates.
- **`js/bom/bom-panel.js`** (~80 lines) — public API: `init()` wires the above together, `processBOM()`, exports for app-init.

The existing `js/bom/bom-logic.js` (147 lines, pure functions) stays as-is.

### 3b. Split inventory-panel.js (497 lines)

Split into files under `js/inventory/`:
- **`js/inventory/inv-state.js`** (~40 lines) — `collapsedSections`, `bomData`, `activeFilter`, `expandedAlts`, `rowMap`, `hideDescs` as state object.
- **`js/inventory/inv-render.js`** (~200 lines) — `render()`, `renderHierarchySection()`, `renderSection()`, `renderBomComparison()`.
- **`js/inventory/inv-events.js`** (~100 lines) — event listeners, EventBus subscriptions.
- **`js/inventory/inventory-panel.js`** (~80 lines) — public API: `init()`, `render()` delegation.

### 3c. Clean up store.js dual API

Remove the `Proxy`-based `App` object. Choose one API:
- **Keep:** direct named exports from store.js (`getInventory()`, `setInventory()`, `getBomResults()`, etc.)
- **Remove:** the `App` proxy and `window.App` global
- **Migration:** find all `App.property` references (grep), replace with store function calls
- **Exception:** `window.App` for Python `evaluate_js` calls — provide a thin shim that delegates to store exports, clearly marked as the only approved global access point

### 3d. Formalize modal state machines

Replace bare boolean state flags with explicit state enums:

```javascript
// js/bom/bom-state.js
const ConsumeState = { IDLE: 'idle', ARMED: 'armed', CONFIRMING: 'confirming' };
let consumeState = ConsumeState.IDLE;

// js/inventory/inv-state.js
const LinkState = { IDLE: 'idle', LINKING: 'linking', CONFIRMING: 'confirming' };
let linkState = LinkState.IDLE;
```

Transition functions validate legal state changes and throw on illegal transitions. Reduces risk of "impossible state" bugs.

---

## Phase 4 — Python Splits (After Data-Architecture Merges)

### 4a. Split inventory_api.py (709 lines)

Extract into focused classes, keep `inventory_api.py` as the composition root:

- **`distributor_manager.py`** (~120 lines) — manages the 4 distributor clients, `_infer_distributor()`, `_infer_distributor_for_key()`, `fetch_part_info()`. Currently scattered across inventory_api.py.
- **`price_manager.py`** (~80 lines) — `update_part_price()`, price history recording, integration with `price_ops.py`.
- **`generic_parts_api.py`** — already exists as `generic_parts.py`, may need minor wiring changes after data-architecture merges.
- **`inventory_api.py`** (~350 lines) — core: `load_organized()`, `rebuild_cache()`, `adjust_part()`, `consume_bom()`, `import_purchase()`. Composes the above managers.

### 4b. Split digikey_client.py (763 lines)

- **`digikey_cdp.py`** (~120 lines) — CDP WebSocket cookie extraction (`_cdp_get_cookies`, frame parsing). Isolates the fragile low-level protocol code.
- **`digikey_session.py`** (~100 lines) — session management, cookie injection, login state checking, poll loop.
- **`digikey_normalizer.py`** (~180 lines) — `_normalize_result()` split into strategy functions: `_normalize_jsonld()`, `_normalize_nextdata()`, `_normalize_fallback()`.
- **`digikey_client.py`** (~200 lines) — public API: `DigikeyClient` class, `fetch_product()`, `start_login()`. Composes the above.
- **`digikey_fetch.js`** (new, ~50 lines) — extract inline JavaScript string from `fetch_product()` into a standalone JS file loaded at runtime.

### 4c. Error handling improvements

- Define `dubis_errors.py` with exception hierarchy:
  ```python
  class DubISError(Exception): ...
  class DistributorError(DubISError): ...
  class DistributorTimeout(DistributorError): ...
  class DistributorAuthError(DistributorError): ...
  class CacheError(DubISError): ...
  class CSVError(DubISError): ...
  ```
- Replace broad `except Exception` in digikey_client.py with specific catches
- Add transaction rollback to `cache_db.py` for multi-step operations
- Ensure callers can distinguish "not found" from "error" (return None vs raise)

---

## Success Criteria

- No file over 350 lines (JS) or 400 lines (Python) after refactoring
- All existing tests pass without modification (except import path changes)
- ESLint passes with expanded rules
- No `var` remaining in JS files
- Each module has a single clear responsibility
- State mutations are centralized (state objects, not scattered lets)
- Distributor clients share a common interface
- Price calculation exists in exactly one place

## Out of Scope

- New features (generic parts, new distributors, etc.)
- TypeScript migration (too large, separate initiative)
- Database ORM/query builder (cache_db works, just needs transactions)
- Build system / bundling (no-build-step is a feature of this project)
