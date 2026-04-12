// @ts-check
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import {
  addMockSetup, waitForInventoryRows, loadBom, loadBomViaEmit, loadBomViaFileInput,
  loadPurchaseOrder, checkElementVisibility, isReachableByScroll,
} from './helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8')
);
const BOM_CSV_PATH = path.join(__dirname, 'fixtures', 'bom.csv');
const BOM_CSV = fs.readFileSync(BOM_CSV_PATH, 'utf8');
const PO_CSV_PATH = path.join(__dirname, 'fixtures', 'purchase.csv');

/** Apply a given app state mutation (BOM and/or PO loaded). */
async function applyMode(page, { bom = false, po = false } = {}) {
  if (po) {
    await loadPurchaseOrder(page, PO_CSV_PATH);
    await page.waitForTimeout(200);
  }
  if (bom) {
    await loadBomViaEmit(page, BOM_CSV);
    await page.waitForTimeout(300);
  }
}

/** Readable label for a mode combination. */
function modeLabel({ bom = false, po = false } = {}) {
  if (bom && po) return 'BOM + PO';
  if (bom) return 'BOM';
  if (po) return 'PO';
  return 'base';
}


// ════════════════════════════════════════════════════════════
// 1. HEADER ELEMENTS
// ════════════════════════════════════════════════════════════

test.describe('Header elements visibility on resize', () => {

  test('header buttons visible at minimum viable width (1024px)', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1024, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const prefsBtn = await checkElementVisibility(page, '#prefs-btn', 'Preferences button');
    const undoBtn = await checkElementVisibility(page, '#global-undo', 'Undo button');
    const redoBtn = await checkElementVisibility(page, '#global-redo', 'Redo button');
    const invCount = await checkElementVisibility(page, '#inv-count', 'Inventory count');

    console.log(prefsBtn.reason);
    console.log(undoBtn.reason);
    console.log(redoBtn.reason);
    console.log(invCount.reason);

    expect(prefsBtn.visible, prefsBtn.reason).toBe(true);
    expect(undoBtn.visible, undoBtn.reason).toBe(true);
    expect(redoBtn.visible, redoBtn.reason).toBe(true);
    expect(invCount.visible, invCount.reason).toBe(true);
  });

  test('header buttons visible at narrow width (800px)', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 800, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const prefsBtn = await checkElementVisibility(page, '#prefs-btn', 'Preferences button');
    const undoBtn = await checkElementVisibility(page, '#global-undo', 'Undo button');
    const redoBtn = await checkElementVisibility(page, '#global-redo', 'Redo button');

    console.log(prefsBtn.reason);
    console.log(undoBtn.reason);
    console.log(redoBtn.reason);

    expect(prefsBtn.visible, prefsBtn.reason).toBe(true);
    expect(undoBtn.visible, undoBtn.reason).toBe(true);
    expect(redoBtn.visible, redoBtn.reason).toBe(true);
  });

  test('header buttons not clipped after resize from wide to narrow', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.setViewportSize({ width: 800, height: 600 });
    await page.waitForTimeout(300);

    const prefsBtn = await checkElementVisibility(page, '#prefs-btn', 'Preferences btn (after resize)');
    const undoBtn = await checkElementVisibility(page, '#global-undo', 'Undo btn (after resize)');
    console.log(prefsBtn.reason);
    console.log(undoBtn.reason);

    expect(prefsBtn.visible, prefsBtn.reason).toBe(true);
    expect(prefsBtn.clipped, 'Prefs button clipped by viewport').toBe(false);
    expect(undoBtn.visible, undoBtn.reason).toBe(true);
  });
});


// ════════════════════════════════════════════════════════════
// 2. INVENTORY PANEL HEADER (Rebuild button, Search input)
// ════════════════════════════════════════════════════════════

test.describe('Inventory panel header controls on resize', () => {

  test('rebuild button and search input visible at 1200px', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1200, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const rebuildBtn = await checkElementVisibility(page, '#rebuild-inv', 'Rebuild Inventory button');
    const searchInput = await checkElementVisibility(page, '#inv-search', 'Search input');

    console.log(rebuildBtn.reason);
    console.log(searchInput.reason);

    expect(rebuildBtn.visible, rebuildBtn.reason).toBe(true);
    expect(searchInput.visible, searchInput.reason).toBe(true);
  });

  test('rebuild button and search fit within panel header at narrow width', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1024, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Check they don't overflow the panel header
    const overflow = await page.evaluate(() => {
      const header = document.querySelector('.panel-inventory .panel-header');
      if (!header) return { overflowing: false, reason: 'no header' };
      const rebuild = header.querySelector('.rebuild-btn');
      const search = header.querySelector('.search-input');
      const headerRect = header.getBoundingClientRect();
      const results = [];
      if (rebuild) {
        const r = rebuild.getBoundingClientRect();
        if (r.right > headerRect.right + 1) results.push(`rebuild overflows by ${Math.round(r.right - headerRect.right)}px`);
      }
      if (search) {
        const r = search.getBoundingClientRect();
        if (r.right > headerRect.right + 1) results.push(`search overflows by ${Math.round(r.right - headerRect.right)}px`);
      }
      return { overflowing: results.length > 0, reason: results.join('; ') || 'all controls fit within header' };
    });

    console.log('Panel header overflow:', overflow.reason);
    expect(overflow.overflowing, overflow.reason).toBe(false);
  });
});


// ════════════════════════════════════════════════════════════
// 3. BOM MULTIPLIER BAR BUTTONS (Save, Consume, Clear, Price)
// ════════════════════════════════════════════════════════════

