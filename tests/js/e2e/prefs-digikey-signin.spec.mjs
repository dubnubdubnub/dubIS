// @ts-check
/* prefs-digikey-signin.spec.mjs — the DigiKey section of Preferences in the
   real modal: Sign in mints a pairing code (create_digikey_pairing), the code,
   countdown and instruction render, and the extra height does not collapse the
   category-colour sliders (the flex-column scroll-pane trap in CLAUDE.md). The
   poll/expiry/logout behaviour is covered by tests/js/digikey-pairing.test.js. */
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { waitForInventoryRows } from './helpers.mjs';
import { installRouteMocks } from './route-mocks.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MOCK_INVENTORY = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'inventory.json'), 'utf8'));

for (const [w, h] of [[1200, 700], [800, 600]]) {
  test(`Sign in to DigiKey shows a pairing code at ${w}x${h}`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h });
    await installRouteMocks(page, MOCK_INVENTORY, { digikeyPairing: { nonce: 'DK-E2ECODE', ttl: 600 } });
    await page.goto('/index.html');
    await waitForInventoryRows(page);

    await page.locator('#prefs-btn').click();
    await expect(page.locator('#prefs-modal')).toBeVisible();
    await expect(page.locator('#dk-status')).toHaveText('Not logged in');
    await expect(page.locator('#dk-logout')).toBeHidden();

    await page.locator('#dk-login').click();
    await expect(page.locator('#dk-code')).toHaveText('DK-E2ECODE');
    await expect(page.locator('#dk-countdown')).toContainText('Good for');
    await expect(page.locator('#dk-pairing')).toContainText('digikey.com');
    await expect(page.locator('#dk-pairing [data-act="copy"]')).toBeVisible();

    const slidersH = await page.locator('#prefs-sliders').evaluate((el) => el.getBoundingClientRect().height);
    expect(slidersH, 'category sliders collapsed after the pairing panel appeared').toBeGreaterThan(40);

    // Closing the modal drops the code; reopening starts clean.
    await page.keyboard.press('Escape');
    await expect(page.locator('#prefs-modal')).toBeHidden();
    await page.locator('#prefs-btn').click();
    await expect(page.locator('#dk-pairing')).toBeHidden();
  });
}
