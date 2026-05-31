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

## The two layers (`tests/js/e2e/visual-helpers.mjs`)

### 1. Semantic pixel sampler — `sampleDashedFrame()` (primary CI guard)

Screenshots the component, decodes it with `pngjs`, and **measures the rendered
result against intent**: it scans outward from the ★ Direct button's edges to
locate the dashed stroke and returns the margin on each facing side plus whether
the NW corner is rounded.

- **Portable** — no golden image, uses a ±3px tolerance, so it survives font/AA
  differences across OSes. This is what runs in CI everywhere.
- **Intent-expressing** — asserts the actual requirement ("8px margin, rounded
  corners"), so it survives implementation swaps (clip-path → SVG → canvas).
- Used by the viewport tests in `tests/js/e2e/mfg-direct.spec.mjs`.

### 2. Golden-image snapshot — `expectStrictScreenshot()`

`toHaveScreenshot` baseline comparison (`tests/js/e2e/drop-zone-visual.spec.mjs`).
Catches *any* visual drift, but baselines are **per-platform** and need
maintenance.

> **Thin-line gotcha (important):** the common `maxDiffPixelRatio: 0.01` (1%) is
> far too lenient for thin/sparse UI like dashed lines. A catastrophic frame
> shift changes well under 1% of the pixels in the clip, so the test passes
> anyway. `expectStrictScreenshot` uses `maxDiffPixelRatio: 0` and absorbs
> sub-pixel AA noise via a small per-pixel `threshold` (0.1). If real AA jitter
> makes a snapshot flaky, raise `threshold` slightly — **never** relax the ratio.

## Snapshot baselines across platforms

Playwright names baselines per-platform: `drop-zone-medium-functional-win32.png`,
`...-linux.png`, etc. CI runs the `functional` project on the **self-hosted Linux
runner** (`pnp-testbox`, the ux430 box), so the golden test needs **`-linux`
baselines committed** or it fails in CI with "snapshot doesn't exist".

Baselines must be generated in the **same rendering environment as CI** (the
runner itself) so anti-aliasing matches. Do not generate Linux baselines with a
generic Docker image — AA can differ from the self-hosted runner.

### Regenerating Linux baselines (after an intentional visual change)

On a branch that is already pushed, regenerate on the runner and commit:

```bash
# from your dev machine — runs on the actual CI box so AA matches
ssh -i ~/.ssh/mauler ux430@ux430 '
  set -e
  cd ~/dubis-baseline && git fetch origin && git checkout <branch> && git pull
  npm install
  npx playwright install chromium
  npx playwright test drop-zone-visual --project functional --update-snapshots
'
# copy the regenerated *-linux.png back and commit them
scp -i ~/.ssh/mauler 'ux430@ux430:~/dubis-baseline/tests/js/e2e/drop-zone-visual.spec.mjs-snapshots/*-linux.png' \
  tests/js/e2e/drop-zone-visual.spec.mjs-snapshots/
git add tests/js/e2e/drop-zone-visual.spec.mjs-snapshots/*-linux.png
git commit -m "test(visual): regenerate Linux drop-zone baselines"
```

The **semantic** sampler test needs no baseline and is the real cross-platform
guard — if you ever can't maintain golden images for a platform, keep the
sampler and let the snapshot test be the bonus, not the gate.