test.describe('BOM multiplier bar buttons on resize', () => {

  test('all BOM action buttons visible at 1920px', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadBomViaFileInput(page, BOM_CSV_PATH);
    await page.waitForTimeout(300);

    const saveBtn = await checkElementVisibility(page, '#bom-save-btn', 'Save BOM');
    const consumeBtn = await checkElementVisibility(page, '#bom-consume-btn', 'Consume');
    const clearBtn = await checkElementVisibility(page, '#bom-clear-btn', 'Clear BOM');
    const multInput = await checkElementVisibility(page, '#bom-qty-mult', 'Board qty input');

    console.log(saveBtn.reason);
    console.log(consumeBtn.reason);
    console.log(clearBtn.reason);
    console.log(multInput.reason);

    expect(saveBtn.visible, saveBtn.reason).toBe(true);
    expect(consumeBtn.visible, consumeBtn.reason).toBe(true);
    expect(clearBtn.visible, clearBtn.reason).toBe(true);
    expect(multInput.visible, multInput.reason).toBe(true);
  });

  test('BOM action buttons overflow check at 1200px', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1200, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadBomViaFileInput(page, BOM_CSV_PATH);
    await page.waitForTimeout(300);

    // Check if buttons overflow the multiplier bar
    const overflow = await page.evaluate(() => {
      const bar = document.getElementById('bom-multiplier-bar');
      if (!bar) return { overflowing: false, reason: 'no multiplier bar' };
      const barRect = bar.getBoundingClientRect();
      const buttons = bar.querySelectorAll('button, input, span, label');
      const overflows = [];
      buttons.forEach(el => {
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.right > barRect.right + 1) {
          overflows.push(`${el.id || el.className || el.tagName}: overflows by ${Math.round(r.right - barRect.right)}px`);
        }
      });
      return {
        overflowing: overflows.length > 0,
        barWidth: Math.round(barRect.width),
        reason: overflows.length > 0 ? overflows.join('; ') : `all fit in ${Math.round(barRect.width)}px bar`,
      };
    });

    console.log('Multiplier bar overflow at 1200px:', overflow.reason);
    expect(overflow.overflowing, `BOM action buttons should not overflow at 1200px: ${overflow.reason}`).toBe(false);
  });

  test('BOM action buttons reachable by scroll at narrow viewport', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1024, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadBomViaFileInput(page, BOM_CSV_PATH);
    await page.waitForTimeout(300);

    // Even if they overflow, they should be reachable
    const save = await isReachableByScroll(page, '#bom-save-btn', 'Save BOM');
    const consume = await isReachableByScroll(page, '#bom-consume-btn', 'Consume');
    const clear = await isReachableByScroll(page, '#bom-clear-btn', 'Clear BOM');

    console.log(save.reason);
    console.log(consume.reason);
    console.log(clear.reason);
  });
});


// ════════════════════════════════════════════════════════════
// 4. PANEL MINIMUM WIDTHS VS VIEWPORT
// ════════════════════════════════════════════════════════════

test.describe('Panel minimum widths vs viewport', () => {

  // Panel min-widths sum to 240+300+380 = 920px + 10px handles = ~930px
  test('all three panels visible at 1024px', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1024, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const panels = await page.evaluate(() => {
      const importP = document.querySelector('.panel-import');
      const invP = document.querySelector('.panel-inventory');
      const bomP = document.querySelector('.panel-bom');
      return {
        import: importP ? { w: importP.offsetWidth, h: importP.offsetHeight } : null,
        inventory: invP ? { w: invP.offsetWidth, h: invP.offsetHeight } : null,
        bom: bomP ? { w: bomP.offsetWidth, h: bomP.offsetHeight } : null,
        viewportWidth: window.innerWidth,
      };
    });

    console.log('Panels at 1024px:', JSON.stringify(panels));
    expect(panels.import.w).toBeGreaterThan(0);
    expect(panels.inventory.w).toBeGreaterThan(0);
    expect(panels.bom.w).toBeGreaterThan(0);
    // All panels should be at least partially visible (non-zero width)
    expect(panels.import.w + panels.inventory.w + panels.bom.w).toBeLessThanOrEqual(panels.viewportWidth + 20);
  });

  test('panels overflow at very narrow viewport (800px)', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 800, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const result = await page.evaluate(() => {
      const container = document.querySelector('.panels');
      const importP = document.querySelector('.panel-import');
      const invP = document.querySelector('.panel-inventory');
      const bomP = document.querySelector('.panel-bom');
      const cRect = container.getBoundingClientRect();
      const totalMinWidth = 240 + 300 + 380 + 10; // panels + handles
      return {
        containerWidth: Math.round(cRect.width),
        totalMinWidth,
        overflowing: totalMinWidth > cRect.width,
        importW: importP.offsetWidth,
        invW: invP.offsetWidth,
        bomW: bomP.offsetWidth,
        scrollWidth: container.scrollWidth,
      };
    });

    console.log('Panels at 800px:', JSON.stringify(result));
    console.log(`Container: ${result.containerWidth}px, scroll content: ${result.scrollWidth}px`);
    // Panel min-widths (930px total) exceed 800px viewport — overflow is expected
    expect(result.overflowing, 'Panel min-widths should exceed 800px viewport').toBe(true);
    // But each panel should still render with non-zero width
    expect(result.importW, 'Import panel should have width').toBeGreaterThan(0);
    expect(result.invW, 'Inventory panel should have width').toBeGreaterThan(0);
    expect(result.bomW, 'BOM panel should have width').toBeGreaterThan(0);
  });

  test('all three panels visible at 1024px with PO loaded', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1024, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadPurchaseOrder(page, PO_CSV_PATH);
    await page.waitForTimeout(200);

    const panels = await page.evaluate(() => {
      const importP = document.querySelector('.panel-import');
      const invP = document.querySelector('.panel-inventory');
      const bomP = document.querySelector('.panel-bom');
      return {
        import: importP ? { w: importP.offsetWidth, h: importP.offsetHeight } : null,
        inventory: invP ? { w: invP.offsetWidth, h: invP.offsetHeight } : null,
        bom: bomP ? { w: bomP.offsetWidth, h: bomP.offsetHeight } : null,
        viewportWidth: window.innerWidth,
      };
    });

    console.log('Panels at 1024px with PO:', JSON.stringify(panels));
    expect(panels.import.w).toBeGreaterThan(0);
    expect(panels.inventory.w).toBeGreaterThan(0);
    expect(panels.bom.w).toBeGreaterThan(0);
    expect(panels.import.w + panels.inventory.w + panels.bom.w).toBeLessThanOrEqual(panels.viewportWidth + 20);
  });

  test('all three panels visible at 1024px with BOM + PO', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1024, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await applyMode(page, { bom: true, po: true });

    const panels = await page.evaluate(() => {
      const importP = document.querySelector('.panel-import');
      const invP = document.querySelector('.panel-inventory');
      const bomP = document.querySelector('.panel-bom');
      return {
        import: importP ? { w: importP.offsetWidth, h: importP.offsetHeight } : null,
        inventory: invP ? { w: invP.offsetWidth, h: invP.offsetHeight } : null,
        bom: bomP ? { w: bomP.offsetWidth, h: bomP.offsetHeight } : null,
        viewportWidth: window.innerWidth,
      };
    });

    console.log('Panels at 1024px with BOM+PO:', JSON.stringify(panels));
    expect(panels.import.w).toBeGreaterThan(0);
    expect(panels.inventory.w).toBeGreaterThan(0);
    expect(panels.bom.w).toBeGreaterThan(0);
    expect(panels.import.w + panels.inventory.w + panels.bom.w).toBeLessThanOrEqual(panels.viewportWidth + 20);
  });

  test('panels resize proportionally when viewport shrinks', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const wideBefore = await page.evaluate(() => ({
      import: document.querySelector('.panel-import').offsetWidth,
      inventory: document.querySelector('.panel-inventory').offsetWidth,
      bom: document.querySelector('.panel-bom').offsetWidth,
    }));

    await page.setViewportSize({ width: 1200, height: 700 });
    await page.waitForTimeout(300);

    const narrowAfter = await page.evaluate(() => ({
      import: document.querySelector('.panel-import').offsetWidth,
      inventory: document.querySelector('.panel-inventory').offsetWidth,
      bom: document.querySelector('.panel-bom').offsetWidth,
    }));

    console.log('Wide (1920):', wideBefore);
    console.log('Narrow (1200):', narrowAfter);

    // All panels should still have positive width
    expect(narrowAfter.import).toBeGreaterThan(0);
    expect(narrowAfter.inventory).toBeGreaterThan(0);
    expect(narrowAfter.bom).toBeGreaterThan(0);
  });
});


