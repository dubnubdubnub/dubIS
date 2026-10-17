# BOM match corpus

Labelled BOM-matching cases that `tests/js/bom-match-corpus.test.mjs` runs through
the app's real pipeline: `processBOM` → `matchBOM` → LCSC spec lookup for the
rows that pass could not identify (`js/bom/bom-spec-lookup.js`) → `matchBOM` again.
Only the network is replaced, by the case's captured LCSC answers.

Each case directory holds:

| File | What it is |
|------|------------|
| `bom.csv` | The BOM exactly as the EDA tool exports it — same headers, quoting, and the same misuse of columns (e.g. KiCad's Value column holding the MPN). |
| `inventory.json` | `{parts: [{record, truth}]}`. `record` is an inventory row as `/v1/parts` returns it (only the fields matching reads are needed). `truth` is that part's hand-checked spec: `type` (`C`/`R`/`L`/`other`), `value` (SI units: farads, ohms, henries), `package`, and `voltage` for capacitors. |
| `lcsc-lookups.json` | What LCSC answers for every LCSC code in the BOM: `{code: {product}}` or `{code: {error: {status: 404, code: "product_not_found"}}}`. Made by `scripts/capture-bom-corpus-lookups.mjs`, never by hand. |
| `expected.json` | `{lines: {<bomAggKey>: {status, part, match?, truth, why}}}`. `status` is `matchBOM`'s raw status (`ok`/`short`/`possible`/`missing`), `part` the inventory part key it must land on or `null`, `match` the matchType if it matters, `truth` the line's own hand-checked spec, `why` one sentence a reviewer can check. |
| `generic-parts.json` | Optional. Generic groups, in `list_generic_parts` shape. |

The test holds the expected table exactly, and runs a **gate** on both the
pre-lookup and post-lookup match: any non-identity suggestion (value, fuzzy,
prefix, generic, or an alternate) whose part `truth` contradicts the line's
`truth` fails the suite. A contradiction is a different type or value, a
different package, or a capacitor rated lower. A passive line with no known
value fails any suggestion at all. The gate reads the `truth` blocks only, not
anything the matcher parsed. A gate that re-parsed descriptions would share
every parsing bug the matcher has.

## Turning a bug report into a case

The repo is public. **Never commit a user's BOM or inventory.** Reproduce
the shape of the report instead:

1. Run the user's BOM against their inventory once, locally, and find the lines
   that went wrong. Note the BOM's shape (which tool, which headers, what each
   column actually holds) and what each wrong line should have matched.
2. Make a new directory named for the shape (e.g. `jlc-bom-no-footprint/`). Write
   a `bom.csv` with the same headers and quoting that has a few lines per
   failure mode. Use real public LCSC parts of the same kind, not the
   user's parts: same manufacturer families and the same near-neighbour trap
   (e.g. UniOhm `0402WGF####TCE` values that differ only in their digits).
   Add one identity line (an exact LCSC match) as a control.
3. Write `inventory.json` from real LCSC descriptions copied verbatim, with
   invented quantities. Include the *wrong* answer the bug produced, so the case
   proves it is no longer chosen. Fill in each part's `truth` from the product
   page.
4. Capture the lookups through a running dubIS. These are read-only GETs and
   record nothing:
   ```bash
   node scripts/capture-bom-corpus-lookups.mjs tests/fixtures/bom-match-corpus/<case>/bom.csv
   ```
5. Write `expected.json`: for every line, the right outcome and a `why`. Lines
   that should stay missing are as valuable as lines that should match.
6. `npx vitest run tests/js/bom-match-corpus.test.mjs`. It should fail on the
   unfixed code (check this), then pass with the fix.

If a later change improves a line, update its row in `expected.json` in the
same commit. The table is meant to move only on purpose.
