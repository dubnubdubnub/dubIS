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
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
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
        const m = buf.match(/READY port=(\d+) sid=(\S+)/);
        if (m) {
          clearTimeout(timeout);
          resolve({ port: m[1], sid: m[2] });
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

    // Success state: the page reports the upload landed.
    await expect(page.locator('#msg.ok')).toContainText('Sent — check the desktop app');

    // The backend actually ran OCR and fired the desktop UI push.
    await expect.poll(() => {
      const rec = JSON.parse(readFileSync(recordPath, 'utf8'));
      return rec.ocr_calls.length;
    }).toBeGreaterThan(0);

    const rec = JSON.parse(readFileSync(recordPath, 'utf8'));
    expect(rec.ocr_calls[0]).toMatchObject({ filename: 'po.png', template: 'lcsc' });
    expect(rec.js_calls.length).toBeGreaterThan(0);
    expect(rec.js_calls[0]).toContain('window._scanReceived(');
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
    await expect(page.locator('#msg.ok')).toContainText('Sent — check the desktop app');

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