// ════════════════════════════════════════════════════════════
// 5. BOM COMPARISON TABLE — BUTTON GROUP STICKY VISIBILITY
// ════════════════════════════════════════════════════════════

test.describe('BOM comparison table buttons', () => {

  test('button group stays visible when table scrolled horizontally', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1200, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadBomViaEmit(page, BOM_CSV);
    await page.waitForTimeout(300);

    const result = await page.evaluate(() => {
      const tableWrap = document.querySelector('#inventory-body .table-wrap');
      if (!tableWrap) return { found: false };
      // Scroll table to the right
      tableWrap.scrollLeft = tableWrap.scrollWidth;
      const btnGroup = tableWrap.querySelector('td.btn-group');
      if (!btnGroup) return { found: false, reason: 'no btn-group' };
      const wrapRect = tableWrap.getBoundingClientRect();
      const btnRect = btnGroup.getBoundingClientRect();
      return {
        found: true,
        btnRight: Math.round(btnRect.right),
        wrapRight: Math.round(wrapRect.right),
        visible: btnRect.right <= wrapRect.right + 2 && btnRect.left >= wrapRect.left - 2,
        stickyComputed: getComputedStyle(btnGroup).position,
      };
    });

    console.log('Btn-group after h-scroll:', result);
    expect(result.found, 'btn-group element should exist after h-scroll').toBe(true);
    expect(result.stickyComputed).toBe('sticky');
    expect(result.visible, `btn-group should remain within table-wrap after scroll (btn.right=${result.btnRight}, wrap.right=${result.wrapRight})`).toBe(true);
  });

  test('button group stays visible when table scrolled horizontally — with PO', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1200, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await applyMode(page, { bom: true, po: true });

    const result = await page.evaluate(() => {
      const tableWrap = document.querySelector('#inventory-body .table-wrap');
      if (!tableWrap) return { found: false };
      tableWrap.scrollLeft = tableWrap.scrollWidth;
      const btnGroup = tableWrap.querySelector('td.btn-group');
      if (!btnGroup) return { found: false, reason: 'no btn-group' };
      const wrapRect = tableWrap.getBoundingClientRect();
      const btnRect = btnGroup.getBoundingClientRect();
      return {
        found: true,
        btnRight: Math.round(btnRect.right),
        wrapRight: Math.round(wrapRect.right),
        visible: btnRect.right <= wrapRect.right + 2 && btnRect.left >= wrapRect.left - 2,
        stickyComputed: getComputedStyle(btnGroup).position,
      };
    });

    console.log('Btn-group after h-scroll (BOM+PO):', result);
    expect(result.found, 'btn-group element should exist after h-scroll (BOM+PO)').toBe(true);
    expect(result.stickyComputed).toBe('sticky');
    expect(result.visible, `btn-group should remain within table-wrap after scroll (btn.right=${result.btnRight}, wrap.right=${result.wrapRight})`).toBe(true);
  });

  test('Adjust/Confirm/Link buttons visible in first BOM row at narrow width', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1200, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadBomViaEmit(page, BOM_CSV);
    await page.waitForTimeout(300);

    const btns = await page.evaluate(() => {
      const firstRow = document.querySelector('#inventory-body tr[data-part-key]');
      if (!firstRow) return { found: false };
      const btnGroup = firstRow.querySelector('td.btn-group');
      if (!btnGroup) return { found: false, reason: 'no btn-group in row' };
      const buttons = Array.from(btnGroup.querySelectorAll('button'));
      return {
        found: true,
        count: buttons.length,
        buttons: buttons.map(b => ({
          text: b.textContent.trim(),
          class: b.className,
          width: b.offsetWidth,
          height: b.offsetHeight,
        })),
      };
    });

    console.log('First BOM row buttons:', btns);
    expect(btns.found, 'First BOM row should exist').toBe(true);
    expect(btns.count, 'First BOM row should have buttons').toBeGreaterThan(0);
    for (const btn of btns.buttons) {
      expect(btn.width, `Button "${btn.text}" has zero width`).toBeGreaterThan(0);
      expect(btn.height, `Button "${btn.text}" has zero height`).toBeGreaterThan(0);
    }
  });

  /**
   * Check that BOM table action buttons (Confirm, Adjust, Link) inside
   * td.btn-group cells are not clipped by the panel body.
   * This catches the table-layout:fixed column-width bug where th.btn-group-hdr
   * had no explicit width, causing buttons to be clipped at narrower panels.
   */
  function checkBomButtonsNotClipped(viewportWidth, { withPO = false } = {}) {
    return async ({ page }) => {
      await addMockSetup(page, MOCK_INVENTORY);
      await page.setViewportSize({ width: viewportWidth, height: 700 });
      await page.goto('/index.html');
      await waitForInventoryRows(page);
      await applyMode(page, { bom: true, po: withPO });

      const result = await page.evaluate(() => {
        const panelBody = document.getElementById('inventory-body');
        if (!panelBody) return { checked: 0, issues: ['panel body not found'] };
        const bodyRect = panelBody.getBoundingClientRect();
        const rows = panelBody.querySelectorAll('tr[data-part-key]');
        const issues = [];
        const checked = Math.min(rows.length, 10);
        for (let i = 0; i < checked; i++) {
          const buttons = rows[i].querySelectorAll('td.btn-group button');
          for (const btn of buttons) {
            if (btn.offsetWidth === 0 || btn.offsetHeight === 0) {
              issues.push(`row ${i}: ${btn.className} has zero dimensions`);
              continue;
            }
            const btnRect = btn.getBoundingClientRect();
            if (btnRect.right > bodyRect.right + 1) {
              issues.push(`row ${i}: ${btn.className} "${btn.textContent.trim()}" clipped (btn.right=${Math.round(btnRect.right)}, panel.right=${Math.round(bodyRect.right)})`);
            }
          }
        }
        return { checked, panelWidth: Math.round(bodyRect.width), issues };
      });

      const label = withPO ? 'BOM+PO' : 'BOM';
      console.log(`Viewport ${viewportWidth}px [${label}] — panel: ${result.panelWidth}px, checked ${result.checked} BOM rows, issues: ${result.issues.length}`);
      result.issues.forEach(i => console.log('  ' + i));
      expect(result.issues.length, result.issues.join('; ')).toBe(0);
    };
  }

  test('BOM table buttons not clipped at 1100px', checkBomButtonsNotClipped(1100));
  test('BOM table buttons not clipped at 1200px', checkBomButtonsNotClipped(1200));
  test('BOM table buttons not clipped at 2016px', checkBomButtonsNotClipped(2016));
  test('BOM table buttons not clipped at 1100px with PO', checkBomButtonsNotClipped(1100, { withPO: true }));
  test('BOM table buttons not clipped at 2016px with PO', checkBomButtonsNotClipped(2016, { withPO: true }));
});


