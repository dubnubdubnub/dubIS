/**
 * Structural tests — verify CSS + HTML contracts that are hard to
 * catch visually but easy to regress (e.g. line-clamp prerequisites).
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..', '..');

const CSS_FILES = [
  'css/variables.css', 'css/layout.css', 'css/buttons.css', 'css/tables.css',
  'css/modals.css', 'css/panels/import.css', 'css/panels/inventory.css',
  'css/panels/bom.css', 'css/components/toast.css', 'css/components/tooltip.css',
  'css/components/badges.css', 'css/components/console.css', 'css/components/linking.css',
];
const css = CSS_FILES.map(f => readFileSync(join(ROOT, f), 'utf-8')).join('\n');
// Read all inventory panel files (split across wiring + renderer + bom view)
const invPanelJs = readFileSync(join(ROOT, 'js/inventory/inventory-panel.js'), 'utf-8')
  + readFileSync(join(ROOT, 'js/inventory/inv-html-builders.js'), 'utf-8')
  + readFileSync(join(ROOT, 'js/inventory/inv-bom-view.js'), 'utf-8');

/**
 * Extract the content of a CSS rule block by selector.
 * Returns the raw string between { and } for the first match.
 */
function cssRule(selector) {
  // Escape special regex chars in the selector, but keep spaces
  const escaped = selector.replace(/([.[\](){}+*?^$|\\])/g, '\\$1');
  const re = new RegExp(escaped + '\\s*\\{([^}]+)\\}');
  const m = css.match(re);
  return m ? m[1].trim() : null;
}

function hasProperty(ruleText, prop) {
  // Match "prop:" or "prop :" with possible whitespace
  return new RegExp('(^|;|\\s)' + prop.replace(/[-]/g, '\\-') + '\\s*:').test(ruleText);
}

function propertyValue(ruleText, prop) {
  const re = new RegExp('(?:^|;|\\s)' + prop.replace(/[-]/g, '\\-') + '\\s*:\\s*([^;]+)');
  const m = ruleText.match(re);
  return m ? m[1].trim() : null;
}

describe('Description auto-hide when panel is narrow', () => {
  it('JS uses ResizeObserver to track panel width', () => {
    expect(invPanelJs).toContain('ResizeObserver');
    expect(invPanelJs).toContain('hideDescs');
  });

  it('JS conditionally skips .part-desc based on hideDescs flag', () => {
    // The truthy branch must not render the full description (no part-desc-inner);
    // the falsy branch is the real description with .part-desc-inner. The truthy
    // branch is allowed to be either an empty string or an empty .part-desc-pad
    // spacer used to keep column alignment when descriptions auto-hide.
    expect(invPanelJs).toMatch(/hideDescs\s*\?\s*['"][^'"]*['"][^]*?part-desc-inner/);
  });
});

describe('Description line-clamp contract', () => {
  it('JS renders .part-desc-inner wrapper inside .part-desc', () => {
    // The inventory panel must wrap description text in an inner span
    // so that -webkit-line-clamp works (it fails on direct flex children)
    expect(invPanelJs).toContain('class="part-desc-inner"');
    expect(invPanelJs).toMatch(/class="part-desc"[^>]*>.*<span class="part-desc-inner"/s);
  });

  it('.part-desc-inner has display: -webkit-box', () => {
    const rule = cssRule('.part-desc-inner');
    expect(rule).not.toBeNull();
    expect(propertyValue(rule, 'display')).toBe('-webkit-box');
  });

  it('.part-desc-inner has -webkit-line-clamp', () => {
    const rule = cssRule('.part-desc-inner');
    expect(hasProperty(rule, '-webkit-line-clamp')).toBe(true);
    const val = parseInt(propertyValue(rule, '-webkit-line-clamp'), 10);
    expect(val).toBeGreaterThan(0);
    expect(val).toBeLessThanOrEqual(10);
  });

  it('.part-desc-inner has -webkit-box-orient: vertical', () => {
    const rule = cssRule('.part-desc-inner');
    expect(propertyValue(rule, '-webkit-box-orient')).toBe('vertical');
  });

  it('.part-desc-inner has overflow: hidden', () => {
    const rule = cssRule('.part-desc-inner');
    expect(propertyValue(rule, 'overflow')).toBe('hidden');
  });

  it('.part-desc-inner has width: 100% to fill flex parent', () => {
    const rule = cssRule('.part-desc-inner');
    expect(propertyValue(rule, 'width')).toBe('100%');
  });

  it('.part-desc outer has overflow: hidden', () => {
    const rule = cssRule('.inv-part-row .part-desc');
    expect(rule).not.toBeNull();
    expect(propertyValue(rule, 'overflow')).toBe('hidden');
  });
});

