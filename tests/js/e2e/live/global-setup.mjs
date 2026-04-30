// tests/js/e2e/live/global-setup.mjs
import { spawn } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const E2E_SERVER = join(__dirname, '..', '..', '..', 'e2e-server.py');
const FIXTURE_DIR = join(__dirname, '..', 'fixtures', 'e2e-seed');

/**
 * Path to the file where globalSetup writes the server URL for workers to read.
 * Workers can't inherit process.env from globalSetup (separate processes),
 * so we use a file as the communication channel.
 */
export const SERVER_URL_FILE = join(__dirname, '.server-url');

/** @type {import('child_process').ChildProcess | null} */
let serverProcess = null;

/**
 * @param {import('@playwright/test').FullConfig} config
 */
export default async function globalSetup(config) {
  // Only start the server when the 'live' project is selected.
  const liveProject = config.projects.find(p => p.name === 'live');
  if (!liveProject) return;

  const pythonExe = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');

  const child = spawn(pythonExe, [
    E2E_SERVER,
    '--fixture-dir', FIXTURE_DIR,
    '--port', '0',
  ], {
    stdio: ['ignore', 'pipe', 'pipe'],
  });

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

  serverProcess = child;
  process.env.E2E_SERVER_URL = url;

  // Write the URL to a file so worker processes can read it.
  writeFileSync(SERVER_URL_FILE, url, 'utf8');
}

export { serverProcess };
