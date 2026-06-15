// @ts-check
//
// Capture-page upload E2E (phone-scan PO feature, Task 4 test #1).
//
// Harness note: the default JS E2E webServer (serve-static.mjs) serves static
// files only and cannot render the Python /scan capture page or run the upload
// route. Rather than invent a parallel harness, this spec boots the REAL
// pnp_server via tests/js/e2e/scan-server.py on an ephemeral port (a fake
// api/window record OCR + UI-push calls), then drives the genuinely-served
// mobile capture page with real user interactions:
//   - real navigation to http://127.0.0.1:<port>/scan?s=<valid_session>
//   - real setInputFiles on the camera <input type=file accept image capture>
//   - real .click() on the Send button
// and asserts the success state plus that the backend ran OCR and fired the
// window._scanReceived push. No dispatchEvent, no force, no faked clicks.
//
// Prefer throwing over silent failure: helper code aborts loudly if the server
// process never prints its READY handshake.

import { test, expect } from '@playwright/test';
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SCAN_SERVER = join(__dirname, 'scan-server.py');
const PYTHON = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');

// A tiny valid 1x1 PNG so the upload carries a real (valid-extension) image.
const PNG_1X1_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAAWg' +
  'mWQ0AAAAASUVORK5CYII=';
const PNG_1X1 = Buffer.from(PNG_1X1_B64, 'base64');

/** @type {import('child_process').ChildProcess} */
let serverProc;
let baseUrl;
let sessionId;
let recordPath;
let recordDir;
let dataDir;

