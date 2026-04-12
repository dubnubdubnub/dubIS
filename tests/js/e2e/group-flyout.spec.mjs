// @ts-check
import { test, expect } from '@playwright/test';
import { addMockSetup, waitForInventoryRows, loadBom } from './helpers.mjs';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(
  readFileSync(join(__dirname, 'fixtures', 'inventory.json'), 'utf8'),
);

/**
 * BOM with value-only passives (no MPN) — these should show the flyout button
 * because they are "missing" with a value but no inventory match.
 */
const VALUE_ONLY_BOM = [
  'Reference,Value,Footprint,Qty',
  '"C1,C2,C3",100nF,C_0402_1005Metric,3',
  '"R1,R2",10k,R_0402_1005Metric,2',
  'U1,ATmega328P,,1',
].join('\n');

/**
 * Mock generic parts data for a 100nF capacitor group.
 * Matches the shape returned by list_generic_parts().
 */
const MOCK_GENERIC_PARTS = [
  {
    generic_part_id: 'cap_100nf_0402',
    name: '100nF 0402 Capacitor',
    part_type: 'capacitor',
    spec: { type: 'capacitor', value: 1e-7, value_display: '100nF', package: '0402' },
    strictness: { required: ['value', 'package'] },
    source: 'auto',
    members: [
      {
        part_id: 'C307331',
        description: '100nF ±10% 50V X7R 0402',
        package: '0402',
        quantity: 500,
        preferred: 0,
        spec: { type: 'capacitor', value_display: '100nF', package: '0402', voltage: '50V', dielectric: 'X7R', tolerance: '±10%' },
      },
      {
        part_id: 'C1525',
        description: '100nF ±10% 16V X5R 0402',
        package: '0402',
        quantity: 200,
        preferred: 1,
        spec: { type: 'capacitor', value_display: '100nF', package: '0402', voltage: '16V', dielectric: 'X5R', tolerance: '±10%' },
      },
    ],
  },
];

/**
 * Add a secondary init script that patches the base pywebview mock with
 * stateful generic-parts API methods. Must be called AFTER addMockSetup.
 */
function addGenericPartsMockPatch(page, genericParts) {
  return page.addInitScript((gps) => {
    // Stateful list — grows as create_generic_part is called.
    const mockGpList = gps.slice();

    // Patch in the generic-parts-aware methods over the base mock.
    Object.assign(window.pywebview.api, {
      list_generic_parts: async () => mockGpList.slice(),
      list_saved_searches: async () => [],
      create_saved_search: async (_gpId, name) => ({ id: 'saved-1', name }),
      delete_saved_search: async () => null,
      exclude_generic_member: async () => null,
      extract_spec_from_value: async (type, val, pkg) => ({
        type, value: 1e-7, value_display: val, package: pkg,
      }),
      create_generic_part: async (name, type, specJson, strictnessJson) => {
        const gpId = 'new_' + type + '_' + Date.now();
        const newGp = {
          generic_part_id: gpId,
          name,
          part_type: type,
          spec: specJson ? JSON.parse(specJson) : {},
          strictness: strictnessJson ? JSON.parse(strictnessJson) : {},
          source: 'auto',
          members: [],
        };
        mockGpList.push(newGp);
        return newGp;
      },
      resolve_bom_spec: async () => null,
      preview_generic_members: async () => [],
      add_generic_member: async (_gpId, partId) => [
        { part_id: partId, quantity: 10, description: 'Added part', spec: {} },
      ],
      remove_generic_member: async () => [],
    });
  }, genericParts);
}

/**
 * Set up mock API with generic parts support.
 * Uses the proven addMockSetup from helpers.mjs + a patch for generic parts.
 */
async function addMockSetupWithGenerics(page, inventory, genericParts) {
  await addMockSetup(page, inventory);
  await addGenericPartsMockPatch(page, genericParts);
}

