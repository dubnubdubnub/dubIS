// @ts-check
/*
 * End-to-end coverage for the PO source-image preview + lightbox.
 *
 * Flow (realistic interactions — real clicks + real keyboard, no force/dispatch):
 *   1. The PO picker (left "Purchase Import" panel) lists POs on load.
 *   2. Expanding a PO whose source file is an image renders a thumbnail on the
 *      right edge of the detail, alongside the line-items table.
 *   3. Clicking the thumbnail opens a full-screen lightbox showing the image.
 *   4. Pressing Escape closes the lightbox.
 *   5. A PO whose source is a non-image (CSV) shows NO thumbnail.
 *
 * The backend method get_po_source_preview is stubbed to return a data: URI
 * (image kind) or {kind:"none"}, mirroring the real Python contract. The
 * frontend gates the preview fetch on source_file_ext, so the CSV PO never
 * requests one.
 */
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { waitForInventoryRows } from './helpers.mjs';
import { installRouteMocks } from './route-mocks.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BASE_INVENTORY = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'),
);

// A 1×1 red PNG as a data URI — what the stubbed backend "renders".
const PNG_DATA_URI =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC';

const IMG_PO = 'PO-IMG';
const CSV_PO = 'PO-CSV';

const POS = [
  { po_id: IMG_PO, purchase_date: '2026-05-02', vendor_id: 'v_lcsc',
    source_file_hash: 'abc123', source_file_ext: '.png' },
  { po_id: CSV_PO, purchase_date: '2026-05-01', vendor_id: 'v_lcsc',
    source_file_hash: 'def456', source_file_ext: '.csv' },
];

// Was addPreviewStubs — a window.pywebview.api bridge patch for
// list_purchase_orders/get_po_with_items/get_po_source_preview. All three are
// now plain ctx.options-driven /v1 route mocks in route-mocks.mjs
// (purchaseOrders, poWithItems, poSourcePreview — keyed by po_id), so this
// spec just supplies the same fixture data as installRouteMocks options.
const PO_WITH_ITEMS = {
  [IMG_PO]: {
    po_id: IMG_PO,
    line_items: [{ mpn: 'PART-1', manufacturer: 'ACME', package: '0402', quantity: 10 }],
  },
  [CSV_PO]: {
    po_id: CSV_PO,
    line_items: [{ mpn: 'PART-1', manufacturer: 'ACME', package: '0402', quantity: 10 }],
  },
};
const PO_SOURCE_PREVIEW = {
  [IMG_PO]: { kind: 'image', mime: 'image/png', data_uri: PNG_DATA_URI, width: 1, height: 1, page_count: 1 },
  [CSV_PO]: { kind: 'none', reason: 'unsupported source type .csv' },
};

test.describe('PO source-image preview', () => {
  test.beforeEach(async ({ page }) => {
    await installRouteMocks(page, BASE_INVENTORY, {
      purchaseOrders: POS,
      poWithItems: PO_WITH_ITEMS,
      poSourcePreview: PO_SOURCE_PREVIEW,
    });
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto('/index.html');
    await waitForInventoryRows(page);
  });

  test('expand → thumbnail on right edge → click → lightbox → Esc closes', async ({ page }) => {
    // POs render newest-first, so the image PO (2026-05-02) is the first row.
    const imgRow = page.locator('.label-po-row').first();
    await expect(imgRow.locator('.label-po-label')).toContainText(IMG_PO);

    // No detail/thumbnail until expanded.
    await expect(imgRow.locator('.label-po-thumb-img')).toHaveCount(0);

    // Expand: line items appear AND a thumbnail loads on the right edge.
    await imgRow.locator('.label-po-expand').click();
    await expect(imgRow.locator('.label-po-items')).toBeVisible();
    const thumb = imgRow.locator('.label-po-thumb-img');
    await expect(thumb).toBeVisible();
    await expect(thumb).toHaveAttribute('src', PNG_DATA_URI);

    // The thumbnail sits to the right of the items table (right edge).
    const itemsBox = await imgRow.locator('.label-po-detail-items').boundingBox();
    const thumbBox = await thumb.boundingBox();
    expect(thumbBox.x).toBeGreaterThan(itemsBox.x + itemsBox.width - 1);

    // Lightbox is hidden until the thumbnail is clicked.
    const modal = page.locator('#po-image-modal');
    await expect(modal).toBeHidden();

    await thumb.click();
    await expect(modal).toBeVisible();
    await expect(modal.locator('.po-image-modal-img')).toHaveAttribute('src', PNG_DATA_URI);

    // Escape closes the lightbox.
    await page.keyboard.press('Escape');
    await expect(modal).toBeHidden();
  });

  test('clicking the dimmed backdrop closes the lightbox', async ({ page }) => {
    const imgRow = page.locator('.label-po-row').first();
    await imgRow.locator('.label-po-expand').click();
    await imgRow.locator('.label-po-thumb-img').click();

    const modal = page.locator('#po-image-modal');
    await expect(modal).toBeVisible();
    // Click the overlay at a corner (the backdrop, not the centered image).
    await modal.click({ position: { x: 5, y: 5 } });
    await expect(modal).toBeHidden();
  });

  test('non-image (CSV) source shows no thumbnail', async ({ page }) => {
    // The CSV PO (2026-05-01) is the second, older row.
    const csvRow = page.locator('.label-po-row').nth(1);
    await expect(csvRow.locator('.label-po-label')).toContainText(CSV_PO);

    await csvRow.locator('.label-po-expand').click();
    // Items still render, but no thumbnail (frontend skips the fetch for .csv).
    await expect(csvRow.locator('.label-po-items')).toBeVisible();
    await expect(csvRow.locator('.label-po-thumb-img')).toHaveCount(0);
  });
});
