/**
 * Playwright helpers for live-backend E2E tests.
 *
 * Instead of injecting static mock data, these helpers spawn a real Python
 * server (tests/e2e-server.py) and wire window.pywebview.api methods to HTTP
 * calls against it. File dialogs and distributor fetches remain mocked since
 * they require OS-level interaction or network access.
 */

import { spawn } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

// Re-export helpers that work unchanged with both mock and live backends.
export { waitForInventoryRows, loadBomViaFileInput, loadPurchaseOrder } from './helpers.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const E2E_SERVER = join(__dirname, '..', '..', 'e2e-server.py');
const FIXTURE_DIR = join(__dirname, 'fixtures', 'e2e-seed');

/**
 * Spawn the Python E2E server, wait for READY:<port>, return control handles.
 *
 * @returns {Promise<{url: string, process: import('child_process').ChildProcess, reset: () => Promise<void>, cleanup: () => Promise<void>}>}
 */
export async function startServer() {
  const pythonExe = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');

  const child = spawn(pythonExe, [
    E2E_SERVER,
    '--fixture-dir', FIXTURE_DIR,
    '--port', '0',
  ], {
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  // Pipe stderr to parent with prefix for debuggability.
  child.stderr.on('data', (chunk) => {
    for (const line of chunk.toString().split('\n')) {
      if (line) process.stderr.write(`[e2e-server] ${line}\n`);
    }
  });

  const url = await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      child.kill();
      reject(new Error('e2e-server did not print READY:<port> within 15 seconds'));
    }, 15_000);

    let buffer = '';
    child.stdout.on('data', (chunk) => {
      buffer += chunk.toString();
      const match = buffer.match(/READY:(\d+)/);
      if (match) {
        clearTimeout(timeout);
        resolve(`http://127.0.0.1:${match[1]}`);
      }
    });

    child.on('error', (err) => {
      clearTimeout(timeout);
      reject(new Error(`Failed to spawn e2e-server: ${err.message}`));
    });

    child.on('exit', (code) => {
      clearTimeout(timeout);
      reject(new Error(`e2e-server exited with code ${code} before READY`));
    });
  });

  /** POST /api/_reset — restore fixture data and rebuild inventory. */
  async function reset() {
    const resp = await fetch(`${url}/api/_reset`, { method: 'POST' });
    const body = await resp.json();
    if (!body.ok) throw new Error(`_reset failed: ${body.error}`);
  }

  /** Kill the server process and wait for it to exit. */
  async function cleanup() {
    if (child.exitCode !== null) return; // already exited
    return new Promise((resolve) => {
      child.on('exit', resolve);
      child.kill();
    });
  }

  return { url, process: child, reset, cleanup };
}

/**
 * Inject the pywebview bridge that routes API calls to the live server.
 *
 * Methods in the MOCKED set return static values without hitting the server
 * (file dialogs, distributor fetches, session checks). All other method calls
 * are forwarded as POST /api/<method> with {args: [...]}.
 */
export function addLiveSetup(page, serverUrl) {
  return page.addInitScript((url) => {
    /** Methods that return static values — no backend call needed. */
    const MOCKED = {
      open_file_dialog:         () => null,
      save_file_dialog:         () => null,
      load_file:                () => null,
      confirm_close:            () => null,
      set_bom_dirty:            () => null,

      fetch_lcsc_product:       () => null,
      fetch_digikey_product:    () => null,
      fetch_pololu_product:     () => null,
      fetch_mouser_product:     () => null,

      get_price_summary:        () => null,
      record_fetched_prices:    () => null,

      check_digikey_session:    () => ({ logged_in: false }),
      get_digikey_login_status: () => ({ logged_in: false }),
      start_digikey_login:      () => null,
      sync_digikey_cookies:     () => ({ logged_in: false }),
      logout_digikey:           () => null,
    };

    window.pywebview = {
      api: new Proxy({}, {
        get(_target, method) {
          if (typeof method !== 'string') return undefined;

          // Return mocked method if it exists.
          if (method in MOCKED) {
            return async (..._args) => MOCKED[method]();
          }

          // Otherwise, proxy to the live server.
          return async (...args) => {
            const resp = await fetch(`${url}/api/${method}`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ args }),
            });
            const body = await resp.json();
            if (!body.ok) throw new Error(`${method}: ${body.error}`);
            return body.result;
          };
        },
      }),
    };
  }, serverUrl);
}