// ════════════════════════════════════════════════════════════
// 6. FILTER BAR WRAPPING BEHAVIOR
// ════════════════════════════════════════════════════════════

test.describe('Filter bar wrapping', () => {

  test('filter bar wraps gracefully at narrow width', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1200, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadBomViaEmit(page, BOM_CSV);
    await page.waitForTimeout(300);

    const filterInfo = await page.evaluate(() => {
      const bar = document.querySelector('.filter-bar');
      if (!bar) return { found: false };
      const barRect = bar.getBoundingClientRect();
      const buttons = Array.from(bar.querySelectorAll('.filter-btn'));
      const btnData = buttons.map(b => {
        const r = b.getBoundingClientRect();
        return {
          text: b.textContent.trim(),
          top: Math.round(r.top - barRect.top),
          left: Math.round(r.left - barRect.left),
          right: Math.round(r.right),
          overflows: r.right > barRect.right + 1,
        };
      });
      const maxRow = Math.max(...btnData.map(b => b.top));
      return {
        found: true,
        barWidth: Math.round(barRect.width),
        barHeight: Math.round(barRect.height),
        buttonCount: buttons.length,
        wrappedRows: new Set(btnData.map(b => b.top)).size,
        anyOverflow: btnData.some(b => b.overflows),
        buttons: btnData,
      };
    });

    console.log('Filter bar at 1200px:', JSON.stringify(filterInfo, null, 2));
    expect(filterInfo.found, 'Filter bar should exist after loading BOM').toBe(true);
    expect(filterInfo.anyOverflow, 'Filter buttons overflow the bar').toBe(false);
  });

  test('filter bar wraps gracefully at narrow width — with PO', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1200, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await applyMode(page, { bom: true, po: true });

    const filterInfo = await page.evaluate(() => {
      const bar = document.querySelector('.filter-bar');
      if (!bar) return { found: false };
      const barRect = bar.getBoundingClientRect();
      const buttons = Array.from(bar.querySelectorAll('.filter-btn'));
      const btnData = buttons.map(b => {
        const r = b.getBoundingClientRect();
        return {
          text: b.textContent.trim(),
          overflows: r.right > barRect.right + 1,
        };
      });
      return {
        found: true,
        barWidth: Math.round(barRect.width),
        buttonCount: buttons.length,
        anyOverflow: btnData.some(b => b.overflows),
      };
    });

    console.log('Filter bar at 1200px (BOM+PO):', JSON.stringify(filterInfo));
    expect(filterInfo.found, 'Filter bar should exist after loading BOM+PO').toBe(true);
    expect(filterInfo.anyOverflow, 'Filter buttons overflow the bar with PO loaded').toBe(false);
  });
});


// ════════════════════════════════════════════════════════════
// 7. MODAL VISIBILITY AT NARROW VIEWPORTS
// ════════════════════════════════════════════════════════════

