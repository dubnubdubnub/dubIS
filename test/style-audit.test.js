/**
 * Structural tests — verify CSS + HTML contracts that are hard to
 * catch visually but easy to regress (e.g. line-clamp prerequisites).
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..');

const css = readFileSync(join(ROOT, 'css/styles.css'), 'utf-8');
const invPanelJs = readFileSync(join(ROOT, 'js/inventory-panel.js'), 'utf-8');

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
    expect(invPanelJs).toMatch(/hideDescs\s*\?\s*['"]{2}\s*:/);
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