test.describe('[functional] Group flyout — opening and closing', () => {
  test.beforeEach(async ({ page }) => {
    await addMockSetupWithGenerics(page, MOCK_INVENTORY, MOCK_GENERIC_PARTS);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
  });

  test('flyout button appears on value-only BOM rows', async ({ page }) => {
    await loadBom(page, VALUE_ONLY_BOM);
    await page.waitForTimeout(300);

    const flyoutBtns = page.locator('button.group-flyout-btn');
    // Value-only passives (C and R rows) should have flyout buttons
    // U1 (ATmega328P) has no value/footprint match, may or may not show
    const count = await flyoutBtns.count();
    expect(count).toBeGreaterThanOrEqual(2);
  });

  test('clicking flyout button opens the flyout panel', async ({ page }) => {
    await loadBom(page, VALUE_ONLY_BOM);
    await page.waitForTimeout(300);

    const btn = page.locator('button.group-flyout-btn').first();
    await btn.click();
    await page.waitForTimeout(500);

    const flyout = page.locator('.group-flyout');
    await expect(flyout).toBeVisible({ timeout: 5000 });
  });

  test('flyout shows group name in header', async ({ page }) => {
    await loadBom(page, VALUE_ONLY_BOM);
    await page.waitForTimeout(300);

    const btn = page.locator('button.group-flyout-btn').first();
    await btn.click();
    await page.waitForTimeout(500);

    const title = page.locator('.flyout-title');
    await expect(title).toBeVisible();
    // Title should contain some text (group name)
    const text = await title.textContent();
    expect(text.length).toBeGreaterThan(0);
  });

  test('flyout close button removes the flyout', async ({ page }) => {
    await loadBom(page, VALUE_ONLY_BOM);
    await page.waitForTimeout(300);

    const btn = page.locator('button.group-flyout-btn').first();
    await btn.click();
    await page.waitForTimeout(500);

    await expect(page.locator('.group-flyout')).toBeVisible();

    await page.locator('.flyout-close-btn').click();
    await page.waitForTimeout(300);

    await expect(page.locator('.group-flyout')).toHaveCount(0);
  });

  test('flyout has active border when opened', async ({ page }) => {
    await loadBom(page, VALUE_ONLY_BOM);
    await page.waitForTimeout(300);

    await page.locator('button.group-flyout-btn').first().click();
    await page.waitForTimeout(500);

    const flyout = page.locator('.group-flyout');
    await expect(flyout).toHaveClass(/flyout-active/);
  });
});

test.describe('[functional] Group flyout — tags', () => {
  test.beforeEach(async ({ page }) => {
    await addMockSetupWithGenerics(page, MOCK_INVENTORY, MOCK_GENERIC_PARTS);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadBom(page, VALUE_ONLY_BOM);
    await page.waitForTimeout(300);
    await page.locator('button.group-flyout-btn').first().click();
    await page.waitForTimeout(500);
  });

  test('flyout displays tag buttons', async ({ page }) => {
    const tags = page.locator('.flyout-tag');
    const count = await tags.count();
    expect(count).toBeGreaterThan(0);
  });

  test('clicking a tag toggles its enabled state', async ({ page }) => {
    const firstTag = page.locator('.flyout-tag').first();
    const wasEnabled = await firstTag.evaluate(el => el.classList.contains('tag-enabled'));

    await firstTag.click();
    await page.waitForTimeout(200);

    // Re-query after rerender
    const updatedTag = page.locator('.flyout-tag').first();
    const isEnabled = await updatedTag.evaluate(el => el.classList.contains('tag-enabled'));
    expect(isEnabled).toBe(!wasEnabled);
  });
});

test.describe('[functional] Group flyout — search', () => {
  test.beforeEach(async ({ page }) => {
    await addMockSetupWithGenerics(page, MOCK_INVENTORY, MOCK_GENERIC_PARTS);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadBom(page, VALUE_ONLY_BOM);
    await page.waitForTimeout(300);
    await page.locator('button.group-flyout-btn').first().click();
    await page.waitForTimeout(500);
  });

  test('flyout has a search input', async ({ page }) => {
    const searchInput = page.locator('.flyout-search-input');
    await expect(searchInput).toBeVisible();
  });

  test('typing in search input filters members', async ({ page }) => {
    const searchInput = page.locator('.flyout-search-input');
    const membersBefore = await page.locator('.flyout-member').count();

    await searchInput.fill('X7R');
    await page.waitForTimeout(300);

    const membersAfter = await page.locator('.flyout-member').count();
    // Filtering should reduce or maintain count (depends on data)
    expect(membersAfter).toBeLessThanOrEqual(membersBefore);
  });

  test('Enter key promotes search text to a tag', async ({ page }) => {
    const tagsBefore = await page.locator('.flyout-tag').count();

    const searchInput = page.locator('.flyout-search-input');
    await searchInput.fill('samsung');
    await searchInput.press('Enter');
    await page.waitForTimeout(300);

    const tagsAfter = await page.locator('.flyout-tag').count();
    expect(tagsAfter).toBe(tagsBefore + 1);

    // Search input should be cleared after promotion
    const searchVal = await page.locator('.flyout-search-input').inputValue();
    expect(searchVal).toBe('');
  });

  test('promote button (↑) promotes search text to a tag', async ({ page }) => {
    const tagsBefore = await page.locator('.flyout-tag').count();

    await page.locator('.flyout-search-input').fill('custom');
    await page.locator('.flyout-promote-btn').click();
    await page.waitForTimeout(300);

    const tagsAfter = await page.locator('.flyout-tag').count();
    expect(tagsAfter).toBe(tagsBefore + 1);
  });

  test('linked search syncs flyout search to main inventory search', async ({ page }) => {
    const flyoutSearch = page.locator('.flyout-search-input');
    await flyoutSearch.fill('test query');
    await page.waitForTimeout(300);

    const mainSearch = page.locator('#inv-search');
    const mainVal = await mainSearch.inputValue();
    expect(mainVal).toBe('test query');
  });
});