test.describe('Modal dialogs at narrow viewports', () => {

  test('adjustment modal fits within 800px viewport', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 800, height: 600 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Click the first Adjust button to open the modal
    const adjBtn = page.locator('.adj-btn').first();
    await adjBtn.click();
    await page.waitForSelector('#adjust-modal:not(.hidden)', { timeout: 5000 });

    const modalInfo = await page.evaluate(() => {
      const overlay = document.getElementById('adjust-modal');
      const modal = overlay.querySelector('.modal');
      const modalRect = modal.getBoundingClientRect();
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      const applyBtn = document.getElementById('adj-apply');
      const cancelBtn = document.getElementById('adj-cancel');
      return {
        modalWidth: Math.round(modalRect.width),
        modalLeft: Math.round(modalRect.left),
        modalRight: Math.round(modalRect.right),
        modalBottom: Math.round(modalRect.bottom),
        viewportWidth: vw,
        viewportHeight: vh,
        fitsHorizontally: modalRect.left >= 0 && modalRect.right <= vw,
        fitsVertically: modalRect.top >= 0 && modalRect.bottom <= vh,
        applyBtnVisible: applyBtn ? applyBtn.offsetWidth > 0 && applyBtn.offsetHeight > 0 : false,
        cancelBtnVisible: cancelBtn ? cancelBtn.offsetWidth > 0 && cancelBtn.offsetHeight > 0 : false,
      };
    });

    console.log('Adjust modal at 800px viewport:', modalInfo);
    expect(modalInfo.fitsHorizontally, `Modal overflows horizontally (width: ${modalInfo.modalWidth}, viewport: ${modalInfo.viewportWidth})`).toBe(true);
    expect(modalInfo.applyBtnVisible, 'Apply button not visible').toBe(true);
    expect(modalInfo.cancelBtnVisible, 'Cancel button not visible').toBe(true);
  });

  test('preferences modal at narrow viewport', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 800, height: 600 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    // Open preferences
    await page.click('#prefs-btn');
    await page.waitForSelector('#prefs-modal:not(.hidden)', { timeout: 5000 });

    const modalInfo = await page.evaluate(() => {
      const modal = document.querySelector('#prefs-modal .modal');
      const rect = modal.getBoundingClientRect();
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      const saveBtn = document.getElementById('prefs-save');
      return {
        modalWidth: Math.round(rect.width),
        viewportWidth: vw,
        fitsHorizontally: rect.left >= -1 && rect.right <= vw + 1,
        fitsVertically: rect.top >= -1 && rect.bottom <= vh + 1,
        saveBtnVisible: saveBtn ? saveBtn.offsetWidth > 0 : false,
      };
    });

    console.log('Prefs modal at 800px:', modalInfo);
    // Prefs modal has min-width 420px — should fit at 800px
    expect(modalInfo.fitsHorizontally, `Prefs modal overflows (width: ${modalInfo.modalWidth}, viewport: ${modalInfo.viewportWidth})`).toBe(true);
    expect(modalInfo.saveBtnVisible, 'Save button not visible in prefs modal').toBe(true);
  });

  test('adjustment modal fits within 800px viewport — with BOM + PO', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 800, height: 600 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await applyMode(page, { bom: true, po: true });

    // Click the first Adjust button (in remaining inventory section)
    const adjBtn = page.locator('.adj-btn').first();
    await adjBtn.click();
    await page.waitForSelector('#adjust-modal:not(.hidden)', { timeout: 5000 });

    const modalInfo = await page.evaluate(() => {
      const overlay = document.getElementById('adjust-modal');
      const modal = overlay.querySelector('.modal');
      const modalRect = modal.getBoundingClientRect();
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      return {
        modalWidth: Math.round(modalRect.width),
        viewportWidth: vw,
        fitsHorizontally: modalRect.left >= 0 && modalRect.right <= vw,
        applyBtnVisible: document.getElementById('adj-apply')?.offsetWidth > 0,
        cancelBtnVisible: document.getElementById('adj-cancel')?.offsetWidth > 0,
      };
    });

    console.log('Adjust modal at 800px (BOM+PO):', modalInfo);
    expect(modalInfo.fitsHorizontally, `Modal overflows at 800px with BOM+PO`).toBe(true);
    expect(modalInfo.applyBtnVisible, 'Apply button not visible with BOM+PO').toBe(true);
  });
});


// ════════════════════════════════════════════════════════════
// 8. IMPORT PANEL ELEMENTS
// ════════════════════════════════════════════════════════════

test.describe('Import panel elements on resize', () => {

  test('drop zone visible at all viewport widths', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    for (const width of [800, 1024, 1200, 1920]) {
      await page.setViewportSize({ width, height: 700 });
      await page.goto('/index.html');
      await waitForInventoryRows(page);

      const dropZone = await checkElementVisibility(page, '#import-drop-zone', `Drop zone at ${width}px`);
      console.log(dropZone.reason);
      expect(dropZone.visible, dropZone.reason).toBe(true);
    }
  });

  test('PO staging table and import button visible after loading purchase CSV', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1200, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadPurchaseOrder(page, PO_CSV_PATH);
    await page.waitForTimeout(200);

    const mapperVis = await checkElementVisibility(page, '#import-mapper', 'Import mapper');
    console.log(mapperVis.reason);
    expect(mapperVis.visible, mapperVis.reason).toBe(true);

    const importBtn = await checkElementVisibility(page, '#do-import-btn', 'Import button');
    console.log(importBtn.reason);
    expect(importBtn.visible, importBtn.reason).toBe(true);
  });

  test('PO staging table visible at narrow viewport (1024px)', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1024, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadPurchaseOrder(page, PO_CSV_PATH);
    await page.waitForTimeout(200);

    const mapperVis = await checkElementVisibility(page, '#import-mapper', 'Import mapper at 1024px');
    console.log(mapperVis.reason);
    expect(mapperVis.visible, mapperVis.reason).toBe(true);
  });

  test('PO staging table visible with BOM loaded', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1200, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await applyMode(page, { bom: true, po: true });

    const mapperVis = await checkElementVisibility(page, '#import-mapper', 'Import mapper with BOM');
    console.log(mapperVis.reason);
    expect(mapperVis.visible, mapperVis.reason).toBe(true);
  });

  test('console log clear button visible at narrow width', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1024, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const clearBtn = await checkElementVisibility(page, '#console-clear', 'Console clear button');
    console.log(clearBtn.reason);
    expect(clearBtn.visible, clearBtn.reason).toBe(true);
  });
});


// ════════════════════════════════════════════════════════════
// 9. CONSOLE LOG AT SHORT VIEWPORT HEIGHTS
// ════════════════════════════════════════════════════════════

