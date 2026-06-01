# Visual Testing

How we catch **rendering** bugs — ones where the DOM/CSS values are correct but
the pixels on screen are wrong (viewBox/scale errors, clipping by ancestor
`overflow`, z-index occlusion, stale measurement, sharp-vs-rounded corners).

## Why DOM/property assertions aren't enough

A property-based E2E test that reads an element's computed geometry (e.g. an SVG
path's `d` attribute) **and** the rect it was generated from, then checks they
agree, is **tautological** — both numbers come from the same
`getBoundingClientRect`, and the geometry is *derived from* that rect. The test
only proves the math is internally consistent, never that the result renders
correctly.

Concrete example from this repo: the import drop-zone's dashed L-frame
(`updateDropZoneFrame`/`buildFramePath` in `js/import/import-panel.js`) was
"verified" by a test that parsed the SVG path string. Injecting a `viewBox` bug
that renders the frame at the wrong scale — visually abandoning the button
entirely — **still passed** that test. The margin bug shipped repeatedly because
the test never looked at a pixel.

**Rule: assert against rendered pixels, never against the same numbers used to
produce them. Test the cake, not the recipe.**

## Three-layer architecture (`tests/js/e2e/visual/`)

### Layer 1 — `capture.mjs` (screenshot foundation)

One screenshot decoded once; everything else in a test reuses that `Frame`.

- **`capture(page, target, opts)`** — Screenshots a locator or viewport-px clip
  rect (expanded by `pad`, default 12px), decodes via `pngjs`, and returns a
  `Frame` with four helpers:
  - `toImg(cssX, cssY)` — map CSS px to device-px image coordinates
  - `toCss(devX, devY)` — reverse map: device px back to CSS px
  - `pixel(x, y)` — `[R,G,B,A]` for a device-px coordinate, or `null` if OOB
  - `frame.png`, `frame.clip`, `frame.scale` — raw decoded image and geometry
- **`rectOf(locator)`** — Returns the locator's bounding rect in viewport CSS px
  (a thin `getBoundingClientRect` wrapper used to feed `measureGap`/`scanRay`).

### Layer 2 — `measure.mjs` + `color.mjs` (semantic checks, primary CI gate)

Portable, **baseline-free** checks that express layout intent directly. These
run identically on every OS because they measure distances and dominance ratios,
not exact pixel colors.

**`measure.mjs`:**

- **`scanRay(frame, fromCss, dir, predicate, opts)`** — Advances a ray from a
  CSS-px origin in a CSS-px direction, scanning a ±`band`-px perpendicular strip
  at each step to bridge dashed-line gaps. Returns CSS-px distance to the first
  pixel matching `predicate`, or `Infinity`.
- **`measureGap(frame, rect, predicate, side, opts)`** — Convenience wrapper:
  measures the CSS-px distance from a rect's named edge (`'top'`/`'right'`/
  `'bottom'`/`'left'`) to the nearest matching pixel. Returns `Infinity` if none
  found within the search window.
- **`detectClipping(page, locator)`** — Hybrid DOM+hit-test: walks ancestor
  `overflow` containers to compute the `visibleRatio` of a locator, then uses
  `elementFromPoint` to detect occlusion by another element. Returns
  `{clipped, occluded, visibleRatio, reason}`.
- **`measureAlignment(a, b, edge)`** — Signed CSS-px offset between the same
  named edge of two independent rects (`a − b`). ≈0 means aligned. Compares
  elements from different parts of the DOM, so it is **not** a tautology — it
  is legitimate layout truth.

**`color.mjs`:**

- **`isColorNear(rgb, target, tol)`** — True when every channel of `rgb` is
  within `tol` of `target`. Simple color-proximity check.
- **`channelDominant(rgb, ch, byAtLeast, min)`** — True when channel `ch`
  (0=R, 1=G, 2=B) exceeds both other channels by at least `byAtLeast` and is
  itself at least `min`. Robust detection of tinted (e.g. bluish) anti-aliased
  strokes against a dark background without requiring an exact color.

### Snapshot layer — `snapshot.mjs` (anchor views only)

Golden-image comparison. Used **sparingly** — only for the drop-zone resting
state where a pixel-exact anchor is worth the maintenance cost.

- **`paddedClip(page, locator, pad)`** — Returns a Playwright clip rect around
  the locator's bounding box, expanded by `pad` (default 12px) so inset strokes
  that sit outside the border box aren't cut off.
