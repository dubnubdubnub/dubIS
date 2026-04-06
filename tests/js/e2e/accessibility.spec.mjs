// @ts-check
/**
 * Accessibility tests using axe-core.
 *
 * Scans the rendered page for WCAG 2.2 AA violations including:
 * - Color contrast (SC 1.4.3, 1.4.11)
 * - Missing ARIA roles and labels
 * - Focus management
 * - Heading hierarchy
 *
 * Run in two states: empty (initial load) and with data (inventory + BOM loaded).
 */
import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { addMockSetup, waitForInventoryRows, loadBomViaEmit } from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8')
);
const BOM_CSV = fs.readFileSync(path.join(__dirname, 'fixtures', 'bom.csv'), 'utf8');

/**
 * Run axe-core and return violations.
 * @param {import('@playwright/test').Page} page
 * @param {object} [options]
 * @param {string[]} [options.exclude] - CSS selectors to exclude
 * @param {string[]} [options.rules] - Specific rules to run (omit for all wcag2aa)
 */
async function runAxe(page, options = {}) {
  let builder = new AxeBuilder({ page });

  if (options.rules) {
    builder = builder.withRules(options.rules);
  } else {
    builder = builder.withTags(['wcag2a', 'wcag2aa']);
  }

  // Exclude tooltip (not visible by default)
  builder = builder.exclude('.part-preview');

  if (options.exclude) {
    for (const sel of options.exclude) {
      builder = builder.exclude(sel);
    }
  }

  return (await builder.analyze()).violations;
}

/**
 * Format violations for readable test output.
 */
function formatViolations(violations) {
  return violations.map(v => {
    const nodes = v.nodes.slice(0, 5).map(n => {
      let detail = `  - ${n.html.slice(0, 120)}`;
      if (n.any?.[0]?.data) {
        const d = n.any[0].data;
        if (d.contrastRatio) {
          detail += `\n    ratio: ${d.contrastRatio}, fg: ${d.fgColor}, bg: ${d.bgColor}`;
        }
      }
      return detail;
    }).join('\n');
    const more = v.nodes.length > 5 ? `\n  ... and ${v.nodes.length - 5} more` : '';
    return `[${v.id}] ${v.help} (${v.impact})\n${nodes}${more}`;
  }).join('\n\n');
}

// ── Tests ──

test.describe('Accessibility — WCAG 2.2 AA', () => {

  test('empty state — catalog violations', async ({ page }) => {
    addMockSetup(page, []);
    await page.goto('/index.html');
    await page.waitForSelector('.header');

    const violations = await runAxe(page);
    // Log all violations for visibility
    if (violations.length > 0) {
      console.log('Violations in empty state:\n' + formatViolations(violations));
    }
    // Track violation count — reduce this as issues are fixed
    const violationCount = violations.reduce((sum, v) => sum + v.nodes.length, 0);
    console.log(`Total violation nodes: ${violationCount}`);
    // Threshold: current known issues (hint text contrast). Reduce to 0 over time.
    expect(violationCount).toBeLessThanOrEqual(10);
  });

  test('BOM comparison — catalog violations', async ({ page }) => {
    addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadBomViaEmit(page, BOM_CSV);
    await page.waitForTimeout(500); // let BOM comparison render

    const violations = await runAxe(page);
    if (violations.length > 0) {
      console.log('Violations with BOM loaded:\n' + formatViolations(violations));
    }
    const violationCount = violations.reduce((sum, v) => sum + v.nodes.length, 0);
    console.log(`Total violation nodes: ${violationCount}`);
    // Most violations are --text-muted (#484f58) contrast failures repeated
    // across many elements. Track total node count; reduce as colors are fixed.
    expect(violationCount).toBeLessThanOrEqual(200);
  });

  test('color contrast — detailed report', async ({ page }) => {
    addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadBomViaEmit(page, BOM_CSV);
    await page.waitForTimeout(500);

    const violations = await runAxe(page, { rules: ['color-contrast'] });

    // Build a deduplicated summary of contrast issues
    const issues = new Map();
    for (const v of violations) {
      for (const node of v.nodes) {
        const d = node.any?.[0]?.data;
        if (d) {
          const key = `${d.fgColor}|${d.bgColor}`;
          if (!issues.has(key)) {
            issues.set(key, {
              fg: d.fgColor, bg: d.bgColor,
              ratio: d.contrastRatio, expected: d.expectedContrastRatio,
              example: node.html.slice(0, 80),
              count: 0,
            });
          }
          issues.get(key).count++;
        }
      }
    }

    if (issues.size > 0) {
      console.log('\n=== Contrast issues (deduplicated by color pair) ===');
      for (const [, issue] of issues) {
        console.log(`  ${issue.ratio}:1 (need ${issue.expected}) — fg:${issue.fg} bg:${issue.bg} (${issue.count} elements)`);
        console.log(`    e.g.: ${issue.example}`);
      }
    }

    // Track unique color-pair violations. Reduce to 0 as colors are fixed.
    console.log(`\nUnique contrast pair violations: ${issues.size}`);
    expect(issues.size).toBeLessThanOrEqual(15);
  });
});