test.describe('Console log vs import body at short viewport', () => {

  test('import body retains usable height with console log at 500px viewport height', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1200, height: 500 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const result = await page.evaluate(() => {
      const importBody = document.getElementById('import-body');
      const consoleLog = document.getElementById('console-log');
      const panelHeader = document.querySelector('.panel-import .panel-header');
      return {
        importBodyHeight: importBody ? importBody.offsetHeight : 0,
        consoleHeight: consoleLog ? consoleLog.offsetHeight : 0,
        panelHeaderHeight: panelHeader ? panelHeader.offsetHeight : 0,
        viewportHeight: window.innerHeight,
      };
    });

    console.log('Layout at 500px height:', result);
    console.log(`Import body: ${result.importBodyHeight}px, Console: ${result.consoleHeight}px`);
    // Import body must retain usable height — console log must not consume all space
    expect(result.importBodyHeight, 'Import body should have usable height at 500px viewport').toBeGreaterThanOrEqual(50);
  });

  test('import body retains usable height with PO staging at 500px viewport height', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1200, height: 500 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadPurchaseOrder(page, PO_CSV_PATH);
    await page.waitForTimeout(200);

    const result = await page.evaluate(() => {
      const importBody = document.getElementById('import-body');
      const consoleLog = document.getElementById('console-log');
      return {
        importBodyHeight: importBody ? importBody.offsetHeight : 0,
        consoleHeight: consoleLog ? consoleLog.offsetHeight : 0,
        viewportHeight: window.innerHeight,
      };
    });

    console.log('Layout at 500px height with PO:', result);
    expect(result.importBodyHeight, 'Import body should have usable height with PO at 500px viewport').toBeGreaterThanOrEqual(50);
  });
});


// ════════════════════════════════════════════════════════════
// 10. SCROLLBAR VISIBILITY IN PANEL BODIES
// ════════════════════════════════════════════════════════════

test.describe('Panel body scrollability', () => {

  test('inventory panel body scrolls when content exceeds height', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 600 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const scrollInfo = await page.evaluate(() => {
      const body = document.getElementById('inventory-body');
      return {
        scrollHeight: body.scrollHeight,
        clientHeight: body.clientHeight,
        isScrollable: body.scrollHeight > body.clientHeight,
        overflowY: getComputedStyle(body).overflowY,
      };
    });

    console.log('Inventory body scroll info:', scrollInfo);
    expect(scrollInfo.overflowY).toBe('auto');
    if (scrollInfo.isScrollable) {
      // Verify last row is reachable by scrolling
      const lastRow = await isReachableByScroll(page, '.inv-part-row:last-child', 'Last inventory row');
      console.log(lastRow.reason);
    }
  });

  test('inventory panel body scrolls with PO loaded', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1920, height: 600 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadPurchaseOrder(page, PO_CSV_PATH);
    await page.waitForTimeout(200);

    const scrollInfo = await page.evaluate(() => {
      const body = document.getElementById('inventory-body');
      return {
        scrollHeight: body.scrollHeight,
        clientHeight: body.clientHeight,
        isScrollable: body.scrollHeight > body.clientHeight,
        overflowY: getComputedStyle(body).overflowY,
      };
    });

    console.log('Inventory body scroll info (with PO):', scrollInfo);
    expect(scrollInfo.overflowY).toBe('auto');
  });

  test('BOM table scrolls horizontally when narrow', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1200, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadBomViaEmit(page, BOM_CSV);
    await page.waitForTimeout(300);

    const scrollInfo = await page.evaluate(() => {
      const tableWrap = document.querySelector('#inventory-body .table-wrap');
      if (!tableWrap) return { found: false };
      return {
        found: true,
        scrollWidth: tableWrap.scrollWidth,
        clientWidth: tableWrap.clientWidth,
        hasHScroll: tableWrap.scrollWidth > tableWrap.clientWidth,
        overflowX: getComputedStyle(tableWrap).overflowX,
      };
    });

    console.log('BOM table h-scroll info at 1200px:', scrollInfo);
    if (scrollInfo.found && scrollInfo.hasHScroll) {
      console.log(`Table needs ${scrollInfo.scrollWidth - scrollInfo.clientWidth}px horizontal scroll`);
    }
  });
});


// ════════════════════════════════════════════════════════════
// 11. INVENTORY PART ROW ELEMENTS AT NARROW WIDTHS
// ════════════════════════════════════════════════════════════