- **`expectStrictScreenshot(page, name, clip)`** — `toHaveScreenshot` with
  `maxDiffPixelRatio: 0` and `threshold: 0.1`.

> **Thin-line gotcha (important):** the common `maxDiffPixelRatio: 0.01` (1%) is
> far too lenient for thin/sparse UI like dashed lines. A catastrophic frame
> shift changes well under 1% of the pixels in the clip, so the test passes
> anyway. `expectStrictScreenshot` uses `maxDiffPixelRatio: 0` and absorbs
> sub-pixel AA noise via a small per-pixel `threshold` (0.1). If real AA jitter
> makes a snapshot flaky, raise `threshold` slightly — **never** relax the ratio.

## Self-test convention

Every primitive ships a `.selftest.spec.mjs` that **injects a known break** and
asserts the primitive catches it. This is the technique that would have caught
the original bug.

- **`capture.selftest.spec.mjs`** — Verifies the screenshot decodes and the
  `toImg`/`toCss` coordinate round-trip is within 1 CSS px.
- **`measure.selftest.spec.mjs`** — Four injected breaks:
  1. **viewBox-scale break**: enlarges the SVG `viewBox` so the dashed frame
     renders far from the button; `measureGap` returns `Infinity` (not ~8px).
  2. **overflow-clip break**: wraps the drop-zone in a 20px `overflow:hidden`
     container; `detectClipping` returns `clipped: true`.
  3. **occlusion-overlay break**: drops an opaque `position:fixed` div over the
     button center; `detectClipping` returns `occluded: true`.
  4. **`measureAlignment` nudge**: a rect shifted 7px returns offset ≈ −7.

`color.mjs` is covered by the Vitest unit test `tests/js/visual-color.test.js`
which checks `isColorNear` and `channelDominant` against known RGB values.

## Surfaces covered

| Spec | Primitives used | What it guards |
|------|----------------|----------------|
| `mfg-direct.spec.mjs` | `capture`, `rectOf`, `scanRay`, `measureGap`, `channelDominant` | Drop-zone L-frame margins (top/right/bottom/left ≈ 8px) and rounded NW corner; surface-specific `sampleDropZoneFrame` lives here, close to the geometry it expresses |
| `drop-zone-visual.spec.mjs` | `paddedClip`, `expectStrictScreenshot` | Drop-zone resting-state pixel anchor at narrow (1280×720) and medium (1600×900) |
| `inv-alignment-visual.spec.mjs` | `rectOf`, `measureAlignment` | Column headers align with first-row cells (left edge ≤ 1.5 CSS px) at 1280 and 1920px wide |
| `sticky-clip-visual.spec.mjs` | `detectClipping` | Action buttons (`.adj-btn`) not clipped/occluded at 1024/1280/1600px; BOM sticky `td.btn-group` survives horizontal scroll; header buttons (`#prefs-btn`, undo/redo, inv-count) not clipped |

## Snapshot baselines across platforms

Playwright names baselines per-platform: `drop-zone-medium-linux.png`,
`drop-zone-medium-win32.png`, etc. The snapshot layer needs **`-linux` baselines
committed** or it fails in CI (which runs on the self-hosted Linux runner).

Baselines must be generated in the **same rendering environment as CI** so
anti-aliasing matches. Do not generate Linux baselines with a generic Docker
image — AA can differ from the self-hosted runner.

The **semantic layer** (`measure.mjs`, `color.mjs`) needs no baselines and is
the real cross-platform guard. If you ever can't maintain golden images for a
platform, keep the semantic tests and treat snapshots as a bonus.

### Regenerating baselines (after an intentional visual change)

**Primary path — `workflow_dispatch` (recommended):**

1. Push your branch.
2. In GitHub Actions, trigger the `Update visual baselines (Linux)` workflow
   with `suite = visual-baselines`. This runs `drop-zone-visual` with
   `--update-snapshots` on the self-hosted runner.
3. Download the `visual-baselines-linux` artifact from the completed run.
4. Unzip the `*-linux.png` files into
   `tests/js/e2e/drop-zone-visual.spec.mjs-snapshots/`.
5. Commit them.

**Local shortcut (generates win32 baselines on your dev machine):**

```bash
npm run visual:baselines   # playwright test drop-zone-visual --project functional --update-snapshots
```

**Fallback — harvest from a CI failure artifact:**

When the snapshot test fails in CI, Playwright uploads a diff artifact. Download
it, inspect the `*-actual.png`, and if the change is intentional commit that
file renamed to the expected baseline name (e.g. `drop-zone-medium-linux.png`).