describe('Part preview tooltip text selection contract', () => {
  it('.part-preview-card has user-select: text', () => {
    const rule = cssRule('.part-preview-card');
    expect(rule).not.toBeNull();
    expect(propertyValue(rule, 'user-select')).toBe('text');
  });

  it('.part-preview-card has -webkit-user-select: text', () => {
    const rule = cssRule('.part-preview-card');
    expect(rule).not.toBeNull();
    expect(propertyValue(rule, '-webkit-user-select')).toBe('text');
  });

  it('.part-preview has pointer-events: auto', () => {
    const rule = cssRule('.part-preview');
    expect(rule).not.toBeNull();
    expect(propertyValue(rule, 'pointer-events')).toBe('auto');
  });
});

/**
 * The inventory panel header is a `flex-wrap: wrap` row whose controls swap
 * layout on a width threshold: `.dist-filter-bar.compact` (js/inventory/
 * inv-events.js, at FILTER_BAR_MIN_WIDTH) hides the pill labels AND shrinks
 * `.dist-filter-btn`'s padding. That swap is a discrete decision and must land
 * in the same frame as the resize that caused it.
 *
 * `transition: all` broke that. Padding is a layout property, so the pills kept
 * growing for the full transition duration after the width had already changed;
 * the header's flex row kept re-fitting, and ~50ms after the user collapsed a
 * side panel it finally un-wrapped a whole chip row and slid every inventory
 * row 9px UP under a pointer that had not moved. That silently kills an armed
 * hover: js/part-preview.js arms a 300ms timer on `mouseover` and clears it on
 * `mouseout`, and the browser's re-hit-test lands on `.inv-part-row` — an
 * ancestor, so `closest('[data-lcsc], ...)` finds nothing and nothing re-arms.
 * The tooltip then never opens until the user jiggles the mouse. It reached CI
 * as an intermittent failure of tests/js/e2e/panel-collapse-passive.spec.mjs.
 *
 * The E2E spec only catches it when the race falls the wrong way, so this is
 * the deterministic guard: these controls animate paint, never layout.
 */
describe('Inventory header controls animate paint, never layout', () => {
  // `transition: all` is the trap itself; the rest are the layout-affecting
  // longhands that would reintroduce it by name.
  const LAYOUT_ANIMATABLE = [
    'all', 'width', 'height', 'padding', 'margin', 'font-size', 'gap',
    'border-width', 'inset', 'top', 'right', 'bottom', 'left', 'flex',
  ];

  /**
   * Like cssRule, but anchored so `.dist-filter-btn` cannot match inside the
   * longer `.dist-filter-bar.compact .dist-filter-btn` selector, and with
   * comments stripped so prose about `transition: all` is not read as a
   * declaration.
   */
  function ownRule(selector) {
    const escaped = selector.replace(/([.[\](){}+*?^$|\\])/g, '\\$1');
    const re = new RegExp('(?:^|[};])\\s*' + escaped + '\\s*\\{([^}]+)\\}', 'm');
    const m = css.match(re);
    return m ? m[1].replace(/\/\*[\s\S]*?\*\//g, '').trim() : null;
  }

  for (const selector of ['.dist-filter-btn', '.clear-filters-btn', '.filter-chip']) {
    it(`${selector} transitions no layout-affecting property`, () => {
      const rule = ownRule(selector);
      expect(rule, `${selector} rule not found`).not.toBeNull();
      const transition = propertyValue(rule, 'transition');
      expect(transition, `${selector} has no transition`).not.toBeNull();
      for (const prop of LAYOUT_ANIMATABLE) {
        expect(transition, `${selector} must not transition "${prop}"`)
          .not.toMatch(new RegExp('(^|[\\s,])' + prop + '($|[\\s,])'));
      }
    });
  }
});
