# Property-based testing in dubIS

dubIS has thousands of example-based tests. A human picks the inputs, so they
prove the cases someone thought of. This is the complementary axis: **state an
invariant, let a generator attack it, and get a shrunk minimal counterexample
when it breaks.**

It does not replace anything. Every property suite here sits *next to* the
example suite for the same module, and the example suite stays the place where
specific known values are pinned.

- Python: [Hypothesis](https://hypothesis.readthedocs.io), in `requirements-dev.txt`
- JavaScript: [fast-check](https://fast-check.dev), in `package.json` devDependencies

Both are real dependencies. Per the Test policy in `CLAUDE.md` there is no
`importorskip` fallback: without them the property tests fail to collect, which
is the point — a skipped property test reports success for a search that never
ran.

---

## Where everything lives

| Thing | Path |
|---|---|
| Python strategies | `tests/python/strategies.py` |
| Python profiles (dev / ci / nightly) | `tests/python/conftest.py` |
| Python reference suites | `tests/python/test_federation_properties.py`, `tests/python/test_cache_catchup_properties.py` |
| JS arbitraries | `tests/js/property/arbitraries.mjs` |
| JS global config | `tests/js/property/setup.mjs` |
| JS reference suite | `tests/js/property/part-keys.property.test.mjs` |
| Schema table the JS side reads | `tests/js/property/inventory-schema.generated.mjs` |
| Its generator + staleness guard | `scripts/gen-property-schema.py` |

Naming: Python `test_<module>_properties.py`, JS `<module>.property.test.mjs`.
The JS suffix is what puts a file in the `property` vitest project instead of
`core`; the Python suffix is only a convention, since pytest collects either way.

## Running them

```bash
bash scripts/verify.sh                       # both, in the pre-PR gate
pytest tests/python/test_federation_properties.py -q
npx vitest run --project property
```

`property` runs **beside** `core`, never instead of it (`scripts/verify.sh`,
`.github/workflows/ci.yml`). A property suite that replaced an example suite
would trade specific, reviewed cases for a random search — strictly worse.

---

## The strategy library, and why it is generated

The load-bearing piece is that a **schema-valid inventory record generator is
derived from `domain/schema.py`'s `INVENTORY_FIELDS`** — the same list that
produces `js/inventory-record.d.ts`. Neither `tests/python/strategies.py` nor
`tests/js/property/arbitraries.mjs` names a single field. Add a field to the
schema and both generators start producing it on the next run.

`ts_type` alone is not quite enough: it collapses `int` and `float` into
`number`, and a generator needs to know whether `qty` is a count or money. That
information *is* in the schema already — in the type of the declared default
(`qty`'s is `0`, `unit_price`'s is `0.0`) — so the generators key off the pair
`(ts_type, type(default))` rather than hardcoding field names. Nothing was added
to `domain/schema.py`; it already said everything the harness needed.

**A shape neither side has seen raises rather than guessing.** Add a field whose
type pair is new and you get `UnknownFieldShape` naming the field at collection
time, not a plausible-looking record that the schema would reject.

**The staleness guard.** Python can import `domain.schema`; vitest cannot. So
`scripts/gen-property-schema.py` emits the field table into
`tests/js/property/inventory-schema.generated.mjs`, guarded exactly like
`gen-inventory-types.py --check` guards the `.d.ts`:

```bash
python scripts/gen-property-schema.py          # regenerate
python scripts/gen-property-schema.py --check  # exit 1 if stale
```

`verify.sh` runs it as the `property-schema` step and CI runs it on the python
leg (gated on `run_js` too, since a JS-only PR still consumes the table).

### What the library gives you

Python (`tests/python/strategies.py`):

| Strategy | What it generates |
|---|---|
| `inventory_records()` | one schema-valid record; `keyable=False` to include the unkeyable branch |
| `consistent_inventory_records()` | the same, with `ext_price == unit_price * qty` |
| `identity_fields(branch=...)` | the five part-number fields, aimed at one branch of `invPartKey` |
| `part_key_cases()` | uniform over **every** branch, including `unkeyable` |
| `source_infos()` / `rosters()` | named sources with distinct ids |
| `federated_inputs()` | `[(SourceInfo, [record, ...])]` with genuinely overlapping key sets |

JS (`tests/js/property/arbitraries.mjs`) mirrors it: `inventoryRecord`,
`identityFields`, `sourceInfo`, `roster`, `federatedInputs`, `isSchemaValid`.

`federatedInputs` deals from a *shared pool* of part numbers rather than drawing
each source's records independently. Independently drawn part numbers essentially
never collide, and a merge test where nothing merges proves nothing.

---

## Determinism and the time budget

Flaky property tests would poison the pipeline, so the policy is explicit.

### Python — registered profiles

`tests/python/conftest.py` registers three, selected by `HYPOTHESIS_PROFILE`,
defaulting to `ci` when `CI` is set and `dev` otherwise:

| Profile | `max_examples` | Seeding |
|---|---|---|
| `dev` | 50 | random |
| `ci` | 100 | `derandomize=True` |
| `nightly` | 2000 | random |

`nightly` is **not** wired to a scheduled workflow. Deliberately: a nightly deep
search on the self-hosted runners is a standing cost the repo has not agreed to,
and the profile is just as useful run by hand before merging something that
touches one of these modules:

```bash
HYPOTHESIS_PROFILE=nightly pytest tests/python/test_federation_properties.py -q   # ~2 min
FC_NUM_RUNS=5000 npx vitest run --project property
```

Doing both found one further (documented, low-severity) behaviour that 50
examples never reached. If it ever earns a schedule, `.github/workflows/win11-nightly.yml`
is the pattern to copy.

`deadline=None` everywhere: a per-example time limit measures the *runner*, not
the code, and a loaded self-hosted box would fail tests that are correct. Timing
belongs in a benchmark.

Local runs are random on purpose — over many runs the repo explores far more of
the space than any fixed seed, and a local flake costs one rerun. CI is
derandomized so a green commit never turns red on a re-run of the same code, and
a red one reproduces exactly.

Heavy suites (file + SQLite I/O, e.g. `test_cache_catchup_properties.py`) scale
their own count off the loaded profile rather than pinning a constant, so
`HYPOTHESIS_PROFILE=nightly` still deepens them:

```python
HEAVY = settings(max_examples=max(10, settings.default.max_examples // 4), ...)
```

### JavaScript — global config

`tests/js/property/setup.mjs` calls `fc.configureGlobal`: 100 runs locally, 200
on CI, and on CI a fixed `seed`. Every knob has an env override.

### Reproducing a CI failure locally

**Python.** Both the failing input and a `@reproduce_failure(...)` blob are in
the CI log (`print_blob=True`).

```bash
# 1. the same search CI ran, from the same starting point
HYPOTHESIS_PROFILE=ci pytest tests/python/test_federation_properties.py::TestMoney -q

# 2. or replay that exact example, whatever the profile
#    paste the blob from the log above the failing test:
#    @reproduce_failure('6.168.0', b'AXicY2w0aGBsMgASzQZAkoHRkcGRodEARIIgoyMzgskEZjBBOIxQDhNEMRAgqW0AAkcGDQYogDMaYAx0e3BrAAC4vRVX')
```

Hypothesis also caches counterexamples in `.hypothesis/` (gitignored), so once a
failure is reproduced locally it is retried first on every subsequent run.

**JavaScript.** fast-check prints `seed` and `path` with every counterexample:

```
{ seed: 1452973569, path: "1:1:2:2", endOnFailure: true }
```

```bash
FC_SEED=1452973569 FC_PATH=1:1:2:2 npx vitest run --project property
```

`FC_NUM_RUNS=5000` deepens a local search.

### Time added to `scripts/verify.sh`

Measured on an M4 Air, `dev` profile:

| Step | Added |
|---|---|
| `pytest` — `test_federation_properties.py` (25 properties) | 2.7 s |
| `pytest` — `test_cache_catchup_properties.py` (3 heavy, file + SQLite I/O) | 0.4 s |
| `pytest` — `test_gen_property_schema.py` (4 guard tests) | 0.02 s |
| `vitest --project core --project property` vs `--project core` alone | 0.1 s (the two projects run in parallel workers) |
| `property-schema` staleness guard | 0.03 s |
| **Total** | **≈ 3.2 s** |

Keep it there. A property suite that pushes `verify.sh` past a few seconds gets
its `max_examples` cut, not its assertions.

---

## When a property test is the right tool

**Yes** when all three hold:

1. **Pure function.** Same inputs, same outputs, no clock, no filesystem, no DOM.
   (`domain/federation.py`, `js/part-keys.js`, `js/ui-zoom-logic.js`.)
2. **There is a statable invariant** that does not restate the implementation —
   a conservation law, a round-trip, an ordering, an agreement between two
   implementations.
3. **The input space is large** enough that picking examples by hand leaves
   obvious holes.

The strongest sub-case is a **differential** property: two implementations that
must agree. `cache_db.catch_up` vs. a full rebuild is the canonical one here,
and it found a live bug on its first run.

**No** when:

- **It touches the DOM, the filesystem or the network.** Generate the inputs to
  a pure decision function and property-test *that*; test the wiring with the
  example suite or Playwright. (`js/server-tabs-logic.js` is property-testable;
  `js/server-tabs.js` is not.)
- **Pixels are the point.** See `docs/visual-testing.md` — geometry properties
  can pass while the visual bug persists.
- **The assertion would re-implement the function.** This is the
  **tautological-property anti-pattern** and it is the main way property suites
  rot: the test passes forever because it computes the same wrong answer.

  A real example from this repo's own first draft:

  ```js
  // BAD — this IS invPartKey, so it can only ever agree with itself
  const expected = IDENTITY_FIELDS.map((f) => fields[f]).find(Boolean) ?? '';
  expect(invPartKey(fields)).toBe(expected);
  ```

  It was not even a correct copy — it failed immediately on `{lcsc: " "}`,
  because a non-C `lcsc` does not win. It was replaced by two *metamorphic*
  properties, which say something the function does not:

  ```js
  // GOOD — blanking a field the key did not come from cannot change the key
  // GOOD — blanking the winning field promotes a LATER field, never an earlier one
  ```

## How to write a good invariant

Look for these shapes, roughly in order of how much they catch:

| Shape | Example here |
|---|---|
| **Differential** — two implementations must agree | `catch_up` vs. full rebuild |
| **Conservation** — nothing created or destroyed | merged qty == sum of input qty |
| **Round-trip** — `decode(encode(x)) == x` | `compressRefs` expands back to its input |
| **Metamorphic** — a controlled input change makes a predictable output change | blanking an identity field cannot change an earlier field's win |
| **Algebraic** — additive, idempotent, commutative, monotonic | `countStatuses(a ++ b) == countStatuses(a) + countStatuses(b)` |
| **Invariant of the output shape** — a bound or a reconciliation | `sources` breakdown sums to the row's `qty`; merged `unit_price` never exceeds the largest input |
| **Never-throws / totality** | `invPartKey` always returns a string |

Two rules learned the hard way while writing the reference suites:

- **Test the generator too.** A suite whose generator quietly stopped reaching a
  branch still passes. Both reference suites assert that their generated records
  are schema-valid and that `identityFields` reaches every branch.
- **Do not import the implementation's own helpers into the property.** The
  conflict property here re-states `_is_empty` in four lines rather than
  importing it; reusing the helper would stop it being an independent statement.

## Pinning a discovered counterexample forever

A property that once failed should never be able to regress silently. Hypothesis'
`.hypothesis/` cache is local and gitignored, so it is **not** a regression test.
The rule:

> When a property finds a bug, fix the bug, then add the shrunk counterexample as
> a plain example-based test next to the existing ones, and name it in a comment
> at the fix site.

Both bugs found so far follow it:

- `domain/federation.py`'s `_weighted_mean` and `_preserve_identity` carry a
  comment naming the shrunk input that produced them.
- `cache_db.catch_up`'s `set`-for-unknown-part branch carries the two-row ledger
  that exposed it.

Why an example and not just the property: the property re-finds the bug only if
the random search happens to wander back to it; an example finds it every time,
in a second, with a name a human can read in a failure list.

---

## Ranked backlog

Ranked by (likelihood of a silently wrong answer) x (blast radius). "Silent"
means the wrong answer looks exactly like a right one — no exception, no red
row, nothing to notice.

| # | Target | Shape | Why it ranks here | Status |
|---|---|---|---|---|
| 1 | `cache_db.catch_up` vs `domain.inventory.rebuild` | differential | Two implementations of "what is the stock". The fast one is what the user sees; nothing asserts they agree, and a disagreement persists until someone deletes `cache.db`. | **done** — found a live bug |
| 2 | `domain/federation.py` | conservation | Merges stock across servers; under-reported stock looks exactly like correct stock. | **done** — found two live bugs |
| 3 | `js/part-keys.js` `invPartKey` | metamorphic | Identity for the whole inventory table: a bad key silently merges or duplicates a row. | **done** |
| 4 | `domain/pricing.py` (600 lines) | conservation / monotonic | Price-break ladders decide what gets ordered and for how much. A ladder that picks the wrong break is a quietly wrong number on a purchase order. Invariants: a larger quantity never costs more per unit; the chosen break is the best one at that quantity; total == unit x qty. | next |
| 5 | `inventory_ops.py` merge → adjust → categorize → sort | conservation / idempotence | Every quantity on screen goes through it. Merging duplicate ledger rows must conserve quantity; `apply_adjustments` must be order-dependent only through the documented floor-at-zero; sorting must be a permutation. | next |
| 6 | `csv_io.py` | round-trip | `read(write(rows)) == rows` for every field, including the migration path. CSV is the source of truth, so a lossy round-trip loses real data. Quoting, embedded commas, BOM, blank rows. | |
| 7 | `domain/purchase_candidates.py` (495 lines) | ordering / totality | Enumerates and *ranks* every distributor x packaging x price-break. A ranking bug picks a worse offer and nothing says so. Invariants: ranking is a total order; a quantity nobody quoted yields no candidate, never an invented one. | |
| 8 | `domain/cart_plan.py` | arithmetic | `per_board_qty * board_count - on_hand`, then presets. Small and pure, and the output is a number someone buys against. | |
| 9 | `domain/packages.py` (823 lines) | symmetry / transitivity | "Does a substitute physically fit". Fit should be symmetric and reflexive; a table this large is exactly where a hand-written case list has holes. | |
| 10 | `domain/predicates.py` | soundness | "Unknown is never a pass" is already the stated rule and is precisely a property: for any parametrics with a missing attribute, evaluation must not return pass. | |
| 11 | `js/matching.js` (497 lines) | metamorphic | BOM-to-inventory matching. A wrong match reads as a confident green row. | |
| 12 | `js/server-tabs-logic.js` (786 lines) | invariant | New, pure, and stateful-by-transform: after any sequence of open/close/move/group, exactly one tab is active, ids stay unique, and no tab ends up with an empty source set. | |
| 13 | `domain/attribute_parse.py` | round-trip | Parsing distributor parametrics; garbage in must not become a confident value. | |
| 14 | `js/ui-zoom-logic.js` | algebraic | Ladder arithmetic: monotonic, clamped, `scaleRect` composes. Small, and the root-zoom trap in `CLAUDE.md` shows what a slip costs. | |
| 15 | `js/col-resize-logic.js` | invariant | Widths stay within min/max and total width is conserved. | |
| 16 | `spec_extractor.py` | totality | Never throws on arbitrary description text; a parsed value is always consistent with the text it came from. | |

Items 1–3 are implemented. Take them in order; each is a self-contained PR.
