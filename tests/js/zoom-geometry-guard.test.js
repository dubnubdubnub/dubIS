/* Contract-over-a-family guard for the root-zoom coordinate-space seam.

   Under `html { zoom: z }` the DOM API straddles two px spaces: rects, clientX and
   window.inner* are post-zoom, while offsetWidth and any px written to a style are
   authored. Positioning code that mixes them is displaced by exactly the zoom
   factor — and looks perfect at 100%, so it survives every test written without
   zoom in mind.

   E2E specs cover the popovers whose triggers the fixtures can actually produce
   (see zoom-popover-clamp.spec.mjs). This guard covers the whole family instead of
   just the reachable members: any *new* positioning code that clamps against the
   raw window, or writes a rect-derived px value, has to come through
   js/ui-zoom.js's helpers or explicitly opt out here with a reason. */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const JS_ROOT = fileURLToPath(new URL('../../js', import.meta.url));

/** Files allowed to read window.inner*, each with the reason it is safe. */
const WINDOW_SIZE_ALLOWED = {
  'js/ui-zoom.js': 'owns the conversion: divides by zoom to produce authored px',
  'js/inventory/inv-events.js': 'logs the real window size for diagnostics; never positions with it',
  'js/part-preview.js': 'mentions window.innerWidth only in an explanatory comment',
};

function jsFiles(dir = JS_ROOT, out = []) {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) jsFiles(full, out);
    else if (name.endsWith('.js')) out.push(full);
  }
  return out;
}

/** Strip comments so a doc mention of window.innerWidth is not a violation. */
function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/.*$/gm, '$1');
}

const FILES = jsFiles().map(full => ({
  path: relative(join(JS_ROOT, '..'), full).replace(/\\/g, '/'),
  code: stripComments(readFileSync(full, 'utf8')),
}));

describe('root-zoom geometry seam', () => {
  it('finds the js/ tree (guard is not silently scanning nothing)', () => {
    expect(FILES.length).toBeGreaterThan(40);
  });

  it('no module clamps against the raw window size', () => {
    const offenders = FILES
      .filter(f => /window\.inner(Width|Height)/.test(f.code))
      .map(f => f.path)
      .filter(p => !(p in WINDOW_SIZE_ALLOWED));

    expect(offenders,
      'Use zoomedViewport() from js/ui-zoom.js instead of window.innerWidth/innerHeight — '
      + 'the raw window is post-zoom, so clamping a written px value against it '
      + 'misplaces the element at any zoom != 1. Offenders: ' + offenders.join(', '))
      .toEqual([]);
  });

  it('every allow-listed file still exists and still reads window.inner*', () => {
    // Keeps the allow-list from rotting into a set of stale exemptions.
    for (const [path, reason] of Object.entries(WINDOW_SIZE_ALLOWED)) {
      const f = FILES.find(x => x.path === path);
      expect(f, `${path} is allow-listed (${reason}) but no longer exists`).toBeTruthy();
    }
  });

  it('positioning modules import the seam rather than raw rects', () => {
    /* Files that write style.left/top/width from a measured rect. Each must import
       from ui-zoom.js; otherwise it is doing arithmetic across the two spaces. */
    const POSITIONERS = [
      'js/part-preview.js',
      'js/text-popover.js',
      'js/inventory/vendor-flyout.js',
      'js/inventory/filter-chips-bar.js',
      'js/group-flyout/flyout-panel.js',
      'js/group-flyout/flyout-events.js',
      'js/resize-panels.js',
    ];
    for (const path of POSITIONERS) {
      const f = FILES.find(x => x.path === path);
      expect(f, `${path} should exist`).toBeTruthy();
      expect(f.code, `${path} positions elements, so it must use js/ui-zoom.js's helpers`)
        .toMatch(/from '\.{1,2}\/(\.\.\/)?ui-zoom\.js'/);
    }
  });

  it('no positioning module reads getBoundingClientRect directly', () => {
    /* innerRect() wraps it. A direct call in a file that also writes px is the
       exact mixed-space bug; allow it only where the value is never written back. */
    const RECT_ALLOWED = {
      'js/ui-zoom.js': 'innerRect() is the wrapper',
      'js/inventory/inv-events.js': 'reads a rect for hit-testing in the same space',
      'js/import/mfg-direct/ocr-overlay/ocr-overlay-panel.js':
        'OCR token positions are inline percentages, not px, so they scale with zoom',
    };
    const offenders = FILES
      .filter(f => /getBoundingClientRect\s*\(/.test(f.code))
      .map(f => f.path)
      .filter(p => !(p in RECT_ALLOWED));

    expect(offenders,
      'Use innerRect() from js/ui-zoom.js so the rect arrives in authored px. '
      + 'Offenders: ' + offenders.join(', '))
      .toEqual([]);
  });
});