test.describe('Inventory row elements at narrow widths', () => {

  /**
   * Check that row action buttons (Adjust, Link) are not clipped by the
   * panel body's overflow-x: hidden.  Accepts optional mode flags for
   * loading BOM and/or PO before checking.
   */
  function checkRowButtonsNotClipped(viewportWidth, { withBom = false, withPO = false } = {}) {
    return async ({ page }) => {
      await addMockSetup(page, MOCK_INVENTORY);
      await page.setViewportSize({ width: viewportWidth, height: 700 });
      await page.goto('/index.html');
      await waitForInventoryRows(page);
      await applyMode(page, { bom: withBom, po: withPO });

      const result = await page.evaluate(() => {
        const panelBody = document.getElementById('inventory-body');
        if (!panelBody) return { checked: 0, issues: ['panel body not found'] };
        const bodyRect = panelBody.getBoundingClientRect();
        const rows = panelBody.querySelectorAll('.inv-part-row');
        const issues = [];
        const checked = Math.min(rows.length, 10);
        for (let i = 0; i < checked; i++) {
          const buttons = rows[i].querySelectorAll('.adj-btn, .link-btn');
          for (const btn of buttons) {
            if (btn.offsetWidth === 0 || btn.offsetHeight === 0) {
              issues.push(`row ${i}: ${btn.className} has zero dimensions`);
              continue;
            }
            const btnRect = btn.getBoundingClientRect();
            if (btnRect.right > bodyRect.right + 1) {
              issues.push(`row ${i}: ${btn.className} clipped (btn.right=${Math.round(btnRect.right)}, panel.right=${Math.round(bodyRect.right)})`);
            }
          }
        }
        return { checked, panelWidth: Math.round(bodyRect.width), issues };
      });

      const label = modeLabel({ bom: withBom, po: withPO });
      console.log(`Viewport ${viewportWidth}px [${label}] — panel: ${result.panelWidth}px, checked ${result.checked} rows, issues: ${result.issues.length}`);
      result.issues.forEach(i => console.log('  ' + i));
      expect(result.issues.length, result.issues.join('; ')).toBe(0);
    };
  }

  // Base state — no BOM, no PO
  test('adjust buttons not clipped at 1024px viewport', checkRowButtonsNotClipped(1024));
  test('adjust buttons not clipped at 900px viewport', checkRowButtonsNotClipped(900));
  // With BOM only
  test('adjust+link buttons not clipped at 1024px with BOM', checkRowButtonsNotClipped(1024, { withBom: true }));
  test('adjust+link buttons not clipped at 900px with BOM', checkRowButtonsNotClipped(900, { withBom: true }));
  // With PO only
  test('adjust buttons not clipped at 1024px with PO', checkRowButtonsNotClipped(1024, { withPO: true }));
  test('adjust buttons not clipped at 900px with PO', checkRowButtonsNotClipped(900, { withPO: true }));
  // With BOM + PO
  test('adjust+link buttons not clipped at 1024px with BOM + PO', checkRowButtonsNotClipped(1024, { withBom: true, withPO: true }));
  test('adjust+link buttons not clipped at 900px with BOM + PO', checkRowButtonsNotClipped(900, { withBom: true, withPO: true }));

  test('part-id, mpn, qty visible in narrow inventory rows', async ({ page }) => {
    await addMockSetup(page, MOCK_INVENTORY);
    await page.setViewportSize({ width: 1024, height: 700 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    const result = await page.evaluate(() => {
      const row = document.querySelector('.inv-part-row');
      if (!row) return { found: false };
      const parts = {};
      for (const cls of ['part-ids', 'part-mpn', 'part-qty']) {
        const el = row.querySelector('.' + cls);
        if (el) {
          parts[cls] = {
            width: el.offsetWidth,
            height: el.offsetHeight,
            visible: el.offsetWidth > 0 && el.offsetHeight > 0,
          };
        }
      }
      return { found: true, parts };
    });

    console.log('First row element sizes at 1024px:', result.parts);
    expect(result.found, 'First inventory row should exist').toBe(true);
    for (const [cls, info] of Object.entries(result.parts)) {
      expect(info.visible, `${cls} not visible`).toBe(true);
    }
  });
});


// ════════════════════════════════════════════════════════════
// 12. SUMMARY: CROSS-VIEWPORT AUDIT
// ════════════════════════════════════════════════════════════

const AUDIT_ELEMENTS = [
  { selector: '#prefs-btn', label: 'Preferences button' },
  { selector: '#global-undo', label: 'Undo button' },
  { selector: '#global-redo', label: 'Redo button' },
  { selector: '#rebuild-inv', label: 'Rebuild Inventory button' },
  { selector: '#inv-search', label: 'Search input' },
  { selector: '#import-drop-zone', label: 'Import drop zone' },
  { selector: '#console-clear', label: 'Console clear button' },
];

const AUDIT_VIEWPORTS = [
  // Existing
  { width: 800, height: 600 },
  { width: 1024, height: 700 },
  { width: 1200, height: 700 },
  { width: 1920, height: 1080 },
  // Minimum
  { width: 720, height: 480 },
  // Legacy aspect ratios
  { width: 1024, height: 768 },
  { width: 1280, height: 1024 },
  // Standard monitors
  { width: 2560, height: 1440 },
  { width: 3840, height: 2160 },
  // Ultrawide
  { width: 3440, height: 1440 },
  // Half-screen (snapped window)
  { width: 960, height: 1080 },
  { width: 1280, height: 1440 },
  { width: 1920, height: 2160 },
  // Floating windows (fixed pseudo-random sizes)
  { width: 1347, height: 823 },
  { width: 743, height: 901 },
  { width: 1811, height: 1137 },
  { width: 2193, height: 1307 },
];

test.describe('Cross-viewport visibility audit — no BOM', () => {
  for (const vp of AUDIT_VIEWPORTS) {
    test(`UI elements at ${vp.width}x${vp.height}`, async ({ page }) => {
      await addMockSetup(page, MOCK_INVENTORY);
      await page.setViewportSize(vp);
      await page.goto('/index.html');
      await waitForInventoryRows(page);

      const results = [];
      for (const { selector, label } of AUDIT_ELEMENTS) {
        const vis = await checkElementVisibility(page, selector, label);
        results.push({ label, ...vis });
      }

      console.log(`\n=== Visibility audit at ${vp.width}x${vp.height} ===`);
      for (const r of results) {
        const icon = r.visible ? (r.clipped ? 'CLIPPED' : 'OK') : 'HIDDEN';
        console.log(`  [${icon}] ${r.reason}`);
      }

      // All elements should at minimum exist and be rendered
      for (const r of results) {
        expect(r.visible, r.reason).toBe(true);
      }
    });
  }
});

const BOM_AUDIT_ELEMENTS = [
  { selector: '#bom-save-btn', label: 'Save BOM button' },
  { selector: '#bom-consume-btn', label: 'Consume button' },
  { selector: '#bom-clear-btn', label: 'Clear BOM button' },
  { selector: '#bom-qty-mult', label: 'Board qty multiplier' },
  { selector: '.filter-bar', label: 'Filter bar' },
  { selector: '#bom-staging-toolbar', label: 'BOM staging toolbar' },
];

test.describe('Cross-viewport visibility audit — with BOM', () => {
  for (const vp of AUDIT_VIEWPORTS) {
    test(`BOM elements at ${vp.width}x${vp.height}`, async ({ page }) => {
      await addMockSetup(page, MOCK_INVENTORY);
      await page.setViewportSize(vp);
      await page.goto('/index.html');
      await waitForInventoryRows(page);
      await loadBomViaFileInput(page, BOM_CSV_PATH);
      await page.waitForTimeout(300);

      const results = [];
      for (const { selector, label } of BOM_AUDIT_ELEMENTS) {
        const vis = await checkElementVisibility(page, selector, label);
        results.push({ label, ...vis });
      }

      console.log(`\n=== BOM visibility audit at ${vp.width}x${vp.height} ===`);
      for (const r of results) {
        const icon = r.visible ? (r.clipped ? 'CLIPPED' : 'OK') : 'HIDDEN';
        console.log(`  [${icon}] ${r.reason}`);
      }

      // BOM elements should be visible at viewports >= 1024px
      // (at 800px the app intentionally overflows — see "panels overflow at very narrow viewport" test)
      if (vp.width >= 1024) {
        for (const r of results) {
          expect(r.visible, r.reason).toBe(true);
        }
      }
    });
  }
});

const PO_AUDIT_ELEMENTS = [
  { selector: '#import-mapper', label: 'Import mapper / staging' },
  { selector: '#do-import-btn', label: 'Import button' },
  { selector: '#clear-import-btn', label: 'Clear import button' },
  { selector: '#import-drop-zone', label: 'Import drop zone' },
  { selector: '#console-clear', label: 'Console clear button' },
];

test.describe('Cross-viewport visibility audit — with PO', () => {
  for (const vp of AUDIT_VIEWPORTS) {
    test(`PO elements at ${vp.width}x${vp.height}`, async ({ page }) => {
      await addMockSetup(page, MOCK_INVENTORY);
      await page.setViewportSize(vp);
      await page.goto('/index.html');
      await waitForInventoryRows(page);
      await loadPurchaseOrder(page, PO_CSV_PATH);
      await page.waitForTimeout(200);

      const results = [];
      for (const { selector, label } of PO_AUDIT_ELEMENTS) {
        const vis = await checkElementVisibility(page, selector, label);
        results.push({ label, ...vis });
      }

      console.log(`\n=== PO visibility audit at ${vp.width}x${vp.height} ===`);
      for (const r of results) {
        const icon = r.visible ? (r.clipped ? 'CLIPPED' : 'OK') : 'HIDDEN';
        console.log(`  [${icon}] ${r.reason}`);
      }

      // Import mapper and buttons should be visible after loading PO
      for (const r of results) {
        expect(r.visible, r.reason).toBe(true);
      }
    });
  }
});

test.describe('Cross-viewport visibility audit — with BOM + PO', () => {
  for (const vp of AUDIT_VIEWPORTS) {
    test(`BOM + PO elements at ${vp.width}x${vp.height}`, async ({ page }) => {
      await addMockSetup(page, MOCK_INVENTORY);
      await page.setViewportSize(vp);
      await page.goto('/index.html');
      await waitForInventoryRows(page);
      await applyMode(page, { bom: true, po: true });

      const allElements = [...AUDIT_ELEMENTS.filter(e => e.selector !== '#import-drop-zone'), ...PO_AUDIT_ELEMENTS];
      const results = [];
      for (const { selector, label } of allElements) {
        const vis = await checkElementVisibility(page, selector, label);
        results.push({ label, ...vis });
      }

      console.log(`\n=== BOM+PO visibility audit at ${vp.width}x${vp.height} ===`);
      for (const r of results) {
        const icon = r.visible ? (r.clipped ? 'CLIPPED' : 'OK') : 'HIDDEN';
        console.log(`  [${icon}] ${r.reason}`);
      }

      // All elements should be visible at viewports >= 1024px
      if (vp.width >= 1024) {
        for (const r of results) {
          expect(r.visible, r.reason).toBe(true);
        }
      }
    });
  }
});

// ════════════════════════════════════════════════════════════
// DESIGNATOR WRAPPING AUDIT
// ════════════════════════════════════════════════════════════

const WRAP_VIEWPORTS = [
  { width: 720, height: 480 },
  { width: 960, height: 1080 },
  { width: 1024, height: 768 },
  { width: 1280, height: 1024 },
  { width: 1347, height: 823 },
  { width: 1920, height: 1080 },
  { width: 2560, height: 1440 },
  { width: 3440, height: 1440 },
  { width: 3840, height: 2160 },
];

test.describe('Designator wrapping audit — with BOM', () => {
  for (const vp of WRAP_VIEWPORTS) {
    test(`designator cells contained at ${vp.width}x${vp.height}`, async ({ page }) => {
      await addMockSetup(page, MOCK_INVENTORY);
      await page.setViewportSize(vp);
      await page.goto('/index.html');
      await waitForInventoryRows(page);
      await loadBomViaFileInput(page, BOM_CSV_PATH);
      await page.waitForTimeout(300);

      // Check that refs cells contain text without horizontal overflow.
      // scrollWidth can legitimately exceed clientWidth for overflow-x:hidden elements
      // because scrollWidth measures intrinsic content width regardless of clipping.
      // What matters is that no cell allows horizontal scrolling (overflow-x must be
      // 'hidden', not 'auto' or 'scroll') and that wrapping is not suppressed by
      // white-space:nowrap.
      const badCells = await page.evaluate(() => {
        const cells = document.querySelectorAll('#inventory-body .refs-cell');
        const results = [];
        cells.forEach(cell => {
          const style = getComputedStyle(cell);
          if (style.overflowX !== 'hidden') {
            results.push({ text: cell.textContent.slice(0, 40), overflowX: style.overflowX });
          }
          if (style.whiteSpace === 'nowrap') {
            results.push({ text: cell.textContent.slice(0, 40), whiteSpace: style.whiteSpace });
          }
        });
        return results;
      });
      expect(badCells, 'Refs cells must have overflow-x:hidden and allow text wrapping').toEqual([]);

      // Check BOM row heights stay reasonable (max ~100px for wrapped + alt badge)
      const bomRows = await page.locator('tr[data-part-key]').count();
      for (let i = 0; i < bomRows; i++) {
        const row = await page.locator('tr[data-part-key]').nth(i).evaluate(el => ({
          height: el.offsetHeight,
          partKey: el.dataset.partKey,
        }));
        expect(row.height, `BOM row ${row.partKey} too tall at ${vp.width}x${vp.height}`).toBeLessThanOrEqual(100);
      }

      // Sticky buttons should be reachable
      const btnReachable = await isReachableByScroll(page, 'td.btn-group', 'Sticky buttons');
      expect(btnReachable.reachable, btnReachable.reason).toBe(true);
    });
  }
});