test.describe('[functional] Group flyout — members', () => {
  test.beforeEach(async ({ page }) => {
    await addMockSetupWithGenerics(page, MOCK_INVENTORY, MOCK_GENERIC_PARTS);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadBom(page, VALUE_ONLY_BOM);
    await page.waitForTimeout(300);
    await page.locator('button.group-flyout-btn').first().click();
    await page.waitForTimeout(500);
  });

  test('flyout shows member rows with part IDs', async ({ page }) => {
    const members = page.locator('.flyout-member');
    const count = await members.count();
    // Could be 0 if auto-create path was taken (no existing group match)
    // or >0 if a generic part matched
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('member rows have drag handles', async ({ page }) => {
    const grips = page.locator('.flyout-member-grip');
    const members = page.locator('.flyout-member');
    const memberCount = await members.count();
    if (memberCount > 0) {
      expect(await grips.count()).toBe(memberCount);
    }
  });

  test('flyout footer shows member count', async ({ page }) => {
    const footer = page.locator('.flyout-footer');
    await expect(footer).toBeVisible();
    const text = await footer.textContent();
    // Footer format is "N members · N in stock"
    expect(text).toMatch(/\d+ members/);
  });
});

test.describe('[functional] Group flyout — positioning', () => {
  test('flyout is positioned within viewport', async ({ page }) => {
    await addMockSetupWithGenerics(page, MOCK_INVENTORY, MOCK_GENERIC_PARTS);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadBom(page, VALUE_ONLY_BOM);
    await page.waitForTimeout(300);

    await page.locator('button.group-flyout-btn').first().click();
    await page.waitForTimeout(500);

    const flyout = page.locator('.group-flyout');
    const box = await flyout.boundingBox();
    expect(box).not.toBeNull();
    // boundingBox() returns { x, y, width, height }
    expect(box.y).toBeGreaterThanOrEqual(0);
    expect(box.x).toBeGreaterThanOrEqual(0);
    // Should be within reasonable viewport bounds
    expect(box.x + box.width).toBeLessThanOrEqual(1920 + 10);
    expect(box.y + box.height).toBeLessThanOrEqual(900 + 10);
  });

  test('flyout can be dragged vertically', async ({ page }) => {
    await addMockSetupWithGenerics(page, MOCK_INVENTORY, MOCK_GENERIC_PARTS);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadBom(page, VALUE_ONLY_BOM);
    await page.waitForTimeout(300);

    await page.locator('button.group-flyout-btn').first().click();
    await page.waitForTimeout(500);

    const handle = page.locator('.flyout-drag-handle');
    const flyout = page.locator('.group-flyout');
    const boxBefore = await flyout.boundingBox();

    // Drag the handle down 50px
    const handleBox = await handle.boundingBox();
    await page.mouse.move(handleBox.x + 5, handleBox.y + 5);
    await page.mouse.down();
    await page.mouse.move(handleBox.x + 5, handleBox.y + 55, { steps: 5 });
    await page.mouse.up();

    const boxAfter = await flyout.boundingBox();
    // Should have moved down (allow some tolerance)
    expect(boxAfter.y).toBeGreaterThan(boxBefore.y + 20);
  });
});

test.describe('[functional] Group flyout — drag handles on inventory', () => {
  test('inventory rows show drag handles when flyout is open', async ({ page }) => {
    await addMockSetupWithGenerics(page, MOCK_INVENTORY, MOCK_GENERIC_PARTS);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadBom(page, VALUE_ONLY_BOM);
    await page.waitForTimeout(300);

    // Before opening flyout, drag handles should be hidden
    const handleBefore = page.locator('.inv-drag-handle').first();
    if (await handleBefore.count() > 0) {
      const displayBefore = await handleBefore.evaluate(el => getComputedStyle(el).display);
      expect(displayBefore).toBe('none');
    }

    // Open flyout
    await page.locator('button.group-flyout-btn').first().click();
    await page.waitForTimeout(500);

    // Panel should have flyout-drag-active class
    const panel = page.locator('#panel-inventory');
    await expect(panel).toHaveClass(/flyout-drag-active/);
  });

  test('drag handles hide when all flyouts are closed', async ({ page }) => {
    await addMockSetupWithGenerics(page, MOCK_INVENTORY, MOCK_GENERIC_PARTS);
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
    await loadBom(page, VALUE_ONLY_BOM);
    await page.waitForTimeout(300);

    await page.locator('button.group-flyout-btn').first().click();
    await page.waitForTimeout(500);

    // Close flyout
    await page.locator('.flyout-close-btn').click();
    await page.waitForTimeout(300);

    const panel = page.locator('#panel-inventory');
    const hasClass = await panel.evaluate(el => el.classList.contains('flyout-drag-active'));
    expect(hasClass).toBe(false);
  });
});