test.describe('Phone-scan capture page → upload', () => {
  test.beforeAll(async () => {
    recordDir = mkdtempSync(join(tmpdir(), 'dubis-scan-'));
    recordPath = join(recordDir, 'record.json');
    serverProc = spawn(PYTHON, [SCAN_SERVER, '--template', 'lcsc', '--record', recordPath], {
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    serverProc.stderr.on('data', (c) => process.stderr.write(`[scan-server] ${c}`));

    const handshake = await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        serverProc.kill();
        reject(new Error('scan-server did not print READY within 15s'));
      }, 15_000);
      let buf = '';
      serverProc.stdout.on('data', (chunk) => {
        buf += chunk.toString();
        const m = buf.match(/READY port=(\d+) sid=(\S+) data_dir=(.+)/);
        if (m) {
          clearTimeout(timeout);
          resolve({ port: m[1], sid: m[2], dataDir: m[3].trim() });
        }
      });
      serverProc.on('exit', (code) => {
        clearTimeout(timeout);
        reject(new Error(`scan-server exited with code ${code} before READY`));
      });
      serverProc.on('error', (err) => {
        clearTimeout(timeout);
        reject(new Error(`Failed to spawn scan-server: ${err.message}`));
      });
    });

    baseUrl = `http://127.0.0.1:${handshake.port}`;
    sessionId = handshake.sid;
    dataDir = handshake.dataDir;
  });

  test.afterAll(async () => {
    if (serverProc && !serverProc.killed) serverProc.kill();
    if (recordDir) rmSync(recordDir, { recursive: true, force: true });
  });

  test('valid session renders camera input, real upload reaches the success state', async ({ page }) => {
    await page.goto(`${baseUrl}/scan?s=${sessionId}`);

    // The camera <input> is present with the mobile capture attributes.
    const fileInput = page.locator('#file');
    await expect(fileInput).toHaveCount(1);
    await expect(fileInput).toHaveAttribute('accept', 'image/*');
    await expect(fileInput).toHaveAttribute('capture', 'environment');

    // Send is disabled until a photo is chosen.
    const sendBtn = page.locator('#send');
    await expect(sendBtn).toBeDisabled();

    // Real file-chooser path: attach a tiny PNG (buffer, no temp file needed).
    await fileInput.setInputFiles({ name: 'po.png', mimeType: 'image/png', buffer: PNG_1X1 });

    // Preview shows and Send enables once the FileReader resolves.
    await expect(sendBtn).toBeEnabled();
    await expect(page.locator('#preview')).toBeVisible();

    // Real click → real POST to /api/scan/upload.
    await sendBtn.click();

    // The progress UI appears and the bar completes at 100%.
    await expect(page.locator('#progress-wrap')).toBeVisible();
    await expect(page.locator('#progress-bar')).toHaveAttribute('style', /width:\s*100%/);

    // Success state: the page reports the upload landed.
    await expect(page.locator('#msg.ok')).toContainText('Found 1 item');

    // The backend actually ran OCR and fired the desktop UI push.
    await expect.poll(() => {
      const rec = JSON.parse(readFileSync(recordPath, 'utf8'));
      return rec.ocr_calls.length;
    }).toBeGreaterThan(0);

    const rec = JSON.parse(readFileSync(recordPath, 'utf8'));
    expect(rec.ocr_calls[0]).toMatchObject({ filename: 'po.png', template: 'lcsc' });
    expect(rec.js_calls.length).toBeGreaterThan(0);
    expect(rec.js_calls[0]).toContain('window._scanReceived(');

    // The raw photo was saved to <data_dir>/scans the moment it was uploaded.
    const scansDir = join(dataDir, 'scans');
    expect(existsSync(scansDir)).toBe(true);
    const saved = readdirSync(scansDir).filter((f) => f.endsWith('.png'));
    expect(saved.length).toBeGreaterThan(0);
  });

  test('after upload, the OCR tokens are overlaid on the captured photo with a verdict', async ({ page }) => {
    await page.goto(`${baseUrl}/scan?s=${sessionId}`);
    await page.locator('#file').setInputFiles({ name: 'po.png', mimeType: 'image/png', buffer: PNG_1X1 });
    await expect(page.locator('#send')).toBeEnabled();

    // No overlay boxes before the OCR result comes back.
    await expect(page.locator('#ocr-overlay-layer .ocr-box')).toHaveCount(0);

    await page.locator('#send').click();

    // The verdict reports the parsed item count (the mock returns 1 row).
    await expect(page.locator('#msg.ok')).toContainText('Found 1 item');

    // The two detected tokens from the mock OCR are overlaid on the preview, and
    // each box is positioned as a percentage of the page dimensions.
    const boxes = page.locator('#ocr-overlay-layer .ocr-box');
    await expect(boxes).toHaveCount(2);
    await expect(page.locator('#preview-wrap')).toBeVisible();
    // First word: x=1,y=1,w=4,h=2 of a 10x10 page → 10%/10%/40%/20%.
    await expect(boxes.first()).toHaveAttribute('style', /left:\s*10%/);
    await expect(boxes.first()).toHaveAttribute('style', /width:\s*40%/);
  });

  test('"Save to Photos" button appears when sharing is available and shares the file', async ({ page }) => {
    // iOS Safari supports navigator.share with files; headless Chromium does
    // not. Stub the Web Share API before navigation so this exercises the page
    // wiring deterministically on any platform (the spec asserts the button is
    // revealed and that clicking it calls share() with the captured file).
    await page.addInitScript(() => {
      const shared = [];
      // @ts-ignore - augment for assertion
      window.__sharedFiles = shared;
      Object.defineProperty(navigator, 'canShare', {
        configurable: true,
        value: (data) => !!(data && data.files && data.files.length),
      });
      Object.defineProperty(navigator, 'share', {
        configurable: true,
        value: (data) => {
          (data.files || []).forEach((f) => shared.push(f.name));
          return Promise.resolve();
        },
      });
    });

    await page.goto(`${baseUrl}/scan?s=${sessionId}`);

    const saveBtn = page.locator('#save-photos');
    await expect(saveBtn).toBeHidden();

    // Select a photo → the Save to Photos button is revealed.
    await page.locator('#file').setInputFiles({ name: 'po.png', mimeType: 'image/png', buffer: PNG_1X1 });
    await expect(saveBtn).toBeVisible();

    // Clicking it shares the captured file (our stub records the filename).
    await saveBtn.click();
    await expect.poll(() => page.evaluate(() => window.__sharedFiles)).toContain('po.png');
  });

  test('library input (no capture) lets you upload an existing photo', async ({ page }) => {
    await page.goto(`${baseUrl}/scan?s=${sessionId}`);

    // The library <input> accepts images but deliberately has NO capture
    // attribute, so the phone offers the gallery/file picker instead of forcing
    // the camera.
    const libraryInput = page.locator('#file-library');
    await expect(libraryInput).toHaveCount(1);
    await expect(libraryInput).toHaveAttribute('accept', 'image/*');
    expect(await libraryInput.getAttribute('capture')).toBeNull();

    const sendBtn = page.locator('#send');
    await expect(sendBtn).toBeDisabled();

    // Real file-chooser path on the library input.
    await libraryInput.setInputFiles({ name: 'existing.png', mimeType: 'image/png', buffer: PNG_1X1 });
    await expect(sendBtn).toBeEnabled();
    await expect(page.locator('#preview')).toBeVisible();

    await sendBtn.click();
    await expect(page.locator('#msg.ok')).toContainText('Found 1 item');

    // The upload from the library input also reaches OCR with its filename.
    await expect.poll(() => {
      const rec = JSON.parse(readFileSync(recordPath, 'utf8'));
      return rec.ocr_calls.some((c) => c.filename === 'existing.png');
    }).toBe(true);
  });

  test('unknown session shows the expired page (404)', async ({ page }) => {
    const resp = await page.goto(`${baseUrl}/scan?s=bogus-session`);
    expect(resp?.status()).toBe(404);
    await expect(page.locator('body')).toContainText(/expired/i);
  });
});
